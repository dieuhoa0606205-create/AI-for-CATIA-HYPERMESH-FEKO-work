from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperworks_mcp import workflow as wf


class FakeBridge:
    """Records every call and answers with the shape the real bridge returns."""

    def __init__(self, surfaces, solids=(41, 42), duplicate_ids=(), model_counts=None):
        self.surfaces = list(surfaces)
        self.solids = list(solids)
        self.duplicate_ids = list(duplicate_ids)
        self.model_counts = model_counts or {"surface": 0, "element": 0}
        self.calls: list[tuple[str, dict]] = []
        self.components: dict[str, int] = {"default": 1}
        self.next_component = 100
        self.import_count = 0

    def __call__(self, method, params, timeout=None):
        self.calls.append((method, dict(params)))
        if method == "get_model_summary":
            return {"model_name": None, "counts": dict(self.model_counts),
                    "count_errors": {}}
        if method == "list_entities":
            if params.get("entity_type") == "surface":
                return {"entities": [{"id": i, "name": f"S{i:02d}"}
                                     for i in self.surfaces]}
            return {"entities": [{"id": i, "name": n} for n, i in self.components.items()]}
        if method == "import_cad":
            self.import_count += 1
            return {"surface_ids": self.surfaces, "solid_ids": self.solids,
                    "component_ids": [1],
                    "checkpoint": {"created": True, "checkpoint_file": "cp-import.hm",
                                   "label": "before_cad_import"}}
        if method == "delete_entities":
            if params["entity_type"] == "surface":
                self.surfaces = [i for i in self.surfaces
                                 if i not in params["entity_ids"]]
            return {"deleted": params["entity_ids"]}
        if method == "remove_duplicate_surfaces":
            removed = [i for i in self.duplicate_ids if i in self.surfaces]
            self.surfaces = [i for i in self.surfaces if i not in removed]
            return {"removed": bool(removed), "deleted_ids": removed,
                    "deleted_count": len(removed)}
        if method == "stitch_free_edges":
            return {"free_edges": 0}
        if method == "create_component":
            self.next_component += 1
            self.components[params["name"]] = self.next_component
            return {"component": {"id": self.next_component, "name": params["name"]}}
        if method == "automesh_surfaces":
            count = 10 * len(params["surface_ids"])
            return {"element_count": count, "element_ids": list(range(count))}
        if method == "organize_elements":
            return {"elements_in_target": 10 * len(params["surface_ids"]),
                    "checkpoint_file": "cp.hm"}
        if method == "clear_reference_mesh":
            return {"cleared": True, "component_names": params["component_names"],
                    "component_ids": list(range(len(params["component_names"]))),
                    "deleted_element_count": 10 * 3,
                    "element_count_after": 0,
                    "checkpoint": {"created": True, "checkpoint_file": "cp-clear.hm"}}
        if method == "export_feko_mesh":
            if not params.get("template_file"):
                # Mirrors the real bridge, which resolves the template path eagerly.
                raise TypeError("expected str, bytes or os.PathLike object, not NoneType")
            path = Path(params["output_file"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stub", encoding="utf-8")
            return {"output_file": str(path)}
        raise AssertionError(f"unexpected bridge call {method}")

    def calls_named(self, method):
        return [params for name, params in self.calls if name == method]


def make_config(mode, **overrides):
    values = dict(
        input_file="model.CATPart",
        export_name="out.fhm",
        element_size=5.0,
        template_file="templates/feko.tpl",
        surface_materials={1: "A", 2: "B", 3: "A"},
        mode=mode,
        drop_surfaces=(3,),
    )
    values.update(overrides)
    return wf.WorkflowConfig(**values)


def write_pairs(tmp_path, rows):
    (tmp_path / "out.pairs.json").write_text(json.dumps(rows), encoding="utf-8")


def fake_classifier(pair_for_surface):
    """Build a classify stand-in from {surface: (minus, plus)}."""

    def fake_classify(path, material_map, targets, face_tags, samples, threshold, outside):
        items = []
        for label in targets:
            surface = int(label[1:])
            minus, plus = pair_for_surface(surface)
            items.append(SimpleNamespace(
                label=label, material=material_map[label], plus=plus, minus=minus,
                tags=tuple(face_tags.get(label, ())), agreement=1.0, margin=1.0,
            ))
        return SimpleNamespace(labels=items, unknown_labels=[], shells={})

    return fake_classify


def test_general_mode_runs_the_fixed_steps_and_groups_from_the_table(tmp_path):
    write_pairs(tmp_path, {
        "1": {"pair": "A-air", "minus": "A", "plus": "air"},
        "2": {"pair": "A-B", "minus": "A", "plus": "B"},
        "3": {"pair": "A-air", "minus": "A", "plus": "air"},
    })
    bridge = FakeBridge([1, 2, 3])

    result = wf.run(bridge, make_config(wf.GENERAL), tmp_path)

    assert [step["step"] for step in result["steps"]] == list(wf.PRODUCTION_STEPS)
    assert result["stopped_at"] is None
    # One mesh call covers every retained surface; surface 3 was dropped.
    meshes = bridge.calls_named("automesh_surfaces")
    assert len(meshes) == 1
    assert meshes[0]["surface_ids"] == [1, 2]
    assert meshes[0]["density_mode"] == "proximity_curvature"
    assert meshes[0]["mesh_algorithm"] == "free"
    # One component per distinct pair, and the elements moved by surface.
    assert sorted(group["name"] for group in result["groups"]) == ["A-B", "A-air"]
    organised = {tuple(item["surface_ids"]): item for item in bridge.calls_named("organize_elements")}
    assert set(organised) == {(1,), (2,)}
    # Export comes last and carries the group labels.
    assert bridge.calls[-1][0] == "export_feko_mesh"
    assert Path(result["exports"]["mesh"]).name == "out.fhm"


def test_surfaces_removed_as_duplicates_leave_the_mesh_plan(tmp_path):
    write_pairs(tmp_path, {
        "1": {"pair": "A-air", "minus": "A", "plus": "air"},
        "2": {"pair": "A-B", "minus": "A", "plus": "B"},
        "3": {"pair": "A-air", "minus": "A", "plus": "air"},
    })
    bridge = FakeBridge([1, 2, 3], duplicate_ids=[2])

    result = wf.run(bridge, make_config(wf.GENERAL), tmp_path)

    assert result["surfaces"]["retained"] == [1]
    assert result["surfaces"]["removed_as_duplicates"] == [2]
    meshes = bridge.calls_named("automesh_surfaces")
    assert meshes[0]["surface_ids"] == [1]
    drop = [params for params in bridge.calls_named("delete_entities")
            if params["entity_type"] == "surface"][0]
    assert drop["entity_ids"] == [3]


def test_density_mode_alias_and_mesh_algorithm_validation():
    config = make_config(wf.GENERAL, mesh_type="curvature")
    assert config.density_mode == "curvature"
    assert config.mesh_type == "curvature"
    with pytest.raises(ValueError, match="Mapped"):
        make_config(wf.GENERAL, mesh_algorithm="mapped")


def test_general_mode_without_a_pair_table_fails_before_meshing(tmp_path):
    bridge = FakeBridge([1, 2, 3])
    with pytest.raises(wf.WorkflowError) as error:
        wf.run(bridge, make_config(wf.GENERAL), tmp_path)
    assert "pair table" in str(error.value)
    assert bridge.calls_named("automesh_surfaces") == []


def test_verification_meshes_every_shell_surface_then_rebuilds_from_scratch(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: ("A", "air") if surface == 1 else ("A", "B"))
    )
    bridge = FakeBridge([1, 2, 3])

    result = wf.run(bridge, make_config(wf.VERIFICATION), tmp_path)

    expected = list(wf.REFERENCE_STEPS) + [
        step for step in wf.PRODUCTION_STEPS[1:] if step != "pairs"
    ]
    assert [step["step"] for step in result["steps"]] == expected
    # The reference pass meshes every material surface, dropped ones included.
    reference = bridge.calls_named("automesh_surfaces")[:3]
    assert [item["surface_ids"] for item in reference] == [[1], [2], [3]]
    assert all(item["component_id"] is not None for item in reference)
    # The reference components are deleted; the model is imported exactly once.
    clears = bridge.calls_named("clear_reference_mesh")
    assert clears == [{"component_names": ["S01", "S02", "S03"], "model_name": None}]
    assert bridge.import_count == 1
    # Production meshes the retained surfaces in one pass, not one by one.
    production = bridge.calls_named("automesh_surfaces")[3:]
    assert len(production) == 1
    assert production[0]["surface_ids"] == [1, 2]
    # Two exports: the reference mesh and the final grouped mesh.
    exports = bridge.calls_named("export_feko_mesh")
    assert len(exports) == 2
    assert exports[0]["output_file"].endswith("out-reference.fhm")
    assert exports[1]["output_file"].endswith("out.fhm")
    # The pair table is written for later general-mode runs.
    table = json.loads((tmp_path / "out.pairs.json").read_text(encoding="utf-8"))
    assert table["1"]["pair"] == "A-air"
    assert table["2"]["pair"] == "A-B"


def test_reference_mesh_can_use_a_coarse_size(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: ("A", "air") if surface == 1 else ("A", "B")))
    bridge = FakeBridge([1, 2, 3])
    config = make_config(
        wf.VERIFICATION, element_size=5.0, reference_element_size=20.0
    )

    wf.run(bridge, config, tmp_path)

    meshes = bridge.calls_named("automesh_surfaces")
    assert [item["element_size"] for item in meshes[:3]] == [20.0, 20.0, 20.0]
    assert [item["element_size"] for item in meshes[3:]] == [5.0]


def test_resume_produces_without_importing_or_reference_meshing(tmp_path):
    write_pairs(tmp_path, {
        "1": {"pair": "A-air", "minus": "A", "plus": "air"},
        "2": {"pair": "A-B", "minus": "A", "plus": "B"},
        "3": {"pair": "A-air", "minus": "A", "plus": "air"},
    })
    bridge = FakeBridge([1, 2, 3])
    imported = {
        "surface_ids": [1, 2, 3],
        "solid_ids": [41, 42],
        "component_ids": [1],
    }

    result = wf.run(
        bridge,
        make_config(wf.VERIFICATION),
        tmp_path,
        resume={"imported": imported},
    )

    assert bridge.import_count == 0
    assert [step["step"] for step in result["steps"]] == [
        "drop_solids",
        "duplicates",
        "drop_surfaces",
        "stitch",
        "mesh",
        "pairs",
        "groups",
        "export",
    ]
    meshes = bridge.calls_named("automesh_surfaces")
    assert len(meshes) == 1
    assert meshes[0]["surface_ids"] == [1, 2]
    assert result["import"] == imported
    assert result["resumed"] is True


def test_resume_refuses_while_reference_elements_remain(tmp_path):
    write_pairs(tmp_path, {
        "1": {"pair": "A-air", "minus": "A", "plus": "air"},
    })
    bridge = FakeBridge(
        [1, 2, 3], model_counts={"surface": 3, "element": 4}
    )

    with pytest.raises(wf.WorkflowError) as error:
        wf.run(
            bridge,
            make_config(wf.VERIFICATION),
            tmp_path,
            resume={"imported": {"surface_ids": [1, 2, 3]}},
        )

    assert "reference mesh" in str(error.value)
    assert bridge.calls_named("remove_duplicate_surfaces") == []


def test_missing_export_template_names_the_failing_step(tmp_path):
    bridge = FakeBridge([1, 2, 3])
    config = make_config(wf.VERIFICATION, template_file=None)

    with pytest.raises(wf.WorkflowError) as error:
        wf.run(bridge, config, tmp_path)

    assert "step pairs" in str(error.value)
    assert "PathLike" in str(error.value)


def test_clearing_the_reference_mesh_must_leave_no_elements(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(lambda surface: ("A", "air")))
    bridge = FakeBridge([1, 2, 3])

    original = bridge.__call__

    def dispatch(method, params, timeout=None):
        if method == "clear_reference_mesh":
            bridge.calls.append((method, dict(params)))
            return {"cleared": True, "component_names": params["component_names"],
                    "component_ids": [], "deleted_element_count": 20,
                    "element_count_after": 5,
                    "checkpoint": {"created": True, "checkpoint_file": "cp-clear.hm"}}
        return original(method, params, timeout)

    with pytest.raises(wf.WorkflowError) as error:
        wf.run(dispatch, make_config(wf.VERIFICATION), tmp_path)
    assert "step clear_reference" in str(error.value)
    assert "elements remain" in str(error.value)


def test_same_medium_surfaces_are_reported_not_grouped(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: (("air", "air") if surface == 1 else ("A", "B")))
    )
    bridge = FakeBridge([1, 2, 3])

    result = wf.run(bridge, make_config(wf.VERIFICATION), tmp_path)

    table = json.loads((tmp_path / "out.pairs.json").read_text(encoding="utf-8"))
    assert table["1"]["pair"] == "air-air"
    assert result["same_medium"] == [{"surface": 1, "pair": "air-air"}]
    assert [group["name"] for group in result["groups"]] == ["A-B"]
    assert bridge.calls_named("organize_elements")[0]["surface_ids"] == [2]


def test_same_medium_with_a_face_tag_is_grouped(tmp_path, monkeypatch):
    # An ideal-conductor sheet can sit inside one medium: air on both sides,
    # PEC on the face.  It is a boundary, not a same-medium exclusion.
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: (("air", "air") if surface == 1 else ("A", "B")))
    )
    bridge = FakeBridge([1, 2, 3])

    result = wf.run(
        bridge,
        make_config(wf.VERIFICATION, face_tags={1: ["PEC"]}),
        tmp_path,
    )

    assert result["same_medium"] == []
    assert [group["name"] for group in result["groups"]] == [
        "A-B",
        "air-air-PEC",
    ]
    moves = bridge.calls_named("organize_elements")
    assert sorted(item["surface_ids"] for item in moves) == [[1], [2]]


def test_face_tags_reach_the_pair_table(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: ("A", "air"))
    )
    bridge = FakeBridge([1, 2, 3])

    wf.run(bridge, make_config(wf.VERIFICATION, face_tags={1: ["PEC"]}), tmp_path)

    table = json.loads((tmp_path / "out.pairs.json").read_text(encoding="utf-8"))
    assert table["1"]["pair"] == "A-air-PEC"
    assert table["2"]["pair"] == "A-air"


def test_stop_after_halts_before_the_next_step(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(lambda surface: ("A", "air")))
    bridge = FakeBridge([1, 2, 3])

    result = wf.run(bridge, make_config(wf.VERIFICATION, stop_after="stitch"), tmp_path)

    assert result["stopped_at"] == "stitch"
    names = [step["step"] for step in result["steps"]]
    assert names[-1] == "stitch"
    assert names == ["import", "reference_mesh", "pairs", "clear_reference",
                     "drop_solids", "duplicates", "drop_surfaces", "stitch"]
    # Only the three reference meshes happened; the production mesh did not.
    assert len(bridge.calls_named("automesh_surfaces")) == 3


def test_import_must_create_every_retained_surface(tmp_path):
    bridge = FakeBridge([1, 2])
    config = make_config(wf.VERIFICATION, drop_surfaces=())
    with pytest.raises(wf.WorkflowError) as error:
        wf.run(bridge, config, tmp_path)
    assert "not created by the import" in str(error.value)


def test_a_non_empty_model_is_refused_before_import(tmp_path):
    bridge = FakeBridge([1, 2, 3], model_counts={"surface": 4, "element": 0})

    with pytest.raises(wf.WorkflowError) as error:
        wf.run(bridge, make_config(wf.VERIFICATION), tmp_path)

    assert "step preflight" in str(error.value)
    assert "not empty" in str(error.value)
    assert bridge.import_count == 0


def test_unknown_stop_after_is_rejected_before_the_run(tmp_path):
    bridge = FakeBridge([1, 2, 3])

    with pytest.raises(wf.WorkflowError) as error:
        wf.run(bridge, make_config(wf.VERIFICATION, stop_after="meshing"), tmp_path)

    assert "stop_after must name one of" in str(error.value)
    assert bridge.calls == []


def test_mode_is_checked(tmp_path):
    bridge = FakeBridge([1, 2, 3])
    with pytest.raises(wf.WorkflowError):
        wf.run(bridge, make_config("turbo"), tmp_path)


def test_surface_labels_keep_a_stable_width():
    assert wf.surface_label(7) == "S07"
    assert wf.surface_label(7, width=3) == "S007"

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from hyperworks_mcp import server
from hyperworks_mcp import workflow as wf
from hyperworks_mcp import workflow_stages
from hyperworks_mcp.workflow_gate import GATE, STAGE_AUTHORIZED, STAGE_CLASSIFIED


class FakeBridge:
    """Minimal live-bridge stand-in for the staged workflow."""

    def __init__(self, surfaces, solids=(41, 42), duplicate_ids=()):
        self.surfaces = list(surfaces)
        self.solids = list(solids)
        self.duplicate_ids = list(duplicate_ids)
        self.calls: list[tuple[str, dict]] = []
        self.components: dict[str, int] = {"default": 1}
        self.next_component = 100
        self.import_count = 0
        self.imported = False

    def __call__(self, method, params, timeout=None):
        self.calls.append((method, dict(params)))
        if method == "get_model_summary":
            surfaces = len(self.surfaces) if self.imported else 0
            return {"model_name": None, "counts": {"surface": surfaces,
                                                   "element": 0},
                    "count_errors": {}}
        if method == "list_entities":
            if params.get("entity_type") == "surface":
                return {"entities": [{"id": i, "name": f"S{i:02d}"}
                                     for i in self.surfaces]}
            return {"entities": [{"id": i, "name": n}
                                 for n, i in self.components.items()]}
        if method == "import_cad":
            self.import_count += 1
            self.imported = True
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
                raise TypeError("expected str, bytes or os.PathLike object, not NoneType")
            path = Path(params["output_file"])
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("stub", encoding="utf-8")
            return {"output_file": str(path)}
        raise AssertionError(f"unexpected bridge call {method}")

    def calls_named(self, method):
        return [params for name, params in self.calls if name == method]


def make_config(**overrides):
    values = dict(
        input_file="model.CATPart",
        export_name="out.fhm",
        element_size=5.0,
        reference_element_size=20.0,
        template_file="templates/feko.tpl",
        surface_materials={1: "A", 2: "B", 3: "A"},
        mode=wf.VERIFICATION,
        drop_surfaces=(3,),
    )
    values.update(overrides)
    return wf.WorkflowConfig(**values)


def fake_classifier(pair_for_surface):
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


@pytest.fixture(autouse=True)
def _reset_gate():
    GATE.reset()
    yield
    GATE.reset()


def test_start_returns_the_question_without_calling_the_bridge(tmp_path):
    result = workflow_stages.start(make_config(), tmp_path, "run-key")

    assert result["status"] == "decision_required"
    assert "材料属性判定" in result["question"]
    assert "粗尺寸" in result["coarse_mesh_hint"]
    assert [item["value"] for item in result["options"]] == ["classify", "skip"]
    assert result["must_ask_operator"] is True
    assert result["token"]
    assert result["surface_count"] == 3
    assert result["materials"] == ["A", "B"]


def test_classify_stops_before_duplicate_removal(tmp_path, monkeypatch):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: ("A", "air") if surface == 1 else ("A", "B")))
    config = make_config()
    session, _ = GATE.start("run-key")
    GATE.resolve(session.token, "classify")
    bridge = FakeBridge([1, 2, 3])

    result = workflow_stages.classify(
        GATE.session(session.token), bridge, config, tmp_path
    )

    assert result["status"] == "pair_confirmation_required"
    assert result["pairs"]["1"]["pair"] == "A-air"
    assert result["pairs"]["2"]["pair"] == "A-B"
    # The reference pass stops before any duplicate or surface deletion.
    assert bridge.calls_named("remove_duplicate_surfaces") == []
    assert [params for params in bridge.calls_named("delete_entities")
            if params["entity_type"] == "surface"] == []
    reference = bridge.calls_named("automesh_surfaces")
    assert [item["surface_ids"] for item in reference] == [[1], [2], [3]]
    assert all(item["element_size"] == 20.0 for item in reference)
    assert (tmp_path / "out.pairs.json").exists()
    assert GATE.model_gate(None)["stage"] == STAGE_CLASSIFIED


def test_produce_requires_confirmation_and_does_not_import_again(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(wf, "classify", fake_classifier(
        lambda surface: ("A", "air") if surface == 1 else ("A", "B")))
    config = make_config()
    session, _ = GATE.start("run-key")
    GATE.resolve(session.token, "classify")
    bridge = FakeBridge([1, 2, 3])
    workflow_stages.classify(GATE.session(session.token), bridge, config, tmp_path)

    pending = workflow_stages.produce(
        GATE.session(session.token), bridge, config, tmp_path, confirm_pairs=False
    )
    assert pending["status"] == "pair_confirmation_required"
    assert bridge.calls_named("remove_duplicate_surfaces") == []

    result = workflow_stages.produce(
        GATE.session(session.token), bridge, config, tmp_path, confirm_pairs=True
    )

    assert result["status"] == "complete"
    assert bridge.import_count == 1
    assert [step["step"] for step in result["steps"][-8:]] == [
        "drop_solids",
        "duplicates",
        "drop_surfaces",
        "stitch",
        "mesh",
        "pairs",
        "groups",
        "export",
    ]
    assert sorted(group["name"] for group in result["groups"]) == ["A-B", "A-air"]
    assert result["classify"]["pair_table"].endswith("out.pairs.json")


def test_skip_records_undetermined_and_touches_nothing():
    session, _ = GATE.start("run-key")

    result = workflow_stages.skip(GATE.session(session.token))

    assert result["status"] == "skipped"
    assert result["material_pairs"] == "not_determined"
    assert result["model_changed"] is False
    assert GATE.model_gate(None) is None


def test_produce_after_skip_is_blocked():
    session, _ = GATE.start("run-key")
    workflow_stages.skip(GATE.session(session.token))
    bridge = FakeBridge([1, 2, 3])

    result = workflow_stages.produce(
        GATE.session(session.token), bridge, make_config(), "/tmp/export",
        confirm_pairs=True,
    )

    assert result["status"] == "blocked"
    assert bridge.calls == []


def test_model_gate_blocks_until_classified_or_skipped():
    gate = GATE.arm_model(None, "model.CATPart")
    assert gate["stage"] == "pending"
    blocked = GATE.require_model_clear(None, "remove_duplicate_surfaces")
    assert blocked["status"] == "decision_required"

    GATE.resolve_model(None, "classify")
    assert GATE.model_gate(None)["stage"] == STAGE_AUTHORIZED
    assert GATE.require_model_clear(None, "delete_surface") is not None

    GATE.mark_model_classified(None)
    assert GATE.require_model_clear(None, "delete_surface") is None

    GATE.reset()
    GATE.arm_model(None, "model.CATPart")
    GATE.resolve_model(None, "skip")
    assert GATE.require_model_clear(None, "remove_duplicate_surfaces") is None


def test_primitive_tools_return_the_question_instead_of_deleting(monkeypatch):
    calls = []
    monkeypatch.setattr(server.live, "call", lambda *args, **kwargs: calls.append(args) or {})
    GATE.arm_model(None, "model.CATPart")

    duplicate = server.remove_live_duplicate_surfaces.fn()
    deletion = server.delete_live_entities.fn("surface", [1])
    stitch = server.stitch_live_free_edges.fn()
    trim = server.trim_live_free_edges.fn()

    assert duplicate["status"] == "decision_required"
    assert deletion["status"] == "decision_required"
    assert stitch["status"] == "decision_required"
    assert trim["status"] == "decision_required"
    assert calls == []

    GATE.resolve_model(None, "skip")
    server.remove_live_duplicate_surfaces.fn()
    server.delete_live_entities.fn("surface", [1])
    server.stitch_live_free_edges.fn()
    server.trim_live_free_edges.fn()
    assert len(calls) == 4


def test_start_tool_needs_no_live_call(monkeypatch):
    calls = []
    monkeypatch.setattr(server.projects, "input_file",
                        lambda project_id, input_name: Path("model.CATPart"))
    monkeypatch.setattr(server.live, "call", lambda *args, **kwargs: calls.append(args) or {})

    result = server.run_catia_feko_workflow.fn(
        project_id="project",
        input_name="model.CATPart",
        surface_materials={"1": "A"},
        element_size=5.0,
        export_name="out.fhm",
        action="start",
    )

    assert result["status"] == "decision_required"
    assert result["token"]
    assert calls == []

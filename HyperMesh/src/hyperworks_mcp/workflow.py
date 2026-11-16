"""One fixed workflow: a CATIA export becomes a FEKO-ready mesh with groups.

The production chain is always the same nine steps, in this order:

    import -> drop_solids -> duplicates -> drop_surfaces -> stitch
           -> mesh -> pairs -> groups -> export

The caller picks a mode, and the mode decides where the surface-to-surface
material pairs come from:

``verification``
    The pairs are computed here.  The winding-number method needs every
    material to be a closed shell, so this pass first meshes *all* surfaces
    that carry a material - including the duplicates and the surfaces the
    operator will exclude later - then exports that reference mesh, classifies
    it, and writes the pair table.  The reference components are then deleted
    together with their elements, so the production chain continues in the
    same model with a single import from start to finish.  Slow, and it never
    leaves reference elements in the exported mesh.

``general``
    The whole model is meshed in one pass and the pairs are read from the table
    the verification pass wrote.  The pair table is a property of the geometry,
    so repeat runs on the same model skip the reference pass.  Fast, and
    nothing here assumes a particular material set, surface count, naming
    scheme or element size.

Both modes end with one component per boundary group and a single ``.fhm``
export whose labels are the group names.  A surface whose two sides resolve to
the same medium carries no material contrast; without a face tag it is
reported separately and left out of the groups, while a tagged sheet inside
one medium (a perfect conductor in air, for example) is a real boundary and is
grouped under its repeated medium name.  Both modes import into an empty model; a preflight
check refuses to start when surfaces or elements are already present, because
the imported IDs would no longer match the caller's surface map.  Every step
records what it did, and ``stop_after`` halts the chain at a named step for
inspection; an unknown step name is rejected before the run starts.

Grouping is decided per surface, not per element
------------------------------------------------
The reviewable way to group is to decide before the production mesh which
surfaces belong to which boundary group: one ordered material pair plus the
face tags yields one group name per surface, and a group can then be moved by
selecting its surfaces instead of its elements.  That decision is what the
pair table records; it does not depend on the mesh.

The link between a surface and its elements can be known in two ways.

The explicit way is to mesh one surface at a time into a component named for
that surface.  The reference pass does exactly this: ``automesh_surfaces`` is
called once per surface with that surface's component, and the in-application
handler refuses a result that places any created element outside the
requested component.  Component membership is then the mesh-to-surface
record, and nothing has to be inferred later.

The production pass deliberately does not do this.  Meshing all surfaces in
one call keeps adjacent surfaces conformal across shared edges; meshing each
surface separately can leave duplicate nodes at a boundary between groups.
So the production mesh is one call, and the only link between an element and
its surface is the association HyperMesh creates while meshing.  Grouping
selects elements through that association (``by surface``).  This module does
not read the association back or verify it.  Before a grouped mesh is
accepted, mark every planned surface, count its elements, check every count is
non-zero, check the counts sum to the mesh total, and check no element is
claimed by two surfaces.  Without that check the result is expected to be
correct, not verified.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Mapping, Sequence

from .material_pairs import OUTSIDE, classify, group_name

Executor = Callable[..., dict]

GENERAL = "general"
VERIFICATION = "verification"
MODES = (GENERAL, VERIFICATION)

PRODUCTION_STEPS = (
    "import",
    "drop_solids",
    "duplicates",
    "drop_surfaces",
    "stitch",
    "mesh",
    "pairs",
    "groups",
    "export",
)

REFERENCE_STEPS = (
    "import",
    "reference_mesh",
    "pairs",
    "clear_reference",
)

STEPS = PRODUCTION_STEPS


@dataclass
class WorkflowConfig:
    """Everything one run needs; no value is inferred from the model."""

    input_file: str
    export_name: str
    element_size: float
    surface_materials: Mapping[int, str]
    reference_element_size: float | None = None
    mode: str = GENERAL
    translator: str = "catia_ct"
    drop_solids: bool = True
    drop_surfaces: Sequence[int] = ()
    duplicate_tolerance: float = 0.1
    stitch_tolerance: float = 0.2
    element_type: str = "tria"
    mesh_type: str | None = None
    density_mode: str = "proximity_curvature"
    mesh_algorithm: str = "free"
    min_size: float | None = None
    max_size: float | None = None
    face_tags: Mapping[int, Sequence[str]] = field(default_factory=dict)
    samples: int = 10
    threshold: float = 0.55
    outside: str = OUTSIDE
    template_file: str | None = None
    stop_after: str | None = None

    def __post_init__(self) -> None:
        if self.mesh_type in ("free", "mapped"):
            if self.mesh_algorithm != "free" and self.mesh_algorithm != self.mesh_type:
                raise ValueError("mesh_algorithm and mesh_type disagree")
            self.mesh_algorithm = self.mesh_type
            self.mesh_type = None
        if self.mesh_type is not None:
            if (
                self.density_mode != "proximity_curvature"
                and self.density_mode != self.mesh_type
            ):
                raise ValueError("density_mode and mesh_type disagree")
            self.density_mode = self.mesh_type
        # Keep the old read path working; the value is a density mode, not the
        # GUI Mesh type (Free/Mapped).
        self.mesh_type = self.density_mode
        if self.mesh_algorithm != "free":
            raise ValueError(
                "Mapped surface meshing is not implemented; "
                "use mesh_algorithm='free'"
            )

    def retained(self) -> list[int]:
        dropped = set(self.drop_surfaces)
        return [surface for surface in sorted(self.surface_materials) if surface not in dropped]


class WorkflowError(RuntimeError):
    """A step failed; the message names the step and what it was doing."""


def surface_label(surface: int, width: int = 2) -> str:
    """Component and export label for one CAD surface."""
    return f"S{surface:0{width}d}"


def pair_table_name(export_name: str) -> str:
    return Path(export_name).stem + ".pairs.json"


def run(
    executor: Executor,
    config: WorkflowConfig,
    export_dir: str | Path,
    resume: Mapping | None = None,
) -> dict:
    """Run the chain and return every step's result.

    ``resume`` continues a run whose import and reference pass already
    happened.  It carries the import result under ``imported`` and skips the
    empty-model preflight, the import and the reference pass; the production
    chain starts at ``drop_solids``.  The reference mesh must already be
    cleared, and the pair table must already exist.
    """
    if config.mode not in MODES:
        raise WorkflowError(f"mode must be one of {MODES}, got {config.mode!r}")
    if not config.surface_materials:
        raise WorkflowError("surface_materials must name at least one surface")
    resuming = resume is not None
    allowed_steps = set(PRODUCTION_STEPS)
    if config.mode == VERIFICATION and not resuming:
        allowed_steps |= set(REFERENCE_STEPS)
    if config.stop_after is not None and config.stop_after not in allowed_steps:
        raise WorkflowError(
            f"stop_after must name one of {sorted(allowed_steps)}, got {config.stop_after!r}")

    export_dir = Path(export_dir)
    export_dir.mkdir(parents=True, exist_ok=True)
    retained = config.retained()
    if not retained:
        raise WorkflowError("every surface was dropped; nothing to mesh")
    width = max(2, len(str(max(config.surface_materials))))
    table_path = export_dir / pair_table_name(config.export_name)
    if config.mode == GENERAL and not table_path.exists():
        # Fail before touching the model: general mode is a repeat run, and
        # without the table there is nothing to group by.
        raise WorkflowError(
            f"general mode needs a pair table at {table_path}; "
            "run the same model once in verification mode first"
        )
    if resuming and not table_path.exists():
        # The classify stage must have written the table before production.
        raise WorkflowError(
            f"a resumed run needs the pair table at {table_path}; "
            "run the classify stage first"
        )

    result: dict = {
        "mode": config.mode,
        "resumed": resuming,
        "steps": [],
        "import": None,
        "surfaces": {"retained": retained, "dropped": sorted(config.drop_surfaces)},
        "groups": [],
        "same_medium": [],
        "exports": {},
        "stopped_at": None,
    }

    def record(name: str, detail: dict) -> bool:
        result["steps"].append({"step": name, "ok": True, "detail": detail})
        if config.stop_after == name:
            result["stopped_at"] = name
            return True
        return False

    current = {"step": "preflight"}

    def call(method: str, params: dict, timeout: int = 300) -> dict:
        try:
            return executor(method, params, timeout=timeout)
        except Exception as exc:  # noqa: BLE001 - re-raised with the step name
            raise WorkflowError(f"step {current['step']}: {method} failed: {exc}") from exc

    def import_cad(surfaces_to_check: Sequence[int]) -> dict:
        imported = call(
            "import_cad",
            {
                "input_file": config.input_file,
                "translator": config.translator,
                "options": None,
                "model_name": None,
            },
            timeout=900,
        )
        missing = [
            surface for surface in surfaces_to_check
            if surface not in imported.get("surface_ids", [])
        ]
        if missing:
            raise WorkflowError(f"surfaces {missing} were not created by the import")
        return imported

    def mesh_params(reference: bool = False, **extra) -> dict:
        element_size = config.element_size
        if reference and config.reference_element_size is not None:
            element_size = config.reference_element_size
        return {
            "element_size": element_size,
            "element_type": config.element_type,
            "density_mode": config.density_mode,
            "mesh_algorithm": config.mesh_algorithm,
            "min_size": config.min_size,
            "max_size": config.max_size,
            "keep_existing_mesh": True,
            "model_name": None,
            **extra,
        }

    if resuming:
        # A resumed run starts after the import and the reference pass.  The
        # geometry is already there; only the reference elements must be gone.
        imported = _resume_import(resume)
        current["step"] = "preflight"
        summary = call("get_model_summary",
                       {"entity_types": ["surface", "element"], "model_name": None})
        counts = summary.get("counts", {})
        existing_elements = int(counts.get("element", 0) or 0)
        if existing_elements:
            raise WorkflowError(
                "step preflight: a resumed run expects the reference mesh to be "
                f"cleared, but {existing_elements} element(s) remain; clear the "
                "reference mesh before producing")
        existing = _surface_ids(call)
        missing = [surface for surface in imported["surface_ids"]
                   if surface not in existing]
        if missing:
            raise WorkflowError(
                f"step preflight: imported surfaces {missing} are missing; "
                "the model was changed or reloaded after classification")
        components = _component_index(call)
        result["import"] = imported
    else:
        # Both modes import into an empty model: imported entity IDs must be
        # the IDs the caller's surface map names.  Fail loudly rather than let
        # a non-empty model shift them.
        summary = call("get_model_summary",
                       {"entity_types": ["surface", "element"], "model_name": None})
        counts = summary.get("counts", {})
        existing_surfaces = int(counts.get("surface", 0) or 0)
        existing_elements = int(counts.get("element", 0) or 0)
        if existing_surfaces or existing_elements:
            raise WorkflowError(
                "step preflight: the model is not empty "
                f"({existing_surfaces} surface(s), {existing_elements} element(s) present); "
                "the workflow imports into an empty model - clear the model or start a "
                "new HyperMesh session")

        current["step"] = "import"
        components = _component_index(call)

    if not resuming and config.mode == VERIFICATION:
        # Reference pass: every surface that carries a material, meshed on its
        # own, so each material's shell is complete.  Duplicates and surfaces
        # the operator will exclude stay in on purpose; removing them first
        # would open the shells and corrupt the winding numbers.
        shell_surfaces = sorted(config.surface_materials)

        current["step"] = "import"
        reference_import = import_cad(shell_surfaces)
        imported = reference_import
        result["import"] = imported
        if record("import", {"solids": reference_import.get("solid_ids", []),
                             "surfaces": reference_import.get("surface_ids", []),
                             "components": reference_import.get("component_ids", [])}):
            return result

        current["step"] = "reference_mesh"
        # One surface per call, into the component named for that surface: the
        # component is the explicit mesh-to-surface record.  The handler refuses
        # a result that places created elements outside the requested component.
        reference_total = 0
        for surface in shell_surfaces:
            label = surface_label(surface, width)
            component_id = _ensure_component(call, components, label)
            outcome = call(
                "automesh_surfaces",
                mesh_params(reference=True, surface_ids=[surface],
                            component_id=component_id),
                timeout=900,
            )
            if not outcome.get("element_count"):
                raise WorkflowError(f"reference surface {surface} produced no elements")
            reference_total += int(outcome["element_count"])
        if record("reference_mesh", {"per_surface": True,
                                     "surfaces": shell_surfaces,
                                     "element_count": reference_total}):
            return result

        current["step"] = "pairs"
        reference_export = export_dir / (Path(config.export_name).stem + "-reference.fhm")
        call("export_feko_mesh", {"output_file": str(reference_export),
                                  "template_file": config.template_file,
                                  "model_name": None}, timeout=600)
        material_map = {
            surface_label(surface, width): name
            for surface, name in config.surface_materials.items()
        }
        tags = {
            surface_label(surface, width): list(tags)
            for surface, tags in config.face_tags.items()
            if surface in config.surface_materials
        }
        report = classify(
            reference_export,
            material_map,
            targets=sorted(material_map),
            face_tags=tags,
            samples=config.samples,
            threshold=config.threshold,
            outside=config.outside,
        )
        label_to_surface = {surface_label(surface, width): surface for surface in shell_surfaces}
        pairs = {
            str(label_to_surface[item.label]): {
                "pair": group_name(item.minus, item.plus, item.tags),
                "material": item.material,
                "minus": item.minus,
                "plus": item.plus,
                "tags": list(item.tags),
                "agreement": round(item.agreement, 4),
                "margin": round(item.margin, 4),
            }
            for item in report.labels
            if item.label in label_to_surface
        }
        table_path.write_text(json.dumps(pairs, indent=2, ensure_ascii=False), encoding="utf-8")
        if record("pairs", {"source": "computed", "table": str(table_path),
                            "reference_mesh": str(reference_export),
                            "shells": {name: shell.triangle_count
                                       for name, shell in sorted(report.shells.items())},
                            "unknown_labels": report.unknown_labels,
                            "undecided": [item.label for item in report.labels
                                          if item.agreement < 1.0]}):
            return result

        # Delete the reference components and their elements so the reference
        # mesh cannot leak into the exported model.  The geometry stays, and the
        # production chain continues in this same model - no model reload, which
        # keeps the CAD importer usable for the rest of the session.
        current["step"] = "clear_reference"
        reference_components = [surface_label(surface, width) for surface in shell_surfaces]
        cleared = call(
            "clear_reference_mesh",
            {"component_names": reference_components, "model_name": None},
            timeout=1200,
        )
        if cleared.get("element_count_after"):
            raise WorkflowError(
                "step clear_reference: elements remain after the reference "
                f"components were deleted: {cleared['element_count_after']}"
            )
        components = _component_index(call)
        if record("clear_reference", {"component_names": reference_components,
                                      "deleted_element_count": cleared.get("deleted_element_count"),
                                      "checkpoint": cleared.get("checkpoint")}):
            return result
    elif not resuming:
        current["step"] = "import"
        imported = import_cad(retained)
        result["import"] = imported
        if record("import", {"solids": imported.get("solid_ids", []),
                             "surfaces": imported.get("surface_ids", []),
                             "components": imported.get("component_ids", [])}):
            return result

    # Production chain: identical in both modes.
    current["step"] = "drop_solids"
    solids = imported.get("solid_ids", [])
    if config.drop_solids and solids:
        call("delete_entities", {"entity_type": "solid", "entity_ids": solids,
                                 "model_name": None})
    if record("drop_solids", {"deleted": solids if config.drop_solids else []}):
        return result

    current["step"] = "duplicates"
    if config.duplicate_tolerance is not None:
        duplicates = call(
            "remove_duplicate_surfaces",
            {"tolerance": config.duplicate_tolerance, "surface_ids": None,
             "model_name": None},
        )
    else:
        duplicates = {"skipped": True}
    if record("duplicates", duplicates):
        return result

    current["step"] = "drop_surfaces"
    # Duplicate removal may have deleted surfaces that are still listed for
    # meshing; drop what is gone from the plan and delete only what remains.
    existing = _surface_ids(call)
    duplicate_removed = [surface for surface in retained if surface not in existing]
    gone = [surface for surface in sorted(config.drop_surfaces)
            if surface not in existing]
    drop_ids = [surface for surface in sorted(config.drop_surfaces)
                if surface in existing]
    if drop_ids:
        call(
            "delete_entities",
            {"entity_type": "surface", "entity_ids": drop_ids, "model_name": None},
        )
        existing -= set(drop_ids)
    surfaces = [surface for surface in retained if surface in existing]
    if not surfaces:
        raise WorkflowError("every retained surface disappeared; nothing to mesh")
    result["surfaces"]["retained"] = surfaces
    result["surfaces"]["removed_as_duplicates"] = duplicate_removed
    result["surfaces"]["dropped_after_duplicates"] = gone
    if record("drop_surfaces", {"deleted": drop_ids,
                                "already_removed_by_duplicates": gone,
                                "removed_as_duplicates": duplicate_removed,
                                "retained": surfaces}):
        return result

    current["step"] = "stitch"
    stitched = call(
        "stitch_free_edges",
        {"tolerance": config.stitch_tolerance, "max_attempts": 5000,
         "surface_ids": None, "model_name": None},
        timeout=1200,
    )
    if record("stitch", stitched):
        return result

    current["step"] = "mesh"
    # One call for all surfaces keeps shared edges conformal.  HyperMesh records
    # which elements came from which surface, but this module does not read that
    # record back or verify it; the groups step selects through it.  See the
    # module docstring for the verification required before claiming correctness.
    outcome = call(
        "automesh_surfaces",
        mesh_params(surface_ids=surfaces, component_id=None),
        timeout=3600,
    )
    if not outcome.get("element_count"):
        raise WorkflowError("the surface mesh produced no elements")
    element_total = int(outcome["element_count"])
    if record("mesh", {"per_surface": False, "element_count": element_total}):
        return result

    current["step"] = "pairs"
    pairs = json.loads(table_path.read_text(encoding="utf-8"))
    absent = [str(surface) for surface in surfaces if str(surface) not in pairs]
    if absent:
        raise WorkflowError(f"the pair table has no entry for surfaces {absent}")
    if config.mode == GENERAL or resuming:
        detail = {"source": "table", "table": str(table_path)}
        if record("pairs", detail):
            return result

    # 8. One component per boundary group, then move that group's elements in.
    # The group name is decided from the surface's ordered material pair and
    # face tags; the move selects elements by surface.  See the module
    # docstring: the surface-to-element association is not verified here.
    current["step"] = "groups"
    buckets: dict[str, list[int]] = {}
    for surface in surfaces:
        entry = pairs[str(surface)]
        name = str(entry["pair"])
        tags = entry.get("tags") or []
        if (
            entry.get("minus") is not None
            and entry.get("minus") == entry.get("plus")
            and not tags
        ):
            result["same_medium"].append({"surface": surface, "pair": name})
            continue
        buckets.setdefault(name, []).append(surface)
    groups = []
    for name, group_surfaces in sorted(buckets.items()):
        component_id = _ensure_component(call, components, name)
        moved = call(
            "organize_elements",
            {"target_component_id": component_id, "surface_ids": group_surfaces,
             "mode": "move", "model_name": None},
            timeout=1200,
        )
        groups.append({"name": name, "component_id": component_id,
                       "surfaces": group_surfaces,
                       "elements": moved.get("elements_in_target"),
                       "checkpoint": moved.get("checkpoint_file")})
    result["groups"] = groups
    if record("groups", {"count": len(groups), "names": sorted(buckets),
                         "same_medium": result["same_medium"]}):
        return result

    current["step"] = "export"
    output = export_dir / config.export_name
    call("export_feko_mesh", {"output_file": str(output),
                              "template_file": config.template_file,
                              "model_name": None}, timeout=600)
    result["exports"]["mesh"] = str(output)
    result["exports"]["pairs"] = str(table_path)
    record("export", {"output": str(output)})
    return result


def _resume_import(resume: Mapping) -> dict:
    """Normalize the import result carried by a resumed run."""
    imported = resume.get("imported")
    if not isinstance(imported, Mapping):
        raise WorkflowError(
            "resume.imported must be the import result from the classify stage"
        )
    surfaces = imported.get("surface_ids", imported.get("surfaces"))
    if not surfaces:
        raise WorkflowError("resume.imported must list at least one surface")
    solids = imported.get("solid_ids", imported.get("solids", ()))
    components = imported.get("component_ids", imported.get("components", ()))
    return {
        "surface_ids": [int(value) for value in surfaces],
        "solid_ids": [int(value) for value in (solids or ())],
        "component_ids": [int(value) for value in (components or ())],
    }


def _component_index(call: Callable[[str, dict], dict]) -> dict[str, int]:
    listing = call("list_entities", {"entity_type": "component", "limit": 1000})
    return {str(item["name"]): int(item["id"]) for item in listing.get("entities", [])}


def _surface_ids(call: Callable[[str, dict], dict]) -> set[int]:
    listing = call("list_entities", {"entity_type": "surface", "limit": 1000})
    return {int(item["id"]) for item in listing.get("entities", [])}


def _ensure_component(call: Callable[[str, dict], dict], index: dict[str, int], name: str) -> int:
    if name not in index:
        created = call("create_component", {"name": name})
        index[name] = int(created["component"]["id"])
    return index[name]

# HyperWorks MCP

A local MCP server that lets an AI agent drive Altair HyperMesh, HyperView,
HyperGraph, HyperStudy, OptiStruct, and Radioss on Windows.

[中文](README.zh-CN.md) · [Architecture](ARCHITECTURE.md) · [Changelog](../CHANGELOG.md)

The server manages projects inside a workspace, launches HyperMesh or batch
jobs, and talks to an authenticated in-application extension for live geometry
and meshing. Model-changing live operations are typed and checkpointed.
Arbitrary Python, Tcl, and shell execution is not exposed.

Current versions: Python package `0.13.0`, in-application extension `0.29.0`.

## What you can do

- Import CATIA, STEP, IGES, and Parasolid files into a live HyperMesh session.
- Mesh surfaces and solids, inspect mesh quality, repair selected meshes, and
  roll back to checkpoints.
- Export a grouped `.fhm` mesh for FEKO. Material pairs are decided from closed
  material shells and winding numbers, not from distance thresholds.
- Run the staged CATIA → HyperMesh → FEKO workflow, which asks the operator
  about the material pairs before any surface is removed.
- Build typed OptiStruct and Radioss models, submit jobs, and read the real
  status, logs, and artifacts.
- Post-process results in HyperView and HyperGraph: contours, queries, PNG
  evidence, CSV time histories, and HTML/JSON reports.
- Prepare typed HyperStudy internal-math studies.

## Requirements

- Windows 10 or 11.
- Altair HyperWorks with HyperMesh installed and licensed. `HYPERWORKS_HOME`
  must point to the version directory that contains `hwdesktop` and, when
  solvers are installed, `hwsolvers`.
- Python 3.10 or newer.
- An MCP client that supports STDIO servers, such as Claude Code or Codex.

To locate `HYPERWORKS_HOME`, check the Target of the HyperWorks desktop
shortcut, or search for `runhwx.exe` under the usual Altair install roots. Do
not copy a path from another machine. `probe_environment.py` reports which
desktop, batch, and solver launchers are actually present.

## Install

```powershell
cd HyperMesh
uv sync --extra dev

$env:HYPERWORKS_HOME = '<HYPERWORKS_INSTALL_ROOT>'
$env:HYPERWORKS_MCP_WORKSPACE = Join-Path `
  ([Environment]::GetFolderPath('MyDocuments')) 'HyperWorksMCP\workspace'

.\.venv\Scripts\python.exe .\probe_environment.py
```

Any Python 3.10+ virtual environment works if you prefer not to use `uv`.

Register the server with your MCP client. A Codex example is in
[`examples/codex_config.example.toml`](examples/codex_config.example.toml), or
run:

```powershell
.\register_codex_mcp.ps1 `
  -PythonExe "$PWD\.venv\Scripts\python.exe" `
  -HyperWorksHome $env:HYPERWORKS_HOME `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

Install the in-application extension:

```powershell
.\install_hyperworks_extension.ps1 -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

Restart HyperMesh. If the extension is not enabled automatically, enable
`HyperWorks MCP Bridge` from `File > Extension Manager`, then verify the live
session:

```powershell
.\.venv\Scripts\python.exe .\probe_live_bridge.py
```

The bridge binds only to `127.0.0.1`, checks a generated token on every
request, and runs allowlisted `hm` calls through the HyperWorks Qt main
thread.

## Quick start: CATIA → HyperMesh → FEKO

The staged preset `run_catia_feko_workflow` starts by asking the operator
whether to decide the material pairs before duplicate removal. `action="start"`
returns only the question, the reason, a coarse-mesh hint, two options and a
token; nothing is imported and no entity is touched. `action="classify"`
imports the CAD file, builds the closed reference shells from every material
surface, writes the pair table and stops for confirmation. `action="produce"`
runs the production steps only after `confirm_pairs=true`. `action="skip"`
records that the pairs are undetermined and does not assume a production
chain. The reference pass may use a coarse `reference_element_size`; it never
reaches the final mesh.

Required inputs:

| Input | Meaning |
|---|---|
| `project_id` | Project that already contains the CAD file |
| `input_name` | File name inside the project |
| `surface_materials` | Label-to-material map covering every material surface |
| `drop_surfaces` | Surface labels to remove after de-duplication |
| `face_tags` | Face-only boundaries such as PEC or impedance sheets |
| `element_size` | Target element size |
| `export_name` | Output name ending in `.fhm` |
| `mode` | Retained for compatibility; `classify` uses verification semantics and `produce` uses general semantics |

The classify pass meshes all material surfaces, including duplicates and
surfaces that will be dropped, because each material must form a closed shell.
It writes the pair table, removes the reference mesh, and continues in the same
model. The produce pass reads that table instead of repeating the reference
pass. The `action` now decides which pass runs, not `mode`.

Rules that are not optional:

- The model must be empty before import, or entity IDs will shift.
- `surface_materials` must cover every material surface, including duplicates
  and surfaces that will be dropped later.
- `face_tags` must be supplied. Omitting it silently drops PEC, impedance, and
  symmetry attributes from the group names.
- Ordered material pairs must not be merged. The negative-normal side and the
  positive-normal side are different FEKO boundary groups.
- Do not decide pairs from nearest-surface distance or shared-edge topology.
- Do not split a CAD surface to resolve an `undecided` label. Report it to the
  operator instead.
- Only empty components may be deleted. A component holding elements,
  surfaces, solids, lines, or points is refused before a checkpoint is written.

### Material-pair interlock

After a CATIA import, and until the material pairs are decided, the server
interlocks every operation that changes the surface set. The pairs rely on a
closed reference shell per material: removing duplicates, deleting surfaces,
stitching or trimming can open that shell and corrupt the winding-number
pairs, while the pair table still looks plausible.

The model-level interlock is armed by `import_live_cad` for a `.CATPart` or
`.CATProduct` file, which returns `material_pair_gate` with the question. The
staged preset `action="start"` puts the same question to the operator before
anything is imported; `action="classify"` arms the interlock when it imports
the CAD file and clears it when the classification succeeds.

While the interlock is open, these tools perform no work and return the
question verbatim with `status="decision_required"`:

| Tool | Operation held back |
|---|---|
| `remove_live_duplicate_surfaces` | duplicate removal |
| `delete_live_entities` (`surface`, `solid`) | surface or solid deletion |
| `stitch_live_free_edges` | free-edge stitching |
| `trim_live_free_edges` | free-edge trimming |

Read-only queries and reference meshing are not affected.

The model-level state is held in server process memory:

| Stage | Meaning | The four tools above |
|---|---|---|
| `pending` | imported, not answered | blocked |
| `authorized` | operator chose classification, not finished | blocked |
| `classified` | classification done | open |
| `skipped` | operator chose to skip | open; pairs recorded as undetermined |

To resolve it:

- Choose classification: call
  `resolve_material_pair_decision(decision="classify")`, then finish
  `classify_material_pairs`; a successful classification moves the gate to
  `classified`. `run_catia_feko_workflow` `action="classify"` does both.
- Choose to skip: call `resolve_material_pair_decision(decision="skip")`. The
  interlock opens, the pairs stay undetermined, and nothing else runs.

The state lives only in the server process and is not saved with the model.
Restarting the server clears it; a new import arms it again. A
`decision_required` result is a stop for the operator, not a failure - do not
reach the same change through another tool.

The full operator prompt is
[`PROMPTS/catia-feko-workflow.md`](PROMPTS/catia-feko-workflow.md).

## Tool groups

| Area | Representative tools |
|---|---|
| Environment and projects | `get_environment`, `create_project`, `import_project_file` |
| Live session | `get_live_bridge_status`, `get_live_session_info`, `get_live_model_summary` |
| Geometry and mesh | `import_live_cad`, `automesh_live_surfaces`, `solid_map_live_solids`, `tetra_mesh_live_solids`, `get_live_mesh_quality`, `repair_live_mesh_quality` |
| FEKO | `export_live_feko_mesh`, `classify_material_pairs`, `resolve_material_pair_decision`, `run_catia_feko_workflow` |
| Connections | `create_live_rigid_link`, `create_live_rbe3`, `create_live_weld`, `create_live_spot_weld` |
| Solver jobs | `prepare_analysis_template`, `submit_solver_job`, `get_job_status`, `tail_job_log`, `get_solver_result_artifacts` |
| Post-processing | `postprocess_solver_result_in_hyperview`, `extract_solver_time_history_in_hypergraph`, `generate_solver_job_report` |
| HyperStudy | `prepare_hyperstudy_math_study`, `generate_hyperstudy_study` |

Every geometry import or creation, automatic mesh, connection, solver-card, and
model load writes a checkpoint under
`<HYPERWORKS_MCP_WORKSPACE>\.hyperworks_mcp\checkpoints`. If the HyperMesh API
raises or produces no new entities, the bridge restores that checkpoint.
Manual rollback uses `rollback_live_checkpoint` with `confirm=true`.

## Solver workflows

OptiStruct and Radioss jobs use typed templates rather than raw solver decks.
A typical OptiStruct run is:

1. `create_project`
2. `prepare_optistruct_cantilever_analysis`
3. `submit_solver_job` after explicit approval
4. `get_job_status`, `tail_job_log`, `get_solver_result_artifacts`
5. `postprocess_solver_result_in_hyperview`

A Radioss explicit run uses `prepare_radioss_block_impact_analysis`, submits
the paired Starter/Engine decks, and can then call
`audit_radioss_explicit_job` to evaluate termination, energy error, added mass,
time step, negative volume, and penetration.

<details>
<summary>Validated solver templates</summary>

- `optistruct.linear_static_solid`
- `optistruct.normal_modes_solid`
- `optistruct.linear_buckling_solid`
- `optistruct.multi_case_static_solid`
- `optistruct.gap_contact_static_solid`
- `optistruct.uniform_thermal_stress_solid`
- `radioss.explicit_block_impact`
- `radioss.plate_impact_solid`
- `radioss.drop_weight_solid_surrogate`
- `radioss.solid_axial_collision`

The two `*_surrogate` entries are solid, initial-velocity fixtures. They are
not gravity-driven drop tests or thin-wall crash-box models. The following
templates are discoverable but return a clear not-runnable error:
`radioss.three_point_bending`, `radioss.tube_crush`,
`radioss.thin_wall_axial_collision`, `radioss.vehicle_crash_subsystem`, and
`hyperstudy.template_doe_optimization`.

</details>

The `*_e2e_smoke.py` scripts submit real solver jobs and may consume
licenses. Run them only when that is intended.

## Launch selector

`runhwx.exe` may open a product selector before HyperMesh starts. Until
someone chooses a product and clicks **Create Session**, the process looks
healthy but the extension is not loaded and the bridge does not answer.

Check the setting before launching with `get_hypermesh_launcher_setting`.
`launch_hypermesh` returns a `launcher` object describing whether the window
was found. If the bridge still does not answer, `wait_for_live_bridge`
distinguishes "no session yet" from a broken bridge. With the operator's
agreement, `set_hypermesh_launcher_setting(show=false, confirm=true)` can turn
the selector off; every settings file is backed up first.

## Tests

```powershell
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
```

The suite has 120 tests and takes about 4 seconds. It does not start
HyperWorks, submit a solver job, or consume a license. See
[`tests/README.md`](tests/README.md) for what each file covers.

## Limitations

- The chain stops at a `.fhm` export. CADFEKO import and boundary setup are
  outside this repository.
- A single creation call is limited to 5,000 nodes or elements.
- Bridge responses are capped at 4 MiB. Meshing returns `element_count` plus a
  sample of at most 2,000 element IDs.
- Generic connector realization and fasteners remain profile-gated.
- Remeshing an existing mesh with `keep_existing_mesh=false` can reuse element
  IDs and roll back. Return to a mesh-free checkpoint first.
- Passing `model_name` to `import_live_cad` before the model exists returns
  `HyperMesh model not found` and imports nothing. Omit the parameter for a
  fresh import and rename the model afterwards.
- The cylindrical O-grid tool is a bounded radial topology, not a replacement
  for the interactive HyperMesh O-grid panel.

## Documentation

| Document | Purpose |
|---|---|
| [AGENTS.md](../AGENTS.md) | Entry point for AI agents |
| [PROMPTS/catia-feko-workflow.md](PROMPTS/catia-feko-workflow.md) | Full CATIA → FEKO operator prompt |
| [ARCHITECTURE.md](ARCHITECTURE.md) | Server, extension, workspace, and checkpoint design |
| [ADVANCED_CAPABILITY_AUDIT.md](ADVANCED_CAPABILITY_AUDIT.md) | Audited connection, dummy, seatbelt, and airbag boundaries |
| [tests/README.md](tests/README.md) | Test map and license-free test commands |
| [CHANGELOG.md](../CHANGELOG.md) | Stage changes and version history |

## License

MIT. This directory is a modified version of the HyperWorks module from
[Cai-aa/CAE-Agent-Hub](https://github.com/Cai-aa/CAE-Agent-Hub). See
[NOTICE](NOTICE) for attribution and [LICENSE](LICENSE) for the full text.

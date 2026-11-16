# HyperWorks MCP

A local, workspace-scoped FastMCP server for auditable Altair HyperWorks automation.

The server discovers the installed HyperMesh/HyperStudy/solver launchers, manages
isolated projects, runs screened Tcl through HyperMesh Batch, launches HyperMesh or
HyperView, submits typed OptiStruct/Radioss jobs, and exposes real status, bounded logs,
cancellation, and artifact inventory. It also includes an authenticated in-application
Extension that runs allowlisted `hm` API operations on the HyperWorks Qt main thread,
including bounded node/element/material creation, controlled `.hm` loading, and redraw.
The live bridge now also exposes controlled block/cylinder geometry, workspace-scoped
STEP/IGES/Parasolid/CATIA import, curvature/proximity surface automeshing, native Solid Map,
a bounded cylindrical radial O-grid generator, mesh-quality summaries, and checkpoints.
It also provides checkpointed rigid links, RBE3 spiders, and node welds, plus a typed
HyperStudy internal-math study generator for variables, responses, DOE, and GRSM goals.
It adds a solver-validated OptiStruct SOL 101 vertical slice: typed MAT1/PSOLID/CHEXA
properties, distributed FORCE loading, SPC1 constraints, optional PGAP/CGAP contact,
subcase/output controls, asynchronous solution, artifact classification, and live
HyperView contour/query/PNG evidence. Version 0.8 adds a real Radioss explicit-dynamics
vertical slice: paired Starter/Engine decks, LAW2/SOLID/BRICK property assignment,
initial velocity, BCS constraints, TYPE7 contact, time-history and H3D output controls,
actual solver submission, artifact classification, and machine-readable quality gates
for termination, energy error, added mass, time step, negative volume, and penetration.
HyperView postprocessing can select the first, last, or a specific result simulation ID.
Version 0.9 adds checkpointed solver-card entities for Property, Loadcol, Loadstep, Set,
Constraint, and contact cards; typed force/moment/SPC/temperature/flux/velocity/
acceleration and pressure creation; native structural tetra meshing; bounded quality
repair with before/after evidence; property-backed spot welds; reusable template
discovery/dispatch; all-frame HyperGraph time histories; HTML/JSON job reports; and
unrealized spot/seam/area/bolt connector intent with explicit location and link entities.
Version 0.10 expands the reusable solver template registry with solver-verified
OptiStruct normal modes, linear buckling, multi-case linear statics, CGAP/PGAP contact
statics, and uniform thermal stress. It also adds solver-verified Radioss solid fixtures
for plate impact, an initial-velocity drop-weight surrogate, and a two-solid axial
collision surrogate. Each template exposes its validation state; three-point bending,
tube crush, thin-wall crash boxes, vehicle subsystems, and solver-linked HyperStudy
studies remain explicitly gated until their geometry or model-coupling fixtures exist.
Version 0.11 adds `classify_material_pairs`, which names the two materials on either
side of every labelled FEKO surface from closed material shells and the winding number,
plus the face attributes (ideal conductor, impedance boundary, and so on) that have no
volume and therefore cannot be probed.
Version 0.12 adds launch-selector awareness. `get_hypermesh_launcher_setting` reports
whether `runhwx.exe` will open its selector window before the application starts, and
`launch_hypermesh` now says whether that window was actually found on screen.
`wait_for_live_bridge` separates "no session yet" from "the bridge is broken", so a
waiting selector is never mistaken for a failed launch or an unloaded Extension.
Version 0.12.1 replaces the workflow's rollback-and-reimport with
`clear_live_reference_mesh`: the reference meshes are deleted component by component
and the production chain continues in the same model, because reloading an `.hm`
checkpoint breaks the CATIA reader in that session.
Version 0.12.2 adds an empty-model preflight check to the workflow, so a model that
already holds surfaces or elements is refused before the import instead of silently
shifting entity IDs; an unknown `stop_after` name is likewise rejected before the run.
It also corrects the supported CAD format list to include CATIA.
Version 0.12.3 bounds the element ID list returned by surface automeshing and native
Solid Map: `element_count` stays exact while `element_ids` is a sample of at most 2,000
entries with `element_ids_truncated`, so a mesh of any size stays far below the 4 MiB
bridge response limit.
Version 0.13.0 lets `delete_live_entities` remove empty components. HyperMesh deletes a
component's contents with the component, so the extension first checks that the
component holds no elements, surfaces, solids, lines, or points and refuses any other
component before writing a checkpoint; after deletion it verifies that no retained
entity class lost IDs.

## Find your HyperWorks installation

Do not copy another machine's installation path. `HYPERWORKS_HOME` must point to the
Altair version directory that contains `hwdesktop` and, when solvers are installed,
`hwsolvers`. A typical layout is:

```text
<HYPERWORKS_INSTALL_ROOT>\
  hwdesktop\hwx\bin\win64\runhwx.exe
  hwdesktop\hm\bin\win64\hmbatch.exe
  hwdesktop\hst\bin\win64\hstbatch.exe
  hwsolvers\scripts\optistruct.bat        # optional
  hwsolvers\scripts\radioss.bat           # optional
```

First check the **Target** of the HyperWorks/HyperMesh desktop shortcut. You can also
search the standard Windows installation locations with PowerShell:

```powershell
$searchRoots = @(
  "$env:ProgramFiles\Altair"
  "$env:ProgramW6432\Altair"
  "$env:SystemDrive\Altair"
) | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -Unique

Get-ChildItem -Path $searchRoots -Filter runhwx.exe -File -Recurse `
  -ErrorAction SilentlyContinue |
  Where-Object FullName -Match '\\hwdesktop\\hwx\\bin\\win64\\runhwx\.exe$' |
  Select-Object -ExpandProperty FullName
```

For a custom installation location, search its parent directory instead. Remove the
trailing `\hwdesktop\hwx\bin\win64\runhwx.exe` from the result to obtain
`<HYPERWORKS_INSTALL_ROOT>`. `probe_environment.py` reports which desktop, batch, and
solver launchers are actually available; missing solver launchers are reported as a
capability limitation rather than assumed from this repository.

## Setup

```powershell
uv sync --extra dev
$env:HYPERWORKS_HOME = '<HYPERWORKS_INSTALL_ROOT>'
$env:HYPERWORKS_MCP_WORKSPACE = Join-Path `
  ([Environment]::GetFolderPath('MyDocuments')) 'HyperWorksMCP\workspace'
.\.venv\Scripts\python.exe .\probe_environment.py
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe .\stdio_smoke.py
# This briefly starts hmbatch and may consume a license.
.\.venv\Scripts\python.exe .\hmbatch_smoke.py --run
```

Use [`examples/codex_config.example.toml`](examples/codex_config.example.toml), or run:

```powershell
.\register_codex_mcp.ps1 `
  -PythonExe "$PWD\.venv\Scripts\python.exe" `
  -HyperWorksHome $env:HYPERWORKS_HOME `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

Install the in-application Extension:

```powershell
.\install_hyperworks_extension.ps1 `
  -Workspace $env:HYPERWORKS_MCP_WORKSPACE
```

The installer also registers `HyperWorks MCP Bridge` in the current user's Altair
extension registry. Restart HyperMesh; if it is not enabled automatically, enable it
from `File > Extension Manager`, then verify the live session:

```powershell
.\.venv\Scripts\python.exe .\probe_live_bridge.py
```

The bridge binds only to `127.0.0.1`, authenticates every request with a generated token,
has a fixed method allowlist, and performs HyperWorks API calls through a Qt main-thread
queue. It does not expose `eval`, arbitrary Python, shell, or Tcl.

## The launch selector window

`runhwx.exe` may open a launch selector before HyperMesh itself starts. That window waits
for a product choice and a **Create Session** click. Until someone clicks it, the
process exists and its main window looks healthy, but there is no session, the Extension
is not loaded, and the live bridge does not answer - which reads exactly like a broken
Extension and is not one.

Whether the selector appears is the user setting `launcher_show_on_startup`, stored in
`~/.altair/<version>/hwx/settings.xml`, in the active profile under
`~/.altair/<version>/hwx/profiles/`, and in `~/Documents/hwsettings.xml`. A
value of `1` in any of them shows the selector.

Check it before launching:

```text
get_hypermesh_launcher_setting
```

The answer names every file, its value, and whether a selector window is on screen right
now. Two ways to proceed:

- keep the setting and click through the selector - choose the product, then **Create
  Session**; or
- with the operator's agreement only, call
  `set_hypermesh_launcher_setting(show=false, confirm=true)` once. Each settings file is
  backed up before it is written, and no other change is made.

`launch_hypermesh` returns a `launcher` object with `will_show`, `window_found`,
`window_titles`, `setting_files`, and `next_step`. Read it instead of assuming the
application started. If the bridge still does not answer, `wait_for_live_bridge` polls it
and reports whether a selector is waiting, which distinguishes "no session yet" from "the
bridge is broken".

## Safe workflow

Call `get_environment`, create a project, import explicit files, write a screened Tcl
script, then request approval before `run_hmbatch`, `launch_hypermesh`,
`submit_solver_job`, or `cancel_job`. Monitor real job state and logs; the server never
guesses progress or convergence.

Tcl process/network/dynamic-runtime commands, direct file access, absolute/parent paths,
and environment access are rejected. This is defense in depth rather than an OS sandbox;
review scripts before execution. The server exposes no arbitrary shell, PowerShell,
Python, or raw command-line tool. See [ARCHITECTURE.md](ARCHITECTURE.md).

## Live geometry and meshing

The 0.9 live workflow uses only typed, allowlisted operations:

1. Call `create_project`, then `import_project_file` for CAD input.
2. Call `import_live_cad`; use its returned `surface_ids` or `solid_ids`.
3. For a shell mesh, call `automesh_live_surfaces` with an explicit element size.
4. For mappable solids, call `solid_map_live_solids`. HyperMesh decides whether the
   topology is actually mappable; failure restores the automatic checkpoint.
5. For a controlled cylindrical validation mesh, call
   `create_live_cylindrical_ogrid`. This creates annular Hex8 layers around a Penta6
   core; it is not presented as the interactive HyperMesh O-grid panel.
6. For general solid geometry, call `tetra_mesh_live_solids`; it invokes the native
   structural `Model.tetmesh` API and rejects non-positive-volume output.
7. Call `get_live_mesh_quality`; selected meshes can be smoothed through
   `repair_live_mesh_quality`, which returns before/after evidence and can roll back.
8. For FEKO boundary groups, mesh each CAD surface into its own component, export with
   `export_live_feko_mesh`, then call `classify_material_pairs` with the label-to-material
   map. Each material is treated as a closed shell and both sides are read from the
   winding number, so the answer does not depend on element size or stitching quality.

Steps 1-8 stay separately callable. When a team wants the whole chain as one fixed,
repeatable preset, `run_catia_feko_workflow` runs it in order - import, drop solids,
remove duplicates, drop the excluded surfaces, stitch, mesh, decide the pairs, create one
component per boundary group, move the elements in, export - with a checkpoint per step
and an optional `stop_after` for inspection. `mode="verification"` computes the pairs: it
meshes *all* material surfaces - duplicates and later-excluded ones included, because a
material must form a closed shell - exports that reference mesh, classifies it, writes the
pair table, deletes the reference components together with their elements, and then runs
the production steps in that same model. The CAD file is imported exactly once per run.
`mode="general"` skips the reference pass and reuses the table, so repeat runs on the same
geometry are fast. The workflow assumes the target model is empty before it imports. A
surface whose two sides resolve to the same medium is listed in `same_medium` and left out
of the groups.

Every geometry import/create and automatic meshing call first writes a bridge-managed
`.hm` checkpoint under `<HYPERWORKS_MCP_WORKSPACE>\.hyperworks_mcp\checkpoints`.
If the HyperMesh API raises or produces no new entities, the bridge loads that checkpoint
automatically. Manual rollback is available through `rollback_live_checkpoint` and
requires `confirm=true`.

CAD paths must already be inside the MCP workspace. Supported live CAD formats are STEP,
IGES, Parasolid, and CATIA (CATPart, CATProduct). Import options use bounded `name=value`
strings; arbitrary commands
and arbitrary Python/Tcl remain unavailable.

## Connections and HyperStudy

Use `get_live_connection_capabilities` before creating connections. The verified live
operations are `create_live_rigid_link`, `create_live_rbe3`, `create_live_weld`, and
property-backed `create_live_spot_weld`. `create_live_connector` creates spot, seam,
area, or bolt connector intent from node/line locations and component/surface links;
it deliberately reports `realized=false` until solver-specific FE controls are supplied.
Each modifying connection call writes a checkpoint and rolls back if HyperMesh does not
expose the expected connector or element entity.
Generic connector realization and fasteners remain profile-gated. HyperMesh's native
fastener-creation API is currently documented for the Abaqus profile only, so the bridge
does not advertise it as a solver-neutral operation.

Version 0.9.1 restores the HyperView result page after exporting a HyperGraph image. The
HyperGraph page remains in the session, while the Extension stays hosted by a compatible
client so follow-up MCP calls do not lose the localhost bridge.

For a safe HyperStudy setup, call `create_project`, then
`prepare_hyperstudy_math_study` with typed continuous variables, expression responses,
and optional DOE/GRSM definitions. `generate_hyperstudy_study` executes only the
MCP-generated script through Altair `hstpy.bat` and creates a project-scoped `.hstudy`.
It does not automatically evaluate the DOE or optimization. Arbitrary Python is rejected.

## General solver cards, loads, histories, and reports

`create_live_solver_card_entity` creates a bounded Property, Loadcol, Loadstep, Set,
Constraint, Contactbehavior, Contactgroup, Contactsurf, or Group card. Card images and
solver data names remain solver-profile-specific; references are explicit typed
`entity_type`/`entity_id` objects instead of unvalidated integers. Use
`create_live_nodal_load`, `create_live_pressure_load`, and `create_live_loadstep` for the
common load chain. All model-changing calls checkpoint first.

Use `list_analysis_templates`, `get_analysis_template`, and `prepare_analysis_template`
to discover and dispatch validated profiles without hard-coding tool names. After a
completed job, `extract_solver_time_history_in_hypergraph` walks every available result
frame, writes CSV, creates a HyperGraph XY curve, and captures PNG evidence.
`generate_solver_job_report` combines actual job state, classified artifacts, Radioss
quality audit data when available, and selected project screenshots into HTML plus JSON.

The 0.10.0 real-solver-verified template set is:

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

The two entries marked `*_surrogate` are deliberately solid, initial-velocity fixtures:
they do not imply gravity-driven drop motion or thin-wall shell crash-box fidelity.
`radioss.three_point_bending`, `radioss.tube_crush`,
`radioss.thin_wall_axial_collision`, `radioss.vehicle_crash_subsystem`, and
`hyperstudy.template_doe_optimization` are discoverable but return a clear not-runnable
error. This lets an agent plan against the full roadmap without silently substituting a
two-block benchmark for a different physical test.

## End-to-end OptiStruct and HyperView workflow

Version 0.9 retains the complete, typed linear-static validation profile rather than a
raw card or command execution channel:

1. `create_project`
2. `prepare_optistruct_cantilever_analysis` to generate the material/property/mesh,
   load, constraint, optional contact, and SOL 101 control chain
3. `submit_solver_job` after explicit approval
4. `get_job_status`, `tail_job_log`, and `get_solver_result_artifacts`
5. `postprocess_solver_result_in_hyperview` to load the produced H3D in a new page,
   plot a requested scalar result, return legend extrema and entity query rows, and
   save a project-scoped PNG screenshot

Run `optistruct_e2e_smoke.py` only when a real OptiStruct license may be consumed. The
smoke fixture includes one PGAP/CGAP pair and requires an actual H3D/OP2 result before it
passes. HyperView accepts only project/job files inside the configured workspace and the
bridge still exposes no arbitrary HWC, Tcl, or Python execution.

## End-to-end Radioss explicit impact workflow

The 0.8 profile is a bounded, solver-validated block-impact benchmark in kg-mm-ms units:

1. `create_project`
2. `prepare_radioss_block_impact_analysis` creates `*_0000.rad` and `*_0001.rad`
3. inspect the returned LAW2/property/load/constraint/contact/control chains
4. after approval, call `submit_solver_job` with `solver="radioss"`
5. monitor `get_job_status` and `tail_job_log`
6. call `get_solver_result_artifacts` to inspect H3D, `A###`, `T##`, OUT, and restart files
7. call `audit_radioss_explicit_job` to evaluate the actual Engine output
8. after the 0.9 Extension is loaded, call `postprocess_solver_result_in_hyperview`
   with `simulation="first"`, `"last"`, or an available integer simulation ID

`radioss_e2e_smoke.py` consumes a real Radioss license. The verified 2026 regression
contains 45 nodes and 12 HEXA8 solids, completed Starter with zero errors/warnings,
completed Engine normally, generated 11 animation frames plus H3D and time history,
and passed the default gates with 11.4% maximum absolute energy error, 0% added-mass
error, and a positive minimum time step of 2.853e-4 ms. These figures describe only the
shipped regression fixture; the MCP always parses each new job instead of reusing them.

See [ADVANCED_CAPABILITY_AUDIT.md](ADVANCED_CAPABILITY_AUDIT.md) for the audited
connector, Design Explorer, dummy, seatbelt, and airbag boundaries.

## Current boundary

Version 0.10 provides verified bounded chains for six OptiStruct solid analyses and four
Radioss solid impact fixtures, alongside controlled connections and typed HyperStudy
internal-math setup. It is not a claim that arbitrary crash, occupant, airbag, material,
failure, or solver-linked optimization models can be synthesized without a dedicated
geometry and coupling profile. Controlled live
geometry and meshing remain available. A call can create at most 5000 nodes or
elements; elements must reference existing positive node IDs. Live model loading accepts
only `.hm` files already staged in an MCP project and requires explicit
`replace_current=true`. View refresh executes only fixed `hm_viewfit` and `hm_redraw`
commands; arbitrary Python and Tcl remain unavailable. Native Solid Map works only for
geometry HyperMesh can map, and the cylindrical O-grid tool is a deliberately bounded
radial topology, not a general interactive O-grid replacement. HyperView contour,
result query, and capture are available for workspace-scoped result files; general CAD
cleanup and model-changing safety/airbag operations remain future work pending
solver-specific fixtures.

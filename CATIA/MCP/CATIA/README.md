# CATIA Agent MCP

A Windows STDIO MCP server for CATIA V5 modeling and native CATIA
Analysis/ELFINI simulation.

[中文](README.zh-CN.md) · [Safe export](docs/safe-export.md) · [Advanced surfaces](docs/advanced-surfaces.md) · [Compatibility](docs/compatibility-and-architecture.md)

The server controls a running CATIA V5 session through COM. It does not invoke
Abaqus, ANSYS, CalculiX, or another third-party solver.

## What it does

- Native CATPart modeling: sketch, pad, pocket, constant-radius edge fillet,
  parameters, materials, update, save, export, and model-tree inspection.
- Native GSD wireframe and surface operations: 3D points, open and closed
  splines, offset planes, G0/G1/G2 connect curves, `HybridShapeLoft`, `Join`,
  `Healing`, `Boundary`, `Close Surface`, and `Thick Surface`.
- Native CATProduct creation, component insertion, and assembly inspection.
- Native CATAnalysis work: document import, cases, solutions, sets, entities,
  supports, mesh parts, mesh specifications, internal compute, result
  images/data, and HTML reports.
- 55 fixed, typed MCP tools. Arbitrary Python, CATScript, shell, and raw COM
  execution are intentionally not exposed.
- A dedicated STA worker serializes COM calls. Requests carry IDs for
  idempotency, and file access is restricted to configured roots.

## Requirements

- Windows with CATIA V5 installed and licensed.
- Python 3.10 or newer.
- CATIA and the MCP process must run as the same Windows user and at the same
  integrity level. Otherwise the process may see `CNEXT.exe` while
  `CATIA.Application` remains unavailable through the Running Object Table.
- MCP SDK 1.x or 2.x (`mcp>=1.7,<3`).

## Install and start

Copy [`examples/codex_config.example.toml`](examples/codex_config.example.toml)
into your MCP client configuration, then adjust:

- `CATIA_MCP_ENV_NAME` for the installed release, for example
  `CATIA_P3.V5-6R2023.B33`.
- `CATIA_MCP_ENV_DIR` for the CATEnv directory.
- `CATIA_MCP_WORKSPACE` and `CATIA_MCP_ALLOWED_ROOTS` for the project and file
  roots.
- `CATIA_MCP_MIN_RELEASE_INDEX`, normally `28`.

Start the server through the wrapper:

```powershell
& '.\scripts\run_server.ps1'
```

The wrapper selects `CATIA_MCP_PYTHON`, a local `.venv`, or an available Codex
Python runtime without writing logs to STDIO.

## Quick start

1. Call `catia_health_check` to confirm the selected release and Analysis
   TypeLib.
2. Call `catia_connect`. By default it connects to an existing session and
   does not start a new CATIA instance.
3. Build in small native steps: `catia_create_part`, `catia_create_sketch`,
   `catia_add_pad` or `catia_add_pocket`, then `catia_inspect_active` after
   each update.
4. Assign a material before creating a CATAnalysis document.
5. Save only under `CATIA_MCP_ALLOWED_ROOTS`.
6. Export with the default `overwrite_policy="error"`. Use `versioned` to keep
   the existing file or `replace` to overwrite it. Exports are staged through a
   unique temporary file, and STEP/IGES is re-imported by default.

## Analysis workflow

1. `catia_create_analysis_document` imports a part or assembly.
2. `catia_inspect_analysis` shows the Model, Case, Set, and Mesh Part tree.
3. Add cases, solutions, sets, entities, loads, restraints, and supports.
4. Create the mesh with `catia_add_analysis_mesh_part` and
   `catia_set_analysis_mesh_specification`.
5. Run `catia_compute_analysis` with `mesh_only=true` first, inspect the mesh,
   then run the full compute.
6. Create a result image, export numerical data, and build the native HTML
   report.

A returned COM call is not proof that the physics is correct. Check materials,
units, boundary conditions, mesh, result images, displacement/stress data, and
the report.

## Validation

```powershell
$env:PYTHONPATH=(Resolve-Path '.\src').Path
python -m unittest discover -s '.\tests' -v
python -m compileall -q '.\src'
python '.\scripts\stdio_smoke.py'
python '.\scripts\probe_environment.py'
python '.\scripts\probe_live.py'
python '.\scripts\validate_issue_fixes.py'
python '.\scripts\validate_edge_fillet.py'
```

Use `probe_live.py --start-if-missing` only when starting the selected CATIA
environment is intended. A test map is in
[`tests/README.md`](tests/README.md).

## Version support

| Release | Status |
|---|---|
| B28 / V5-6R2018 and newer | Target baseline; the full tool surface remains available, and runtime probes gate only missing operations |
| B33 / V5-6R2023 | Locally validated |
| B21 / V5R21 SP6+HF5 | Adapted and live-validated; requires `CATIA_MCP_ALLOW_LEGACY=true` |
| Older than B28 | Best-effort maintenance; V5R21 is the validated legacy case |

Installed DLLs or TypeLibs do not prove that a GPS/GAS/EST license is
available. The first live license test is creating and importing a
`CATAnalysis` document. Release-specific behaviour and the capability model are
described in
[docs/compatibility-and-architecture.md](docs/compatibility-and-architecture.md).

## Limits

- No arbitrary Python, CATScript, shell, or raw COM execution.
- `catia_capture_view` returns BMP only for stable V5 Automation compatibility.
- Version 0.3.0 exposes constant-radius edge fillets only. Variable-radius,
  face, tritangent, and surface fillets, as well as chamfers, are not included.
- `catia_inspect_edges` returns a topology snapshot. Its one-based indices are
  not persistent IDs; reacquire them after any upstream geometry change.

<details>
<summary>Pocket direction, BMP capture, and edge-fillet details</summary>

`catia_add_pocket` accepts `reverse=false` by default. Set `reverse=true` when
an origin-plane sketch must cut toward material on the opposite side of the
sketch normal. The result reports the direction, body volume before and after
in mm³, `removed_volume_mm3`, and `material_removed`. A zero-volume change is
returned as `status="no_material_removed"` with a warning rather than looking
like a verified cut.

`catia_capture_view` uses `catCaptureFormatBMP` (numeric value `4`) and checks
the `BM` file signature before returning `is_image=true`. A non-image payload
is an error.

Call `catia_inspect_edges` immediately before filleting. It searches the
current 3D solid topology of `body_name`, filters out sketch-wire edges, and
can be scoped to a named `source_feature_name`. `catia_add_edge_fillet` creates
a native Part Design `ConstRadEdgeFillet` with `radius_mm`, one or more
`edge_indices`, and `propagation="minimal"` or `"tangency"`. Input order is
preserved because multi-edge fillet order can affect the native result. The
response reads back the radius, propagation, `ObjectsToFillet` count, and body
volume before and after.

</details>

## Documentation

| Document | Purpose |
|---|---|
| [docs/safe-export.md](docs/safe-export.md) | Safe overwrite, temporary validation, and timeout recovery |
| [docs/advanced-surfaces.md](docs/advanced-surfaces.md) | GSD tool contracts |
| [docs/compatibility-and-architecture.md](docs/compatibility-and-architecture.md) | Release handling, capability model, and legacy notes |
| [tests/README.md](tests/README.md) | Test map |

## License

MIT. This server is adapted from the CATIA segment of
[Cai-aa/CAD-Agent-Hub](https://github.com/Cai-aa/CAD-Agent-Hub). See
[NOTICE](../../NOTICE) and [LICENSE](../../LICENSE).

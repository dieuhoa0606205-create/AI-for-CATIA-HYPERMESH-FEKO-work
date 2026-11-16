# Tests

The unit suite uses fake COM objects and does not start CATIA or consume a
license.

```powershell
cd CATIA\MCP\CATIA
$env:PYTHONPATH=(Resolve-Path '.\src').Path
python -m unittest discover -s '.\tests' -v
```

The suite contains 55 tests.

## What each file covers

| File | Scope |
|---|---|
| `test_compatibility.py` | Release mapping, support tiers, and CATEnv parsing |
| `test_contracts.py` | Positive-number validation, safe names, and path-escape rejection |
| `test_modeling.py` | Sketch, pad, pocket direction and removed volume, edge inspection and fillet, BMP capture, and export overwrite policies |
| `test_runtime.py` | STA worker serialization and busy-worker rejection |
| `test_server_contract.py` | Required 55-tool surface, export defaults, pocket flag, fillet arguments, and error recoverability |
| `test_simulation.py` | Analysis compute and mesh-only calls, structural catalog types, support binding, and report generation |
| `test_surfaces.py` | GSD point, spline, loft, join, fill, and thick-surface validation and fallback paths |

The files are split by layer: contracts, modeling, simulation, surfaces,
runtime, and server surface. They do not repeat the same operation across
files.

## Live validation scripts

The scripts under `scripts/` are not part of the unit suite. `stdio_smoke.py`
and `probe_environment.py` do not start CATIA. `probe_live.py`,
`validate_issue_fixes.py`, and `validate_edge_fillet.py` require a running
CATIA session; `probe_live.py --start-if-missing` may start one.

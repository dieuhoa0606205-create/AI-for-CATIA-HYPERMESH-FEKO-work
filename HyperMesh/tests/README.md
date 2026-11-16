# Tests

The unit suite runs without HyperWorks, a solver license, or a live session.

```powershell
cd HyperMesh
$env:PYTHONPATH = 'src'
.\.venv\Scripts\python.exe -m pytest -q
```

`pytest` is the supported runner. It reports 120 tests in about 4 seconds.
`python -m unittest discover` shows fewer cases because it does not count
parametrized pytest cases the same way.

## What each file covers

| File | Scope |
|---|---|
| `test_environment.py` | `HYPERWORKS_HOME` normalization and discovery of desktop, batch, and solver launchers |
| `test_projects.py` | Project creation, file import, script writes, and rejection of project IDs that escape the workspace |
| `test_security.py` | Tcl allowlist and rejection of process, network, absolute-path, and environment escapes |
| `test_jobs.py` | Subprocess tracking, exit status, and bounded log capture |
| `test_solver_command.py` | OptiStruct and Radioss batch command construction, quoting, and CPU options |
| `test_launcher.py` | Launch-selector settings across three files, idempotent writes, backups, and window enumeration |
| `test_live_bridge.py` | Authenticated localhost round trip and explicit handling of a missing bridge config |
| `test_extension_handlers.py` | In-application handler contracts: session and entity queries, creation, deletion, surface repair, stitching, trimming, overlap review, checkpoints, model loading, solver cards, tetra meshing, quality repair, and HyperView post-processing |
| `test_material_pairs.py` | FEKO material classification: label parsing, winding numbers, ordered pairs, face tags, same-medium surfaces, unknown labels, and element sampling |
| `test_workflow.py` | `run_catia_feko_workflow` step order, verification and general modes, empty-model preflight, `stop_after`, duplicate-surface removal, reference-mesh cleanup, `reference_element_size`, resume, and same-medium face tags |
| `test_workflow_gate.py` | Staged start/classify/produce/skip flow, the model-level material-pair gate, and the duplicate, delete, stitch, and trim tools returning `decision_required` while it is open |
| `test_analysis_profiles.py` | Construction and validation of OptiStruct and Radioss template chains |
| `test_templates_and_reporting.py` | Template registry dispatch and HTML/JSON job reports |
| `test_radioss_results.py` | Parsing of Engine quality signals and application of explicit quality gates |
| `test_hyperstudy.py` | Typed DOE and optimization script generation, plus rejection of unsafe expressions |
| `test_mcp_contract.py` | MCP tool and resource discovery; requires the `fastmcp` dependency |

The files are split by layer rather than by feature. `test_extension_handlers.py`
is the largest because it covers the in-application handler surface against a
fake HyperMesh model. `test_workflow.py` covers orchestration only, and
`test_material_pairs.py` covers the geometry algorithm only, so the three do not
repeat each other.

## Smoke scripts

The scripts below are not part of the unit suite. Those that start HyperWorks
or a solver may consume a license and require explicit approval.

| Script | Purpose | May consume a license |
|---|---|---|
| `stdio_smoke.py` | Start the MCP server over STDIO and list its tools | No |
| `probe_environment.py` | Report installed desktop, batch, and solver launchers | No |
| `probe_live_bridge.py` | Connect to an already running HyperMesh session | No additional license |
| `live_connection_smoke.py` | Exercise the live bridge against a running session | Uses the existing session |
| `hmbatch_smoke.py --run` | Start `hmbatch` briefly | Yes |
| `hyperstudy_smoke.py` | Start HyperStudy batch | Yes |
| `optistruct_e2e_smoke.py` | Submit a real OptiStruct job | Yes |
| `radioss_e2e_smoke.py` | Submit a real Radioss job | Yes |
| `hyperview_e2e_smoke.py` | Open results in HyperView | Yes |

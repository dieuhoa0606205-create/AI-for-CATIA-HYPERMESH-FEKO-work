from __future__ import annotations

import os
import re
import subprocess
import time
import uuid
from pathlib import Path
from typing import Literal

from fastmcp import FastMCP

from .environment import (
    discover_installation,
    environment_report,
    normalize_installation_root,
    tool_paths,
)
from .analysis_profiles import AnalysisProfileService
from .analysis_templates import AnalysisTemplateService
from .jobs import JobService, SUCCESS_MARKER, stage_solver_input
from .hyperstudy import DOE_DESIGNS, OPTIMIZATION_DESIGNS, HyperStudyService
from .launcher import (
    launcher_windows,
    read_setting as read_launcher_setting,
    set_setting as write_launcher_setting,
    wait_for_launcher,
)
from .live_bridge import LiveBridgeClient
from .material_pairs import classify
from .workflow import WorkflowConfig
from .workflow_gate import GATE, decision_payload, run_key as workflow_run_key
from . import workflow_stages
from .projects import ProjectService
from .radioss_results import audit_radioss_output_files
from .reporting import write_job_report
from .settings import Settings


INSTRUCTIONS = """OPERATOR DECISIONS - STOP AND ASK.
Many choices in this server change the physical model or the meaning of its result, and
the tools cannot choose them for you. Before any modifying call, stop and ask the operator
when a value is missing, ambiguous, or contradicted by what the tools returned. This
includes, at minimum: the surface-to-material map and which surfaces to exclude; face
attributes that have no volume (ideal conductor, impedance sheet, symmetry plane,
coating); element size, element type, and mesh type; which CAD parameter to change and
its new value; how to treat labels reported undecided, agreement below 1.0, same-medium
faces, free edges, duplicate or overlapping surfaces, and other items the tools only
report; and whether to delete, roll back, overwrite, or save over an existing model.
Never infer these from geometry, from a previous run, or from a default. A question must
name the decision, the candidate answers, and the consequence of each. If the client
supports MCP elicitation, use it; otherwise ask in your reply and do not call a modifying
tool until the operator answers. A result that carries questions, undecided labels, or
operator_report_required is a stop, not a warning to work around. Only the operator may
authorize proceeding with an unresolved item.

Control Altair HyperWorks through typed, workspace-scoped tools.
Use get_environment before launching anything. Prefer HyperMesh batch Tcl for repeatable
pre-processing and use GUI launch only when interactive work is required. Tcl scripts are
screened against OS/process escape commands and run from isolated project/job folders.
Solver and GUI launches are side effects. Ask for approval before launching a GUI, starting
a solver, or cancelling a job. Never infer mesh quality, convergence, solver availability,
or successful completion; read the actual job status and logs. This server does not expose
arbitrary shell, PowerShell, or Python execution. The live in-application Python bridge is
available through an authenticated localhost Extension. Live entity modification, model
saving, controlled creation, model loading, view refresh, interactive selection, GUI launch,
solver launch, and cancellation are side effects. Loading a model requires explicit
replace_current=true. The live bridge never exposes arbitrary Python or Tcl execution.
Live entity deletion covers solids, surfaces, elements, and components; a component is
deleted only when it holds no elements, surfaces, solids, lines, or points, because
HyperMesh would otherwise delete its contents together with it.
Geometry creation, CAD import, surface/tetra meshing, native Solid Map, controlled
cylindrical O-grid generation, solver-card entities, loads, load steps, and quality repair
create automatic .hm checkpoints and roll back on failure. Rigid links, RBE3 spiders,
node welds, and property-backed spot welds are checkpointed and require explicit existing
entities. Generic connector realization and fasteners remain capability-gated until their
active profile and connector controls are validated.
CAD import is limited to project-scoped STEP, IGES, Parasolid, and CATIA (CATPart,
CATProduct) files. Solid Map may
replace existing solid elements, so inspect the returned checkpoint before continuing.
FEKO mesh export writes .fhm files into the shared export folder for the FEKO MCP to import.
When assigning surfaces to FEKO boundary groups, give every CAD surface its own label by
meshing it into its own component, export the .fhm, and call classify_material_pairs with
a label-to-material map. It treats each material as a closed shell and reads the material
on each side from the winding number, so no distance threshold is involved and the answer
does not depend on element size. Do not decide pairs from nearest-surface distance, which
is dominated by the element edge length, or from shared-edge topology, which in a stitched
model connects every material to every other. The result is an ordered pair, because
air/carrier and carrier/air are different groups. Labels whose samples disagree are
reported undecided: assign their elements individually rather than splitting the CAD
surface, since FEKO labels are per triangle, and list them for the operator instead of
choosing for them. Simulation assignments that live on a face and have no volume -
perfect-conductor sheets, impedance boundaries, symmetry planes - can never be resolved
from the volume on either side; pass them per label in face_tags and they are appended to
the group name.
Every material must be a closed shell in the exported mesh, so the export used for
classification must contain every surface that carries that material - duplicates and
surfaces that will be excluded later included. Classifying a mesh from which surfaces
were already removed opens the shells and silently corrupts the pairs. The outside name
may also name a material: an enclosed cavity holding the same medium as the surroundings
is one medium, and both report that same name. A label whose two sides resolve to the
same medium and carries no face tag is reported separately, not grouped; the same medium
with a face tag is still a boundary - a perfect-conductor sheet inside air is grouped as
air-air-PEC.
Grouping is a surface-level decision, not an element-level one. Decide before the
production mesh which surfaces belong to which boundary group: one ordered material pair
plus the face tags yields one group name per surface, and the pair table records that
decision. A group is then moved by selecting its surfaces (organize_live_elements with
surface_ids) instead of its elements, so no per-element decision is made after meshing.
That selection relies on HyperMesh's element-to-surface association, which is created when
the surface is meshed. The reference pass makes the link explicit: it meshes one surface
at a time into the component named for that surface, and the handler refuses elements that
land outside it, so component membership is the record. The production pass meshes all
surfaces in one call to keep shared edges conformal and keeps no such record. The tools do
not read the association back or verify it. Before accepting a grouped mesh, count
elements per surface, check every count is non-zero, check the sum equals the mesh total,
and check no element is claimed by two surfaces. Without that check the grouping is
expected to be correct, not verified.
run_catia_feko_workflow is staged and starts with a question, not with work. action="start"
returns the material-pair decision - whether to decide the pairs before duplicate removal
and surface deletion - with a coarse-mesh hint, two options and a token. Put it to the
operator; do not choose for them. action="classify" imports, meshes every material surface
as its own reference component, computes the ordered pairs, writes the table, deletes the
reference components and stops for confirmation; no duplicate has been removed and no
surface deleted. action="skip" records that the pairs are undetermined and stops without
importing or changing anything; the next task is whatever the operator specifies.
action="produce" runs only after the operator confirms the table with confirm_pairs=true,
continuing from the already imported geometry. The reference pass may use a coarse
reference_element_size; it is only for the pair decision and never reaches the final mesh.
The primitive duplicate, delete, stitch and trim tools
enforce the same gate and return decision_required instead of changing the model while it is open.
A CATIA import through import_live_cad opens that gate and returns the question in
material_pair_gate. The workflow imports into an empty model; it refuses to start when
surfaces or elements already exist, because imported IDs would shift.
runhwx.exe may open a launch selector window before the application starts. When the user
setting launcher_show_on_startup is 1, that window always appears, waits for a product and
a Create Session click, and the live bridge does not answer until the session exists - the
application is running and looks healthy the whole time, so this is not an extension
failure and must not be diagnosed as one. Call get_hypermesh_launcher_setting before the
first launch. If it reports will_show_launcher, either tell the operator to click through
the selector, or, with their agreement, call set_hypermesh_launcher_setting(show=false,
confirm=true) - never change a user setting without asking. After launch, read the
launcher field of the result instead of assuming the application started; a visible
selector means a person still has to act. In case of doubt call wait_for_live_bridge,
which distinguishes "no session yet" from "bridge broken".
"""

settings = Settings.from_env()
settings.ensure()
projects = ProjectService(settings)
jobs = JobService(settings)
hyperstudy = HyperStudyService(projects)
analysis_profiles = AnalysisProfileService(projects)
analysis_templates = AnalysisTemplateService(analysis_profiles)
live = LiveBridgeClient()
mcp = FastMCP("hyperworks", instructions=INSTRUCTIONS)


@mcp.tool()
def get_environment() -> dict:
    """Detect HyperMesh GUI/batch, HyperStudy, OptiStruct, Radioss, and capabilities."""
    report = environment_report(settings)
    bridge = live.status(connect_timeout=0.25)
    report["capabilities"]["live_python_bridge"] = bridge["connected"]
    report["live_bridge"] = bridge
    return report


@mcp.tool()
def configure_installation(path: str) -> dict:
    """Select an existing Altair installation for this MCP process; no system settings change."""
    root = normalize_installation_root(Path(path))
    if not root.is_dir() or not (root / "hwdesktop").is_dir():
        raise ValueError("path does not resolve to an Altair installation root")
    os.environ["HYPERWORKS_HOME"] = str(root)
    report = environment_report(settings)
    return {
        "configured": True,
        "scope": "current MCP process",
        "persistence": "Set HYPERWORKS_HOME in Codex MCP configuration.",
        **report,
    }


@mcp.tool()
def create_project(name: str) -> dict:
    """Create an isolated HyperWorks project with input, scripts, output, and runs folders."""
    return projects.create(name)


@mcp.tool()
def get_project_summary(project_id: str) -> dict:
    """Return project metadata and the current input/script inventory."""
    summary = projects.manifest(project_id)
    summary["jobs"] = jobs.list(project_id=project_id, limit=20)["jobs"]
    return summary


@mcp.tool()
def import_project_file(
    project_id: str, source_file: str, destination_name: str | None = None
) -> dict:
    """Copy an explicitly selected CAD, model, deck, or result file into project input."""
    return projects.import_file(project_id, source_file, destination_name)


# Security patch: script execution entry points are not exposed. The Tcl filter is a
# regex deny-list and can be bypassed by variable indirection, so the tool stays in
# the codebase for upstream compatibility but is never registered with the MCP client.
# @mcp.tool()
def write_tcl_script(project_id: str, name: str, content: str) -> dict:
    """Write a screened HyperMesh Tcl script; OS/process escape and *quit are blocked."""
    return projects.write_tcl(project_id, name, content)


@mcp.tool()
def prepare_optistruct_cantilever_analysis(
    project_id: str,
    name: str,
    dimensions: list[float],
    divisions: list[int],
    youngs_modulus: float,
    poissons_ratio: float,
    density: float,
    total_force: float,
    force_direction: list[float],
    gap_contacts: list[dict] | None = None,
    output_name: str = "cantilever.fem",
) -> dict:
    """Create a complete typed SOL 101 CHEXA deck with property, load, SPC, optional CGAP contact, and H3D controls."""
    return analysis_profiles.prepare_optistruct_cantilever(
        project_id,
        name,
        dimensions,
        divisions,
        youngs_modulus,
        poissons_ratio,
        density,
        total_force,
        force_direction,
        gap_contacts,
        output_name,
    )


@mcp.tool()
def prepare_radioss_block_impact_analysis(
    project_id: str,
    name: str,
    impactor_dimensions: list[float] = [5.0, 6.0, 6.0],
    impactor_divisions: list[int] = [2, 2, 2],
    target_dimensions: list[float] = [2.0, 10.0, 10.0],
    target_divisions: list[int] = [1, 2, 2],
    initial_gap: float = 1.0,
    initial_velocity: float = 5.0,
    end_time: float = 1.0,
    output_interval: float = 0.1,
    youngs_modulus: float = 210.0,
    poissons_ratio: float = 0.3,
    density: float = 7.85e-6,
    yield_stress: float = 0.25,
    hardening_modulus: float = 0.5,
    hardening_exponent: float = 0.5,
    friction: float = 0.1,
    output_name: str = "block_impact_0000.rad",
) -> dict:
    """Create a typed Radioss LAW2/SOLID/TYPE7 impact Starter+Engine pair in kg-mm-ms units."""
    return analysis_profiles.prepare_radioss_block_impact(
        project_id,
        name,
        impactor_dimensions,
        impactor_divisions,
        target_dimensions,
        target_divisions,
        initial_gap,
        initial_velocity,
        end_time,
        output_interval,
        youngs_modulus,
        poissons_ratio,
        density,
        yield_stress,
        hardening_modulus,
        hardening_exponent,
        friction,
        output_name,
    )


@mcp.tool()
def list_analysis_templates(solver: str | None = None) -> dict:
    """List reusable, versioned analysis templates and their validation state."""
    return analysis_templates.list(solver)


@mcp.tool()
def get_analysis_template(template_id: str) -> dict:
    """Return one reusable analysis template contract and its required parameters."""
    return analysis_templates.get(template_id)


@mcp.tool()
def prepare_analysis_template(
    project_id: str,
    template_id: str,
    parameters: dict,
) -> dict:
    """Prepare a solver deck by dispatching one validated reusable template."""
    return analysis_templates.prepare(project_id, template_id, parameters)


@mcp.tool()
def get_hyperstudy_capabilities() -> dict:
    """Audit installed HyperStudy batch/Python APIs and the typed study features exposed here."""
    paths = tool_paths(settings)
    root = discover_installation(settings)
    examples = (
        root / "hwdesktop" / "hst" / "etc" / "examples" / "api" if root else None
    )
    return {
        "installation_root": str(root) if root else None,
        "hyperstudy_batch": str(paths["hyperstudy_batch"])
        if paths["hyperstudy_batch"]
        else None,
        "hyperstudy_python": str(paths["hyperstudy_python"])
        if paths["hyperstudy_python"]
        else None,
        "official_api_examples": str(examples) if examples and examples.is_dir() else None,
        "typed_study_support": {
            "internal_math_model": True,
            "continuous_variables": True,
            "expression_responses": True,
            "doe_designs": sorted(DOE_DESIGNS),
            "optimization_designs": sorted(OPTIMIZATION_DESIGNS),
            "prepare_without_execution": True,
            "generate_hstudy_with_official_python_api": bool(
                paths["hyperstudy_python"]
            ),
        },
        "not_yet_exposed": [
            "arbitrary user Python",
            "solver-specific model resource wiring",
            "automatic DOE or optimization evaluation",
        ],
    }


@mcp.tool()
def prepare_hyperstudy_math_study(
    project_id: str,
    study_name: str,
    variables: list[dict],
    responses: list[dict],
    doe: dict | None = None,
    optimization: dict | None = None,
) -> dict:
    """Prepare a validated internal-math HyperStudy setup script from typed parameters."""
    return hyperstudy.prepare_math_study(
        project_id, study_name, variables, responses, doe, optimization
    )


@mcp.tool()
def generate_hyperstudy_study(project_id: str, script_name: str) -> dict:
    """Run only an MCP-generated script through Altair hstpy to create a .hstudy file."""
    executable = tool_paths(settings)["hyperstudy_python"]
    if not executable:
        raise RuntimeError("HyperStudy Python launcher was not detected. Set HYPERWORKS_HOME.")
    script = hyperstudy.generated_script(project_id, script_name)
    project_root = projects.root(project_id)
    run_dir = project_root / "runs" / (
        "hyperstudy_api_"
        + time.strftime("%Y%m%d_%H%M%S")
        + "_"
        + uuid.uuid4().hex[:6]
    )
    run_dir.mkdir(parents=True)
    command = ["cmd.exe", "/d", "/c", "call", str(executable), str(script)]
    return jobs.start(
        "HYPERSTUDY_API",
        project_id,
        command,
        run_dir,
        {
            "script_file": str(script),
            "installation_root": str(discover_installation(settings)),
        },
    )


# Security patch: paired with write_tcl_script, this entry point is not exposed.
# @mcp.tool()
def run_hmbatch(project_id: str, script_name: str) -> dict:
    """Run an approved project Tcl script asynchronously with HyperMesh Batch."""
    paths = tool_paths(settings)
    hmbatch = paths["hmbatch"]
    if not hmbatch:
        raise RuntimeError("HyperMesh Batch was not detected. Set HYPERWORKS_HOME.")
    script = projects.script(project_id, script_name)
    project_root = projects.root(project_id)
    run_dir = project_root / "runs" / (
        "hmbatch_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    )
    run_dir.mkdir(parents=True)
    wrapper = run_dir / "__mcp_wrapper.tcl"
    script_for_tcl = script.as_posix()
    wrapper.write_text(
        "if {[catch {source {" + script_for_tcl + "}} err opts]} {\n"
        "    puts stderr $err\n"
        "    puts stderr [dict get $opts -errorinfo]\n"
        "    exit 1\n"
        "}\n"
        f'puts "{SUCCESS_MARKER}"\n',
        encoding="utf-8",
    )
    return jobs.start(
        "HMBATCH",
        project_id,
        [str(hmbatch), "-tcl", str(wrapper)],
        run_dir,
        {"script_file": str(script), "installation_root": str(discover_installation(settings))},
    )


@mcp.tool()
def launch_hypermesh(
    project_id: str | None = None,
    start_with: Literal["HyperMesh", "HyperView"] = "HyperMesh",
    language: Literal["en", "zh"] = "en",
) -> dict:
    """Launch the HyperMesh/HyperView GUI. This is a side effect and may consume a license.

    The application may first show a launch selector window that waits for a product
    and a Create Session click. The result reports whether the selector is configured
    to appear and whether it was actually found on screen, so a caller cannot mistake
    a waiting selector for a failed launch or a broken extension.
    """
    launcher_path = tool_paths(settings)["runhwx"]
    if not launcher_path:
        raise RuntimeError("HyperWorks GUI launcher was not detected")
    working_directory = (
        projects.root(project_id) / "output" if project_id else settings.workspace
    )
    command = [
        str(launcher_path),
        "-client",
        "HyperWorksDesktop",
        "-plugin",
        "HyperworksLauncher",
        "-profile",
        "HyperworksLauncher",
        "-l",
        language,
        "-startwith",
        start_with,
    ]
    selector = read_launcher_setting()
    process = subprocess.Popen(
        command,
        cwd=working_directory,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        shell=False,
    )
    # The selector appears within a few seconds when it is enabled; when it is
    # disabled the application starts on its own and nothing is found here.
    found = wait_for_launcher(timeout=5.0)
    return {
        "launched": True,
        "pid": process.pid,
        "application": start_with,
        "working_directory": str(working_directory),
        "launcher": {
            "will_show": selector["will_show_launcher"],
            "window_found": bool(found),
            "window_titles": found,
            "setting_files": selector["files"],
            "next_step": (
                "A launch selector is waiting: a person must choose the product and "
                "click Create Session. The live bridge stays unreachable until then."
                if found
                else "No selector window was found. If the live bridge still does not "
                "answer, call wait_for_live_bridge before investigating the extension."
            ),
        },
    }


@mcp.tool()
def get_hypermesh_launcher_setting() -> dict:
    """Report whether the HyperWorks launch selector will appear, and where that is set."""
    report = read_launcher_setting()
    report["windows_on_screen"] = launcher_windows()
    return report


@mcp.tool()
def set_hypermesh_launcher_setting(show: bool, confirm: bool = False) -> dict:
    """Turn the HyperWorks launch selector on or off. Requires confirm=true.

    This writes the user's own HyperWorks settings, so it is only done on an explicit
    request. Every file is backed up before it is changed.
    """
    if not confirm:
        raise ValueError(
            "this changes the user's HyperWorks settings; repeat the call with confirm=true"
        )
    return write_launcher_setting(show)


@mcp.tool()
def wait_for_live_bridge(timeout_seconds: int = 120) -> dict:
    """Wait for the live bridge, reporting whether a session exists and what is on screen.

    A timeout usually means no HyperMesh session has been created yet, not that the
    extension is broken; the selector window state is included to tell the two apart.
    """
    deadline = time.monotonic() + max(1, min(int(timeout_seconds), 1800))
    last: dict = {}
    while True:
        last = live.status(connect_timeout=1.0)
        if last.get("connected"):
            break
        if time.monotonic() >= deadline:
            break
        time.sleep(1.0)
    result = dict(last)
    result["waited_seconds"] = int(timeout_seconds)
    result["launcher_windows"] = launcher_windows()
    result["launcher_setting"] = read_launcher_setting()
    if result.get("connected"):
        result["diagnosis"] = "the live session answered"
    elif result["launcher_windows"]:
        result["diagnosis"] = (
            "the launch selector is waiting for a product choice and Create Session; "
            "no session exists yet, and the extension has not been loaded"
        )
    else:
        result["diagnosis"] = (
            "no session answered and no selector window is visible; check that HyperMesh "
            "is running, that the Extension is enabled, and that the bridge token matches"
        )
    return result


def _solver_command(
    executable: Path,
    run_input: Path,
    ncpu: int,
    solver: Literal["optistruct", "radioss"],
) -> list[str]:
    cpu_option = "-nt" if solver == "radioss" else "-ncpu"
    solver_args = [str(executable), str(run_input), cpu_option, str(ncpu)]
    if executable.suffix.lower() in {".bat", ".cmd"}:
        # Invoke the batch file through CALL.  Passing a pre-quoted command
        # string to ``cmd /s /c`` makes cmd.exe strip/escape the outer quotes
        # when the launcher lives below "Program Files".
        return ["cmd.exe", "/d", "/c", "call", *solver_args]
    return solver_args


@mcp.tool()
def submit_solver_job(
    project_id: str,
    solver: Literal["optistruct", "radioss"],
    input_file: str,
    ncpu: int | None = None,
) -> dict:
    """Stage project input and start an approved OptiStruct or Radioss job asynchronously."""
    paths = tool_paths(settings)
    executable = paths[solver]
    if not executable:
        raise RuntimeError(
            f"{solver} launcher was not detected. Install HyperWorks Solvers or configure its executable."
        )
    ncpu = settings.default_ncpu if ncpu is None else int(ncpu)
    if not 1 <= ncpu <= settings.max_ncpu:
        raise ValueError(f"ncpu must be between 1 and {settings.max_ncpu}")
    project_root = projects.root(project_id)
    source_deck = projects.input_file(project_id, input_file)
    run_dir = project_root / "runs" / (
        solver + "_" + time.strftime("%Y%m%d_%H%M%S") + "_" + uuid.uuid4().hex[:6]
    )
    run_dir.mkdir(parents=True)
    run_input = stage_solver_input(project_root, source_deck, run_dir / "input")
    command = _solver_command(executable, run_input, ncpu, solver)
    return jobs.start(
        "SOLVER",
        project_id,
        command,
        run_dir,
        {"solver": solver, "input_file": str(run_input), "ncpu": ncpu},
    )


@mcp.tool()
def get_job_status(job_id: str) -> dict:
    """Read actual process state and completion markers without estimating progress."""
    return jobs.status(job_id)


@mcp.tool()
def tail_job_log(job_id: str, lines: int = 100) -> dict:
    """Read a bounded tail of captured HyperWorks or solver output."""
    return jobs.log(job_id, lines)


@mcp.tool()
def cancel_job(job_id: str) -> dict:
    """Terminate an active job process tree and preserve existing outputs."""
    return jobs.cancel(job_id)


@mcp.tool()
def list_jobs(project_id: str | None = None, limit: int = 50) -> dict:
    """List persisted HyperWorks jobs, optionally for one project."""
    return jobs.list(project_id, limit)


@mcp.tool()
def list_job_artifacts(job_id: str) -> dict:
    """Inventory files produced in a job directory; does not interpret result values."""
    return jobs.artifacts(job_id)


def _solver_result_artifacts(job_id: str) -> dict:
    status = jobs.status(job_id)
    inventory = jobs.artifacts(job_id)
    categories: dict[str, list[dict]] = {
        "result": [],
        "animation": [],
        "time_history": [],
        "solver_report": [],
        "model": [],
        "restart": [],
        "other": [],
    }
    result_extensions = {".h3d", ".op2", ".d3plot", ".h5", ".hdf5"}
    report_extensions = {".out", ".stat", ".log", ".err", ".html"}
    model_extensions = {".fem", ".bdf", ".dat", ".rad", ".key"}
    restart_extensions = {".rst", ".rmd", ".hmx"}
    for item in inventory["artifacts"]:
        path = Path(item["path"])
        suffix = path.suffix.lower()
        filename = path.name
        if suffix in result_extensions:
            category = "result"
        elif re.search(r"A\d{3}$", filename, flags=re.IGNORECASE):
            category = "animation"
        elif re.search(r"T\d{2}$", filename, flags=re.IGNORECASE):
            category = "time_history"
        elif suffix in report_extensions:
            category = "solver_report"
        elif suffix in model_extensions:
            category = "model"
        elif suffix in restart_extensions:
            category = "restart"
        else:
            category = "other"
        categories[category].append(item)
    return {
        "job_id": job_id,
        "state": status["state"],
        "return_code": status.get("return_code"),
        "process_alive": status["process_alive"],
        "categories": categories,
        "result_count": len(categories["result"]),
        "report_count": len(categories["solver_report"]),
    }


@mcp.tool()
def get_solver_result_artifacts(job_id: str) -> dict:
    """Classify actual solver deck, log, H3D, OP2, report, and restart artifacts for a job."""
    return _solver_result_artifacts(job_id)


@mcp.tool()
def audit_radioss_explicit_job(
    job_id: str,
    maximum_absolute_energy_error_percent: float = 15.0,
    maximum_added_mass_percent: float = 5.0,
) -> dict:
    """Audit actual Radioss Starter/Engine termination, energy, mass, time step, volume, and penetration signals."""
    status = jobs.status(job_id)
    if status.get("metadata", {}).get("solver") != "radioss":
        raise ValueError("job_id does not identify a Radioss solver job")
    artifacts = _solver_result_artifacts(job_id)
    reports = [Path(item["path"]) for item in artifacts["categories"]["solver_report"]]
    starter_outputs = [path for path in reports if path.name.lower().endswith("_0000.out")]
    engine_outputs = [path for path in reports if path.name.lower().endswith("_0001.out")]
    if not engine_outputs:
        raise RuntimeError("No Radioss Engine *_0001.out artifact is available")
    starter_output = max(starter_outputs, key=lambda path: path.stat().st_mtime) if starter_outputs else None
    engine_output = max(engine_outputs, key=lambda path: path.stat().st_mtime)
    audit = audit_radioss_output_files(
        starter_output,
        engine_output,
        maximum_absolute_energy_error_percent,
        maximum_added_mass_percent,
    )
    return {
        "job_id": job_id,
        "state": status["state"],
        "return_code": status.get("return_code"),
        **audit,
    }


@mcp.tool()
def postprocess_solver_result_in_hyperview(
    job_id: str,
    data_type: str = "Displacement",
    data_component: str = "Mag",
    entity_type: Literal["node", "element"] = "node",
    output_name: str | None = None,
    overwrite: bool = False,
    query_limit: int = 20,
    simulation: str | int = "last",
    average_mode: Literal["none", "simple", "maximum", "minimum", "advanced", "difference"] = "none",
) -> dict:
    """Open a completed solver result in live HyperView, query contour values, and capture PNG evidence."""
    status = jobs.status(job_id)
    if status["state"] != "COMPLETED" or status.get("return_code") != 0:
        raise RuntimeError("Only a successfully completed solver job can be postprocessed")
    artifacts = _solver_result_artifacts(job_id)
    h3d_files = [
        Path(item["path"])
        for item in artifacts["categories"]["result"]
        if Path(item["path"]).suffix.lower() == ".h3d"
    ]
    if not h3d_files:
        raise RuntimeError("The completed job did not produce an H3D result")
    model_files = [
        Path(item["path"])
        for item in artifacts["categories"]["model"]
        if Path(item["path"]).suffix.lower() in {".fem", ".bdf", ".dat", ".rad", ".key"}
    ]
    result_file = max(h3d_files, key=lambda path: path.stat().st_mtime)
    # H3D contains both model topology and results.  Loading it as both sources
    # avoids a second solver-deck reader pass and keeps entity/result IDs aligned.
    model_file = result_file
    project_id = status["project_id"]
    if output_name is None:
        safe_type = "".join(char if char.isalnum() else "_" for char in data_type).strip("_")
        output_name = f"{job_id}_{safe_type or 'contour'}.png"
    output_file = projects.root(project_id) / "output" / Path(output_name).name
    if output_file.suffix.lower() != ".png":
        output_file = output_file.with_suffix(".png")
    if output_file.exists() and not overwrite:
        raise ValueError("Output screenshot already exists; set overwrite=true after approval")
    result = live.call(
        "postprocess_hyperview_result",
        {
            "model_file": str(model_file),
            "result_file": str(result_file),
            "output_file": str(output_file),
            "data_type": data_type,
            "data_component": data_component,
            "entity_type": entity_type,
            "page_title": f"MCP {job_id} {data_type}",
            "query_limit": query_limit,
            "simulation": simulation,
            "average_mode": average_mode,
        },
        timeout=900,
    )
    return {"job_id": job_id, "project_id": project_id, **result}


@mcp.tool()
def extract_solver_time_history_in_hypergraph(
    job_id: str,
    data_type: str = "Displacement",
    data_component: str = "Mag",
    entity_type: Literal["node", "element"] = "node",
    entity_ids: list[int] | None = None,
    statistic: Literal["maximum", "minimum", "mean"] = "maximum",
    output_name: str = "time_history",
) -> dict:
    """Extract all result frames to CSV and render a HyperGraph XY history with PNG evidence."""
    status = jobs.status(job_id)
    if status["state"] != "COMPLETED" or status.get("return_code") != 0:
        raise RuntimeError("Only a successfully completed solver job can be postprocessed")
    artifacts = _solver_result_artifacts(job_id)
    result_items = artifacts["categories"]["result"]
    if not result_items:
        raise RuntimeError("No supported H3D or OP2 result artifact was found")
    result_file = Path(result_items[0]["path"])
    model_candidates = artifacts["categories"]["model"]
    model_file = result_file if result_file.suffix.lower() == ".h3d" else Path(model_candidates[0]["path"])
    project_id = str(status["project_id"])
    root = projects.root(project_id)
    clean_stem = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(output_name).stem)[:80] or "time_history"
    result = live.call(
        "extract_hypergraph_time_history",
        {
            "model_file": str(model_file),
            "result_file": str(result_file),
            "csv_file": str(root / "output" / f"{clean_stem}.csv"),
            "image_file": str(root / "output" / f"{clean_stem}.png"),
            "data_type": data_type,
            "data_component": data_component,
            "entity_type": entity_type,
            "entity_ids": entity_ids,
            "statistic": statistic,
            "curve_label": f"{data_type} {data_component} {statistic}",
        },
        timeout=1800,
    )
    return {"job_id": job_id, "project_id": project_id, **result}


@mcp.tool()
def generate_solver_job_report(
    job_id: str,
    output_name: str = "hyperworks_job_report",
    evidence_files: list[str] | None = None,
) -> dict:
    """Generate workspace-scoped HTML and JSON evidence for one completed solver job."""
    status = jobs.status(job_id)
    if status["state"] not in {"COMPLETED", "FAILED", "CANCELLED"}:
        raise RuntimeError("Report generation requires a terminal job state")
    project_id = str(status["project_id"])
    root = projects.root(project_id)
    output_root = (root / "output").resolve()
    evidence = []
    for raw in evidence_files or []:
        path = Path(raw).resolve()
        try:
            path.relative_to(output_root)
        except ValueError as exc:
            raise ValueError("evidence_files must be inside the project output directory") from exc
        evidence.append(path)
    artifacts = _solver_result_artifacts(job_id)
    audit = None
    if str(status.get("metadata", {}).get("solver", "")).casefold() == "radioss":
        try:
            audit = audit_radioss_explicit_job(job_id)
        except Exception as exc:
            audit = {"passed": False, "audit_error": str(exc)}
    clean_name = re.sub(r"[^A-Za-z0-9_.-]+", "_", Path(output_name).stem)[:80] or "hyperworks_job_report"
    result = write_job_report(
        output_root,
        clean_name,
        status,
        artifacts,
        audit,
        evidence,
    )
    return {"job_id": job_id, "project_id": project_id, **result}


@mcp.tool()
def get_live_bridge_status() -> dict:
    """Probe the authenticated in-application Python API bridge and report real connectivity."""
    return live.status(connect_timeout=1.0)


@mcp.tool()
def get_live_capabilities() -> dict:
    """Return Python modules, live API methods, entity classes, and configured save roots."""
    return live.call("get_capabilities", timeout=10)


@mcp.tool()
def get_live_connection_capabilities(model_name: str | None = None) -> dict:
    """Audit connection APIs and solver-profile constraints in the live HyperMesh client."""
    return live.call(
        "get_connection_capabilities", {"model_name": model_name}, timeout=30
    )


@mcp.tool()
def get_live_safety_airbag_capabilities(model_name: str | None = None) -> dict:
    """Audit dummy, seatbelt, airbag, folding, and stitching APIs without model changes."""
    return live.call(
        "get_safety_airbag_capabilities", {"model_name": model_name}, timeout=30
    )


@mcp.tool()
def get_live_session_info() -> dict:
    """Read current and available HyperMesh models from the running desktop session."""
    return live.call("get_session_info", timeout=10)


@mcp.tool()
def get_live_model_summary(
    model_name: str | None = None, entity_types: list[str] | None = None
) -> dict:
    """Count allowlisted entity types in the current or named live HyperMesh model."""
    return live.call(
        "get_model_summary",
        {"model_name": model_name, "entity_types": entity_types},
        timeout=30,
    )


@mcp.tool()
def list_live_entities(
    entity_type: str,
    model_name: str | None = None,
    offset: int = 0,
    limit: int = 100,
    attributes: list[str] | None = None,
) -> dict:
    """List structured live entity data with pagination and explicit attribute selection."""
    return live.call(
        "list_entities",
        {
            "entity_type": entity_type,
            "model_name": model_name,
            "offset": offset,
            "limit": limit,
            "attributes": attributes,
        },
        timeout=60,
    )


@mcp.tool()
def get_live_entity(
    entity_type: str,
    entity_id: int,
    model_name: str | None = None,
    attributes: list[str] | None = None,
) -> dict:
    """Read one live HyperMesh entity by type and internal ID."""
    return live.call(
        "get_entity",
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "model_name": model_name,
            "attributes": attributes,
        },
        timeout=30,
    )


@mcp.tool()
def get_live_user_mark(entity_type: str, model_name: str | None = None) -> dict:
    """Read the current HyperMesh user mark for an allowlisted entity type."""
    return live.call(
        "get_user_mark",
        {"entity_type": entity_type, "model_name": model_name},
        timeout=30,
    )


@mcp.tool()
def select_live_entities_interactively(
    entity_type: str,
    message: str = "Select entities for Codex",
    highlight: bool = True,
) -> dict:
    """Open HyperMesh's interactive selector and return selected IDs; requires live user interaction."""
    return live.call(
        "interactive_select",
        {"entity_type": entity_type, "message": message, "highlight": highlight},
        timeout=900,
    )


@mcp.tool()
def set_live_entity_attributes(
    entity_type: str,
    entity_id: int,
    values: dict,
    model_name: str | None = None,
) -> dict:
    """Modify allowlisted attributes on one live entity; this is a model-changing side effect."""
    return live.call(
        "set_entity_attributes",
        {
            "entity_type": entity_type,
            "entity_id": entity_id,
            "values": values,
            "model_name": model_name,
        },
        timeout=60,
    )


@mcp.tool()
def delete_live_entities(
    entity_type: Literal["solid", "surface", "element", "component"],
    entity_ids: list[int],
    model_name: str | None = None,
) -> dict:
    """Delete explicit live entities; solids may be removed while surfaces remain.

    The extension creates a checkpoint and restores it if the deletion removes
    the retained entity class (surfaces for solid deletion, solids for surface
    deletion, nodes for element deletion, and elements, surfaces, solids, lines,
    or points for component deletion). Only empty components are accepted,
    because HyperMesh deletes a component's contents together with it.

    Surface and solid deletion is refused while the CATIA material-pair gate is
    open: removing a surface before the reference pass can open a material
    shell and corrupt the pairs.  The returned decision_required payload must
    be put to the operator.
    """
    if entity_type in ("surface", "solid"):
        blocked = GATE.require_model_clear(
            model_name, operation=f"delete_{entity_type}"
        )
        if blocked is not None:
            return blocked
    return live.call(
        "delete_entities",
        {
            "entity_type": entity_type,
            "entity_ids": entity_ids,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def clear_live_reference_mesh(
    component_names: list[str],
    model_name: str | None = None,
) -> dict:
    """Delete named reference components together with the elements they hold.

    This discards a reference mesh without reloading the model, which keeps the
    CAD geometry that came from the import. Deletion is checkpointed; if any
    element survives the component deletion the checkpoint is restored and the
    call fails, so a partially cleared model is never returned.
    """
    return live.call(
        "clear_reference_mesh",
        {
            "component_names": component_names,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def analyze_live_surface_repair(
    tolerance: float = 0.1,
    surface_ids: list[int] | None = None,
    model_name: str | None = None,
) -> dict:
    """Report duplicate surfaces and free-edge loops without changing geometry."""
    return live.call(
        "analyze_surface_repair",
        {
            "tolerance": tolerance,
            "surface_ids": surface_ids,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def analyze_live_surface_intersections(
    tolerance: float = 0.1,
    surface_ids: list[int] | None = None,
    max_pairs: int = 2000,
    model_name: str | None = None,
) -> dict:
    """Classify surface pairs and group coplanar partial overlaps.

    Pairs with no intersection curve are further tested for coplanar overlap
    using sampled boundary geometry. True overlaps are clustered into groups;
    touching pairs are reported as contact and excluded from the default review.
    Temporary intersection lines are created and deleted again; the surface set
    is verified unchanged and a checkpoint restores the model on failure.
    """
    return live.call(
        "analyze_surface_intersections",
        {
            "tolerance": tolerance,
            "surface_ids": surface_ids,
            "max_pairs": max_pairs,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def analyze_live_curved_surface_overlaps(
    tolerance: float | None = None,
    samples_per_surface: int = 64,
    max_pairs: int = 50,
    coincident_coverage: float = 0.95,
    overlap_coverage: float = 0.05,
    normal_angle_limit: float = 10.0,
    model_name: str | None = None,
) -> dict:
    """Test non-planar pairs from the last intersection report for overlap.

    Each surface is sampled by projecting a grid from its boundary frame; the
    fraction of samples within tolerance of the other surface and the largest
    normal angle over those samples classify the pair as coincident, partial
    overlap, contact, or separate. Groups are clustered like the coplanar
    analysis. Read-only.
    """
    return live.call(
        "analyze_curved_surface_overlaps",
        {
            "tolerance": tolerance,
            "samples_per_surface": samples_per_surface,
            "max_pairs": max_pairs,
            "coincident_coverage": coincident_coverage,
            "overlap_coverage": overlap_coverage,
            "normal_angle_limit": normal_angle_limit,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def analyze_live_surface_repair_all(
    tolerance: float = 0.1,
    surface_ids: list[int] | None = None,
    max_pairs: int = 2000,
    samples_per_surface: int = 64,
    max_curved_pairs: int = 50,
    coincident_coverage: float = 0.95,
    overlap_coverage: float = 0.05,
    normal_angle_limit: float = 10.0,
    model_name: str | None = None,
) -> dict:
    """Run duplicate, coplanar-overlap and curved-overlap detection in one call.

    Duplicates and free-edge loops come from the duplicate check, coplanar
    groups from the intersection analysis, and curved groups from the sampled
    normal-projection analysis. Coplanar and curved overlap pairs are regrouped
    into one combined group list for a single review. Read-only.
    """
    return live.call(
        "analyze_surface_repair_all",
        {
            "tolerance": tolerance,
            "surface_ids": surface_ids,
            "max_pairs": max_pairs,
            "samples_per_surface": samples_per_surface,
            "max_curved_pairs": max_curved_pairs,
            "coincident_coverage": coincident_coverage,
            "overlap_coverage": overlap_coverage,
            "normal_angle_limit": normal_angle_limit,
            "model_name": model_name,
        },
        timeout=900,
    )


@mcp.tool()
def start_live_surface_review(
    relations: list[str] | None = None,
    start_index: int = 0,
    model_name: str | None = None,
) -> dict:
    """Start a stepwise visual review of the last intersection report.

    Each group is displayed with review colors while the rest of the model is
    dimmed. Display only: no geometry is deleted or modified.
    """
    return live.call(
        "start_surface_review",
        {
            "relations": relations,
            "start_index": start_index,
            "model_name": model_name,
        },
        timeout=120,
    )


@mcp.tool()
def show_live_surface_review_group(
    index: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Show one review group again; display only."""
    return live.call(
        "show_surface_review_group",
        {"index": index, "model_name": model_name},
        timeout=60,
    )


@mcp.tool()
def next_live_surface_review_group(
    decision: str | None = None,
    note: str | None = None,
    model_name: str | None = None,
) -> dict:
    """Advance one review group after the user confirms the current pair.

    The decision is recorded for later reference only; no surface is deleted.
    """
    return live.call(
        "next_surface_review_group",
        {"decision": decision, "note": note, "model_name": model_name},
        timeout=60,
    )


@mcp.tool()
def stop_live_surface_review(model_name: str | None = None) -> dict:
    """Stop the review and clear its display; no geometry changes."""
    return live.call(
        "stop_surface_review", {"model_name": model_name}, timeout=60
    )


@mcp.tool()
def get_live_surface_review_status() -> dict:
    """Return the active review position and recorded decisions."""
    return live.call("get_surface_review_status", {}, timeout=60)


@mcp.tool()
def start_live_overlap_review(
    start_index: int = 0,
    source: str = "coplanar",
    model_name: str | None = None,
) -> dict:
    """Start a stepwise visual review of overlap groups.

    source selects coplanar groups, curved-surface groups, or all (regrouped
    over the combined pair set). Every surface in the current group is shown
    with a different review colour; the rest of the model is hidden. Display
    only: no geometry is modified.
    """
    return live.call(
        "start_overlap_review",
        {
            "start_index": start_index,
            "source": source,
            "model_name": model_name,
        },
        timeout=120,
    )


@mcp.tool()
def show_live_overlap_review_group(
    index: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Show one overlap group again; display only."""
    return live.call(
        "show_overlap_review_group",
        {"index": index, "model_name": model_name},
        timeout=60,
    )


@mcp.tool()
def next_live_overlap_review_group(
    decision: str | None = None,
    note: str | None = None,
    delete_ids: list[int] | None = None,
    model_name: str | None = None,
) -> dict:
    """Advance one overlap group after the user confirms the current group.

    The decision and any delete_ids are recorded for later reference only; no
    surface is deleted.
    """
    return live.call(
        "next_overlap_review_group",
        {
            "decision": decision,
            "note": note,
            "delete_ids": delete_ids,
            "model_name": model_name,
        },
        timeout=60,
    )


@mcp.tool()
def stop_live_overlap_review(model_name: str | None = None) -> dict:
    """Stop the overlap review and clear its display; no geometry changes."""
    return live.call(
        "stop_overlap_review", {"model_name": model_name}, timeout=60
    )


@mcp.tool()
def get_live_overlap_review_status() -> dict:
    """Return the active overlap review position and recorded decisions."""
    return live.call("get_overlap_review_status", {}, timeout=60)


@mcp.tool()
def remove_live_duplicate_surfaces(
    tolerance: float = 0.1,
    surface_ids: list[int] | None = None,
    model_name: str | None = None,
) -> dict:
    """Delete redundant coincident surfaces, keeping one surface per duplicate set.

    A checkpoint is created before deletion and restored if the deleted set
    differs from the detected duplicates or if a solid is lost.

    The call is refused while the CATIA material-pair gate is open.  Removing
    a duplicate before the reference pass can open a material shell; the
    returned decision_required payload must be put to the operator.
    """
    blocked = GATE.require_model_clear(
        model_name, operation="remove_duplicate_surfaces"
    )
    if blocked is not None:
        return blocked
    return live.call(
        "remove_duplicate_surfaces",
        {
            "tolerance": tolerance,
            "surface_ids": surface_ids,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def stitch_live_free_edges(
    tolerance: float = 0.2,
    max_attempts: int = 5000,
    surface_ids: list[int] | None = None,
    model_name: str | None = None,
) -> dict:
    """Stitch all free surface edges with *edgesmerge until none remain.

    Each pass pairs the current free edges with all model lines, ordered by
    sampled distance, so a free edge can merge onto an existing shared edge as
    well as onto another free edge. A successful merge restarts the pass
    because edge IDs change. The run stops when a full pass merges nothing or
    max_attempts is reached. A checkpoint is created before stitching and
    restored if the surface set changes or the free-edge count increases.

    The call is refused while the CATIA material-pair gate is open: stitching
    rewrites the surface-edge topology the reference shells are built from, and
    the returned decision_required payload must be put to the operator.
    """
    blocked = GATE.require_model_clear(model_name, operation="stitch_free_edges")
    if blocked is not None:
        return blocked
    return live.call(
        "stitch_free_edges",
        {
            "tolerance": tolerance,
            "max_attempts": max_attempts,
            "surface_ids": surface_ids,
            "model_name": model_name,
        },
        timeout=1200,
    )


@mcp.tool()
def trim_live_free_edges(
    surface_ids: list[int] | None = None,
    line_ids: list[int] | None = None,
    vector: list[float] | None = None,
    distance: float = 0.0,
    model_name: str | None = None,
) -> dict:
    """Trim surfaces along free-edge lines (Surface Edit > Trim with Lines).

    When line_ids is omitted, every line in the model's free-edge loops is used.
    Lines sweep through the whole surface along the surface normal, or along the
    supplied vector. The live bridge checkpoints the model and restores it when
    nothing changes, when the element count drops, or when the free-edge count
    increases. Review the returned risk annotations before continuing.

    The call is refused while the CATIA material-pair gate is open: trimming
    splits surfaces and changes the surface set the reference shells are built
    from, and the returned decision_required payload must be put to the
    operator.
    """
    blocked = GATE.require_model_clear(model_name, operation="trim_free_edges")
    if blocked is not None:
        return blocked
    return live.call(
        "trim_free_edges",
        {
            "surface_ids": surface_ids,
            "line_ids": line_ids,
            "vector": vector,
            "distance": distance,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def create_live_nodes(
    coordinates: list[list[float]], model_name: str | None = None
) -> dict:
    """Create up to 5000 nodes in the live model from finite [x, y, z] coordinates."""
    return live.call(
        "create_nodes",
        {"coordinates": coordinates, "model_name": model_name},
        timeout=120,
    )


@mcp.tool()
def create_live_elements(
    node_ids: list[list[int]],
    config: int,
    solver_type: int = 1,
    auto_order: bool = False,
    component_id: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Create live elements from existing node IDs using an explicit HyperMesh config/type.

    When component_id is given, the elements are created in that component and
    membership is verified before returning.
    """
    return live.call(
        "create_elements",
        {
            "node_ids": node_ids,
            "config": config,
            "solver_type": solver_type,
            "auto_order": auto_order,
            "component_id": component_id,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def organize_live_elements(
    target_component_id: int,
    element_ids: list[int] | None = None,
    source_component_id: int | None = None,
    surface_ids: list[int] | None = None,
    bbox: list[float] | None = None,
    mode: str = "move",
    model_name: str | None = None,
) -> dict:
    """Move already-meshed elements into another component without re-meshing.

    Select elements by explicit ID, by all elements of a source component
    (optionally narrowed to an axis-aligned box [xmin, ymin, zmin, xmax, ymax,
    zmax] tested against the element centroid), or by the surfaces they are
    attached to. Connectivity and element IDs are preserved; only the owning
    collector changes. A checkpoint is written before the move. mode="copy"
    duplicates the marked elements instead of moving them.

    A surface-based selection uses HyperMesh's element-to-surface
    association, which is created when the surface is meshed. The reference
    pass makes that link explicit by meshing each surface into its own
    component; the production pass meshes all surfaces in one call for
    conformal shared edges and keeps no such record. The handler does not
    verify the association. Before accepting a grouped mesh, count elements
    per surface, check every count is non-zero, check the sum equals the mesh
    total, and check no element is selected by two surfaces.
    """
    return live.call(
        "organize_elements",
        {
            "target_component_id": target_component_id,
            "element_ids": element_ids,
            "source_component_id": source_component_id,
            "surface_ids": surface_ids,
            "bbox": bbox,
            "mode": mode,
            "model_name": model_name,
        },
        timeout=1200,
    )


@mcp.tool()
def create_live_material(
    name: str,
    cardimage: str | None = None,
    values: dict | None = None,
    model_name: str | None = None,
) -> dict:
    """Create one live material with explicit solver card image and bounded attributes."""
    return live.call(
        "create_material",
        {
            "name": name,
            "cardimage": cardimage,
            "values": values,
            "model_name": model_name,
        },
        timeout=120,
    )


@mcp.tool()
def create_live_component(
    name: str,
    property_id: int | None = None,
    material_id: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Create one live component, optionally bound to an existing property or material."""
    return live.call(
        "create_component",
        {
            "name": name,
            "property_id": property_id,
            "material_id": material_id,
            "model_name": model_name,
        },
        timeout=120,
    )


@mcp.tool()
def create_live_solver_card_entity(
    entity_type: Literal[
        "property", "loadcol", "loadstep", "set", "constraint",
        "contactbehavior", "contactgroup", "contactsurf", "group"
    ],
    name: str,
    cardimage: str | None = None,
    values: dict | None = None,
    references: dict | None = None,
    model_name: str | None = None,
) -> dict:
    """Create a checkpointed Property, collector, load step, set, or contact solver card."""
    return live.call(
        "create_solver_card_entity",
        {
            "entity_type": entity_type,
            "name": name,
            "cardimage": cardimage,
            "values": values,
            "references": references,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_nodal_load(
    node_ids: list[int],
    load_kind: Literal[
        "force", "moment", "constraint", "temperature", "flux", "velocity", "acceleration"
    ],
    components: list[float],
    solver_type: int = 1,
    loadcol_id: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Create a checkpointed nodal force, moment, SPC, temperature, flux, velocity, or acceleration."""
    return live.call(
        "create_nodal_load",
        {
            "node_ids": node_ids,
            "load_kind": load_kind,
            "components": components,
            "solver_type": solver_type,
            "loadcol_id": loadcol_id,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_pressure_load(
    entity_type: Literal["surface", "element"],
    entity_ids: list[int],
    magnitude: float,
    direction: list[float] | None = None,
    face_node_ids: list[int] | None = None,
    break_angle: float = 30.0,
    loadcol_id: int | None = None,
    model_name: str | None = None,
) -> dict:
    """Create a checkpointed pressure on explicit surfaces or solid-element faces."""
    return live.call(
        "create_pressure_load",
        {
            "entity_type": entity_type,
            "entity_ids": entity_ids,
            "magnitude": magnitude,
            "direction": direction,
            "face_node_ids": face_node_ids,
            "break_angle": break_angle,
            "loadcol_id": loadcol_id,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_loadstep(
    name: str,
    analysis_type_attribute: str,
    analysis_type: int | str,
    load_attribute: str | None = None,
    loadcol_id: int | None = None,
    spc_attribute: str | None = None,
    spc_loadcol_id: int | None = None,
    cardimage: str | None = None,
    values: dict | None = None,
    model_name: str | None = None,
) -> dict:
    """Create a solver-specific Load Step with explicit load/SPC collector references."""
    return live.call(
        "create_loadstep",
        {
            "name": name,
            "analysis_type_attribute": analysis_type_attribute,
            "analysis_type": analysis_type,
            "load_attribute": load_attribute,
            "loadcol_id": loadcol_id,
            "spc_attribute": spc_attribute,
            "spc_loadcol_id": spc_loadcol_id,
            "cardimage": cardimage,
            "values": values,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_rigid_link(
    independent_node_id: int,
    dependent_node_ids: list[int],
    dofs: int = 123456,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create a checkpointed rigid link from one independent node to dependent nodes."""
    return live.call(
        "create_rigid_link",
        {
            "independent_node_id": independent_node_id,
            "dependent_node_ids": dependent_node_ids,
            "dofs": dofs,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_rbe3(
    independent_node_ids: list[int],
    dependent_node_id: int | None = None,
    independent_dofs: int = 123456,
    independent_weights: list[float] | None = None,
    dependent_dofs: list[bool] | None = None,
    dependent_weight: float = 1.0,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create a checkpointed RBE3 spider; omit dependent_node_id to use the centroid."""
    return live.call(
        "create_rbe3",
        {
            "independent_node_ids": independent_node_ids,
            "dependent_node_id": dependent_node_id,
            "independent_dofs": independent_dofs,
            "independent_weights": independent_weights,
            "dependent_dofs": dependent_dofs,
            "dependent_weight": dependent_weight,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_weld(
    independent_node_id: int,
    dependent_node_id: int,
    length: float = 0.0,
    create_systems: bool = False,
    move_node: bool = False,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create a checkpointed solver-profile weld element between two live nodes."""
    return live.call(
        "create_weld",
        {
            "independent_node_id": independent_node_id,
            "dependent_node_id": dependent_node_id,
            "length": length,
            "create_systems": create_systems,
            "move_node": move_node,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=300,
    )


@mcp.tool()
def create_live_spot_weld(
    independent_node_id: int,
    dependent_node_id: int,
    config: int,
    property_name: str,
    length: float = 0.0,
    create_system: bool = False,
    move_node: bool = False,
    remesh: bool = False,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create a checkpointed solver-profile spot weld using an existing property."""
    return live.call(
        "create_spot_weld",
        {
            "independent_node_id": independent_node_id,
            "dependent_node_id": dependent_node_id,
            "config": config,
            "property_name": property_name,
            "length": length,
            "create_system": create_system,
            "move_node": move_node,
            "remesh": remesh,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=600,
    )


@mcp.tool()
def create_live_connector(
    location_entity_type: Literal["node", "line"],
    location_ids: list[int],
    style: Literal["spot", "seam", "area", "bolt"],
    link_entity_type: Literal["component", "surface"],
    link_ids: list[int],
    num_links: int = 2,
    tolerance: float = 0.0,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create unrealized HyperMesh connector intent with explicit locations and links."""
    return live.call(
        "create_connector",
        {
            "location_entity_type": location_entity_type,
            "location_ids": location_ids,
            "style": style,
            "link_entity_type": link_entity_type,
            "link_ids": link_ids,
            "num_links": num_links,
            "tolerance": tolerance,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=600,
    )


@mcp.tool()
def create_live_solid_block(
    origin: list[float],
    dimensions: list[float],
    model_name: str | None = None,
) -> dict:
    """Create an axis-aligned HyperMesh solid block after an automatic model checkpoint."""
    return live.call(
        "create_solid_block",
        {"origin": origin, "dimensions": dimensions, "model_name": model_name},
        timeout=180,
    )


@mcp.tool()
def create_live_solid_cylinder(
    base_center: list[float],
    axis: list[float],
    radius: float,
    height: float,
    model_name: str | None = None,
) -> dict:
    """Create a full cylindrical HyperMesh solid after an automatic model checkpoint."""
    return live.call(
        "create_solid_cylinder",
        {
            "base_center": base_center,
            "axis": axis,
            "radius": radius,
            "height": height,
            "model_name": model_name,
        },
        timeout=180,
    )


@mcp.tool()
def import_live_cad(
    project_id: str,
    input_name: str,
    translator: Literal[
        "step_ct", "iges_altair", "parasolid_parasolid", "catia_ct"
    ]
    | None = None,
    options: list[str] | None = None,
    model_name: str | None = None,
) -> dict:
    """Import a project STEP, IGES, Parasolid, or CATIA file through the embedded HyperMesh API."""
    input_file = projects.input_file(project_id, input_name)
    if input_file.suffix.lower() not in {
        ".step",
        ".stp",
        ".iges",
        ".igs",
        ".x_t",
        ".x_b",
        ".catpart",
        ".catproduct",
    }:
        raise ValueError(
            "input_name must refer to a STEP, IGES, Parasolid, or CATIA project input"
        )
    result = live.call(
        "import_cad",
        {
            "input_file": str(input_file),
            "translator": translator,
            "options": options,
            "model_name": model_name,
        },
        timeout=900,
    )
    if input_file.suffix.lower() in {".catpart", ".catproduct"}:
        # A CATIA import starts the FEKO-oriented path, where the material
        # decision must precede duplicate removal and surface deletion.  The
        # gate makes the primitive tools stop and ask even when the caller did
        # not use the staged workflow.
        gate = GATE.arm_model(model_name, source=str(input_file))
        result = dict(result)
        result["material_pair_gate"] = decision_payload(
            gate["token"], operation="import_live_cad"
        )
        result["material_pair_gate"]["gate_stage"] = gate["stage"]
        result["material_pair_gate"]["resolved_by"] = (
            "call resolve_material_pair_decision, or use "
            "run_catia_feko_workflow action='classify'"
        )
    return result


@mcp.tool()
def automesh_live_surfaces(
    surface_ids: list[int],
    element_size: float,
    element_type: Literal["tria", "quad", "mixed", "right_tria", "quad_only"] = "mixed",
    density_mode: Literal[
        "proximity_curvature",
        "curvature",
        "proximity_curvature_free_edge",
        "curvature_free_edge",
    ] = "proximity_curvature",
    mesh_algorithm: Literal["free", "mapped"] = "free",
    min_size: float | None = None,
    max_size: float | None = None,
    chordal_deviation: float | None = None,
    max_angle: float = 30.0,
    growth_rate: float = 1.2,
    keep_existing_mesh: bool = True,
    component_id: int | None = None,
    model_name: str | None = None,
    mesh_type: str | None = None,
) -> dict:
    """Generate a free surface mesh on explicit live surface IDs.

    The Surface Mesh panel has two separate controls.  ``mesh_algorithm`` is
    the GUI Mesh type: ``free`` uses HyperMesh's unstructured growth mesher and
    is the only algorithm implemented; ``mapped`` is rejected.  The
    ``density_mode`` values control Proximity/Curvature density and are not the
    Free/Mapped selector.  ``mesh_type`` is a deprecated alias for
    ``density_mode``; do not read it as the GUI Mesh type.

    When component_id is given, the mesh is organized into that component and
    membership is verified before returning.  element_count is the true total;
    element_ids is a bounded sample and element_ids_truncated says whether more
    exist, so a large mesh never fails on result size.
    """
    return live.call(
        "automesh_surfaces",
        {
            "surface_ids": surface_ids,
            "element_size": element_size,
            "element_type": element_type,
            "density_mode": density_mode,
            "mesh_algorithm": mesh_algorithm,
            "min_size": min_size,
            "max_size": max_size,
            "chordal_deviation": chordal_deviation,
            "max_angle": max_angle,
            "growth_rate": growth_rate,
            "keep_existing_mesh": keep_existing_mesh,
            "component_id": component_id,
            "model_name": model_name,
            "mesh_type": mesh_type,
        },
        timeout=900,
    )


@mcp.tool()
def solid_map_live_solids(
    solid_ids: list[int],
    element_size: float,
    element_type: Literal["tria", "quad", "mixed"] = "mixed",
    organize_to_current_component: bool = False,
    extra_smoothing: bool = True,
    remesh_shell_mesh: bool = False,
    continue_on_negative_jacobian: bool = False,
    model_name: str | None = None,
) -> dict:
    """Run native HyperMesh Solid Map on explicit mappable solids with rollback protection.

    element_count is the true total; element_ids is a bounded sample and
    element_ids_truncated says whether more exist.
    """
    return live.call(
        "solid_map_mesh",
        {
            "solid_ids": solid_ids,
            "element_size": element_size,
            "element_type": element_type,
            "organize_to_current_component": organize_to_current_component,
            "extra_smoothing": extra_smoothing,
            "remesh_shell_mesh": remesh_shell_mesh,
            "continue_on_negative_jacobian": continue_on_negative_jacobian,
            "model_name": model_name,
        },
        timeout=1800,
    )


@mcp.tool()
def tetra_mesh_live_solids(
    solid_ids: list[int],
    element_size: float,
    min_size: float | None = None,
    max_size: float | None = None,
    growth_rate: float = 1.3,
    element_order: Literal[1, 2] = 1,
    use_existing_surface_mesh: bool = True,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Generate a native structural tetra mesh on explicit live solid IDs with rollback protection."""
    return live.call(
        "tetra_mesh_solids",
        {
            "solid_ids": solid_ids,
            "element_size": element_size,
            "min_size": min_size,
            "max_size": max_size,
            "growth_rate": growth_rate,
            "element_order": element_order,
            "use_existing_surface_mesh": use_existing_surface_mesh,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=1800,
    )


@mcp.tool()
def repair_live_mesh_quality(
    element_ids: list[int],
    anchor_node_ids: list[int] | None = None,
    iterations: int = 5,
    method: Literal[
        "Angle", "AutoDecideWithoutQI", "AutoDecideWithQI",
        "AutoDecideWithQI_Params_locked", "QI", "Shape", "Size"
    ] = "AutoDecideWithQI_Params_locked",
    anchor_free_edges: bool = True,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Smooth selected mesh with checkpoint/rollback and before/after quality evidence."""
    return live.call(
        "repair_mesh_quality",
        {
            "element_ids": element_ids,
            "anchor_node_ids": anchor_node_ids,
            "iterations": iterations,
            "method": method,
            "anchor_free_edges": anchor_free_edges,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=1800,
    )


@mcp.tool()
def create_live_cylindrical_ogrid(
    base_center: list[float],
    axis: list[float],
    length: float,
    radius: float,
    axial_divisions: int = 4,
    circumferential_divisions: int = 12,
    radial_layers: int = 2,
    core_ratio: float = 0.4,
    model_name: str | None = None,
    refresh: bool = True,
) -> dict:
    """Create a bounded cylindrical radial O-grid: Hex8 rings around a Penta6 core."""
    return live.call(
        "create_cylindrical_ogrid",
        {
            "base_center": base_center,
            "axis": axis,
            "length": length,
            "radius": radius,
            "axial_divisions": axial_divisions,
            "circumferential_divisions": circumferential_divisions,
            "radial_layers": radial_layers,
            "core_ratio": core_ratio,
            "model_name": model_name,
            "refresh": refresh,
        },
        timeout=900,
    )


@mcp.tool()
def get_live_mesh_quality(
    element_ids: list[int] | None = None,
    model_name: str | None = None,
) -> dict:
    """Report available live volume, Jacobian, aspect, and skew statistics without fabrication."""
    return live.call(
        "get_mesh_quality",
        {"element_ids": element_ids, "model_name": model_name},
        timeout=300,
    )


@mcp.tool()
def create_live_checkpoint(
    label: str = "manual", model_name: str | None = None
) -> dict:
    """Save a bridge-managed .hm checkpoint under the configured workspace."""
    return live.call(
        "create_checkpoint", {"label": label, "model_name": model_name}, timeout=300
    )


@mcp.tool()
def rollback_live_checkpoint(
    checkpoint_file: str,
    confirm: bool = False,
    model_name: str | None = None,
) -> dict:
    """Replace live model state with a bridge-created checkpoint; confirm=true is mandatory."""
    return live.call(
        "rollback_checkpoint",
        {
            "checkpoint_file": checkpoint_file,
            "confirm": confirm,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def load_live_model(
    project_id: str,
    input_name: str,
    replace_current: bool = False,
    load_cad_geometry_as_graphics: bool = False,
    model_name: str | None = None,
) -> dict:
    """Load a project .hm file into the live session; replace_current=true is mandatory."""
    input_file = projects.input_file(project_id, input_name)
    if input_file.suffix.lower() != ".hm":
        raise ValueError("input_name must refer to a .hm file in the project input directory")
    return live.call(
        "load_model",
        {
            "input_file": str(input_file),
            "replace_current": replace_current,
            "load_cad_geometry_as_graphics": load_cad_geometry_as_graphics,
            "model_name": model_name,
        },
        timeout=300,
    )


@mcp.tool()
def refresh_live_view(fit: bool = True) -> dict:
    """Redraw the live HyperMesh graphics window and optionally fit the visible model."""
    return live.call("refresh_view", {"fit": fit}, timeout=30)


@mcp.tool()
def get_live_model_metrics(model_name: str | None = None) -> dict:
    """Query solver, total mass, center of gravity, current view, and existing entity types."""
    return live.call("get_model_metrics", {"model_name": model_name}, timeout=60)


@mcp.tool()
def save_live_model(
    project_id: str,
    output_name: str,
    model_name: str | None = None,
    overwrite: bool = False,
    do_not_write_facets: int = 0,
) -> dict:
    """Save the live model into the MCP project output directory; overwrite is explicit."""
    output = projects.root(project_id) / "output" / Path(output_name).name
    if output.suffix.lower() != ".hm":
        output = output.with_suffix(".hm")
    return live.call(
        "save_model",
        {
            "output_file": str(output),
            "model_name": model_name,
            "overwrite": overwrite,
            "do_not_write_facets": do_not_write_facets,
        },
        timeout=300,
    )


def _feko_template(template_file: str | None) -> str:
    """The FEKO export template to use; the installation default unless one is named."""
    if template_file is not None:
        return template_file
    if settings.installation_root is None:
        raise ValueError(
            "HYPERWORKS_HOME must be configured to locate the FEKO export template"
        )
    return str(
        settings.installation_root
        / "hwdesktop"
        / "templates"
        / "feoutput"
        / "feko"
        / "feko.tpl"
    )


@mcp.tool()
def export_live_feko_mesh(
    output_name: str,
    model_name: str | None = None,
    template_file: str | None = None,
) -> dict:
    """Write the live mesh as a FEKO .fhm file into the shared export folder."""
    output = settings.workspace / "export" / Path(output_name).name
    if output.suffix.lower() != ".fhm":
        output = output.with_suffix(".fhm")
    resolved_template = _feko_template(template_file)
    return live.call(
        "export_feko_mesh",
        {
            "output_file": str(output),
            "template_file": resolved_template,
            "model_name": model_name,
        },
        timeout=600,
    )


@mcp.tool()
def classify_material_pairs(
    fhm_name: str,
    material_map: dict[str, str],
    targets: list[str] | None = None,
    face_tags: dict[str, list[str]] | None = None,
    samples: int = 10,
    threshold: float = 0.55,
    outside: str = "air",
    model_name: str | None = None,
) -> dict:
    """Name the two materials on either side of each labelled surface.

    The mesh must give every CAD surface its own label, and ``material_map``
    must state which material each label belongs to; the set of materials is
    whatever appears in that mapping, so nothing is assumed about the model.
    Each material becomes a closed reference shell, and a point is inside a
    material when the winding number of that shell is close to one.  Probing
    both sides of a surface along its normal therefore names the pair without
    measuring distances, which keeps the answer independent of element size
    and of how well the surfaces were stitched.

    ``face_tags`` carries the simulation assignment of each label, keyed by
    label.  An assignment such as a perfect-conductor sheet, an impedance
    boundary or a symmetry plane lives on the surface and has no volume, so
    probing can never detect it and it has to be supplied; the tags are
    appended to the group name in sorted order.

    ``outside`` names the region no material encloses.  It may also be the
    name of a material: an enclosed cavity holding the same medium as the
    surroundings is one medium, and both report that name.  The per-label
    winding values still distinguish the two origins for diagnostics.

    The result reports, per label, the ordered pair (plus, minus) with the
    material found along and against the surface normal, the vote split over
    the sampled triangles, and the winding margin.  An ordered pair matters:
    air/carrier and carrier/air are different boundary groups.  A label whose
    votes split is not decided - list it for the operator instead of picking.

    A successful call also clears the model-level material-pair gate that a
    CATIA import opened, because this is the classification the gate asked
    for.  The pair table from the staged workflow is still the one production
    reads; this tool is the manual equivalent.
    """
    path = settings.workspace / "export" / Path(fhm_name).name
    if not path.exists():
        raise ValueError(f"no such export file: {path.name}")
    if not material_map:
        raise ValueError("material_map must map at least one label to a material")
    report = classify(
        path,
        material_map,
        targets=targets,
        face_tags=face_tags,
        samples=max(1, int(samples)),
        threshold=float(threshold),
        outside=outside,
    )
    GATE.mark_model_classified(model_name)
    return report.as_dict()


@mcp.tool()
def resolve_material_pair_decision(
    decision: Literal["classify", "skip"],
    model_name: str | None = None,
    token: str | None = None,
) -> dict:
    """Answer the material-pair gate that a CATIA import opened.

    The gate exists because duplicate removal and surface deletion must not
    run before the material pairs are decided: an open reference shell
    silently corrupts the pairs.  ``decision="classify"`` only authorizes the
    work; duplicate removal and deletion stay blocked until
    ``classify_material_pairs`` succeeds.  ``decision="skip"`` records that the
    pairs are undetermined and opens the gate, but performs no other task.
    """
    gate = GATE.model_gate(model_name)
    if gate is None:
        return {
            "status": "no_pending_gate",
            "stage": "material_pair_decision",
            "model_name": model_name,
            "instructions": (
                "当前模型没有待回答的材料对问题；如果刚导入 CATIA 文件，"
                "请先调用 import_live_cad。"
            ),
        }
    if token is not None and token != gate["token"]:
        raise ValueError("token does not match the pending material-pair gate")
    if gate["stage"] == "authorized" and decision == "skip":
        raise ValueError(
            "material-pair classification was already authorized; finish it "
            "or start a new run instead of skipping it"
        )
    resolved = GATE.resolve_model(model_name, decision)
    if decision == "classify":
        return {
            "status": "authorized",
            "stage": "material_pair_decision",
            "decision": "classify",
            "gate_stage": resolved["stage"],
            "token": resolved["token"],
            "instructions": (
                "材料对判定已获授权，但尚未完成。请用 classify_material_pairs "
                "或 run_catia_feko_workflow 的 classify 阶段完成判定；"
                "在判定完成前，去重和删面仍被阻止。"
            ),
        }
    return {
        "status": "skipped",
        "stage": "material_pair_decision",
        "decision": "skip",
        "gate_stage": resolved["stage"],
        "token": resolved["token"],
        "material_pairs": "not_determined",
        "instructions": (
            "材料对未判定，去重和删面已放行。未执行任何其他操作；"
            "下一步由操作者指定。"
        ),
    }


@mcp.tool()
def run_catia_feko_workflow(
    project_id: str,
    input_name: str,
    surface_materials: dict[str, str],
    element_size: float,
    export_name: str,
    mode: Literal["general", "verification"] = "general",
    translator: str | None = None,
    drop_solids: bool = True,
    drop_surfaces: list[int] | None = None,
    duplicate_tolerance: float = 0.1,
    stitch_tolerance: float = 0.2,
    element_type: str = "tria",
    mesh_type: str | None = None,
    density_mode: str | None = None,
    mesh_algorithm: Literal["free", "mapped"] = "free",
    min_size: float | None = None,
    max_size: float | None = None,
    reference_element_size: float | None = None,
    face_tags: dict[str, list[str]] | None = None,
    samples: int = 10,
    threshold: float = 0.55,
    outside: str = "air",
    template_file: str | None = None,
    stop_after: str | None = None,
    action: Literal["start", "classify", "skip", "produce", "status"] = "start",
    token: str | None = None,
    confirm_pairs: bool = False,
) -> dict:
    """Run the staged CATIA-to-FEKO chain with an operator gate.

    The tool is a small state machine.  Its first action is a question the
    assistant must put to the operator:

    ``action="start"``
        Return ``status="decision_required"`` with the question, the reason,
        a coarse-mesh hint, two options and a token.  Nothing is imported and
        no model entity is touched.  Ask the operator; do not choose for them.

    ``action="classify"``
        After the operator chose classification.  Import the CAD file, mesh
        every surface in ``surface_materials`` as its own reference component,
        export the reference mesh, compute the ordered material pairs, write
        the pair table, delete the reference components, and stop.  Duplicates
        and excluded surfaces stay in the model for this pass.  The model is
        left with the original geometry and no elements; no duplicate removal
        or surface deletion has happened.

    ``action="skip"``
        After the operator chose not to classify.  Record the decision and
        stop.  Nothing is imported, removed, meshed or exported.  The caller
        must ask what other task to do; this tool does not assume a production
        chain.

    ``action="produce"``
        Only after the operator confirms the pair table with
        ``confirm_pairs=true``.  Continue from the already imported geometry:
        drop solids, remove duplicates, drop excluded surfaces, stitch, mesh,
        create one component per boundary group, move the elements in, and
        export the grouped .fhm.  It does not import again.

    ``action="status"``
        Report the current stage without changing the model.  A session is
        keyed by the input parameters, so ``start`` is idempotent.

    ``reference_element_size`` is optional and only affects the reference
    pass.  A coarse mesh is enough for the pair decision; its size is the
    operator's choice, and the tool supplies no default number.  The
    production mesh always uses ``element_size``.

    The decision must come before duplicate removal and surface deletion,
    because removing a surface can open a material's reference shell and
    silently corrupt the winding-number pairs.  The primitive duplicate,
    delete, stitch and trim tools enforce the same gate.  ``surface_materials``
    must include every material-bearing surface, duplicates and later-excluded
    surfaces included.  ``face_tags`` carries assignments that live on a face
    and have no volume.  ``mode`` and ``stop_after`` are retained for
    compatibility: ``classify`` uses verification semantics, ``produce``
    reads the table, and ``stop_after`` can halt a production run at a named
    step.

    ``mesh_algorithm`` is the GUI Mesh type; only ``free`` is implemented.
    ``density_mode`` is the Proximity/Curvature size-control setting.
    ``mesh_type`` is a deprecated alias for ``density_mode`` and is not the
    Free/Mapped selector.
    """
    if not surface_materials:
        raise ValueError("surface_materials must name at least one surface")
    if mesh_type in ("free", "mapped"):
        if mesh_algorithm != "free" and mesh_algorithm != mesh_type:
            raise ValueError("mesh_algorithm and mesh_type disagree")
        mesh_algorithm = mesh_type
        mesh_type = None
    if density_mode is not None and mesh_type is not None and density_mode != mesh_type:
        raise ValueError("density_mode and mesh_type disagree; mesh_type is deprecated")
    resolved_density = density_mode or mesh_type or "proximity_curvature"
    if mesh_algorithm != "free":
        raise ValueError(
            "Mapped surface meshing is not implemented; use mesh_algorithm='free'"
        )
    input_file = projects.input_file(project_id, input_name)
    # The template is only needed when a mesh is exported.  Resolving it on
    # action="start" would fail before the operator ever sees the question.
    resolved_template = (
        _feko_template(template_file)
        if action in ("classify", "produce")
        else template_file
    )
    config = WorkflowConfig(
        input_file=str(input_file),
        export_name=Path(export_name).name,
        element_size=float(element_size),
        surface_materials={int(key): str(value) for key, value in surface_materials.items()},
        reference_element_size=(
            None if reference_element_size is None else float(reference_element_size)
        ),
        template_file=resolved_template,
        mode=mode,
        translator=translator or "",
        drop_solids=bool(drop_solids),
        drop_surfaces=tuple(int(value) for value in (drop_surfaces or ())),
        duplicate_tolerance=float(duplicate_tolerance),
        stitch_tolerance=float(stitch_tolerance),
        element_type=element_type,
        density_mode=resolved_density,
        mesh_algorithm=mesh_algorithm,
        min_size=None if min_size is None else float(min_size),
        max_size=None if max_size is None else float(max_size),
        face_tags={
            int(key): list(value) for key, value in (face_tags or {}).items()
        },
        samples=max(1, int(samples)),
        threshold=float(threshold),
        outside=str(outside),
        stop_after=stop_after,
    )
    run_key_value = workflow_run_key(
        {
            "project_id": project_id,
            "input_name": input_name,
            "export_name": Path(export_name).name,
            "surface_materials": {
                str(key): str(value)
                for key, value in sorted(surface_materials.items())
            },
            "drop_surfaces": sorted(int(value) for value in (drop_surfaces or ())),
            "face_tags": {
                str(key): sorted(str(value) for value in values)
                for key, values in sorted((face_tags or {}).items())
            },
            "element_size": float(element_size),
            "reference_element_size": (
                None if reference_element_size is None else float(reference_element_size)
            ),
            "duplicate_tolerance": float(duplicate_tolerance),
            "mode": mode,
        }
    )
    export_dir = settings.workspace / "export"

    if action == "start":
        return workflow_stages.start(config, export_dir, run_key_value)

    if action == "status":
        if token is None:
            session = GATE.session_by_key(run_key_value)
            if session is None:
                return {
                    "status": "no_session",
                    "stage": "material_pair_decision",
                    "instructions": (
                        "没有找到这次配置的运行记录；请先调用 action='start'。"
                    ),
                }
        else:
            session = GATE.session(token)
        return workflow_stages.status(session)

    if token is None:
        raise ValueError(
            f"action={action!r} needs the token returned by action='start'"
        )
    session = GATE.session(token)
    if action == "classify":
        return workflow_stages.classify(session, live.call, config, export_dir)
    if action == "skip":
        return workflow_stages.skip(session)
    if action == "produce":
        return workflow_stages.produce(
            session, live.call, config, export_dir, bool(confirm_pairs)
        )
    raise ValueError(f"unknown action {action!r}")


@mcp.resource("hyperworks://environment")
def environment_resource() -> dict:
    return get_environment()


@mcp.resource("hyperworks://project/{project_id}")
def project_resource(project_id: str) -> dict:
    return get_project_summary(project_id)


@mcp.resource("hyperworks://job/{job_id}")
def job_resource(job_id: str) -> dict:
    return jobs.status(job_id)


@mcp.prompt()
def preprocess_and_solve(project_id: str, solver: str = "optistruct") -> str:
    return (
        f"For HyperWorks project {project_id}, inspect the environment and project inventory. "
        "Prepare a screened Tcl preprocessing script, explain its model assumptions, and run "
        f"HyperMesh Batch. Inspect its real log and artifacts. Before starting {solver}, show "
        "the selected deck, CPU count, and solver availability and ask for approval. Monitor "
        "the returned job ID without inventing progress or convergence."
    )


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()

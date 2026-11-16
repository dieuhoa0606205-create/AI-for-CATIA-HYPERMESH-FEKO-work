"""Staged CATIA -> FEKO workflow with an operator decision at its center.

The one-shot chain in :mod:`hyperworks_mcp.workflow` is still the executor.
This module splits it at the two points where the operator must decide:

``start``
    Return the material-pair question and a token.  Nothing is imported and
    no model entity is touched.

``classify``
    Import, mesh every material surface as its own reference component,
    export that reference mesh, decide the pairs, write the table, delete the
    reference components, and stop.  The geometry and the pair table remain;
    no duplicate removal or surface deletion has happened.

``skip``
    Record that the pairs are undetermined and stop.  Nothing is imported,
    removed, meshed or exported.  The caller must ask the operator what to do
    next; this module does not assume a production chain.

``produce``
    Only after the operator confirmed the pair table.  Continue from the
    already imported geometry with the normal production steps.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

from .workflow import (
    GENERAL,
    VERIFICATION,
    WorkflowConfig,
    pair_table_name,
    run as run_workflow,
)
from .workflow_gate import (
    DECISION_CLASSIFY,
    DECISION_SKIP,
    GATE,
    STAGE_AUTHORIZED,
    STAGE_CLASSIFIED,
    STAGE_PENDING,
    STAGE_SKIPPED,
    WorkflowSession,
    decision_payload,
)


def start(config: WorkflowConfig, export_dir: str | Path, key: str) -> dict[str, Any]:
    """Open the decision and return the question the operator must answer."""
    session, created = GATE.start(key)
    session = GATE.session(session.token)
    if session.stage == STAGE_AUTHORIZED:
        return {
            "status": "classification_authorized",
            "stage": "material_pair_decision",
            "token": session.token,
            "decision": session.decision,
            "instructions": (
                "操作者已选择先判定材料属性；请调用 action='classify' 完成判定。"
                "判定完成前不会去重或删面。"
            ),
        }
    if session.stage == STAGE_CLASSIFIED:
        return confirmation_payload(session)
    if session.stage == STAGE_SKIPPED:
        return skipped_payload(session)

    payload = decision_payload(session.token)
    payload.update(
        {
            "workflow": "catia_feko",
            "run_key": session.key,
            "surface_count": len(config.surface_materials),
            "materials": sorted(set(config.surface_materials.values())),
            "reference_element_size": {
                "parameter": "reference_element_size",
                "optional": True,
                "guidance": (
                    "参考网格只用于材料对判定，不进入最终网格；"
                    "尺寸由操作者按模型尺度决定，工具不设默认值。"
                ),
            },
        }
    )
    if not created:
        payload["resumed_session"] = True
    return payload


def classify(
    session: WorkflowSession,
    executor,
    config: WorkflowConfig,
    export_dir: str | Path,
) -> dict[str, Any]:
    """Run the reference pass and stop for pair confirmation."""
    session = _current(session)
    if session.stage == STAGE_SKIPPED:
        return skipped_payload(session)
    if session.stage == STAGE_CLASSIFIED:
        return confirmation_payload(session)
    if session.stage == STAGE_PENDING:
        # The caller invokes "classify" only after the operator chose it.  Record
        # that choice here, as "skip" already does; otherwise a fresh session can
        # never leave "pending" and the reference pass is unreachable.
        session = GATE.resolve(session.token, DECISION_CLASSIFY)
    if session.stage != STAGE_AUTHORIZED:
        raise ValueError(f"cannot classify from stage {session.stage!r}")

    export_dir = Path(export_dir)
    reference_config = replace(
        config,
        mode=VERIFICATION,
        stop_after="clear_reference",
    )
    # The primitive gate must stay closed until the reference pass succeeds.
    GATE.arm_model(None, source=config.input_file)
    GATE.resolve_model(None, DECISION_CLASSIFY)
    try:
        result = run_workflow(executor, reference_config, export_dir)
    except Exception as exc:  # noqa: BLE001 - reported with the workflow step
        GATE.mark_failed(session.token, str(exc))
        raise

    imported = result.get("import") or {}
    table_path = export_dir / pair_table_name(config.export_name)
    pairs = json.loads(table_path.read_text(encoding="utf-8"))
    pairs_step = next(
        (step for step in result.get("steps", []) if step.get("step") == "pairs"),
        {},
    )
    reference_export = (pairs_step.get("detail") or {}).get("reference_mesh")
    GATE.mark_classified(
        session.token,
        imported=imported,
        pair_table=pairs,
        pair_table_path=str(table_path),
        reference_export=reference_export,
        steps=result.get("steps", []),
    )
    GATE.mark_model_classified(None)
    return confirmation_payload(GATE.session(session.token))


def skip(session: WorkflowSession) -> dict[str, Any]:
    """Record that the operator chose not to decide the pairs."""
    session = _current(session)
    if session.stage == STAGE_SKIPPED:
        return skipped_payload(session)
    if session.stage in (STAGE_AUTHORIZED, STAGE_CLASSIFIED):
        raise ValueError(
            "classification has already started; finish it or start a new run"
        )
    GATE.resolve(session.token, DECISION_SKIP)
    GATE.resolve_model_if_present(None, DECISION_SKIP)
    return skipped_payload(GATE.session(session.token))


def produce(
    session: WorkflowSession,
    executor,
    config: WorkflowConfig,
    export_dir: str | Path,
    confirm_pairs: bool,
) -> dict[str, Any]:
    """Run the production chain after the operator confirmed the pair table."""
    session = _current(session)
    if session.stage == STAGE_PENDING:
        return decision_payload(session.token, operation="produce")
    if session.stage == STAGE_SKIPPED:
        return blocked_payload(session)
    if session.stage == STAGE_AUTHORIZED:
        return {
            "status": "classification_required",
            "stage": "material_pair_decision",
            "token": session.token,
            "instructions": "材料对判定尚未完成；先调用 action='classify'，确认配对表后再生产。",
        }
    if session.stage != STAGE_CLASSIFIED:
        raise ValueError(f"cannot produce from stage {session.stage!r}")

    if not session.pairs_confirmed:
        if not confirm_pairs:
            return confirmation_payload(session)
        GATE.confirm_pairs(session.token)
        session = GATE.session(session.token)

    production_config = replace(config, mode=GENERAL)
    result = run_workflow(
        executor,
        production_config,
        Path(export_dir),
        resume={"imported": session.imported},
    )
    result = dict(result)
    result["status"] = "stopped" if result.get("stopped_at") else "complete"
    result["steps"] = list(session.classify_steps) + list(result.get("steps", []))
    result["classify"] = {
        "pair_table": session.pair_table_path,
        "reference_mesh": session.reference_export,
        "pairs": session.pair_table,
    }
    result["token"] = session.token
    return result


def status(session: WorkflowSession) -> dict[str, Any]:
    """Report the current stage without changing the model."""
    session = _current(session)
    if session.stage == STAGE_PENDING:
        return decision_payload(session.token)
    if session.stage == STAGE_AUTHORIZED:
        return {
            "status": "classification_authorized",
            "stage": "material_pair_decision",
            "token": session.token,
            "decision": session.decision,
            "instructions": "请调用 action='classify' 完成材料对判定。",
        }
    if session.stage == STAGE_CLASSIFIED:
        return confirmation_payload(session)
    if session.stage == STAGE_SKIPPED:
        return skipped_payload(session)
    return {
        "status": "unknown",
        "stage": session.stage,
        "token": session.token,
    }


def confirmation_payload(session: WorkflowSession) -> dict[str, Any]:
    """Present the pair table and ask for the operator's confirmation."""
    pairs = session.pair_table or {}
    same_medium = []
    undecided = []
    for surface, entry in sorted(pairs.items(), key=lambda item: int(item[0])):
        tags = entry.get("tags") or []
        if (
            entry.get("minus") is not None
            and entry.get("minus") == entry.get("plus")
            and not tags
        ):
            same_medium.append({"surface": surface, "pair": entry.get("pair")})
        agreement = entry.get("agreement")
        if agreement is not None and float(agreement) < 1.0:
            undecided.append(
                {
                    "surface": surface,
                    "pair": entry.get("pair"),
                    "agreement": agreement,
                }
            )
    return {
        "status": (
            "ready_to_produce"
            if session.pairs_confirmed
            else "pair_confirmation_required"
        ),
        "stage": "pairs",
        "token": session.token,
        "pairs": pairs,
        "same_medium": same_medium,
        "undecided": undecided,
        "reference_mesh": session.reference_export,
        "pair_table": session.pair_table_path,
        "question": "材料对表是否确认？",
        "why": "确认后才允许去重、删面和后续生产链。",
        "instructions": (
            "把配对表交给操作者确认；确认后调用 action='produce'，"
            "confirm_pairs=true。未确认前不得去重、删面、缝合或划分。"
        ),
    }


def skipped_payload(session: WorkflowSession) -> dict[str, Any]:
    return {
        "status": "skipped",
        "stage": "material_pair_decision",
        "token": session.token,
        "decision": DECISION_SKIP,
        "material_pairs": "not_determined",
        "model_changed": False,
        "next": (
            "材料对未判定。未执行导入、去重、删面、缝合或划分。"
            "请询问操作者下一步要做什么；本工具不会自动继续。"
        ),
        "blocked_operations": [
            "按材料分组",
            "按材料对导出",
            "材料对未判定时的去重和删面",
        ],
        "instructions": "不要自行调用去重或删面工具。等操作者指定任务。",
    }


def blocked_payload(session: WorkflowSession) -> dict[str, Any]:
    return {
        "status": "blocked",
        "stage": "material_pair_decision",
        "token": session.token,
        "reason": (
            "material pairs were skipped; grouping and material-pair export "
            "cannot proceed without a pair table"
        ),
        "material_pairs": "not_determined",
        "instructions": (
            "材料对未判定，无法按材料分组。请按操作者指定的其他任务执行，"
            "或重新开始一次运行并选择 classify。"
        ),
    }


def _current(session: WorkflowSession) -> WorkflowSession:
    return GATE.session(session.token)

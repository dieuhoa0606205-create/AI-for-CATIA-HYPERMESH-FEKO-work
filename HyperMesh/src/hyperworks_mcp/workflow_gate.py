"""Operator gates for the CATIA -> FEKO workflow.

Two decisions must be taken by the operator, never inferred by the assistant:

* whether to decide the material pairs at all, and
* whether the resulting pair table is accepted.

The first decision has to happen before duplicate surfaces are removed,
surfaces are deleted, or free edges are stitched or trimmed.  Each of those
steps can open or reshape a material's reference shell, and a changed shell
silently corrupts the winding-number pairs.  The second decision separates
"the pairs were computed" from "the operator accepted them", so no destructive
step runs on an unreviewed table.

This module keeps both decisions in process memory.  The typed tools consult
it: the workflow stages open and resolve a session, and the primitive
duplicate/delete/stitch/trim tools refuse to run while a model-level gate is
still open.
A session and a model gate are deliberately separate - a manual import arms
the model gate, while the staged workflow owns the session.
"""

from __future__ import annotations

import hashlib
import json
import secrets
import threading
import time
from dataclasses import dataclass, field
from typing import Any

DECISION_CLASSIFY = "classify"
DECISION_SKIP = "skip"
DECISION_CHOICES = (DECISION_CLASSIFY, DECISION_SKIP)

STAGE_PENDING = "pending"
STAGE_AUTHORIZED = "authorized"
STAGE_CLASSIFIED = "classified"
STAGE_SKIPPED = "skipped"

QUESTION = "在去重面和删面之前，是否先进行材料属性判定？"
WHY = (
    "材料对判定依赖每种材料的封闭参考壳。去重或删面之后，参考壳可能开口，"
    "绕数结果会失真，配对表可能看似正常但实际错误。因此这一步必须发生在"
    "去重和删面之前。"
)
COARSE_MESH_HINT = (
    "这一步可以使用粗尺寸网格快速验证；它只用于材料对判定，不进入最终网格。"
    "网格尺寸由操作者按模型尺度决定，本工具不设默认值。"
)


def run_key(payload: dict[str, Any]) -> str:
    """Stable key for one workflow configuration.

    The key only identifies a run inside this server process.  It is not a
    secret and not a security boundary; the token returned by ``start`` is.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()[:24]


def decision_payload(token: str, operation: str | None = None) -> dict[str, Any]:
    """The question the assistant must put to the operator, verbatim."""
    return {
        "status": "decision_required",
        "stage": "material_pair_decision",
        "operation": operation,
        "question": QUESTION,
        "why": WHY,
        "coarse_mesh_hint": COARSE_MESH_HINT,
        "options": [
            {
                "value": DECISION_CLASSIFY,
                "label": "先判定材料属性",
                "consequence": (
                    "用当前全部带材料的面建立参考网格并计算材料对，"
                    "停在配对表确认；确认后才允许去重和删面。"
                ),
            },
            {
                "value": DECISION_SKIP,
                "label": "跳过判定，先做其他任务",
                "consequence": (
                    "不执行去重、删面或生产链；材料对保持未判定。"
                    "后续任务由操作者指定。"
                ),
            },
        ],
        "must_ask_operator": True,
        "instructions": (
            "把 question、why、coarse_mesh_hint 和 options 原样交给操作者，"
            "不要替操作者选择。操作者回答后，再调用本工具并带上 token。"
        ),
        "token": token,
    }


@dataclass
class WorkflowSession:
    """One staged run: the decision, the reference result, and the pair table."""

    token: str
    key: str
    stage: str = STAGE_PENDING
    decision: str | None = None
    pairs_confirmed: bool = False
    imported: dict[str, Any] | None = None
    pair_table: dict[str, Any] | None = None
    pair_table_path: str | None = None
    reference_export: str | None = None
    classify_steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    updated_at: float = field(default_factory=time.time)


class WorkflowGate:
    """Process-local state for the two operator decisions."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._by_token: dict[str, WorkflowSession] = {}
        self._by_key: dict[str, str] = {}
        self._model_gate: dict[str, dict[str, Any]] = {}

    # ------------------------------------------------------------------
    # Staged workflow session
    # ------------------------------------------------------------------
    def start(self, key: str) -> tuple[WorkflowSession, bool]:
        """Open or reuse the session for one configuration.

        Returns ``(session, created)``.  Reusing an open session makes the
        ``start`` action idempotent when a client retries or resumes.
        """
        with self._lock:
            token = self._by_key.get(key)
            if token is not None:
                session = self._by_token[token]
                if session.stage in (STAGE_PENDING, STAGE_AUTHORIZED, STAGE_CLASSIFIED):
                    return session, False
            session = WorkflowSession(token=secrets.token_urlsafe(24), key=key)
            self._by_token[session.token] = session
            self._by_key[key] = session.token
            return session, True

    def session(self, token: str) -> WorkflowSession:
        with self._lock:
            session = self._by_token.get(token)
            if session is None:
                raise ValueError(
                    "unknown or expired workflow token; call action='start' first"
                )
            return session

    def session_by_key(self, key: str) -> WorkflowSession | None:
        with self._lock:
            token = self._by_key.get(key)
            return self._by_token.get(token) if token else None

    def resolve(self, token: str, decision: str) -> WorkflowSession:
        if decision not in DECISION_CHOICES:
            raise ValueError("decision must be 'classify' or 'skip'")
        with self._lock:
            session = self.session(token)
            if session.stage == STAGE_PENDING:
                session.decision = decision
                session.stage = (
                    STAGE_AUTHORIZED if decision == DECISION_CLASSIFY else STAGE_SKIPPED
                )
                session.updated_at = time.time()
            elif session.decision != decision:
                raise ValueError(
                    f"workflow decision is already {session.decision!r}; "
                    f"cannot change it to {decision!r}"
                )
            return session

    def mark_classified(
        self,
        token: str,
        imported: dict[str, Any],
        pair_table: dict[str, Any],
        pair_table_path: str,
        reference_export: str | None,
        steps: list[dict[str, Any]],
    ) -> WorkflowSession:
        with self._lock:
            session = self.session(token)
            session.imported = imported
            session.pair_table = pair_table
            session.pair_table_path = str(pair_table_path)
            session.reference_export = (
                str(reference_export) if reference_export is not None else None
            )
            session.classify_steps = list(steps)
            session.stage = STAGE_CLASSIFIED
            session.updated_at = time.time()
            return session

    def mark_failed(self, token: str, error: str) -> WorkflowSession:
        with self._lock:
            session = self.session(token)
            session.error = str(error)
            session.updated_at = time.time()
            return session

    def confirm_pairs(self, token: str) -> WorkflowSession:
        with self._lock:
            session = self.session(token)
            if session.stage != STAGE_CLASSIFIED:
                raise ValueError("pairs must be classified before they can be confirmed")
            session.pairs_confirmed = True
            session.updated_at = time.time()
            return session

    # ------------------------------------------------------------------
    # Model-level gate for the primitive duplicate/delete tools
    # ------------------------------------------------------------------
    def arm_model(self, model_name: str | None, source: str) -> dict[str, Any]:
        """Open the material-pair gate for a model that was just imported."""
        key = model_name or "default"
        with self._lock:
            gate = self._model_gate.get(key)
            if gate is not None and gate["stage"] in (STAGE_PENDING, STAGE_AUTHORIZED):
                return dict(gate)
            gate = {
                "stage": STAGE_PENDING,
                "source": source,
                "token": secrets.token_urlsafe(16),
                "created_at": time.time(),
            }
            self._model_gate[key] = gate
            return dict(gate)

    def model_gate(self, model_name: str | None) -> dict[str, Any] | None:
        with self._lock:
            gate = self._model_gate.get(model_name or "default")
            return dict(gate) if gate else None

    def resolve_model(self, model_name: str | None, decision: str) -> dict[str, Any]:
        if decision not in DECISION_CHOICES:
            raise ValueError("decision must be 'classify' or 'skip'")
        key = model_name or "default"
        with self._lock:
            gate = self._model_gate.get(key)
            if gate is None:
                raise ValueError("no material-pair decision is pending for this model")
            if gate["stage"] == STAGE_CLASSIFIED:
                return dict(gate)
            previous = gate.get("decision")
            if previous is not None and previous != decision:
                raise ValueError(
                    f"material-pair decision is already {previous!r}; "
                    f"cannot change it to {decision!r}"
                )
            gate["stage"] = (
                STAGE_AUTHORIZED if decision == DECISION_CLASSIFY else STAGE_SKIPPED
            )
            gate["decision"] = decision
            gate["updated_at"] = time.time()
            return dict(gate)

    def resolve_model_if_present(
        self, model_name: str | None, decision: str
    ) -> dict[str, Any] | None:
        with self._lock:
            if self._model_gate.get(model_name or "default") is None:
                return None
            return self.resolve_model(model_name, decision)

    def mark_model_classified(self, model_name: str | None) -> dict[str, Any] | None:
        key = model_name or "default"
        with self._lock:
            gate = self._model_gate.get(key)
            if gate is None:
                return None
            gate["stage"] = STAGE_CLASSIFIED
            gate["updated_at"] = time.time()
            return dict(gate)

    def require_model_clear(
        self, model_name: str | None, operation: str
    ) -> dict[str, Any] | None:
        """Return the blocking question while the gate is open, else ``None``."""
        gate = self.model_gate(model_name)
        if gate is None:
            return None
        if gate["stage"] in (STAGE_PENDING, STAGE_AUTHORIZED):
            payload = decision_payload(gate["token"], operation=operation)
            payload["gate_stage"] = gate["stage"]
            if gate["stage"] == STAGE_AUTHORIZED:
                payload["why"] = (
                    "操作者已选择先判定材料属性，但判定尚未完成。"
                    "在配对表生成并确认之前，去重和删面仍被阻止。"
                )
            return payload
        return None

    def reset(self) -> None:
        """Clear all state; used by tests and by an explicit model reset."""
        with self._lock:
            self._by_token.clear()
            self._by_key.clear()
            self._model_gate.clear()


GATE = WorkflowGate()

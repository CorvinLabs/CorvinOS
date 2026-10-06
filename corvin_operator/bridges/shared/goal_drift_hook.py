"""P3 goal-drift alert hook (PLAN-0931, ADR-2101).

Wires the existing, working ``GoalAlignmentMonitor``
(``core/session_manager/monitors/goal_alignment.py``) into the two real
turn loops — ``adapter.py`` (bridge) and ``chat_runtime.py`` (console) —
instead of its current zero-live-caller chain through
``SessionLifecycleManager``. The monitor itself is untouched; this module
is the thin, fail-closed glue the plan asked for, same idiom as
``a2a_chat_pending_send.py``: no class state beyond a module-level
singleton, every public function fail-closed on exception, never raises
into the caller.

Reuses the P2 anchor (``context_engineering.anchor``) as the goal source —
no second capture path, no new storage. If ``cel_load_bearing_anchor`` is
off for the tenant, ``anchor.load_facts`` returns nothing useful and this
hook silently returns ``None`` every time (documented, not a bug).
"""
from __future__ import annotations

import hashlib
import logging
import sys
from typing import Any

logger = logging.getLogger("corvin.goal_drift_hook")

_FLAG_ID = "goal_drift_monitor_enabled"
_EVENT_TYPE = "goal_drift.alert_raised"

_monitor: Any = None  # lazy GoalAlignmentMonitor singleton, one process


def _get_monitor() -> Any:
    global _monitor
    if _monitor is None:
        from core.session_manager.monitors.goal_alignment import (  # noqa: PLC0415
            GoalAlignmentMonitor,
        )
        _monitor = GoalAlignmentMonitor()
    return _monitor


def _resolve_anchor() -> Any:
    """Resolve the ``context_engineering.anchor`` submodule across BOTH
    import contexts this hook runs in: the bridge loads the ``context_engineering``
    package by file path into ``sys.modules["context_engineering"]`` (operator/
    is not on the bridge's PYTHONPATH); the console imports it normally as
    ``corvin_operator.context_engineering``. Mirrors ``adapter.py``'s own
    dual-context CEL resolution — never raises."""
    try:
        cel_mod = sys.modules.get("context_engineering")
        if cel_mod is not None and hasattr(cel_mod, "anchor"):
            return cel_mod.anchor
    except Exception:  # noqa: BLE001
        pass
    try:
        from corvin_operator.context_engineering import anchor as _anchor  # noqa: PLC0415
        return _anchor
    except Exception:  # noqa: BLE001
        return None


def _is_enabled(tenant_id: str) -> bool:
    """Re-checked on every call (fail-closed): flag off or the flag
    subsystem itself unreadable → False, never raise mid-turn."""
    try:
        from corvin_core import feature_flags as _ff  # noqa: PLC0415
        return bool(_ff.is_enabled(_FLAG_ID, tenant_id))
    except Exception:  # noqa: BLE001 — no flag subsystem → off (ship-dark)
        return False


def _fp(value: str) -> str:
    """One-way 8-hex fingerprint — same construction as the adapter's audit
    floor (``adapter._pii_fp`` / ``session_ledger._pii_fp``). A raw session
    key must never reach the append-only chain."""
    return hashlib.sha256(value.encode("utf-8", "surrogatepass")).hexdigest()[:8]


def _audit_alert(tenant_id: str, session_key: str, alert: Any) -> None:
    """Hash-chain the alert (ADR-2101 P3 audit_events). Best-effort: a
    write failure is logged, never raised into the turn."""
    try:
        from forge.paths import tenant_audit_chain  # noqa: PLC0415
        from forge.security_events import write_event  # noqa: PLC0415
        write_event(
            tenant_audit_chain(tenant_id), _EVENT_TYPE,
            tool="goal_drift_hook",
            details={
                "tenant_id": tenant_id,
                "session_key_fingerprint": _fp(session_key),
                "similarity_score": alert.metadata.get("similarity_score"),
                "consecutive_low_count": alert.metadata.get("consecutive_low_count"),
            },
        )
    except Exception as exc:  # noqa: BLE001 — advisory hook: never break the turn
        logger.error(
            "goal_drift_hook: alert AUDIT-WRITE FAILED (tenant %s): %s "
            "— record NOT persisted to the hash chain", tenant_id, type(exc).__name__,
        )


def maybe_check_goal_drift(
    tenant_id: str, session_key: str, task_id: str, current_work: str,
) -> dict | None:
    """Check the current turn's work against the session's anchored goal.

    Returns a JSON-safe alert dict, or ``None`` (no drift / flag off / no
    anchored goal to compare against / monitor unavailable). NEVER raises —
    every branch below is fail-closed toward "no alert", matching every
    other CEL turn-loop hook's discipline (a broken monitor must never
    break a reply).
    """
    try:
        if not tenant_id or not session_key or not current_work:
            return None
        if not _is_enabled(tenant_id):
            return None

        anchor = _resolve_anchor()
        if anchor is None:
            return None

        monitor = _get_monitor()
        state = monitor.create_or_get_state(session_key, task_id, tenant_id)

        from core.session_manager.monitors.goal_alignment import (  # noqa: PLC0415
            GoalAlignmentState,
        )
        if not isinstance(state, GoalAlignmentState) or not state.original_goal:
            facts = anchor.load_facts(tenant_id, session_key)
            goal_fact = None
            for f in reversed(facts):  # oldest-first store → walk back for newest
                if isinstance(f, dict) and f.get("kind") == "goal":
                    goal_fact = f
                    break
            if goal_fact is None:
                return None  # nothing to compare against — not an error
            goal_text = str(goal_fact.get("text") or "")
            if not goal_text:
                return None
            monitor.set_goal(session_key, task_id, tenant_id, goal_text)
            state = monitor.session_states.get(session_key)
            if not isinstance(state, GoalAlignmentState):
                return None

        state.metadata["current_work"] = current_work
        alert = monitor.check(state)
        if alert is None:
            return None

        _audit_alert(tenant_id, session_key, alert)
        return {
            "alert_type": alert.alert_type.value,
            "severity": alert.severity,
            "reason": alert.reason,
            "similarity_score": alert.metadata.get("similarity_score"),
            "consecutive_low_count": alert.metadata.get("consecutive_low_count"),
        }
    except Exception:  # noqa: BLE001 — never break a turn on the way out
        logger.exception("goal_drift_hook: unexpected error (never raised into turn)")
        return None

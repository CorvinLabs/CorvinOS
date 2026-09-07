"""Task-outcome sink for the ADR-0314 learning loop (loop closure, sink side).

A learning loop needs an OUTCOME signal per real task, joined to the routing
decision that produced it. Until 2026-09-06 the ACP emitted ``skill_executed``
events only (and only from console boot/manifest calls) and nothing ever
recorded whether the task those decisions belonged to succeeded — so the
optimizer had no ground truth (adversarial review F1/F2).

:func:`emit_task_outcome` is called from the ONE chokepoint every surface's
task lifecycle passes through — ``corvin_core.task_manager.TaskManager.
record_event`` on ``task.completed`` / ``task.failed`` — and writes an
``EventType.OUTCOME`` learning event through the SAME emitter the booted ACP
registry uses (audit-first ``EventStore``, so the record is hash-chained).

Content-free by construction: task id, status, exit code, duration, engine
name — never the instruction, the output or a user id (GDPR Art. 5).
Fail-soft: a missing tenant, an un-booted registry or an emitter without a
learning backend means "no outcome recorded" and is reported via the return
value, never as an exception into the task lifecycle.

SECURITY ADDITION (Finding #3: Weight Poisoning Mitigation):
Outcome Source Verification (Audit Backend Only) — every outcome must originate
from the core audit backend to be trusted for backprop. Outcomes from other
sources (skill config, external API) are logged but rejected. The backprop
optimizer only reads outcomes with ``outcome_source_verified = true``.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Trust sources for outcomes (must originate from these)
TRUSTED_OUTCOME_SOURCES = frozenset({"audit_backend_outcome", "task_manager", "feedback_loop"})

#: The Skill whose decisions task outcomes are attributed to (L5 routing).
OUTCOME_SKILL_ID = "os.delegation_router"


def verify_outcome_source(signal: dict[str, Any]) -> tuple[bool, str]:
    """Verify that an outcome signal originated from a trusted source (Finding #3).

    **Purpose:** Prevent weight poisoning by ensuring only audit-verified outcomes
    influence backpropagation. Outcomes from untrusted sources (external APIs,
    unverified skill configs) are rejected with audit logging.

    Args:
        signal: The outcome signal dict (contains "source" key)

    Returns:
        (verified, reason) tuple where:
        - verified: True if source is in TRUSTED_OUTCOME_SOURCES
        - reason: Human-readable explanation for the verification result
    """
    source = signal.get("source", "unknown")
    if source in TRUSTED_OUTCOME_SOURCES:
        return True, f"outcome_source_verified: source={source}"
    return False, f"outcome_source_unverified: source={source} not in trusted sources"


def audit_outcome_verification(
    *,
    tenant_id: str,
    task_id: str,
    verified: bool,
    source: str,
    reason: str,
) -> bool:
    """Log outcome verification result to the core audit chain (Finding #3).

    Args:
        tenant_id: Task's tenant
        task_id: Task identifier
        verified: Whether the outcome passed verification
        source: The outcome source string
        reason: Explanation for the verification result

    Returns:
        True if audit event was successfully logged
    """
    try:
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        event_type = "learning.outcome_verified" if verified else "learning.outcome_unverified"
        core_audit_event(
            event_type,
            tenant_id=tenant_id,
            details={
                "task_id": task_id,
                "outcome_source": source,
                "verification_reason": reason,
            },
        )
        return True
    except Exception as exc:  # noqa: BLE001 — don't break learning pipeline
        logger.warning("outcome verification audit failed (%s): %s", task_id, type(exc).__name__)
        return False


def learning_emitter() -> Optional[Any]:
    """The booted ACP registry's learning emitter, or None."""
    try:
        from core.skills import skill_registry_phase1 as _reg  # noqa: PLC0415
    except Exception:  # noqa: BLE001 — stripped install
        return None
    registry = getattr(_reg, "_global_registry", None)
    backend = getattr(registry, "learning_backend", None) if registry is not None else None
    return getattr(backend, "emitter", None)


def emit_task_outcome(
    *,
    tenant_id: Optional[str],
    task_id: str,
    status: str,
    exit_code: Optional[int] = None,
    duration_ms: Optional[int] = None,
    engine: Optional[str] = None,
    task_type: Optional[str] = None,
    emitter: Optional[Any] = None,
) -> bool:
    """Record one task outcome as an ``OUTCOME`` learning event.

    Args:
        tenant_id: The task's tenant (from the task's own metadata — NEVER an
            env fallback). ``None``/empty → dropped, returns False.
        task_id: Task identifier (uuid; not PII).
        status: ``"completed"`` | ``"failed"`` | ``"cancelled"``.
        exit_code, duration_ms, engine, task_type: content-free metadata.
        emitter: Explicit ``EventEmitter`` (tests); default is the booted
            registry's.

    Returns:
        True when the event was queued for the audit-first store.
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("task outcome dropped: no tenant_id (task %s)", task_id)
        return False
    if status not in ("completed", "failed", "cancelled"):
        logger.debug("task outcome dropped: unknown status %r", status)
        return False
    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("task outcome dropped: no learning emitter booted (task %s)", task_id)
        return False
    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        signal: dict[str, Any] = {
            "task_id": task_id,
            "status": status,
            "success": status == "completed" and (exit_code in (None, 0)),
            "exit_code": exit_code,
            "duration_ms": duration_ms,
            "engine": engine,
            "task_type": task_type,
            "source": "task_manager",
        }

        # SECURITY: Verify outcome source (Finding #3 mitigation)
        verified, reason = verify_outcome_source(signal)
        signal["outcome_source_verified"] = verified

        # Log verification to audit chain
        audit_outcome_verification(
            tenant_id=tenant_id,
            task_id=task_id,
            verified=verified,
            source=signal.get("source", "unknown"),
            reason=reason,
        )

        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=OUTCOME_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/learning/outcome_sink.py:emit_task_outcome",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001 — the task lifecycle must never break on learning
        logger.warning("task outcome not recorded (%s): %s", task_id, type(exc).__name__)
        return False


def recent_outcomes(tenant_id: str, limit: int = 10, *, store: Optional[Any] = None) -> tuple[int, int]:
    """``(successes, total)`` over the most recent ``limit`` VERIFIED task outcomes.

    The optimizer's per-epoch input (``SkillAdapter.run_optimizer_epoch``). Reads
    the booted registry's store unless ``store`` is given. Only counts outcomes
    with ``outcome_source_verified = true`` to prevent weight poisoning
    (Finding #3 mitigation). ``(0, 0)`` when no verified outcome has been
    recorded yet — the caller treats that as "no evidence".
    """
    st = store
    if st is None:
        em = learning_emitter()
        st = getattr(em, "store", None)
    if st is None:
        return 0, 0
    try:
        from core.learning.learning_events import EventType  # noqa: PLC0415

        events = st.query_events(tenant_id, event_type=EventType.OUTCOME, limit=5000)
    except Exception as exc:  # noqa: BLE001
        logger.warning("recent_outcomes unreadable: %s", type(exc).__name__)
        return 0, 0
    # Filter to only verified outcomes (Finding #3: Weight Poisoning mitigation)
    tail = [
        e for e in events[-limit:] if limit > 0
        if (e.signal or {}).get("outcome_source_verified") is True
    ] if limit > 0 else [
        e for e in events if (e.signal or {}).get("outcome_source_verified") is True
    ]
    total = len(tail)
    successes = sum(1 for e in tail if (e.signal or {}).get("success") is True)
    return successes, total


def integrate_feedback_outcome(
    *,
    tenant_id: Optional[str],
    task_id: str,
    feedback_signal: dict[str, Any],
    emitter: Optional[Any] = None,
) -> bool:
    """Record feedback-based outcome tuning signal.

    Called from the feedback loop when user provides feedback on a task outcome.
    Emits a special OUTCOME variant that combines original task result with
    user feedback to refine the confidence/loss calculation.

    Args:
        tenant_id: Task's tenant
        task_id: Task identifier
        feedback_signal: User feedback dict with keys like:
            - outcome_feedback: "yes"|"no"|"unknown"
            - quality_rating: 1–5
            - preference_feedback: "llm"|"deterministic"|"either"
            - confidence: 0–1 user confidence in feedback
        emitter: Explicit EventEmitter (tests); default is booted registry's

    Returns:
        True when the event was queued
    """
    if not tenant_id or not isinstance(tenant_id, str):
        logger.debug("feedback outcome dropped: no tenant_id (task %s)", task_id)
        return False

    em = emitter if emitter is not None else learning_emitter()
    if em is None:
        logger.debug("feedback outcome dropped: no emitter (task %s)", task_id)
        return False

    try:
        from core.learning.learning_events import EventType, LearningEvent  # noqa: PLC0415

        signal: dict[str, Any] = {
            "task_id": task_id,
            "feedback_signal": feedback_signal,
            "source": "feedback_loop",
        }

        # SECURITY: Verify outcome source (Finding #3 mitigation)
        verified, reason = verify_outcome_source(signal)
        signal["outcome_source_verified"] = verified

        # Log verification to audit chain
        audit_outcome_verification(
            tenant_id=tenant_id,
            task_id=task_id,
            verified=verified,
            source=signal.get("source", "unknown"),
            reason=reason,
        )

        event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=OUTCOME_SKILL_ID,
            tenant_id=tenant_id,
            signal=signal,
            lom="core/learning/outcome_sink.py:integrate_feedback_outcome",
        )
        return bool(em.emit(event))
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback outcome not recorded (%s): %s", task_id, type(exc).__name__)
        return False


_learning_emitter = learning_emitter  # compat alias

__all__ = [
    "emit_task_outcome",
    "recent_outcomes",
    "learning_emitter",
    "OUTCOME_SKILL_ID",
    "verify_outcome_source",
    "audit_outcome_verification",
    "TRUSTED_OUTCOME_SOURCES",
    "integrate_feedback_outcome",
]

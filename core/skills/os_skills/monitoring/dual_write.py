"""Dual-Write Integration for L5 Routing — Phase 2a.

Implements the dual-write mechanism described in ADR-0532 Phase 2 synthesis:
1. Route using the real Skill decision
2. Compare with shadow decision (bundled rule)
3. Track correctness in rolling window
4. Trigger auto-fallback if correctness drops > 2%
5. Return either real decision or bundled fallback

This module bridges between delegation_policy.py (entry point) and the monitoring
infrastructure (correctness tracker + rollback detector).

Call site: operator/bridges/shared/delegation_policy.py::resolve_worker_engine()
Integration: Phase 2a exit from shadow mode
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING

from core.skills.os_skills.monitoring.correctness_tracker import (
    CorrectnessTracker,
    RoutingDecision,
    RoutingOutcome,
)
from core.skills.os_skills.monitoring.rollback_detector import RollbackDetector

if TYPE_CHECKING:
    pass

_log = logging.getLogger(__name__)

# Global tracker + detector (singleton per process)
_tracker: CorrectnessTracker | None = None
_detector: RollbackDetector | None = None


def initialize_dual_write(
    storage_dir: Path | None = None,
) -> None:
    """Initialize dual-write monitoring (call at boot).

    Args:
        storage_dir: Directory to persist metrics to (defaults to ~/.corvin/metrics/)
    """
    global _tracker, _detector

    if storage_dir is None:
        from core.paths import corvin_home

        storage_dir = Path(corvin_home()) / "metrics"

    tracker_path = storage_dir / "correctness.jsonl"
    rollback_path = storage_dir / "rollback.log"

    _tracker = CorrectnessTracker(storage_path=tracker_path)
    _detector = RollbackDetector(correctness_tracker=_tracker, storage_path=rollback_path)

    _log.info("Dual-write monitoring initialized: storage_dir=%s", storage_dir)


def get_tracker() -> CorrectnessTracker:
    """Get the global correctness tracker (initializes if needed)."""
    global _tracker
    if _tracker is None:
        initialize_dual_write()
    return _tracker


def get_detector() -> RollbackDetector:
    """Get the global rollback detector (initializes if needed)."""
    global _detector
    if _detector is None:
        initialize_dual_write()
    return _detector


def resolve_worker_engine_dual_write(
    *,
    request_id: str,
    bundled_engine: str,
    bundled_confidence: float = 1.0,
    skill_decision: dict | None = None,  # result from os.delegation_router Skill
    task_type: str = "chat",
    tenant_id: str = "_default",
) -> str:
    """Route using dual-write (real Skill decision + correctness tracking).

    This is the Phase 2a entry point called by delegation_policy.resolve_worker_engine()
    when Phase 2 is enabled (shadows mode exited).

    Args:
        request_id: Unique request identifier
        bundled_engine: Result from bundled routing rule (fallback)
        bundled_confidence: Confidence of bundled decision (always 1.0)
        skill_decision: Decision from os.delegation_router Skill
            Expected keys: 'engine', 'confidence', 'reasoning'
        task_type: Type of task ('chat', 'big_data', 'delegate')
        tenant_id: Tenant for audit trail

    Returns:
        The routing decision: either skill's engine or fallback to bundled
    """
    tracker = get_tracker()
    detector = get_detector()

    # If rollback was triggered, use bundled routing only
    if detector.update():
        _log.info("Rollback active: using bundled routing (request_id=%s)", request_id)
        return bundled_engine

    # Extract skill decision (or default to bundled)
    if skill_decision is None:
        skill_decision = {"engine": bundled_engine, "confidence": 0.0}

    skill_engine = skill_decision.get("engine", bundled_engine)
    skill_confidence = skill_decision.get("confidence", 0.0)

    # Record both decisions
    real_decision = RoutingDecision(
        request_id=request_id,
        timestamp=time.time(),
        engine=skill_engine,
        decision_source="skill",
        confidence=skill_confidence,
        task_type=task_type,
        tenant_id=tenant_id,
    )

    shadow_decision = RoutingDecision(
        request_id=request_id,
        timestamp=time.time(),
        engine=bundled_engine,
        decision_source="bundled",
        confidence=bundled_confidence,
        task_type=task_type,
        tenant_id=tenant_id,
    )

    # Log both to audit trail (ADR-0299)
    _emit_dual_routing_audit(real_decision, shadow_decision)

    # Use skill's decision for real routing (exit shadow mode)
    _log.debug(
        "Dual-write routing: skill=%s(%.2f) vs bundled=%s (request_id=%s)",
        skill_engine,
        skill_confidence,
        bundled_engine,
        request_id,
    )

    return skill_engine


def record_routing_outcome(
    *,
    request_id: str,
    real_engine: str,
    shadow_engine: str,
    success: bool,
    ground_truth: str,
    latency_ms: float,
    error_msg: str | None = None,
) -> None:
    """Record post-execution outcome for correctness tracking.

    This is called after the request finishes, when we know which engine was correct.

    Args:
        request_id: Unique request identifier (must match the original routing decision)
        real_engine: Engine used by the real (Skill-driven) routing
        shadow_engine: Engine used by the shadow (bundled) routing
        success: Whether the request succeeded
        ground_truth: Which engine WAS the correct choice ('native', 'acs', 'tde')
        latency_ms: Request latency in milliseconds
        error_msg: Error message if request failed
    """
    tracker = get_tracker()

    outcome = RoutingOutcome(
        request_id=request_id,
        real_decision=RoutingDecision(
            request_id=request_id,
            timestamp=time.time(),
            engine=real_engine,
            decision_source="skill",
            confidence=0.0,  # unknown at record time
            task_type="chat",  # unknown at record time
            tenant_id="_default",
        ),
        shadow_decision=RoutingDecision(
            request_id=request_id,
            timestamp=time.time(),
            engine=shadow_engine,
            decision_source="bundled",
            confidence=1.0,
            task_type="chat",
            tenant_id="_default",
        ),
        success=success,
        ground_truth=ground_truth,
        latency_ms=latency_ms,
        error_msg=error_msg,
    )

    tracker.record_outcome(outcome)
    _log.debug(
        "Outcome recorded: real=%s ground=%s success=%s (request_id=%s)",
        real_engine,
        ground_truth,
        success,
        request_id,
    )


def _emit_dual_routing_audit(
    real_decision: RoutingDecision, shadow_decision: RoutingDecision
) -> None:
    """Emit dual-write decision to audit trail (ADR-0299)."""
    try:
        from core.security.audit_logger import audit_event  # noqa: PLC0415

        audit_event(
            "l5_routing_dual_write",
            {
                "request_id": real_decision.request_id,
                "real_engine": real_decision.engine,
                "real_confidence": real_decision.confidence,
                "shadow_engine": shadow_decision.engine,
                "task_type": real_decision.task_type,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("Failed to emit audit event: %s", exc)


def get_monitoring_dashboard() -> dict:
    """Get current monitoring dashboard metrics."""
    tracker = get_tracker()
    detector = get_detector()
    metrics = tracker.current_metrics()

    return {
        "is_rolled_back": detector.is_rolled_back,
        "rollback_events": [
            {
                "timestamp": e.timestamp,
                "reason": e.reason,
                "metrics": e.metrics_at_trigger,
            }
            for e in detector.rollback_events
        ],
        "correctness_metrics": {
            "window_size": metrics.window_size,
            "correct_count": metrics.correct_count,
            "total_count": metrics.total_count,
            "correctness": metrics.correctness,
            "shadow_correctness": metrics.shadow_correctness,
            "skill_confidence_mean": metrics.skill_confidence_mean,
            "correctness_drop_percent": (
                (metrics.shadow_correctness - metrics.correctness) * 100
            ),
        },
    }

"""Dual-Write Integration for L5 Routing — Phase 2a.

Implements the dual-write mechanism described in ADR-0532 Phase 2 synthesis:
1. Fetch real Skill decision via os.delegation_router Skill
2. Route using real Skill decision (confidence threshold applied)
3. Compare with shadow decision (bundled rule) for agreement tracking
4. Track correctness in rolling window
5. Trigger auto-fallback if correctness drops > 2%
6. Return either real decision or bundled fallback based on rollback state

This module bridges between delegation_policy.py (entry point) and the monitoring
infrastructure (correctness tracker + rollback detector).

Call site: corvin_operator/bridges/shared/delegation_policy.py::resolve_worker_engine()
Integration: Phase 2a exit from shadow mode

Architecture (ADR-0532 Phase 2):
- Phase 1 (shadow): Skill called, advisory only, bundled stands
- Phase 2a (dual-write): Real Skill decision used; agreement tracked for auto-rollback
- Phase 2b (real): Skill decision is the primary route (requires rollback recovery built-in)

Confidence threshold decision tree:
1. Load learned config for os.delegation_router (if available)
2. Fetch Skill decision (complexity, task_type, force_delegate, is_big_data)
3. If skill_confidence >= threshold (default 0.75, learned override available):
   - Use Skill decision (real routing)
4. Else:
   - Fall back to bundled decision (fail-closed)
5. Record both decisions for agreement tracking + learning feedback
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

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
    complexity: int = 5,
    force_delegate: bool = False,
    is_big_data: bool = False,
    tenant_id: str = "_default",
) -> str:
    """Route using dual-write (real Skill decision + correctness tracking).

    This is the Phase 2a entry point called by delegation_policy.resolve_worker_engine()
    when Phase 2 is enabled (shadow mode exited).

    Phase 2a (dual-write) behavior:
    1. Fetch Skill decision if not provided
    2. Apply confidence threshold (learned or default)
    3. Compare bundled vs. Skill decision
    4. Return real decision if confidence >= threshold; else bundled (fail-closed)
    5. Record both decisions for agreement tracking + learning feedback
    6. Check auto-rollback trigger

    Args:
        request_id: Unique request identifier
        bundled_engine: Result from bundled routing rule (fallback)
        bundled_confidence: Confidence of bundled decision (always 1.0)
        skill_decision: Pre-computed decision from os.delegation_router Skill (optional).
            If None, will be fetched via Skill registry.
            Expected keys: 'decision', 'confidence', 'reasoning'
        task_type: Type of task ('chat', 'big_data', 'delegate')
        complexity: Task complexity (1-10) for Skill input
        force_delegate: Whether user explicitly requested delegation
        is_big_data: Whether task is big-data shaped
        tenant_id: Tenant for audit trail + learned config lookup

    Returns:
        The routing decision: either skill's engine or fallback to bundled (fail-closed)
    """
    tracker = get_tracker()
    detector = get_detector()

    # Check rollback state FIRST: if triggered, always use bundled routing
    if detector.update():
        _log.info("Rollback active: using bundled routing (request_id=%s)", request_id)
        _emit_rollback_decision_audit(request_id, bundled_engine, task_type, tenant_id)
        return bundled_engine

    # Step 1: Fetch Skill decision if not provided
    if skill_decision is None:
        skill_decision = _fetch_skill_decision(
            complexity=complexity,
            task_type=task_type,
            force_delegate=force_delegate,
            is_big_data=is_big_data,
            tenant_id=tenant_id,
        )

    # Step 2: Extract Skill output
    skill_engine = skill_decision.get("decision", bundled_engine)  # key is "decision" not "engine"
    skill_confidence = skill_decision.get("confidence", 0.0)
    skill_reasoning = skill_decision.get("reasoning", "")

    # Step 3: Load learned config to get confidence threshold override (ADR-0314)
    confidence_threshold = _load_confidence_threshold(tenant_id)

    # Step 4: Decide which engine to use based on confidence threshold
    # Confidence >= threshold → use Skill (real routing)
    # Confidence < threshold → use bundled (fail-closed)
    if skill_confidence >= confidence_threshold:
        routing_engine = skill_engine
        decision_source = "skill"
        decision_reason = f"Skill confidence {skill_confidence:.2f} >= threshold {confidence_threshold:.2f}"
    else:
        routing_engine = bundled_engine
        decision_source = "bundled"
        decision_reason = f"Skill confidence {skill_confidence:.2f} < threshold {confidence_threshold:.2f}, using fallback"

    # Step 5: Record both decisions for dual-write audit trail
    real_decision = RoutingDecision(
        request_id=request_id,
        timestamp=time.time(),
        engine=routing_engine,
        decision_source=decision_source,
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

    # Log both to audit trail (ADR-0722 decision attribution)
    _emit_dual_routing_audit(real_decision, shadow_decision, decision_reason)

    # Step 6: Emit decision metric (agreement rate + confidence distribution)
    _emit_decision_metrics(
        request_id=request_id,
        skill_engine=skill_engine,
        bundled_engine=bundled_engine,
        used_engine=routing_engine,
        skill_confidence=skill_confidence,
        threshold=confidence_threshold,
        task_type=task_type,
        tenant_id=tenant_id,
    )

    _log.debug(
        "Dual-write routing: skill=%s(%.2f, %s) vs bundled=%s → using %s (request_id=%s)",
        skill_engine,
        skill_confidence,
        skill_reasoning[:30] if skill_reasoning else "",
        bundled_engine,
        routing_engine,
        request_id,
    )

    return routing_engine


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


def _fetch_skill_decision(
    *,
    complexity: int,
    task_type: str,
    force_delegate: bool,
    is_big_data: bool,
    tenant_id: str,
) -> dict[str, Any]:
    """Fetch real decision from os.delegation_router Skill.

    This is the entry point to the Skill system for Phase 2a dual-write.
    If the Skill is unavailable or times out, returns a conservative fallback.

    Args:
        complexity: Task complexity (1-10)
        task_type: Task type ('chat', 'big_data', 'delegate')
        force_delegate: User explicitly requested delegation
        is_big_data: Task is big-data shaped
        tenant_id: Tenant ID for learned config lookup

    Returns:
        Dict with keys: 'decision' (engine name), 'confidence' (0-1), 'reasoning'
        On error, returns fallback (native, 0.0 confidence)
    """
    try:
        from core.skills import skill_registry_phase1 as _reg  # noqa: PLC0415

        registry = getattr(_reg, "_global_registry", None)
        if registry is None:
            _log.debug("Skill registry not booted, using fallback")
            return {
                "decision": "native",
                "confidence": 0.0,
                "reasoning": "Skill registry unavailable",
            }

        # Execute the Skill (timeout 500ms for Phase 2a dual-write)
        result = registry.execute(
            "os.delegation_router",
            {
                "complexity": complexity,
                "task_type": task_type,
                "force_delegate": force_delegate,
                "is_big_data": is_big_data,
                "tenant_id": tenant_id,
                "shadow": False,  # Not shadow mode in Phase 2a
            },
            timeout_ms=500,  # Fail-fast: 500ms budget for Skill execution
            lom="core/skills/os_skills/monitoring/dual_write.py:_fetch_skill_decision",
            tenant_id=tenant_id,
        )

        # Validate output has required fields
        if result and "decision" in result and "confidence" in result:
            return result

        _log.warning("Skill result missing required fields: %s", result)
        return {
            "decision": "native",
            "confidence": 0.0,
            "reasoning": "Skill output validation failed",
        }

    except TimeoutError:
        _log.warning("Skill execution timed out (500ms budget)")
        return {
            "decision": "native",
            "confidence": 0.0,
            "reasoning": "Skill execution timeout",
        }
    except Exception as exc:  # noqa: BLE001
        _log.exception("Failed to fetch Skill decision: %s", type(exc).__name__)
        return {
            "decision": "native",
            "confidence": 0.0,
            "reasoning": f"Skill execution error: {type(exc).__name__}",
        }


def _load_confidence_threshold(tenant_id: str) -> float:
    """Load confidence threshold for Skill decision from learned config.

    If learned config is available (ADR-0314 feedback loop), returns the
    learned threshold. Otherwise returns the default (0.75).

    The confidence threshold determines when to trust the Skill decision:
    - skill_confidence >= threshold → use Skill (real routing)
    - skill_confidence < threshold → use bundled (fail-closed)

    Default value (0.75) means: if the Skill is at least 75% confident,
    use it; otherwise fall back to the bundled rule (which has 100% confidence
    because it's deterministic).

    Args:
        tenant_id: Tenant ID for config lookup

    Returns:
        Confidence threshold (0.0-1.0), default 0.75
    """
    # Default threshold: 0.75 (Skill needs 75% confidence to be trusted)
    DEFAULT_THRESHOLD = 0.75

    try:
        # Try to load learned config (ADR-0314 integration)
        from core.skills.os_skills.skill_adapter import (  # noqa: PLC0415
            load_skill_config,
        )

        learned_config, _ = load_skill_config("os.delegation_router", tenant_id)
        if learned_config and hasattr(learned_config, "confidence_threshold"):
            threshold = learned_config.confidence_threshold
            if 0.0 <= threshold <= 1.0:
                _log.debug(
                    "Loaded learned confidence threshold: %.2f (tenant=%s)",
                    threshold,
                    tenant_id,
                )
                return threshold

    except Exception as exc:  # noqa: BLE001
        _log.debug("Failed to load learned config: %s", type(exc).__name__)

    return DEFAULT_THRESHOLD


def _emit_dual_routing_audit(
    real_decision: RoutingDecision,
    shadow_decision: RoutingDecision,
    decision_reason: str = "",
) -> None:
    """Emit dual-write decision to audit trail (ADR-0722 decision attribution).

    Logs both the real (Skill-driven) and shadow (bundled) decisions for
    agreement tracking and learning feedback integration.
    """
    try:
        from core.security.audit_logger import audit_event  # noqa: PLC0415

        audit_event(
            "l5_routing_dual_write",
            {
                "request_id": real_decision.request_id,
                "decision_source": real_decision.decision_source,
                "used_engine": real_decision.engine,
                "skill_engine": real_decision.engine
                if real_decision.decision_source == "skill"
                else shadow_decision.engine,
                "bundled_engine": shadow_decision.engine,
                "skill_confidence": real_decision.confidence,
                "decision_reason": decision_reason,
                "task_type": real_decision.task_type,
                "agreement": real_decision.engine == shadow_decision.engine,
                "tenant_id": real_decision.tenant_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("Failed to emit audit event: %s", exc)


def _emit_rollback_decision_audit(
    request_id: str, bundled_engine: str, task_type: str, tenant_id: str
) -> None:
    """Emit audit event when routing uses bundled rule due to active rollback."""
    try:
        from core.security.audit_logger import audit_event  # noqa: PLC0415

        audit_event(
            "l5_routing_rollback_active",
            {
                "request_id": request_id,
                "used_engine": bundled_engine,
                "task_type": task_type,
                "reason": "Rollback active, using bundled routing",
                "tenant_id": tenant_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("Failed to emit rollback audit event: %s", exc)


def _emit_decision_metrics(
    *,
    request_id: str,
    skill_engine: str,
    bundled_engine: str,
    used_engine: str,
    skill_confidence: float,
    threshold: float,
    task_type: str,
    tenant_id: str,
) -> None:
    """Emit decision metrics for observability (agreement rate, confidence distribution).

    These metrics feed into the console dashboard for Phase 2a monitoring.
    """
    try:
        agreement = 1.0 if skill_engine == bundled_engine else 0.0
        threshold_met = 1.0 if skill_confidence >= threshold else 0.0

        from core.security.audit_logger import audit_event  # noqa: PLC0415

        audit_event(
            "l5_routing_metrics",
            {
                "request_id": request_id,
                "agreement": agreement,  # 1.0 if skill == bundled, else 0.0
                "threshold_met": threshold_met,  # 1.0 if confidence >= threshold
                "skill_confidence": skill_confidence,
                "threshold": threshold,
                "task_type": task_type,
                "used_engine": used_engine,
                "tenant_id": tenant_id,
            },
        )
    except Exception as exc:  # noqa: BLE001
        _log.debug("Failed to emit metrics: %s", exc)


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

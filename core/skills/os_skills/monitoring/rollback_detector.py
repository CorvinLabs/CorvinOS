"""L5 Rollback Detector — Phase 2a (Auto-Fallback on Correctness Degradation).

The rollback detector monitors correctness metrics and triggers automatic fallback
to bundled routing when the Skill-driven routing is degrading. This is the fail-closed
mechanism mentioned in ADR-0532 Phase 2 synthesis.

Rollback is AUTOMATIC (no manual gate), non-reversible within a deployment cycle (requires
restart + restart decision to re-enable), and always logged with full audit trail.

Integration:
- Called by delegation_policy.resolve_worker_engine() after dual-write
- If triggered, sets a flag that causes future routing to use bundled rule only
- Console dashboard shows rollback status + reason

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). Its
tracker has no production feed (``dual_write.record_routing_outcome`` has zero
callers), so it can never fire; Phase 2 routing is therefore refused in
``delegation_policy`` (``_PHASE2_ROLLBACK_GUARD_WIRED``).
"""
from __future__ import annotations

import dataclasses
import logging
import time
from enum import Enum
from pathlib import Path

from core.skills.os_skills.monitoring.correctness_tracker import CorrectnessTracker

_log = logging.getLogger(__name__)


class RollbackState(Enum):
    """State of the rollback detector."""

    RUNNING = "running"
    ROLLBACK_TRIGGERED = "rollback_triggered"
    MANUAL_OVERRIDE = "manual_override"


@dataclasses.dataclass(frozen=True)
class RollbackEvent:
    """A rollback trigger event."""

    timestamp: float
    reason: str  # e.g. "correctness dropped from 0.95 to 0.92 (2.5% > threshold 2.0%)"
    metrics_at_trigger: dict  # snapshot of correctness metrics
    audit_event_id: str | None = None  # hash-chain audit event ID


class RollbackDetector:
    """Monitors correctness and triggers automatic fallback."""

    def __init__(
        self,
        correctness_tracker: CorrectnessTracker,
        storage_path: Path | None = None,
        tenant_id: str = "_default",
    ):
        """Initialize rollback detector.

        Args:
            correctness_tracker: Tracker instance to monitor
            storage_path: Optional path to persist rollback events
        """
        self.tracker = correctness_tracker
        self.storage_path = storage_path
        self.tenant_id = tenant_id
        self._state = RollbackState.RUNNING
        self._rollback_events: list[RollbackEvent] = []
        self._last_check_at: float = time.time()

    def update(self) -> bool:
        """Check current correctness and trigger rollback if needed.

        Returns True if rollback was triggered (caller should switch to bundled routing).
        """
        # If already rolled back, stay rolled back (no re-enable without restart)
        if self._state == RollbackState.ROLLBACK_TRIGGERED:
            return True

        # Delegate to tracker's own trigger check
        if self.tracker.should_rollback():
            self._trigger_rollback()
            return True

        return False

    def _trigger_rollback(self) -> None:
        """Trigger rollback to bundled routing."""
        metrics = self.tracker.current_metrics()
        recent = self.tracker.recent_correctness()
        if recent is None:
            recent = metrics.correctness
        drop = metrics.shadow_correctness - recent

        reason = (
            f"correctness dropped from {metrics.shadow_correctness:.2%} to "
            f"{recent:.2%} ({drop*100:.1f}% > threshold 2.0%)"
        )

        event = RollbackEvent(
            timestamp=time.time(),
            reason=reason,
            metrics_at_trigger={
                "window_size": metrics.window_size,
                "correct_count": metrics.correct_count,
                "total_count": metrics.total_count,
                "correctness": recent,
                "shadow_correctness": metrics.shadow_correctness,
                "skill_confidence_mean": metrics.skill_confidence_mean,
            },
        )

        self._state = RollbackState.ROLLBACK_TRIGGERED
        self._rollback_events.append(event)

        _log.critical(
            "ROLLBACK: L5 routing falling back to bundled rule. Reason: %s", reason
        )

        # Emit audit event (audit integration per ADR-0299)
        self._emit_audit_event(event)

        # Persist to disk
        if self.storage_path:
            self._persist_event(event)

    def _emit_audit_event(self, event: RollbackEvent) -> None:
        """Chain the rollback on the tenant audit log (ADR-0299).

        Used to import ``core.security.audit_logger`` — a module that does not
        exist — and swallow the ImportError, so a rollback was never audited.
        Same writer as the dual-write routing events (``dual_write._audit`` →
        ``audit.audit_event`` → ``tenant_audit_chain()``); only content-free
        numbers are recorded, the free-text ``reason`` stays in the log.
        """
        from core.skills.os_skills.monitoring.dual_write import _audit  # noqa: PLC0415

        m = event.metrics_at_trigger
        _audit(
            "l5_rollback_triggered",
            {
                "reason_code": "correctness_drop",
                "correctness": m.get("correctness"),
                "baseline_correctness": m.get("shadow_correctness"),
                "total_count": m.get("total_count"),
                "tenant_id": self.tenant_id,
            },
            self.tenant_id,
        )

    def _persist_event(self, event: RollbackEvent) -> None:
        """Persist rollback event to disk."""
        try:
            self.storage_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.storage_path, "a") as f:
                f.write(f"{event.timestamp} | {event.reason}\n")
        except Exception as exc:  # noqa: BLE001
            _log.warning("Failed to persist rollback event: %s", exc)

    @property
    def state(self) -> RollbackState:
        """Current rollback state."""
        return self._state

    @property
    def is_rolled_back(self) -> bool:
        """Whether rollback was triggered."""
        return self._state == RollbackState.ROLLBACK_TRIGGERED

    @property
    def rollback_events(self) -> list[RollbackEvent]:
        """List of all rollback events."""
        return list(self._rollback_events)

    def manual_reset(self) -> None:
        """Manually reset rollback state (for testing only).

        NOTE: In production, rollback is non-reversible within a deployment cycle.
        Resetting requires a full restart + manual decision via console/config.
        """
        _log.warning("Manual rollback reset requested (should only be in tests)")
        self._state = RollbackState.RUNNING
        self._rollback_events.clear()

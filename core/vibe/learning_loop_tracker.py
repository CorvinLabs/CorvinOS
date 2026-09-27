"""
Learning Loop Tracker — Track convergence and feedback loop velocity.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review) — the only
importer is core/console/routes/learning_dashboard.py, which no app mounts.

Monitors:
  1. Confidence convergence (target: >0.90 within 4 weeks)
  2. Learning loop velocity (feedback → optimization cycle time)
  3. Convergence failure alerts (>2 weeks no improvement)

Audit-first design: every tracking event is logged to the core audit chain.

References: ADR-0314 (learning loop), ADR-0722 (loss signals), ADR-0232 (audit).
"""

from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any, Protocol, Tuple
from datetime import datetime, timedelta
from enum import Enum
import math
import logging

logger = logging.getLogger(__name__)


class ConvergenceStatus(Enum):
    """Convergence state."""
    CONVERGED = "converged"
    CONVERGING = "converging"
    STALLED = "stalled"
    DIVERGING = "diverging"
    UNKNOWN = "unknown"


@dataclass
class ConvergencePoint:
    """One measurement in convergence tracking."""
    timestamp: str
    confidence: float
    sample_count: int
    slope_7d: float  # 7-day slope


@dataclass
class LearningLoopMetrics:
    """Metrics for one feedback → optimization cycle."""
    cycle_id: str
    feedback_received_at: str
    optimization_triggered_at: str
    config_applied_at: str
    cycle_time_minutes: float
    feedback_count: int
    confidence_delta: float  # Change in confidence after optimization

    # Audit chain
    hash: Optional[str] = None
    prev_hash: Optional[str] = None


@dataclass
class ConvergenceForecast:
    """Forecast for reaching target confidence."""
    current_confidence: float
    target_confidence: float = 0.90
    current_slope_7d: float = 0.0
    estimated_days_to_target: float = 0.0
    confidence_interval: Tuple[float, float] = (0.0, 0.0)  # (lower, upper)
    will_converge: bool = False


class AuditBackend(Protocol):
    """Audit chain interface."""
    def write_event(self, event: Dict[str, Any]) -> Optional[str]: ...
    def last_hash(self) -> str: ...


class LearningLoopTracker:
    """Track confidence convergence and feedback loop velocity."""

    def __init__(self, audit_backend: Optional[AuditBackend] = None):
        """Initialize tracker.

        Args:
            audit_backend: Audit chain backend. If None, uses NoOpAudit.
        """
        self.audit_backend = audit_backend or _default_audit()
        self.convergence_history: Dict[str, List[ConvergencePoint]] = {}
        self.loop_metrics: Dict[str, LearningLoopMetrics] = {}
        self.convergence_statuses: Dict[str, ConvergenceStatus] = {}

    def track_confidence_point(
        self,
        tenant_id: str,
        confidence: float,
        sample_count: int,
        slope_7d: float = 0.0,
    ) -> ConvergencePoint:
        """Record a confidence measurement point.

        Args:
            tenant_id: Tenant ID
            confidence: Current confidence value (0-1)
            sample_count: Number of samples used to compute confidence
            slope_7d: 7-day linear regression slope

        Returns:
            ConvergencePoint (recorded to audit chain).
        """
        if tenant_id not in self.convergence_history:
            self.convergence_history[tenant_id] = []

        point = ConvergencePoint(
            timestamp=datetime.utcnow().isoformat() + "Z",
            confidence=confidence,
            sample_count=sample_count,
            slope_7d=slope_7d,
        )

        # Emit audit event
        self._audit_convergence_point(tenant_id, point)

        self.convergence_history[tenant_id].append(point)

        # Update convergence status
        self._update_convergence_status(tenant_id)

        return point

    def get_convergence_status(self, tenant_id: str) -> ConvergenceStatus:
        """Get current convergence status for a tenant.

        Returns:
            ConvergenceStatus: CONVERGED | CONVERGING | STALLED | DIVERGING | UNKNOWN
        """
        return self.convergence_statuses.get(tenant_id, ConvergenceStatus.UNKNOWN)

    def forecast_convergence(
        self,
        tenant_id: str,
        target_confidence: float = 0.90,
        forecast_days: int = 30,
    ) -> Optional[ConvergenceForecast]:
        """Forecast when target confidence will be reached.

        Uses linear extrapolation from recent 7-day slope.

        Args:
            tenant_id: Tenant ID
            target_confidence: Target confidence level (default 0.90)
            forecast_days: Forecast window (days, default 30)

        Returns:
            ConvergenceForecast with estimated days to target, None if no history.
        """
        history = self.convergence_history.get(tenant_id)
        if not history or len(history) < 7:
            return None

        # Get most recent point
        latest = history[-1]
        current_confidence = latest.confidence

        if current_confidence >= target_confidence:
            return ConvergenceForecast(
                current_confidence=current_confidence,
                target_confidence=target_confidence,
                current_slope_7d=latest.slope_7d,
                estimated_days_to_target=0.0,
                confidence_interval=(current_confidence, current_confidence),
                will_converge=True,
            )

        # Compute 95% confidence interval using recent variance
        recent_points = history[-7:]
        confidences = [p.confidence for p in recent_points]
        mean_confidence = sum(confidences) / len(confidences)
        variance = sum((c - mean_confidence) ** 2 for c in confidences) / len(confidences)
        std_dev = math.sqrt(variance)

        # 95% CI using t-distribution approximation
        ci_lower = mean_confidence - 1.96 * std_dev
        ci_upper = mean_confidence + 1.96 * std_dev

        # Forecast days to target using linear extrapolation
        slope = latest.slope_7d
        if slope > 0:  # Only forecast if improving
            days_to_target = (target_confidence - current_confidence) / (slope + 1e-6)
            days_to_target = min(days_to_target, forecast_days)  # Cap at forecast window
            will_converge = days_to_target <= forecast_days and current_confidence + (slope * forecast_days) >= target_confidence
        else:
            days_to_target = float('inf')
            will_converge = False

        return ConvergenceForecast(
            current_confidence=current_confidence,
            target_confidence=target_confidence,
            current_slope_7d=slope,
            estimated_days_to_target=days_to_target,
            confidence_interval=(max(0.0, ci_lower), min(1.0, ci_upper)),
            will_converge=will_converge,
        )

    def record_feedback_cycle(
        self,
        tenant_id: str,
        cycle_id: str,
        feedback_received_at: str,
        optimization_triggered_at: str,
        config_applied_at: str,
        feedback_count: int,
        confidence_delta: float,
    ) -> LearningLoopMetrics:
        """Record a complete feedback → optimization cycle.

        Args:
            tenant_id: Tenant ID
            cycle_id: Unique cycle identifier
            feedback_received_at: When first feedback arrived (ISO 8601)
            optimization_triggered_at: When optimization was triggered (ISO 8601)
            config_applied_at: When config update was applied (ISO 8601)
            feedback_count: Number of feedback samples in cycle
            confidence_delta: Change in confidence after optimization

        Returns:
            LearningLoopMetrics (recorded to audit chain).
        """
        # Compute cycle time in minutes
        received_dt = datetime.fromisoformat(feedback_received_at.replace("Z", "+00:00"))
        applied_dt = datetime.fromisoformat(config_applied_at.replace("Z", "+00:00"))
        cycle_time_minutes = (applied_dt - received_dt).total_seconds() / 60

        metrics = LearningLoopMetrics(
            cycle_id=cycle_id,
            feedback_received_at=feedback_received_at,
            optimization_triggered_at=optimization_triggered_at,
            config_applied_at=config_applied_at,
            cycle_time_minutes=cycle_time_minutes,
            feedback_count=feedback_count,
            confidence_delta=confidence_delta,
        )

        # Emit audit event
        self._audit_loop_metrics(tenant_id, metrics)

        self.loop_metrics[cycle_id] = metrics

        logger.info(
            f"LEARNING LOOP: cycle={cycle_id} tenant={tenant_id} "
            f"time={cycle_time_minutes:.1f}min feedback={feedback_count} "
            f"confidence_delta={confidence_delta:+.3f}"
        )

        return metrics

    def get_loop_velocity(self, tenant_id: str, window_hours: int = 24) -> Dict[str, Any]:
        """Compute learning loop velocity metrics.

        Args:
            tenant_id: Tenant ID
            window_hours: Time window for metrics (default 24 hours)

        Returns:
            Dict with velocity statistics: mean cycle time, feedback rate, etc.
        """
        from datetime import timezone
        cutoff = datetime.now(timezone.utc) - timedelta(hours=window_hours)

        recent_metrics = [
            m for m in self.loop_metrics.values()
            if datetime.fromisoformat(m.config_applied_at.replace("Z", "+00:00")) > cutoff
        ]

        if not recent_metrics:
            return {
                "window_hours": window_hours,
                "cycle_count": 0,
                "mean_cycle_time_minutes": 0.0,
                "median_cycle_time_minutes": 0.0,
                "mean_feedback_per_cycle": 0,
                "mean_confidence_delta": 0.0,
                "velocity_improving": False,
            }

        cycle_times = sorted([m.cycle_time_minutes for m in recent_metrics])
        feedback_counts = [m.feedback_count for m in recent_metrics]
        confidence_deltas = [m.confidence_delta for m in recent_metrics]

        mean_cycle_time = sum(cycle_times) / len(cycle_times)
        median_cycle_time = cycle_times[len(cycle_times) // 2]
        mean_feedback = sum(feedback_counts) / len(feedback_counts)
        mean_confidence_delta = sum(confidence_deltas) / len(confidence_deltas)

        # Velocity improving if recent cycles are faster than older ones
        if len(cycle_times) >= 4:
            first_half_mean = sum(cycle_times[:len(cycle_times)//2]) / (len(cycle_times)//2)
            second_half_mean = sum(cycle_times[len(cycle_times)//2:]) / ((len(cycle_times)+1)//2)
            velocity_improving = second_half_mean < first_half_mean
        else:
            velocity_improving = False

        return {
            "window_hours": window_hours,
            "cycle_count": len(recent_metrics),
            "mean_cycle_time_minutes": mean_cycle_time,
            "median_cycle_time_minutes": median_cycle_time,
            "mean_feedback_per_cycle": mean_feedback,
            "mean_confidence_delta": mean_confidence_delta,
            "velocity_improving": velocity_improving,
        }

    def check_convergence_failure(
        self,
        tenant_id: str,
        stall_threshold_days: int = 14,
    ) -> Tuple[bool, Optional[str]]:
        """Check if convergence has stalled for too long.

        Args:
            tenant_id: Tenant ID
            stall_threshold_days: Days without improvement to flag failure (default 14)

        Returns:
            Tuple of (is_failed: bool, reason: optional str)
        """
        history = self.convergence_history.get(tenant_id)
        if not history or len(history) < 7:
            return (False, None)

        # Check if slope is essentially zero for too long
        recent_points = history[-stall_threshold_days:]  # Last N points
        if len(recent_points) < 7:
            return (False, None)

        recent_confidences = [p.confidence for p in recent_points]
        mean = sum(recent_confidences) / len(recent_confidences)
        variance = sum((c - mean) ** 2 for c in recent_confidences) / len(recent_confidences)

        # Stalled if variance is very small and confidence is still below target
        target = 0.90
        if variance < 0.001 and mean < target:
            return (
                True,
                f"Convergence stalled: confidence={mean:.3f} (target {target}), "
                f"variance={variance:.5f} for {stall_threshold_days} days"
            )

        return (False, None)

    def _update_convergence_status(self, tenant_id: str) -> None:
        """Update convergence status based on recent history.

        Status logic:
          CONVERGED: confidence >= 0.90 for 3+ consecutive points
          CONVERGING: confidence rising (7-day slope > 0) AND < 0.90
          STALLED: no improvement for 14+ days
          DIVERGING: 7-day slope < -0.05
          UNKNOWN: insufficient data
        """
        history = self.convergence_history.get(tenant_id)
        if not history or len(history) < 2:
            self.convergence_statuses[tenant_id] = ConvergenceStatus.UNKNOWN
            return

        latest = history[-1]

        # Check for CONVERGED (last 3 points >= 0.90)
        if len(history) >= 3:
            last_three = [p.confidence for p in history[-3:]]
            if all(c >= 0.90 for c in last_three):
                self.convergence_statuses[tenant_id] = ConvergenceStatus.CONVERGED
                return

        # Check for DIVERGING (slope < -0.05)
        if latest.slope_7d < -0.05:
            self.convergence_statuses[tenant_id] = ConvergenceStatus.DIVERGING
            return

        # Check for STALLED (no improvement for 14 days)
        is_failed, _ = self.check_convergence_failure(tenant_id, stall_threshold_days=14)
        if is_failed:
            self.convergence_statuses[tenant_id] = ConvergenceStatus.STALLED
            return

        # Otherwise CONVERGING
        if latest.slope_7d > 0:
            self.convergence_statuses[tenant_id] = ConvergenceStatus.CONVERGING
        else:
            self.convergence_statuses[tenant_id] = ConvergenceStatus.UNKNOWN

    def _audit_convergence_point(
        self,
        tenant_id: str,
        point: ConvergencePoint,
    ) -> None:
        """Emit audit event for convergence point (fail-closed)."""
        prev_hash = self.audit_backend.last_hash()
        audit_event = {
            "event_type": "convergence_point_recorded",
            "timestamp": point.timestamp,
            "tenant_id": tenant_id,
            "confidence": point.confidence,
            "sample_count": point.sample_count,
            "slope_7d": point.slope_7d,
            "prev_hash": prev_hash,
        }

        try:
            new_hash = self.audit_backend.write_event(audit_event)
            if new_hash:
                point.hash = new_hash
                point.prev_hash = prev_hash
        except Exception as e:
            logger.error(f"Audit write failed for convergence point: {e}")
            # Don't raise; convergence tracking is secondary to core operations

    def _audit_loop_metrics(
        self,
        tenant_id: str,
        metrics: LearningLoopMetrics,
    ) -> None:
        """Emit audit event for learning loop cycle (fail-closed)."""
        prev_hash = self.audit_backend.last_hash()
        audit_event = {
            "event_type": "learning_loop_cycle_completed",
            "timestamp": metrics.config_applied_at,
            "tenant_id": tenant_id,
            "cycle_id": metrics.cycle_id,
            "cycle_time_minutes": metrics.cycle_time_minutes,
            "feedback_count": metrics.feedback_count,
            "confidence_delta": metrics.confidence_delta,
            "prev_hash": prev_hash,
        }

        try:
            new_hash = self.audit_backend.write_event(audit_event)
            if new_hash:
                metrics.hash = new_hash
                metrics.prev_hash = prev_hash
        except Exception as e:
            logger.error(f"Audit write failed for loop metrics: {e}")
            # Don't raise; loop metrics are secondary to core operations


def _default_audit():
    """THE tenant chain (forge writer). The former ``_NoOpAudit`` default
    returned a fabricated hash and wrote nothing."""
    from core.vibe._chain_audit import ForgeChainAudit

    return ForgeChainAudit()


# Singleton instance
_tracker_instance: Optional[LearningLoopTracker] = None


def get_tracker(audit_backend: Optional[AuditBackend] = None) -> LearningLoopTracker:
    """Get or create learning loop tracker singleton.

    Args:
        audit_backend: Audit backend (used only on first call)

    Returns:
        LearningLoopTracker instance.
    """
    global _tracker_instance
    if _tracker_instance is None:
        _tracker_instance = LearningLoopTracker(audit_backend)
    return _tracker_instance


def reset_tracker() -> None:
    """Reset singleton (testing only)."""
    global _tracker_instance
    _tracker_instance = None

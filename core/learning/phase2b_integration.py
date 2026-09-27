"""Phase 2b Learning Loop Integration — Audit-first event logging (ADR-0314, ADR-0722).

Bridges Phase2bOptimizer and ConvergenceDetector with EventStore for audit logging.
All learning events (confidence recorded, convergence detected, optimization triggered,
threshold adjusted, divergence detected) are logged to EventStore with tenant isolation.

Fail-closed (fixed 2026-09-27): every ``log_*`` call commits one content-free
record to the tenant's core audit chain (``event_persistence.core_audit_event``,
event ``learning.phase2b.<type>``) and RAISES ``RuntimeError`` if it does not
commit. Until 2026-09-27 each call invoked ``event_store.write_event`` with a
signature no EventStore has, the TypeError was caught, and the method returned
``None`` — not one of these "audit-first" records was ever written.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

Integration Points:
- phase2b_optimizer.record_confidence_score → emit confidence_trend_recorded event
- convergence_detector.update_phase → emit phase_transition_recorded event
- phase2b_optimizer.trigger_optimization → emit optimization_triggered + threshold_adjusted events
- convergence_detector.detect_divergence → emit divergence_detected event
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional
from dataclasses import asdict

logger = logging.getLogger(__name__)


class Phase2bAuditIntegration:
    """Audit-first wrapper for Phase 2b learning loop (ADR-0314 EventStore integration).

    All operations emit immutable, tenant-scoped audit events.
    """

    def __init__(self, event_store=None):
        """Initialize integration.

        Args:
            event_store: unused; kept for call-compatibility. The record goes to
                the core audit chain, not to a learning EventStore.
        """
        self.event_store = event_store

    @staticmethod
    def _write(event_type: str, event_data: dict, tenant_id: str) -> str:
        """Commit one content-free record; return its audit_ref (fail-closed)."""
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        details = {k: v for k, v in event_data.items() if k not in ("event_type", "timestamp")}
        details["tenant_id"] = tenant_id
        return core_audit_event(f"learning.phase2b.{event_type}", tenant_id=tenant_id,
                                details=details)

    def log_confidence_trend(
        self,
        skill_id: str,
        trend,  # ConfidenceTrend
        tenant_id: str,
    ) -> Optional[str]:
        """Log confidence trend observation.

        Args:
            skill_id: Skill being tracked
            trend: ConfidenceTrend dataclass
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "confidence_trend_recorded",
                "skill_id": skill_id,
                "current_confidence": trend.current_confidence,
                "rolling_avg_7day": trend.rolling_avg_7day,
                "variance_7day": trend.variance_7day,
                "n_samples": trend.n_samples,
                "trend_direction": trend.trend_direction,
                "plateau_days": trend.plateau_days,
                "phase_2b_eligible": trend.phase_2b_eligible,
                "timestamp": trend.last_update.isoformat(),
            }

            # Write to EventStore (audit-first)
            audit_id = self._write("confidence_trend_recorded", event_data, tenant_id)

            logger.debug(
                f"audit_logged: confidence_trend_recorded: {skill_id}: "
                f"audit_id={audit_id}"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_confidence_trend_error: {skill_id}: {e}")
            raise

    def log_phase_transition(
        self,
        transition,  # ConvergencePhaseTransition
        tenant_id: str,
    ) -> Optional[str]:
        """Log phase transition.

        Args:
            transition: ConvergencePhaseTransition dataclass
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "phase_transition_recorded",
                "skill_id": transition.skill_id,
                "from_phase": transition.from_phase.value,
                "to_phase": transition.to_phase.value,
                "reason": transition.reason,
                "confidence_at_transition": transition.confidence_at_transition,
                "n_samples_at_transition": transition.n_samples_at_transition,
                "timestamp": transition.timestamp.isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("phase_transition_recorded", event_data, tenant_id)

            logger.info(
                f"audit_logged: phase_transition_recorded: {transition.skill_id}: "
                f"{transition.from_phase} → {transition.to_phase}"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_phase_transition_error: {transition.skill_id}: {e}")
            raise

    def log_optimization_triggered(
        self,
        delta,  # ParameterDelta
        feedback_signal_strength: float,
        tenant_id: str,
    ) -> Optional[str]:
        """Log parameter optimization trigger.

        Args:
            delta: ParameterDelta dataclass
            feedback_signal_strength: Signal strength [0.0, 1.0]
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "optimization_triggered",
                "skill_id": delta.skill_id,
                "param_name": delta.param_name,
                "old_value": delta.old_value,
                "new_value": delta.new_value,
                "reason": delta.reason,
                "confidence_boost": delta.confidence_boost,
                "feedback_signal_strength": feedback_signal_strength,
                "timestamp": delta.timestamp.isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("optimization_triggered", event_data, tenant_id)

            logger.info(
                f"audit_logged: optimization_triggered: {delta.skill_id}: "
                f"{delta.reason}: {delta.param_name}={delta.old_value:.3f} → {delta.new_value:.3f}"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_optimization_triggered_error: {delta.skill_id}: {e}")
            raise

    def log_threshold_adjusted(
        self,
        skill_id: str,
        old_threshold: float,
        new_threshold: float,
        reason: str,
        tenant_id: str,
    ) -> Optional[str]:
        """Log confidence threshold adjustment.

        Args:
            skill_id: Skill affected
            old_threshold: Previous threshold
            new_threshold: New threshold
            reason: Why was it adjusted?
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "threshold_adjusted",
                "skill_id": skill_id,
                "old_threshold": old_threshold,
                "new_threshold": new_threshold,
                "delta": new_threshold - old_threshold,
                "reason": reason,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("threshold_adjusted", event_data, tenant_id)

            logger.debug(
                f"audit_logged: threshold_adjusted: {skill_id}: "
                f"{old_threshold:.3f} → {new_threshold:.3f} ({reason})"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_threshold_adjusted_error: {skill_id}: {e}")
            raise

    def log_divergence_detected(
        self,
        alert,  # DivergenceAlert
        tenant_id: str,
    ) -> Optional[str]:
        """Log divergence detection alert.

        Args:
            alert: DivergenceAlert dataclass
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "divergence_detected",
                "skill_id": alert.skill_id,
                "plateau_confidence": alert.plateau_confidence,
                "current_confidence": alert.current_confidence,
                "decline_percent": alert.decline_percent,
                "severity": alert.severity,
                "recommended_action": alert.recommended_action,
                "timestamp": alert.timestamp.isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("divergence_detected", event_data, tenant_id)

            logger.warning(
                f"audit_logged: divergence_detected: {alert.skill_id}: "
                f"{alert.severity}: {alert.decline_percent:.1f}% decline"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_divergence_detected_error: {alert.skill_id}: {e}")
            raise

    def log_convergence_detected(
        self,
        skill_id: str,
        confidence: float,
        plateau_days: int,
        n_samples: int,
        tenant_id: str,
    ) -> Optional[str]:
        """Log convergence detection.

        Args:
            skill_id: Skill that converged
            confidence: Convergence confidence level
            plateau_days: Days at plateau
            n_samples: Total samples
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "convergence_detected",
                "skill_id": skill_id,
                "confidence": confidence,
                "plateau_days": plateau_days,
                "n_samples": n_samples,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("convergence_detected", event_data, tenant_id)

            logger.info(
                f"audit_logged: convergence_detected: {skill_id}: "
                f"conf={confidence:.3f}, plateau={plateau_days}d"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_convergence_detected_error: {skill_id}: {e}")
            raise

    def log_false_convergence_detected(
        self,
        skill_id: str,
        plateau_confidence: float,
        resumed_confidence: float,
        resume_improvement_percent: float,
        tenant_id: str,
    ) -> Optional[str]:
        """Log false convergence detection.

        Args:
            skill_id: Skill with false convergence
            plateau_confidence: Confidence at plateau start
            resumed_confidence: Current confidence after resuming
            resume_improvement_percent: % improvement from plateau
            tenant_id: Tenant ID for isolation

        Returns:
            audit_ref of the committed record (raises RuntimeError if it did not commit)
        """
        try:
            event_data = {
                "event_type": "false_convergence_detected",
                "skill_id": skill_id,
                "plateau_confidence": plateau_confidence,
                "resumed_confidence": resumed_confidence,
                "resume_improvement_percent": resume_improvement_percent,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }

            # Write to EventStore
            audit_id = self._write("false_convergence_detected", event_data, tenant_id)

            logger.info(
                f"audit_logged: false_convergence_detected: {skill_id}: "
                f"resumed from {plateau_confidence:.3f} to {resumed_confidence:.3f} "
                f"(+{resume_improvement_percent:.1f}%)"
            )

            return audit_id

        except Exception as e:
            logger.error(f"log_false_convergence_detected_error: {skill_id}: {e}")
            raise


__all__ = ["Phase2bAuditIntegration"]

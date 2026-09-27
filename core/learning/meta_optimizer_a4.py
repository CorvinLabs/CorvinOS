"""
Stream A Module A4: MetaOptimizer Phase 1 Skeleton

Closes feedback loop: ConfidenceScore → config_delta recommendation → apply

Phase 1: confidence_delta → config_delta (learning_rate × delta)
Phase 2: Feedback ingestion + rolling average stability
Phase 3: Multi-skill composition

References: ADR-2087 (A4 Design), ADR-0613 (Stream A), ADR-0232 (Audit-first)

The recommendation is committed to the tenant's core audit chain
(``learning.optimizer_config_updated``) before it is returned; a failed commit
raises. (Until 2026-09-27 the "audit_ref" was a stub uuid written nowhere.)
A score of another tenant is refused.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Optional
import logging
import threading

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class OptimizerConfig:
    """Immutable optimizer config recommendation from A4."""

    skill_id: str
    config_delta: float  # Learning rate adjustment
    confidence_before: float
    confidence_after: float  # Projected
    audit_ref: str
    tenant_id: str
    timestamp: datetime


class MetaOptimizer:
    """
    Phase 1: MetaOptimizer skeleton for feedback loop closure.

    Input: ConfidenceScore from A3 (confidence_delta, trend)
    Output: OptimizerConfig (config_delta recommendation)

    Formula (Phase 1):
    - config_delta = confidence_delta × learning_rate
    - learning_rate = 0.1 (Phase 1 stub; Phase 2: adaptive)

    Invariants:
    - Non-blocking: failures logged, don't break pipeline
    - Audit-first: emit optimizer_config_updated before applying
    - Tenant scoped: all operations carry tenant_id
    """

    def __init__(self, tenant_id: str, learning_rate: float = 0.1):
        """Initialize MetaOptimizer with tenant + learning rate."""
        from core.tenants import validate_tenant_id  # noqa: PLC0415

        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id
        self.learning_rate = learning_rate
        self._recommendations_count = 0
        self._lock = threading.Lock()  # Thread safety for state mutations

    def optimize(self, confidence_score) -> Optional[OptimizerConfig]:
        """
        Generate config_delta recommendation from confidence score.

        Args:
            confidence_score: A3 ConfidenceScore (with confidence_delta, trend)

        Returns:
            OptimizerConfig with recommendation, or None on validation failure
        """
        if confidence_score is None:
            logger.warning("MetaOptimizer.optimize() received None")
            return None
        if getattr(confidence_score, "tenant_id", self.tenant_id) != self.tenant_id:
            logger.error("MetaOptimizer.optimize() refused a score of another tenant")
            return None

        try:
            # Phase 1: Simple linear adjustment
            # confidence_delta range: [-1, 1]
            # config_delta = confidence_delta × learning_rate
            config_delta = confidence_score.confidence_delta * self.learning_rate

            # Clamp to reasonable bounds
            config_delta = max(-0.5, min(0.5, config_delta))

            # Projected confidence after applying config
            confidence_after = confidence_score.confidence_delta + config_delta
            confidence_after = max(0.0, min(1.0, confidence_after))

            # Emit audit event
            audit_ref = self._emit_optimizer_config_updated(
                source_audit_ref=str(getattr(confidence_score, "audit_ref", "") or ""),
                skill_id=confidence_score.skill_id,
                config_delta=config_delta,
                confidence_before=confidence_score.confidence_delta,
                confidence_after=confidence_after,
            )

            # Return immutable OptimizerConfig
            config = OptimizerConfig(
                skill_id=confidence_score.skill_id,
                config_delta=config_delta,
                confidence_before=confidence_score.confidence_delta,
                confidence_after=confidence_after,
                audit_ref=audit_ref,
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow(),
            )

            with self._lock:
                self._recommendations_count += 1

            logger.debug(
                f"A4 Phase 1: {confidence_score.skill_id} "
                f"delta={config_delta:+.4f} "
                f"conf={confidence_score.confidence_delta:.2%}→{confidence_after:.2%}"
            )
            return config

        except Exception as e:
            logger.error(f"MetaOptimizer.optimize() failed: {e}", exc_info=True)
            raise

    def _emit_optimizer_config_updated(
        self,
        skill_id: str,
        config_delta: float,
        confidence_before: float,
        confidence_after: float,
        source_audit_ref: str = "",
    ) -> str:
        """Commit ``learning.optimizer_config_updated`` to the core chain (fail-closed)."""
        from core.learning.event_persistence import core_audit_event  # noqa: PLC0415

        return core_audit_event(
            "learning.optimizer_config_updated",
            tenant_id=self.tenant_id,
            details={
                "skill_id": str(skill_id)[:128],
                "config_delta": round(float(config_delta), 6),
                "confidence_before": round(float(confidence_before), 6),
                "confidence_after": round(float(confidence_after), 6),
                "source_audit_ref": source_audit_ref[:64],
                "tenant_id": self.tenant_id,
            },
        )

    def get_recommendations_count(self) -> int:
        """Return count of recommendations generated."""
        return self._recommendations_count

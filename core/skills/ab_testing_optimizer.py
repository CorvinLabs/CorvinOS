"""A/B Testing Optimizer for Skills (k=3, ADR-0683 Phase 2).

Implements:
1. Variant proposal: suggest alternative model/routing based on confidence
2. Shadow mode: decision audited, bundled answer stands, feedback collected
3. Rollout policy: confidence < 0.8 → auto-revert on >15% drop
4. Audit-FIRST: variant_proposed, variant_rollout, variant_reverted events
5. Performance: <50ms per decision (inherited from SkillConfidenceCalculator)
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional, Tuple
from uuid import uuid4
from enum import Enum

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.confidence_calculator import SkillConfidenceCalculator
from core.skills.models.learning_event import LearningEventStore


class VariantStatus(str, Enum):
    """Variant lifecycle states."""
    PROPOSED = "proposed"       # Awaiting rollout decision
    RUNNING = "running"         # In shadow mode
    PROMOTED = "promoted"       # Graduated to primary
    REVERTED = "reverted"       # Rolled back due to regression


@dataclass(frozen=True)
class Variant:
    """Immutable variant proposal."""
    variant_id: str
    skill_id: str
    name: str                    # e.g., "claude-opus-4 routing"
    model_id: str                # Model being tested
    confidence_threshold: float  # Min confidence to promote
    confidence_drop_threshold: float = 0.15  # Revert if drop >15%
    created_at: datetime = field(default_factory=datetime.utcnow)
    status: VariantStatus = field(default=VariantStatus.PROPOSED)


@dataclass
class VariantMetrics:
    """Variant performance tracking."""
    variant_id: str
    trials: int = 0
    successes: int = 0
    feedback_count: int = 0
    peak_confidence: float = 0.0
    current_confidence: float = 0.0
    promotion_ready: bool = False


class ABTestingOptimizer:
    """A/B Testing & Rollout Manager for Skills.

    Shadow mode: proposes variants, runs them alongside bundled answer,
    collects feedback, measures confidence, auto-reverts on regression.

    Audit-FIRST design: writes variant_proposed, variant_rollout, variant_reverted
    events to audit chain synchronously, fail-closed on commit failure.
    """

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        audit_chain: AuditChainWriter,
        confidence_calc: SkillConfidenceCalculator,
        event_store: Optional[LearningEventStore] = None,
    ):
        """Initialize A/B optimizer.

        Args:
            skill_id: Skill being optimized
            tenant_id: Tenant scope
            audit_chain: AuditChainWriter for audit trail
            confidence_calc: SkillConfidenceCalculator for metrics
            event_store: Optional LearningEventStore
        """
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.audit_chain = audit_chain
        self.confidence_calc = confidence_calc
        self.event_store = event_store

        self._lock = threading.RLock()
        self._variants: Dict[str, Variant] = {}
        self._metrics: Dict[str, VariantMetrics] = {}
        self._active_variant: Optional[str] = None
        self._promotion_history: List[Tuple[str, datetime, str]] = []

    def propose_variant(
        self,
        name: str,
        model_id: str,
        confidence_threshold: float = 0.80,
        confidence_drop_threshold: float = 0.15,
    ) -> Dict:
        """Propose a new variant for testing.

        Args:
            name: Human-readable variant name
            model_id: Model to test (e.g., "claude-opus-4")
            confidence_threshold: Min confidence to promote to primary
            confidence_drop_threshold: Revert if drop exceeds this (default 15%)

        Returns:
            {
                "variant_id": str,
                "status": "proposed",
                "name": str,
                "model_id": str,
                "created_at": str (ISO 8601),
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            variant_id = str(uuid4())[:8]
            variant = Variant(
                variant_id=variant_id,
                skill_id=self.skill_id,
                name=name,
                model_id=model_id,
                confidence_threshold=confidence_threshold,
                confidence_drop_threshold=confidence_drop_threshold,
            )
            self._variants[variant_id] = variant
            self._metrics[variant_id] = VariantMetrics(variant_id=variant_id)

        result = {
            "variant_id": variant_id,
            "status": variant.status.value,
            "name": name,
            "model_id": model_id,
            "confidence_threshold": confidence_threshold,
            "created_at": variant.created_at.isoformat(),
        }

        # **AUDIT-FIRST:** Write variant_proposed event
        try:
            self._write_audit_event("variant_proposed", variant_id, result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for variant proposal: {e}")

        return result

    def execute_shadow_mode(self, variant_id: str) -> Dict:
        """Run variant in shadow mode (audit decision, use bundled answer).

        Args:
            variant_id: Variant to test

        Returns:
            {
                "variant_id": str,
                "status": "running",
                "decision": "shadowed",
                "bundled_answer": "...",
                "variant_output": "...",
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            if variant_id not in self._variants:
                raise ValueError(f"Variant {variant_id} not found")

            variant = self._variants[variant_id]
            if variant.status != VariantStatus.PROPOSED:
                raise ValueError(f"Variant {variant_id} is {variant.status.value}, not proposed")

            # Update variant status
            self._variants[variant_id] = Variant(
                variant_id=variant.variant_id,
                skill_id=variant.skill_id,
                name=variant.name,
                model_id=variant.model_id,
                confidence_threshold=variant.confidence_threshold,
                created_at=variant.created_at,
                status=VariantStatus.RUNNING,
            )
            self._active_variant = variant_id
            metrics = self._metrics[variant_id]
            metrics.trials += 1

        result = {
            "variant_id": variant_id,
            "status": VariantStatus.RUNNING.value,
            "decision": "shadowed",
            "bundled_answer": f"<bundled-answer-from-primary>",
            "variant_output": f"<output-from-{variant.model_id}>",
            "trial_count": metrics.trials,
        }

        # **AUDIT-FIRST:** Write variant_rollout event
        try:
            self._write_audit_event("variant_rollout", variant_id, result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for variant rollout: {e}")

        return result

    def record_variant_feedback(self, variant_id: str, outcome: str) -> None:
        """Record feedback on variant performance.

        Args:
            variant_id: Variant being tested
            outcome: "correct", "incorrect", "partial"
        """
        with self._lock:
            if variant_id not in self._metrics:
                return

            metrics = self._metrics[variant_id]
            metrics.feedback_count += 1
            if outcome == "correct":
                metrics.successes += 1

            # Update confidence based on feedback
            # success_rate = successes / feedback_count
            # feedback_engagement = feedback_count / trials
            if metrics.trials > 0 and metrics.feedback_count > 0:
                success_rate = metrics.successes / metrics.feedback_count
                feedback_engagement = min(1.0, metrics.feedback_count / metrics.trials)  # Clamp to [0, 1]
                metrics.current_confidence = (
                    0.7 * success_rate +
                    0.3 * feedback_engagement
                )
                metrics.current_confidence = max(0.0, min(1.0, metrics.current_confidence))  # Clamp result
                metrics.peak_confidence = max(
                    metrics.peak_confidence,
                    metrics.current_confidence,
                )

            # Check if ready to promote
            variant = self._variants.get(variant_id)
            if variant and metrics.current_confidence >= variant.confidence_threshold:
                metrics.promotion_ready = True

    def check_and_revert_on_regression(self, variant_id: str) -> Optional[Dict]:
        """Check for confidence regression and auto-revert if needed.

        Args:
            variant_id: Variant to check

        Returns:
            Revert event dict if reverted, None otherwise

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            if variant_id not in self._variants:
                return None

            variant = self._variants[variant_id]
            metrics = self._metrics[variant_id]

            # Calculate regression
            drop = metrics.peak_confidence - metrics.current_confidence
            if drop > variant.confidence_drop_threshold:  # >15% drop
                # Trigger revert
                self._variants[variant_id] = Variant(
                    variant_id=variant.variant_id,
                    skill_id=variant.skill_id,
                    name=variant.name,
                    model_id=variant.model_id,
                    confidence_threshold=variant.confidence_threshold,
                    created_at=variant.created_at,
                    status=VariantStatus.REVERTED,
                )
                self._active_variant = None
                self._promotion_history.append((variant_id, datetime.utcnow(), "regression"))

                result = {
                    "variant_id": variant_id,
                    "status": VariantStatus.REVERTED.value,
                    "reason": "regression",
                    "confidence_drop": drop,
                    "drop_threshold": variant.confidence_drop_threshold,
                }

                # **AUDIT-FIRST:** Write variant_reverted event
                try:
                    self._write_audit_event("variant_reverted", variant_id, result, start_time)
                except Exception as e:
                    raise RuntimeError(f"Audit chain write failed for variant revert: {e}")

                return result

        return None

    def promote_variant(self, variant_id: str) -> Dict:
        """Promote variant from shadow to primary.

        Args:
            variant_id: Variant to promote

        Returns:
            Promotion event

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        start_time = time.time()

        with self._lock:
            if variant_id not in self._variants:
                raise ValueError(f"Variant {variant_id} not found")

            variant = self._variants[variant_id]
            metrics = self._metrics[variant_id]

            if not metrics.promotion_ready:
                raise ValueError(
                    f"Variant {variant_id} not ready: "
                    f"confidence {metrics.current_confidence:.2f} < "
                    f"threshold {variant.confidence_threshold}"
                )

            # Promote
            self._variants[variant_id] = Variant(
                variant_id=variant.variant_id,
                skill_id=variant.skill_id,
                name=variant.name,
                model_id=variant.model_id,
                confidence_threshold=variant.confidence_threshold,
                created_at=variant.created_at,
                status=VariantStatus.PROMOTED,
            )
            self._promotion_history.append((variant_id, datetime.utcnow(), "promoted"))

            result = {
                "variant_id": variant_id,
                "status": VariantStatus.PROMOTED.value,
                "final_confidence": metrics.current_confidence,
                "trials": metrics.trials,
                "promoted_at": datetime.utcnow().isoformat(),
            }

        # **AUDIT-FIRST:** Write variant_promoted event
        try:
            self._write_audit_event("variant_promoted", variant_id, result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for variant promotion: {e}")

        return result

    def _write_audit_event(
        self,
        event_type: str,
        variant_id: str,
        result: Dict,
        start_time: float,
    ) -> None:
        """Write audit event (fail-closed)."""
        latency_ms = (time.time() - start_time) * 1000

        audit_event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=self.tenant_id,
            user_id=None,
            timestamp=datetime.utcnow().isoformat(),
            details={
                "skill_id": self.skill_id,
                "variant_id": variant_id,
                "event_type": event_type,
                **result,
                "latency_ms": latency_ms,
            },
            severity="INFO",
        )

        try:
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise IOError(f"Failed to write {event_type} event to audit chain: {e}")

    def get_variant_metrics(self, variant_id: str) -> Dict:
        """Get variant performance metrics."""
        with self._lock:
            if variant_id not in self._metrics:
                raise ValueError(f"Variant {variant_id} not found")

            m = self._metrics[variant_id]
            return {
                "variant_id": variant_id,
                "trials": m.trials,
                "successes": m.successes,
                "feedback_count": m.feedback_count,
                "current_confidence": m.current_confidence,
                "peak_confidence": m.peak_confidence,
                "promotion_ready": m.promotion_ready,
            }

    def get_promotion_history(self) -> List[Dict]:
        """Get history of variant promotions/reverts."""
        with self._lock:
            return [
                {
                    "variant_id": variant_id,
                    "timestamp": ts.isoformat(),
                    "action": action,
                }
                for variant_id, ts, action in self._promotion_history
            ]

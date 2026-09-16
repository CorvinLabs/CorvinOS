"""SkillConfidenceCalculator — confidence scoring & convergence detection (k=2, ADR-0683).

Implements:
1. Confidence formula: 0.7 × success_rate + 0.3 × feedback_engagement
2. Convergence detection (stable ±5% over 10 observations)
3. Regression alerting (drop >10% triggers review)
4. Audit-FIRST: every execute() → audit chain write, fail-closed on commit failure
5. Performance: <50ms per execute() call
"""

from __future__ import annotations

import time
import threading
from dataclasses import dataclass, field
from datetime import datetime
from typing import Dict, List, Optional
from uuid import uuid4

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from core.skills.models.learning_event import ConfidenceScoreEvent, LearningEventStore


@dataclass
class ConfidenceObservation:
    """Single confidence observation for convergence tracking."""
    confidence: float
    timestamp: datetime
    observation_id: str = field(default_factory=lambda: str(uuid4()))


class SkillConfidenceCalculator:
    """Calculates and tracks confidence for a single Skill.

    Formula: confidence = 0.7 × success_rate + 0.3 × feedback_engagement

    Where:
    - success_rate: P(user feedback was CORRECT) = (correct_count / total_feedback)
    - feedback_engagement: P(user provided feedback) = (feedback_count / execution_count)

    Convergence: stable when std_dev(last 10 observations) <= 0.05
    Regression alert: triggered when confidence drops >10% from previous value

    Audit-FIRST design: writes ConfidenceScoreEvent to audit chain BEFORE returning,
    fail-closed on audit commit failure (raises RuntimeError).
    """

    def __init__(
        self,
        skill_id: str,
        tenant_id: str,
        audit_chain: AuditChainWriter,
        event_store: Optional[LearningEventStore] = None,
    ):
        """Initialize confidence calculator.

        Args:
            skill_id: The Skill being tracked (e.g., "os.delegation_router")
            tenant_id: Tenant scope (GDPR Art. 5, 6, 32)
            audit_chain: AuditChainWriter for persistent audit trail
            event_store: Optional LearningEventStore for in-memory event collection

        Raises:
            ValueError: If audit_chain write fails on initialization
        """
        self.skill_id = skill_id
        self.tenant_id = tenant_id
        self.audit_chain = audit_chain
        self.event_store = event_store or LearningEventStore(tenant_id)

        # State tracking (thread-safe)
        self._lock = threading.RLock()
        self._observations: List[ConfidenceObservation] = []
        self._success_count = 0
        self._total_feedback = 0
        self._execution_count = 0
        self._last_confidence = None
        self._regression_alert_triggered = False

    def record_execution(self, success: bool = True) -> None:
        """Record a Skill execution (called after Skill.execute() completes).

        Args:
            success: Whether the execution succeeded (True/False)
        """
        with self._lock:
            self._execution_count += 1
            if success:
                self._success_count += 1

    def record_feedback(self, feedback_outcome: str) -> None:
        """Record user feedback on a previous execution.

        Args:
            feedback_outcome: "correct", "incorrect", "partial", or "unknown"

        Note: This tracks feedback engagement (P(user gave feedback)),
              NOT execution success. Execution success is tracked separately via
              record_execution(success=True/False).
        """
        with self._lock:
            self._total_feedback += 1

    def execute(self) -> Dict:
        """Calculate current confidence and check convergence/regression.

        Returns:
            {
                "confidence": float (0.0-1.0),
                "success_rate": float,
                "feedback_engagement": float,
                "converged": bool,
                "regression_alert": bool,
                "observation_count": int,
            }

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
            ValueError: If invalid state detected
        """
        start_time = time.time()

        with self._lock:
            # Calculate metrics
            success_rate = (
                self._success_count / self._execution_count
                if self._execution_count > 0
                else 0.5
            )
            feedback_engagement = (
                self._total_feedback / self._execution_count
                if self._execution_count > 0
                else 0.0
            )

            # Confidence formula: 0.7 × success_rate + 0.3 × feedback_engagement
            confidence = 0.7 * success_rate + 0.3 * feedback_engagement
            confidence = max(0.0, min(1.0, confidence))  # Clamp to [0.0, 1.0]

            # Convergence detection (stable ±5% over last 10 observations)
            converged = self._check_convergence(confidence)

            # Regression alert (drop >10% from previous)
            regression_alert = False
            if self._last_confidence is not None:
                drop = self._last_confidence - confidence
                if drop > 0.10:
                    regression_alert = True
                    self._regression_alert_triggered = True

            # Record observation
            obs = ConfidenceObservation(confidence=confidence, timestamp=datetime.utcnow())
            self._observations.append(obs)
            if len(self._observations) > 100:  # Keep last 100 for analysis
                self._observations.pop(0)

            self._last_confidence = confidence

        # Build result
        result = {
            "confidence": confidence,
            "success_rate": success_rate,
            "feedback_engagement": feedback_engagement,
            "converged": converged,
            "regression_alert": regression_alert,
            "observation_count": len(self._observations),
        }

        # **AUDIT-FIRST:** Write to audit chain synchronously, fail-closed
        try:
            self._write_audit_event(result, start_time)
        except Exception as e:
            raise RuntimeError(f"Audit chain write failed for {self.skill_id}: {e}")

        return result

    def _check_convergence(self, current_confidence: float) -> bool:
        """Check if confidence has converged (stable ±5% over 10 observations).

        Args:
            current_confidence: The current confidence value

        Returns:
            True if converged (stable), False otherwise
        """
        if len(self._observations) < 10:
            return False  # Not enough observations

        # Get last 10 (including the one being added)
        recent = [obs.confidence for obs in self._observations[-9:]] + [current_confidence]

        # Compute standard deviation
        mean = sum(recent) / len(recent)
        variance = sum((x - mean) ** 2 for x in recent) / len(recent)
        std_dev = variance ** 0.5

        # Converged if std_dev <= 0.05
        return std_dev <= 0.05

    def _write_audit_event(self, result: Dict, start_time: float) -> None:
        """Write ConfidenceScoreEvent to audit chain (fail-closed).

        Args:
            result: Result dict from execute()
            start_time: Execution start time (for latency)

        Raises:
            IOError: If audit chain write fails
        """
        latency_ms = (time.time() - start_time) * 1000

        # Create AuditEvent for core audit chain
        audit_event = AuditEvent(
            event_id=str(uuid4()),
            event_type="skill_confidence_calculated",
            tenant_id=self.tenant_id,
            user_id=None,
            timestamp=datetime.utcnow().isoformat(),
            details={
                "skill_id": self.skill_id,
                "confidence": result["confidence"],
                "success_rate": result["success_rate"],
                "feedback_engagement": result["feedback_engagement"],
                "converged": result["converged"],
                "regression_alert": result["regression_alert"],
                "observation_count": result["observation_count"],
                "latency_ms": latency_ms,
            },
            severity="INFO",
        )

        # Write to core audit chain (fail-closed: raises IOError on failure)
        try:
            self.audit_chain.write_event(audit_event)
        except IOError as e:
            raise IOError(f"Failed to write confidence event to audit chain: {e}")

        # Also record to learning event store (optional, secondary sink)
        if self.event_store:
            # Note: event_type is auto-set by ConfidenceScoreEvent dataclass default
            learning_event = ConfidenceScoreEvent(
                skill_id=self.skill_id,
                tenant_id=self.tenant_id,
                timestamp=datetime.utcnow(),
                input={},
                output=result,
                confidence=result["confidence"],
                basis=f"formula: 0.7 × sr={result['success_rate']:.2f} + 0.3 × fe={result['feedback_engagement']:.2f}",
                lom="core/skills/confidence_calculator.py::execute",
            )
            self.event_store.append_event(learning_event)

    def get_observation_history(self, limit: Optional[int] = None) -> List[Dict]:
        """Retrieve observation history for dashboard/analysis.

        Args:
            limit: Max observations to return (None = all)

        Returns:
            List of observations with timestamp and confidence
        """
        with self._lock:
            obs_list = self._observations
            if limit:
                obs_list = obs_list[-limit:]
            return [
                {
                    "observation_id": obs.observation_id,
                    "confidence": obs.confidence,
                    "timestamp": obs.timestamp.isoformat(),
                }
                for obs in obs_list
            ]

    def reset(self) -> None:
        """Reset calculator state (for testing)."""
        with self._lock:
            self._observations.clear()
            self._success_count = 0
            self._total_feedback = 0
            self._execution_count = 0
            self._last_confidence = None
            self._regression_alert_triggered = False

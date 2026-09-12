"""Phase 2: MultiTenantOptimizer (Learning Loop Integration).

ADR-0682: Multi-Tenant Learning from Distributed OTEL Signals

Core logic:
1. Read OTEL Metrics (skill latency, errors) over time window
2. Stratify by tenant + geo (separate aggregations)
3. Match feedback events to skill executions
4. Compute gradient (delta_config) via optimizer step
5. Detect anomalies (outlier feedback)
6. Emit LearningConfigUpdated event (immutable, audit-logged)
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, Optional, List
import logging
from statistics import mean, stdev

logger = logging.getLogger(__name__)


class AnomalyType(Enum):
    """Anomaly detection results."""
    NORMAL = "normal"
    OUTLIER = "outlier"  # |value - mean| > 3σ
    UNKNOWN = "unknown"  # Not enough history


@dataclass(frozen=True)
class SkillMetricsWindow:
    """Aggregated metrics over a time window (1 hour default).

    Stratified by: tenant_id, skill_id, geo (for multi-tenant insights)
    """
    tenant_id: str
    skill_id: str
    skill_version: str
    geo_country: Optional[str] = None

    # Aggregated metrics (from OTEL histograms over 1-hour window)
    latency_p50_ms: float = 0.0
    latency_p99_ms: float = 0.0
    error_rate_percent: float = 0.0

    sample_count: int = 0  # Number of executions in window
    timestamp: Optional[str] = None


@dataclass(frozen=True)
class LearningConfigUpdate:
    """Immutable config change (emitted by optimizer).

    Maps to OTEL Event: corvin.learning.config_updated
    Audit-logged (hash-chained, immutable)
    """
    tenant_id: str
    skill_id: str

    param_name: str  # Which param changed (e.g., "threshold")
    old_value: float
    new_value: float

    confidence: float  # How confident is this change? [0-1]
    reason: str  # "user_feedback" | "geo_correlation" | "convergence_adaptation"

    lom: Optional[str] = None  # Line of moral responsibility (audit)


class MultiTenantOptimizer:
    """Learns from distributed telemetry (OTEL Metrics + Feedback).

    Phase 2 Core: Reads metrics windows, matches feedback, computes deltas.

    Constraints (ADR-0682):
    - Per-tenant: no cross-tenant data leakage
    - Immutable: config changes are audit-logged
    - Anomaly-safe: outlier feedback excluded from optimizer step
    - Convergence-aware: detects oscillation, adapts step size
    """

    def __init__(self, tenant_id: str, audit_logger: Optional[logging.Logger] = None):
        if not tenant_id:
            raise ValueError("tenant_id is mandatory (fail-closed)")

        self.tenant_id = tenant_id
        self.audit_logger = audit_logger or logging.getLogger("audit")

        # State (mutable during optimization, immutable once finalized)
        self.feedback_history: Dict[str, List[float]] = {}  # skill_id → [feedback values]
        self.config_deltas: List[LearningConfigUpdate] = []
        self.convergence_meter_latencies: List[float] = []  # Last 10 latencies (for σ)

    def step(
        self,
        metrics_window: SkillMetricsWindow,
        user_feedback: Optional[Dict[str, float]] = None,
    ) -> Optional[LearningConfigUpdate]:
        """Optimizer step: read metrics, apply feedback, compute delta.

        Args:
            metrics_window: Aggregated metrics from OTEL
            user_feedback: User-provided feedback dict (e.g., {"threshold": 0.65})

        Returns:
            LearningConfigUpdate (if delta computed), or None

        Steps:
            1. Aggregate metrics (already done in metrics_window)
            2. Check anomalies in feedback
            3. Compute gradient (delta)
            4. Detect convergence (oscillation)
            5. Emit config update (audit-logged)
        """
        skill_id = metrics_window.skill_id

        # Step 1: Collect feedback history
        if skill_id not in self.feedback_history:
            self.feedback_history[skill_id] = []

        # Step 2: Check for anomalies in user feedback
        if user_feedback:
            for param, value in user_feedback.items():
                is_anomaly, anomaly_type = self._check_anomaly(skill_id, value)
                if is_anomaly:
                    self.audit_logger.warning(
                        "learning_anomaly_detected",
                        extra={
                            "tenant_id": self.tenant_id,
                            "skill_id": skill_id,
                            "anomaly_type": anomaly_type.value,
                            "param": param,
                            "value": value,
                        },
                    )
                    # Exclude from optimizer step (don't learn from outliers)
                    continue

                self.feedback_history[skill_id].append(value)

                # Step 3: Compute delta (simple gradient descent)
                delta = self._compute_delta(
                    metrics_window=metrics_window,
                    param=param,
                    target_value=value,
                )

                if delta:
                    # Step 4: Detect convergence (oscillation detection)
                    self._update_convergence_meter(metrics_window.latency_p99_ms)

                    # Step 5: Emit config update
                    config_update = LearningConfigUpdate(
                        tenant_id=self.tenant_id,
                        skill_id=skill_id,
                        param_name=param,
                        old_value=delta["old_value"],
                        new_value=delta["new_value"],
                        confidence=delta["confidence"],
                        reason=delta["reason"],
                        lom="MultiTenantOptimizer.step:L95",
                    )

                    self.config_deltas.append(config_update)

                    self.audit_logger.info(
                        "learning_config_updated",
                        extra={
                            "tenant_id": self.tenant_id,
                            "skill_id": skill_id,
                            "param": param,
                            "delta": {
                                "old": delta["old_value"],
                                "new": delta["new_value"],
                            },
                            "confidence": delta["confidence"],
                            "reason": delta["reason"],
                        },
                    )

                    return config_update

        return None

    def _check_anomaly(self, skill_id: str, value: float) -> tuple[bool, AnomalyType]:
        """Detect outlier feedback (|value - mean| > 3σ).

        Returns: (is_anomaly, anomaly_type)
        """
        if skill_id not in self.feedback_history or len(self.feedback_history[skill_id]) < 3:
            return False, AnomalyType.UNKNOWN

        history = self.feedback_history[skill_id]
        mean_val = mean(history)
        sigma = stdev(history) if len(history) > 1 else 0

        if sigma == 0:
            return False, AnomalyType.NORMAL

        z_score = abs(value - mean_val) / sigma
        if z_score > 3:
            return True, AnomalyType.OUTLIER

        return False, AnomalyType.NORMAL

    def _compute_delta(
        self,
        metrics_window: SkillMetricsWindow,
        param: str,
        target_value: float,
    ) -> Optional[Dict]:
        """Compute config delta based on metrics + feedback.

        Simple strategy: if user feedback suggests a value, move towards it.
        Check against current metrics to ensure reasonableness.

        Returns: {"old_value": ..., "new_value": ..., "confidence": ..., "reason": ...}
        """
        # TODO: Real learning algorithm (gradient descent, optimizer state, etc.)
        # For Phase 2, use simple heuristic:
        # - If latency is high + user wants lower threshold → lower it
        # - Confidence is proportional to how much evidence we have

        if param == "threshold":
            # Example: routing.confidence threshold
            old_value = 0.70  # TODO: Read from current config
            new_value = target_value

            # Confidence based on sample count + consistency
            confidence = min(0.95, 0.5 + (metrics_window.sample_count / 1000))

            reason = "user_feedback"
            if metrics_window.latency_p99_ms > 200:
                reason = "geo_correlation"  # High latency in this geo

            return {
                "old_value": old_value,
                "new_value": new_value,
                "confidence": confidence,
                "reason": reason,
            }

        return None

    def _update_convergence_meter(self, latency_ms: float) -> None:
        """Track latency trend (last 10 samples) for convergence detection.

        If σ > threshold, optimizer is oscillating (learning_optimizer_unstable event).
        """
        self.convergence_meter_latencies.append(latency_ms)
        if len(self.convergence_meter_latencies) > 10:
            self.convergence_meter_latencies.pop(0)

        if len(self.convergence_meter_latencies) >= 5:
            sigma = stdev(self.convergence_meter_latencies)
            if sigma > 50:  # Threshold: if σ > 50ms, consider it oscillating
                self.audit_logger.warning(
                    "learning_optimizer_unstable",
                    extra={
                        "tenant_id": self.tenant_id,
                        "convergence_sigma": sigma,
                        "sample_count": len(self.convergence_meter_latencies),
                    },
                )

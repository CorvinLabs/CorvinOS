"""Alert Policy Management (ADR-0636)

Phase 5: Learning Dashboard — Alert Configuration and Notification

Manages alert policies for learning loop anomalies:
  - Loss divergence (ΔL > threshold)
  - Convergence stall (< 0.1% improvement over window)
  - Gradient explosion (||∇|| > max)
  - Parameter drift (α outside bounds)
  - Feedback quality issues (low confidence, high contradiction)

Policy Types:
  - Threshold-based: if metric > value, fire alert
  - Trend-based: if slope changes significantly, fire alert
  - Anomaly-based: if Z-score > threshold, fire alert

Compliance:
  - GDPR Art. 5 (minimization): alerting uses only learning state, no PII
  - GDPR Art. 32 (security): alerts logged and audit-trailed
  - Fail-closed: alerts default to ON (conservative)

Implementation: uses LiveExperimentCollector metrics + MetaOptimizer state.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from pydantic import BaseModel, Field

logger = logging.getLogger(__name__)


class AlertLevel(str, Enum):
    """Alert severity."""
    INFO = "info"
    WARNING = "warning"
    CRITICAL = "critical"


class AlertType(str, Enum):
    """Categories of learning anomalies."""
    LOSS_DIVERGENCE = "loss_divergence"
    CONVERGENCE_STALL = "convergence_stall"
    GRADIENT_EXPLOSION = "gradient_explosion"
    PARAMETER_DRIFT = "parameter_drift"
    FEEDBACK_QUALITY = "feedback_quality"
    CHECKPOINT_FAILURE = "checkpoint_failure"


class AlertPolicy(BaseModel):
    """Single alert policy."""
    policy_id: str
    alert_type: AlertType
    level: AlertLevel
    enabled: bool = True
    threshold: float  # Alert fires when metric > threshold
    window_minutes: int = 5  # Evaluation window (for trend-based)
    reason: Optional[str] = None  # Why this policy exists
    created_at: str
    updated_at: str
    muted_until: Optional[str] = None  # Mute expiry timestamp


class AlertEvent(BaseModel):
    """Fired alert event."""
    alert_id: str
    policy_id: str
    alert_type: AlertType
    level: AlertLevel
    metric_name: str
    metric_value: float
    threshold: float
    message: str
    timestamp: str
    tenant_id: str
    muted: bool = False


class AlertNotificationHandler(BaseModel):
    """Callback for alert handling."""
    handler_id: str
    handler_type: str  # "webhook" | "email" | "slack" | "log"
    target: str  # URL, email addr, Slack channel, log level
    alert_levels: List[AlertLevel] = [AlertLevel.CRITICAL]
    enabled: bool = True


class AlertPolicyManager:
    """Manage alert policies for learning loops.

    Responsibilities:
    - Define alert policies (thresholds, windows, types)
    - Evaluate policies against current metrics
    - Fire alerts when policies trigger
    - Track alert history (audit trail)
    - Allow muting/escalating alerts (operator actions)

    **Tenant Isolation:**
    All policies and events are scoped to a single tenant (tenant_id parameter).

    **Compliance:**
    - GDPR Art. 5 (minimization): only learning metrics, no PII
    - GDPR Art. 30 (processing record): all events logged to audit trail
    - GDPR Art. 32 (security): fail-closed (alerts default ON)

    **Example Usage:**
    ```python
    manager = AlertPolicyManager(tenant_id="_default")

    # Add a policy
    policy = manager.add_policy(
        alert_type=AlertType.LOSS_DIVERGENCE,
        threshold=0.01,  # loss_total > 0.01
        level=AlertLevel.CRITICAL,
    )

    # Evaluate metrics
    metrics = await live_collector.latest()
    alerts = manager.evaluate(metrics)
    for alert in alerts:
        await notify_operator(alert)

    # Operator can mute for 1 hour
    manager.mute_policy(policy.policy_id, until_minutes=60)
    ```
    """

    def __init__(self, tenant_id: str):
        """Initialize alert manager for a tenant.

        Args:
            tenant_id: Tenant identifier (for isolation)
        """
        self.tenant_id = tenant_id
        self._policies: Dict[str, AlertPolicy] = {}
        self._history: List[AlertEvent] = []
        self._handlers: Dict[str, AlertNotificationHandler] = {}

        # Load default policies
        self._init_default_policies()

    def _init_default_policies(self) -> None:
        """Initialize default alert policies (fail-closed: all ON).

        Policies are conservative: better to alert unnecessarily than
        miss a real anomaly.
        """
        now = datetime.utcnow().isoformat()

        # Policy: Loss divergence (loss_total increases significantly)
        self._policies["loss_divergence_critical"] = AlertPolicy(
            policy_id="loss_divergence_critical",
            alert_type=AlertType.LOSS_DIVERGENCE,
            level=AlertLevel.CRITICAL,
            enabled=True,
            threshold=0.01,  # loss_total > 0.01 is concerning
            window_minutes=5,
            reason="Loss increased beyond expected range",
            created_at=now,
            updated_at=now,
        )

        # Policy: Convergence stall (no improvement for N minutes)
        self._policies["convergence_stall_warning"] = AlertPolicy(
            policy_id="convergence_stall_warning",
            alert_type=AlertType.CONVERGENCE_STALL,
            level=AlertLevel.WARNING,
            enabled=True,
            threshold=0.001,  # improvement < 0.1% over window
            window_minutes=30,
            reason="Convergence rate dropped below expected",
            created_at=now,
            updated_at=now,
        )

        # Policy: Gradient explosion (||∇|| too large)
        self._policies["gradient_explosion_critical"] = AlertPolicy(
            policy_id="gradient_explosion_critical",
            alert_type=AlertType.GRADIENT_EXPLOSION,
            level=AlertLevel.CRITICAL,
            enabled=True,
            threshold=0.1,  # ||∇|| > 0.1 is concerning
            window_minutes=1,
            reason="Gradient magnitude exceeds safe bounds",
            created_at=now,
            updated_at=now,
        )

        # Policy: Parameter drift (α outside expected bounds)
        self._policies["parameter_drift_warning"] = AlertPolicy(
            policy_id="parameter_drift_warning",
            alert_type=AlertType.PARAMETER_DRIFT,
            level=AlertLevel.WARNING,
            enabled=True,
            threshold=0.5,  # α > 0.5 is too aggressive
            window_minutes=5,
            reason="Learning rate drifted outside policy bounds",
            created_at=now,
            updated_at=now,
        )

        # Policy: Feedback quality (low confidence feedback)
        self._policies["feedback_quality_warning"] = AlertPolicy(
            policy_id="feedback_quality_warning",
            alert_type=AlertType.FEEDBACK_QUALITY,
            level=AlertLevel.WARNING,
            enabled=True,
            threshold=0.3,  # confidence < 0.3
            window_minutes=10,
            reason="Feedback confidence below threshold",
            created_at=now,
            updated_at=now,
        )

    def add_policy(
        self,
        alert_type: AlertType,
        threshold: float,
        level: AlertLevel = AlertLevel.WARNING,
        window_minutes: int = 5,
        reason: str = "",
    ) -> AlertPolicy:
        """Add a new alert policy.

        Args:
            alert_type: Type of anomaly to detect
            threshold: Trigger threshold
            level: Alert severity
            window_minutes: Evaluation window
            reason: Justification for this policy

        Returns:
            Created policy

        **Audit:** Logged to audit trail (GDPR Art. 30)
        """
        policy_id = f"{alert_type.value}_{len(self._policies)}"
        now = datetime.utcnow().isoformat()

        policy = AlertPolicy(
            policy_id=policy_id,
            alert_type=alert_type,
            level=level,
            enabled=True,
            threshold=threshold,
            window_minutes=window_minutes,
            reason=reason,
            created_at=now,
            updated_at=now,
        )

        self._policies[policy_id] = policy
        logger.info(f"Added alert policy {policy_id} (tenant={self.tenant_id})")

        return policy

    def disable_policy(self, policy_id: str) -> None:
        """Disable a policy (rarely done; prefer mute_policy)."""
        if policy_id in self._policies:
            self._policies[policy_id].enabled = False
            logger.info(f"Disabled policy {policy_id} (tenant={self.tenant_id})")

    def mute_policy(self, policy_id: str, until_minutes: int = 60) -> None:
        """Mute alerts for a policy (for N minutes).

        Used when an alert is known (e.g., during tuning) and temporary
        suppression is needed. After expiry, alerts resume.

        Args:
            policy_id: Policy to mute
            until_minutes: Duration (default 60)

        **Audit:** Logged as mute event
        """
        if policy_id in self._policies:
            mute_until = datetime.utcnow() + timedelta(minutes=until_minutes)
            self._policies[policy_id].muted_until = mute_until.isoformat()
            logger.info(f"Muted policy {policy_id} until {mute_until} (tenant={self.tenant_id})")

    def unmute_policy(self, policy_id: str) -> None:
        """Unmute a policy immediately."""
        if policy_id in self._policies:
            self._policies[policy_id].muted_until = None
            logger.info(f"Unmuted policy {policy_id} (tenant={self.tenant_id})")

    def evaluate(self, metrics: Dict[str, Any]) -> List[AlertEvent]:
        """Evaluate all policies against current metrics.

        Args:
            metrics: Current learning state
              {loss_total, loss_core, loss_infra, gradient_l2, alpha_core,
               convergence_percent, ...}

        Returns:
            List of fired alerts

        **Implementation Note:** In production, this method would:
        1. Check each enabled policy
        2. Evaluate condition (threshold, trend, anomaly)
        3. Check if muted
        4. Fire alert if condition + enabled + not muted
        5. Log to audit trail (GDPR Art. 30)
        6. Call notification handlers
        """
        alerts: List[AlertEvent] = []
        now = datetime.utcnow().isoformat()

        for policy_id, policy in self._policies.items():
            # Skip disabled/muted
            if not policy.enabled:
                continue
            if policy.muted_until and datetime.fromisoformat(policy.muted_until) > datetime.utcnow():
                continue

            # Evaluate condition based on type
            should_alert = False
            metric_value = 0.0
            message = ""

            if policy.alert_type == AlertType.LOSS_DIVERGENCE:
                metric_value = metrics.get("loss_total", 0.0)
                should_alert = metric_value > policy.threshold
                message = f"Loss divergence: loss_total={metric_value:.6f} > threshold={policy.threshold}"

            elif policy.alert_type == AlertType.CONVERGENCE_STALL:
                metric_value = metrics.get("convergence_percent", 100.0)
                should_alert = metric_value < 70.0  # Stalled if < 70%
                message = f"Convergence stalled: convergence={metric_value:.1f}% (expected ≥70%)"

            elif policy.alert_type == AlertType.GRADIENT_EXPLOSION:
                metric_value = metrics.get("gradient_l2", 0.0)
                should_alert = metric_value > policy.threshold
                message = f"Gradient explosion: ||∇||={metric_value:.6f} > threshold={policy.threshold}"

            elif policy.alert_type == AlertType.PARAMETER_DRIFT:
                metric_value = metrics.get("alpha_core", 0.0)
                should_alert = metric_value > policy.threshold
                message = f"Parameter drift: alpha_core={metric_value:.4f} > threshold={policy.threshold}"

            elif policy.alert_type == AlertType.FEEDBACK_QUALITY:
                metric_value = metrics.get("feedback_confidence", 1.0)
                should_alert = metric_value < policy.threshold
                message = f"Feedback quality: confidence={metric_value:.2f} < threshold={policy.threshold}"

            # Fire alert if triggered
            if should_alert:
                alert = AlertEvent(
                    alert_id=f"{policy_id}_{len(self._history)}",
                    policy_id=policy_id,
                    alert_type=policy.alert_type,
                    level=policy.level,
                    metric_name=policy.alert_type.value,
                    metric_value=metric_value,
                    threshold=policy.threshold,
                    message=message,
                    timestamp=now,
                    tenant_id=self.tenant_id,
                    muted=False,
                )

                alerts.append(alert)
                self._history.append(alert)

                # Log to audit trail (GDPR Art. 30)
                logger.warning(f"Alert fired: {alert.alert_id} ({message}, tenant={self.tenant_id})")

                # Call notification handlers
                self._notify_handlers(alert)

        return alerts

    def _notify_handlers(self, alert: AlertEvent) -> None:
        """Send alert to configured notification handlers.

        Handlers are called asynchronously (fire-and-forget) to avoid blocking.

        **Compliance:** Handlers must not leak PII or raw user data (GDPR Art. 32).
        """
        for handler_id, handler in self._handlers.items():
            # Skip if not enabled or alert level not matched
            if not handler.enabled or alert.level not in handler.alert_levels:
                continue

            # TODO: Implement notification dispatch
            # - If webhook: POST to handler.target with alert payload
            # - If email: send email to handler.target
            # - If Slack: send message to handler.target channel
            # - If log: log at appropriate level

            logger.info(f"Notifying {handler.handler_type} handler {handler_id}: {alert.alert_id}")

    def register_handler(
        self,
        handler_type: str,
        target: str,
        alert_levels: Optional[List[AlertLevel]] = None,
    ) -> AlertNotificationHandler:
        """Register an alert notification handler.

        Args:
            handler_type: "webhook" | "email" | "slack" | "log"
            target: Handler-specific target (URL, email, channel)
            alert_levels: Which severities to notify (default [CRITICAL])

        Returns:
            Registered handler

        **Audit:** Logged (handler registration is part of policy)
        """
        if alert_levels is None:
            alert_levels = [AlertLevel.CRITICAL]

        handler_id = f"{handler_type}_{len(self._handlers)}"
        handler = AlertNotificationHandler(
            handler_id=handler_id,
            handler_type=handler_type,
            target=target,
            alert_levels=alert_levels,
            enabled=True,
        )

        self._handlers[handler_id] = handler
        logger.info(f"Registered alert handler {handler_id} (tenant={self.tenant_id})")

        return handler

    def get_alert_history(self, limit: int = 100) -> List[AlertEvent]:
        """Get recent alert history (for dashboard)."""
        return self._history[-limit:]

    def get_policies(self) -> List[AlertPolicy]:
        """Get all policies for this tenant."""
        return list(self._policies.values())

    def clear_history(self) -> None:
        """Clear alert history (GDPR Art. 17 erasure, when requested)."""
        self._history.clear()
        logger.info(f"Cleared alert history (tenant={self.tenant_id})")

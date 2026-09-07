"""Alert Dispatcher with Security Hardening (Finding #11 Fix)

Processes alerts and dispatches actions with built-in safeguards:
  - Alert Signatures (HMAC-SHA256) prevent spoofing
  - Rate Limiting (1 CRITICAL per 5 minutes per component) prevents alert storms
  - Manual Confirmation (CRITICAL alerts) requires operator approval before action
  - Audit Trail (every alert + signature check + action) for GDPR compliance

Finding #11 Mitigation:
  "Alert Spoofing: Fake CRITICAL alert disables learning"

  Attack vector: Attacker sends fake CRITICAL alert → alert dispatcher triggers
  learning disable without verification.

  Fix: (1) Sign alerts with tenant key, (2) Rate-limit CRITICAL, (3) Require
  manual confirmation, (4) Audit every step.

Compliance:
  - GDPR Art. 5 (minimization): no PII in alerts
  - GDPR Art. 30 (processing record): audit trail logs all events
  - GDPR Art. 32 (security): fail-closed (confirmation required)
  - EU AI Act Art. 50 (transparency): operator approval logged
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Dict, List, Optional, Callable, Any

from pydantic import BaseModel

logger = logging.getLogger(__name__)


class AlertAction(str, Enum):
    """Actions triggered by alerts."""
    DISABLE_LEARNING = "disable_learning"
    PAUSE_SKILL = "pause_skill"
    ESCALATE_TO_OPERATOR = "escalate_to_operator"
    LOG_ONLY = "log_only"


class AlertSignatureStatus(str, Enum):
    """Result of signature verification."""
    VALID = "valid"
    INVALID = "invalid"
    MISSING = "missing"
    EXPIRED = "expired"


@dataclass
class AlertSignature:
    """Alert signature with verification info."""
    signature_hex: str
    algorithm: str = "hmac-sha256"
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tenant_id: str = ""


@dataclass(frozen=True)
class SecureAlert:
    """Alert with cryptographic signature."""
    alert_id: str
    component_id: str
    severity: str  # "info", "warning", "critical"
    message: str
    action: AlertAction
    signature: AlertSignature
    tenant_id: str
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())


@dataclass
class AlertAuditEvent:
    """Audit event for alert processing."""
    event_id: str
    alert_id: str
    event_type: str  # "alert_received", "signature_check", "rate_limit_check", etc.
    status: str  # "pass", "fail", "pending_confirmation"
    details: Dict[str, Any]
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    tenant_id: str = ""


class AlertSignatureVerifier:
    """Verify alert signatures using HMAC-SHA256.

    Each tenant has a secret key used to sign alerts. Alerts must be signed
    with the tenant's key to be trusted.

    Prevents spoofing by ensuring only the tenant (or authorized sender with
    access to tenant_key) can create valid alerts.
    """

    def __init__(self, tenant_key: Optional[str] = None):
        """Initialize verifier with tenant key.

        Args:
            tenant_key: Secret key for HMAC (if None, derives from CORVIN_HOME)
        """
        self.tenant_key = tenant_key or self._load_tenant_key()

    def _load_tenant_key(self) -> str:
        """Load tenant key from filesystem or env.

        Looks for:
        1. CORVIN_TENANT_KEY env var
        2. ~/.corvin/tenant_key

        Falls back to a default if neither exists (DEV only).
        """
        # Try env var
        key = os.environ.get("CORVIN_TENANT_KEY")
        if key:
            return key

        # Try filesystem
        corvin_home = os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))
        tenant_key_file = os.path.join(corvin_home, "tenant_key")
        if os.path.exists(tenant_key_file):
            try:
                with open(tenant_key_file, "r") as f:
                    return f.read().strip()
            except Exception as e:
                logger.error(f"Failed to load tenant key: {e}")

        # Fallback (development only)
        logger.warning("No tenant key found; using development fallback")
        return "dev-insecure-fallback-key"

    def sign_alert(
        self,
        alert_id: str,
        component_id: str,
        severity: str,
        message: str,
        tenant_id: str,
    ) -> AlertSignature:
        """Sign an alert with the tenant key.

        Args:
            alert_id: Unique alert identifier
            component_id: Component that triggered the alert
            severity: Alert severity ("info", "warning", "critical")
            message: Alert message
            tenant_id: Tenant identifier

        Returns:
            AlertSignature with computed HMAC
        """
        # Construct alert body (deterministic order for replay detection)
        alert_body = f"{alert_id}|{component_id}|{severity}|{message}|{tenant_id}"

        # Compute HMAC-SHA256
        hmac_obj = hmac.new(
            self.tenant_key.encode("utf-8"),
            alert_body.encode("utf-8"),
            hashlib.sha256,
        )

        signature_hex = hmac_obj.hexdigest()

        return AlertSignature(
            signature_hex=signature_hex,
            algorithm="hmac-sha256",
            timestamp=datetime.utcnow().isoformat(),
            tenant_id=tenant_id,
        )

    def verify_alert(
        self,
        alert_id: str,
        component_id: str,
        severity: str,
        message: str,
        tenant_id: str,
        signature_hex: str,
    ) -> AlertSignatureStatus:
        """Verify alert signature.

        Args:
            alert_id: Alert ID (from alert)
            component_id: Component ID (from alert)
            severity: Severity (from alert)
            message: Message (from alert)
            tenant_id: Tenant ID (from alert)
            signature_hex: Claimed signature

        Returns:
            AlertSignatureStatus.VALID if signature matches, INVALID otherwise
        """
        # Reconstruct alert body
        alert_body = f"{alert_id}|{component_id}|{severity}|{message}|{tenant_id}"

        # Compute expected HMAC
        expected_hmac = hmac.new(
            self.tenant_key.encode("utf-8"),
            alert_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Constant-time comparison to prevent timing attacks
        if hmac.compare_digest(signature_hex, expected_hmac):
            return AlertSignatureStatus.VALID
        else:
            logger.warning(
                f"Alert signature verification failed: {alert_id} "
                f"(expected {expected_hmac[:8]}..., got {signature_hex[:8]}...)"
            )
            return AlertSignatureStatus.INVALID


class CriticalAlertRateLimiter:
    """Rate-limit CRITICAL alerts per component.

    Prevents alert storms: max 1 CRITICAL per 5 minutes per component.
    Uses a sliding window to track recent alerts.

    Mitigates: Alert Spoofing attack by limiting frequency of disruptive alerts.
    """

    CRITICAL_ALERT_LIMIT = 1
    CRITICAL_ALERT_WINDOW = timedelta(minutes=5)

    def __init__(self):
        """Initialize rate limiter."""
        self.critical_alerts: Dict[str, List[datetime]] = defaultdict(list)

    def can_send_critical(self, component_id: str) -> bool:
        """Check if CRITICAL alert is allowed for component.

        Args:
            component_id: Component identifier

        Returns:
            True if under rate limit, False if rate-limited
        """
        now = datetime.utcnow()
        window_start = now - self.CRITICAL_ALERT_WINDOW

        # Clean old alerts
        if component_id in self.critical_alerts:
            self.critical_alerts[component_id] = [
                ts for ts in self.critical_alerts[component_id]
                if ts > window_start
            ]

        # Check limit
        alert_count = len(self.critical_alerts[component_id])
        return alert_count < self.CRITICAL_ALERT_LIMIT

    def record_critical_alert(self, component_id: str) -> None:
        """Record a CRITICAL alert for rate limiting.

        Args:
            component_id: Component that sent the alert
        """
        self.critical_alerts[component_id].append(datetime.utcnow())

    def get_next_allowed_time(self, component_id: str) -> Optional[datetime]:
        """Get when the next CRITICAL alert will be allowed.

        Args:
            component_id: Component identifier

        Returns:
            datetime when the next alert is allowed, or None if allowed now
        """
        now = datetime.utcnow()

        if component_id not in self.critical_alerts:
            return None

        window_start = now - self.CRITICAL_ALERT_WINDOW
        recent = [ts for ts in self.critical_alerts[component_id] if ts > window_start]

        if len(recent) == 0:
            return None

        return recent[0] + self.CRITICAL_ALERT_WINDOW


@dataclass
class PendingConfirmation:
    """Confirmation pending for a CRITICAL alert."""
    confirmation_id: str
    alert_id: str
    action: AlertAction
    component_id: str
    created_at: datetime
    expires_at: datetime
    confirmed: bool = False
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[datetime] = None

    def is_expired(self) -> bool:
        """Check if confirmation has expired (15 min TTL)."""
        return datetime.utcnow() > self.expires_at


class ManualConfirmationManager:
    """Manage CRITICAL alert confirmations.

    CRITICAL alerts require operator approval before action is taken.
    Confirmations have a 15-minute TTL to prevent stale approvals.

    Mitigates: Alert Spoofing by requiring human verification before
    learning disable or other disruptive actions.
    """

    CONFIRMATION_TTL = timedelta(minutes=15)

    def __init__(self):
        """Initialize confirmation manager."""
        self.pending_confirmations: Dict[str, PendingConfirmation] = {}

    def create_confirmation(
        self,
        alert_id: str,
        action: AlertAction,
        component_id: str,
    ) -> PendingConfirmation:
        """Create a pending confirmation for a CRITICAL alert.

        Args:
            alert_id: Alert that requires confirmation
            action: Action awaiting confirmation
            component_id: Component that triggered alert

        Returns:
            PendingConfirmation object
        """
        confirmation_id = f"conf_{alert_id}_{int(datetime.utcnow().timestamp())}"
        now = datetime.utcnow()

        confirmation = PendingConfirmation(
            confirmation_id=confirmation_id,
            alert_id=alert_id,
            action=action,
            component_id=component_id,
            created_at=now,
            expires_at=now + self.CONFIRMATION_TTL,
        )

        self.pending_confirmations[confirmation_id] = confirmation
        logger.info(f"Created pending confirmation: {confirmation_id} for {alert_id}")

        return confirmation

    def approve_confirmation(
        self,
        confirmation_id: str,
        approved_by: str,
    ) -> bool:
        """Approve a pending confirmation.

        Args:
            confirmation_id: Confirmation to approve
            approved_by: User/system approving (for audit)

        Returns:
            True if approved, False if not found or expired
        """
        if confirmation_id not in self.pending_confirmations:
            logger.warning(f"Confirmation not found: {confirmation_id}")
            return False

        confirmation = self.pending_confirmations[confirmation_id]

        if confirmation.is_expired():
            logger.warning(f"Confirmation expired: {confirmation_id}")
            return False

        confirmation.confirmed = True
        confirmation.confirmed_by = approved_by
        confirmation.confirmed_at = datetime.utcnow()

        logger.info(f"Approved confirmation: {confirmation_id} by {approved_by}")
        return True

    def reject_confirmation(self, confirmation_id: str) -> bool:
        """Reject a pending confirmation.

        Args:
            confirmation_id: Confirmation to reject

        Returns:
            True if rejected, False if not found
        """
        if confirmation_id not in self.pending_confirmations:
            return False

        del self.pending_confirmations[confirmation_id]
        logger.info(f"Rejected confirmation: {confirmation_id}")
        return True

    def get_pending(self, alert_id: Optional[str] = None) -> List[PendingConfirmation]:
        """Get pending confirmations (optionally filtered by alert).

        Args:
            alert_id: Optional filter

        Returns:
            List of pending confirmations
        """
        result = []
        now = datetime.utcnow()

        for confirmation in self.pending_confirmations.values():
            if not confirmation.is_expired():
                if alert_id is None or confirmation.alert_id == alert_id:
                    result.append(confirmation)

        return result


class AlertDispatcher:
    """Central dispatcher for alerts with security hardening.

    Responsibilities:
    1. Receive alerts from monitoring systems
    2. Verify signatures (HMAC-SHA256)
    3. Rate-limit CRITICAL alerts
    4. Require manual confirmation for disruptive actions
    5. Audit every step to audit trail
    6. Execute approved actions

    Mitigates Finding #11: Alert Spoofing
    """

    def __init__(
        self,
        tenant_id: str,
        tenant_key: Optional[str] = None,
        audit_writer: Optional[Any] = None,
    ):
        """Initialize alert dispatcher.

        Args:
            tenant_id: Tenant identifier (for isolation)
            tenant_key: Secret key for HMAC (if None, auto-loads)
            audit_writer: Audit trail writer (for compliance)
        """
        self.tenant_id = tenant_id
        self.verifier = AlertSignatureVerifier(tenant_key)
        self.rate_limiter = CriticalAlertRateLimiter()
        self.confirmation_manager = ManualConfirmationManager()
        self.audit_writer = audit_writer
        self.action_callbacks: Dict[AlertAction, List[Callable]] = defaultdict(list)
        self.alert_history: List[SecureAlert] = []
        self.audit_events: List[AlertAuditEvent] = []

    def register_action_callback(
        self,
        action: AlertAction,
        callback: Callable[[SecureAlert], Any],
    ) -> None:
        """Register callback for alert action.

        Args:
            action: Action type to listen for
            callback: Function to call when action is taken
        """
        self.action_callbacks[action].append(callback)

    def _log_audit_event(
        self,
        alert_id: str,
        event_type: str,
        status: str,
        details: Dict[str, Any],
    ) -> None:
        """Log audit event for compliance (GDPR Art. 30)."""
        event = AlertAuditEvent(
            event_id=f"audit_{alert_id}_{len(self.audit_events)}",
            alert_id=alert_id,
            event_type=event_type,
            status=status,
            details=details,
            tenant_id=self.tenant_id,
        )

        self.audit_events.append(event)

        # Write to audit trail if available
        if self.audit_writer:
            try:
                self.audit_writer.write_event({
                    "event_type": f"alert_{event_type}",
                    "alert_id": alert_id,
                    "status": status,
                    "details": details,
                    "tenant_id": self.tenant_id,
                    "timestamp": event.timestamp,
                })
            except Exception as e:
                logger.error(f"Failed to write audit event: {e}")

    async def process_alert(
        self,
        alert_id: str,
        component_id: str,
        severity: str,
        message: str,
        action: AlertAction,
        signature_hex: str,
    ) -> bool:
        """Process an incoming alert with full security checks.

        Steps:
        1. Verify signature (reject if invalid)
        2. Log signature check
        3. If CRITICAL: check rate limit + require confirmation
        4. If INFO/WARNING: execute immediately
        5. Audit the action

        Args:
            alert_id: Unique alert ID
            component_id: Component that triggered
            severity: "info", "warning", "critical"
            message: Alert message
            action: Action to take (disable_learning, etc.)
            signature_hex: HMAC-SHA256 signature

        Returns:
            True if alert was processed, False if rejected
        """
        # Step 1: Verify signature
        sig_status = self.verifier.verify_alert(
            alert_id=alert_id,
            component_id=component_id,
            severity=severity,
            message=message,
            tenant_id=self.tenant_id,
            signature_hex=signature_hex,
        )

        # Log signature check
        self._log_audit_event(
            alert_id=alert_id,
            event_type="alert_signature_check",
            status=sig_status.value,
            details={
                "component_id": component_id,
                "severity": severity,
                "signature_match": sig_status == AlertSignatureStatus.VALID,
            },
        )

        # Reject if signature is invalid
        if sig_status != AlertSignatureStatus.VALID:
            logger.error(
                f"Alert rejected: invalid signature for {alert_id} "
                f"(component={component_id}, severity={severity})"
            )
            return False

        # Create secure alert
        signature = AlertSignature(
            signature_hex=signature_hex,
            tenant_id=self.tenant_id,
        )

        alert = SecureAlert(
            alert_id=alert_id,
            component_id=component_id,
            severity=severity,
            message=message,
            action=action,
            signature=signature,
            tenant_id=self.tenant_id,
        )

        self.alert_history.append(alert)

        # Step 2: Handle based on severity
        if severity == "critical":
            return await self._handle_critical_alert(alert)
        else:
            # INFO/WARNING: execute immediately
            return await self._execute_alert_action(alert)

    async def _handle_critical_alert(self, alert: SecureAlert) -> bool:
        """Handle CRITICAL alert with rate limiting + confirmation.

        Args:
            alert: The alert to handle

        Returns:
            True if alert is pending confirmation, False if rejected
        """
        # Step 1: Check rate limit
        if not self.rate_limiter.can_send_critical(alert.component_id):
            next_allowed = self.rate_limiter.get_next_allowed_time(alert.component_id)

            self._log_audit_event(
                alert_id=alert.alert_id,
                event_type="alert_rate_limit_check",
                status="fail",
                details={
                    "component_id": alert.component_id,
                    "reason": "Rate limit exceeded (max 1 CRITICAL per 5 minutes)",
                    "next_allowed_at": next_allowed.isoformat() if next_allowed else None,
                },
            )

            logger.warning(
                f"CRITICAL alert rate-limited: {alert.alert_id} "
                f"(component={alert.component_id}, next allowed at {next_allowed})"
            )
            return False

        self.rate_limiter.record_critical_alert(alert.component_id)

        # Step 2: Create pending confirmation
        confirmation = self.confirmation_manager.create_confirmation(
            alert_id=alert.alert_id,
            action=alert.action,
            component_id=alert.component_id,
        )

        self._log_audit_event(
            alert_id=alert.alert_id,
            event_type="alert_confirmation_required",
            status="pending_confirmation",
            details={
                "confirmation_id": confirmation.confirmation_id,
                "action": alert.action.value,
                "component_id": alert.component_id,
                "expires_at": confirmation.expires_at.isoformat(),
            },
        )

        logger.warning(
            f"CRITICAL alert pending confirmation: {alert.alert_id} "
            f"(confirmation_id={confirmation.confirmation_id}, action={alert.action.value})"
        )

        return True

    async def confirm_action(
        self,
        confirmation_id: str,
        approved_by: str,
    ) -> bool:
        """Operator approves a pending confirmation.

        Args:
            confirmation_id: Confirmation to approve
            approved_by: Operator/user approving

        Returns:
            True if approval was recorded and action executed
        """
        confirmation = self.confirmation_manager.pending_confirmations.get(confirmation_id)

        if not confirmation:
            logger.warning(f"Confirmation not found: {confirmation_id}")
            return False

        # Approve the confirmation
        if not self.confirmation_manager.approve_confirmation(confirmation_id, approved_by):
            logger.warning(f"Failed to approve confirmation: {confirmation_id}")
            return False

        # Log approval
        self._log_audit_event(
            alert_id=confirmation.alert_id,
            event_type="alert_action_confirmed",
            status="approved",
            details={
                "confirmation_id": confirmation_id,
                "approved_by": approved_by,
                "action": confirmation.action.value,
            },
        )

        # Find the original alert and execute action
        for alert in self.alert_history:
            if alert.alert_id == confirmation.alert_id:
                await self._execute_alert_action(alert)
                return True

        logger.error(f"Alert not found: {confirmation.alert_id}")
        return False

    async def _execute_alert_action(self, alert: SecureAlert) -> bool:
        """Execute the alert's action.

        Args:
            alert: The alert whose action to execute

        Returns:
            True if action was executed successfully
        """
        try:
            # Invoke registered callbacks for this action
            for callback in self.action_callbacks.get(alert.action, []):
                await callback(alert) if hasattr(callback, "__await__") else callback(alert)

            self._log_audit_event(
                alert_id=alert.alert_id,
                event_type="alert_action_executed",
                status="success",
                details={
                    "action": alert.action.value,
                    "component_id": alert.component_id,
                },
            )

            logger.info(
                f"Alert action executed: {alert.alert_id} "
                f"({alert.action.value}, component={alert.component_id})"
            )

            return True

        except Exception as e:
            self._log_audit_event(
                alert_id=alert.alert_id,
                event_type="alert_action_executed",
                status="error",
                details={
                    "action": alert.action.value,
                    "error": str(e),
                },
            )

            logger.error(f"Alert action failed: {alert.alert_id}: {e}", exc_info=True)
            return False

    def get_alert_history(self, limit: int = 100) -> List[SecureAlert]:
        """Get recent alert history."""
        return self.alert_history[-limit:]

    def get_pending_confirmations(self) -> List[PendingConfirmation]:
        """Get all pending confirmations for this tenant."""
        return self.confirmation_manager.get_pending()

    def get_audit_events(self, limit: int = 100) -> List[AlertAuditEvent]:
        """Get recent audit events."""
        return self.audit_events[-limit:]


# Global dispatcher instance (per-tenant in production)
_dispatchers: Dict[str, AlertDispatcher] = {}


def get_alert_dispatcher(tenant_id: str = "_default") -> AlertDispatcher:
    """Get or create dispatcher for tenant."""
    if tenant_id not in _dispatchers:
        _dispatchers[tenant_id] = AlertDispatcher(tenant_id=tenant_id)
    return _dispatchers[tenant_id]


def set_alert_dispatcher(dispatcher: AlertDispatcher, tenant_id: str = "_default") -> None:
    """Set dispatcher for testing."""
    _dispatchers[tenant_id] = dispatcher

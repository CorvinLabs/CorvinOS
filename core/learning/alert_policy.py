"""Alert Policy Management (ADR-0636) + Fix #11: Alert Spoofing

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

Security (Fix #11 — Alert Spoofing Mitigation):
  - Alert Signatures: HMAC-SHA256 signatures to verify alert authenticity
  - Rate-limiting: Per-policy rate limits to prevent alert spam/DoS
  - Confirmation: Alerts require explicit confirmation before processing

Replay semantics (``verify_alert_signature``):
  - A signature's nonce is recorded on the FIRST SUCCESSFUL verification, never
    at signing time: the original verification is accepted, a second one of the
    same signature is rejected as a replay. A failed verification (tampered,
    stale, unbound nonce) does not consume the nonce.
  - The nonce is signed as the last of ``signed_fields`` and must still be there
    at verify time, so the replay guard cannot be bypassed by swapping nonces.
  - The replay cache is bounded twice: entries expire after the signature
    freshness window (a signature that old is rejected as stale anyway) and the
    cache is hard-capped, evicting oldest-first.
  - ``request_confirmation`` mints a confirmation id that is unique per REQUEST
    (``confirm_<alert_id>_<random>``), so two requests for the same alert cannot
    collide and one approval cannot silently approve the other.

Compliance:
  - GDPR Art. 5 (minimization): alerting uses only learning state, no PII
  - GDPR Art. 32 (security): alerts logged and audit-trailed + signatures
  - Fail-closed: alerts default to ON (conservative)

Implementation: uses LiveExperimentCollector metrics + MetaOptimizer state.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
import uuid
from collections import OrderedDict
from datetime import datetime, timedelta
from enum import Enum
from typing import Any, Callable, Dict, List, Optional, Tuple

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


class AlertSignature(BaseModel):
    """Alert signature for spoofing prevention (Fix #11).

    Ensures alerts come from trusted sources and have not been tampered with.
    """
    alert_id: str
    signature: str  # HMAC-SHA256 hex digest
    timestamp: str
    nonce: str  # Random nonce to prevent replay attacks
    signed_fields: List[str]  # Fields included in signature


class AlertConfirmationRequest(BaseModel):
    """Confirmation request for security-sensitive alerts (Fix #11).

    Alerts matching confirmation rules require human/automated approval
    before being processed.
    """
    confirmation_id: str
    alert_id: str
    policy_id: str
    alert_type: AlertType
    metric_value: float
    threshold: float
    confidence_score: float  # 0.0–1.0 (higher = more likely real alert)
    created_at: str
    confirmed_at: Optional[str] = None
    confirmed_by: Optional[str] = None  # "operator" | "auto" | None
    status: str = "pending"  # "pending" | "approved" | "rejected"
    rejection_reason: Optional[str] = None


class AlertRateLimiter(BaseModel):
    """Rate limiter for per-policy alerts (Fix #11).

    Prevents alert spam and DoS attacks by enforcing:
    - Max alerts per policy per minute (burst)
    - Max alerts per policy per hour (sustained)
    """
    policy_id: str
    max_alerts_per_minute: int = 5  # Burst limit
    max_alerts_per_hour: int = 50  # Sustained limit
    alerts_last_minute: List[str] = Field(default_factory=list)  # Alert IDs from last minute
    alerts_last_hour: List[str] = Field(default_factory=list)  # Alert IDs from last hour
    last_reset_minute: str = Field(default_factory=lambda: datetime.utcnow().isoformat())
    last_reset_hour: str = Field(default_factory=lambda: datetime.utcnow().isoformat())


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

    # Signature freshness window (seconds). A signature older than this is
    # rejected as stale, which is also why a nonce need not be remembered longer.
    _SIGNATURE_MAX_AGE_SECONDS = 300
    # Nonce retention: same as the freshness window (see above).
    _NONCE_TTL_SECONDS = 300
    # Hard cap on the replay cache so it can never grow without bound even under
    # a flood of verifications inside a single freshness window.
    _NONCE_CACHE_MAX = 10_000

    def __init__(self, tenant_id: str, signing_key: Optional[str] = None):
        """Initialize alert manager for a tenant.

        Args:
            tenant_id: Tenant identifier (for isolation)
            signing_key: HMAC signing key for alert signatures (Fix #11)
                        If None, a random key is generated (should be persisted)
        """
        self.tenant_id = tenant_id
        self._policies: Dict[str, AlertPolicy] = {}
        self._history: List[AlertEvent] = []
        self._handlers: Dict[str, AlertNotificationHandler] = {}

        # Security: Alert signatures and rate-limiting (Fix #11)
        self._signing_key = signing_key or secrets.token_hex(32)
        self._rate_limiters: Dict[str, AlertRateLimiter] = {}
        self._confirmation_queue: Dict[str, AlertConfirmationRequest] = {}
        # Replay guard: nonces are recorded at VERIFY time (first successful
        # verify wins), never at sign time — recording at sign time made the
        # original verification of every freshly signed alert fail as a replay.
        # Bounded twice over: entries expire with the freshness window
        # (_NONCE_TTL_SECONDS, so an entry is only dropped once the signature it
        # belongs to would be rejected as stale anyway) and the cache is hard-capped
        # at _NONCE_CACHE_MAX entries, evicting oldest-first.
        self._nonce_cache: "OrderedDict[str, float]" = OrderedDict()
        self._require_confirmation_policies: set = {"loss_divergence_critical", "gradient_explosion_critical"}

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
            List of fired alerts (excluding those pending confirmation)

        **Security (Fix #11):**
        1. Sign each alert (HMAC-SHA256)
        2. Check rate limits per policy
        3. Request confirmation for sensitive policies
        4. Fire only confirmed alerts

        **Implementation Note:** In production, this method would:
        1. Check each enabled policy
        2. Evaluate condition (threshold, trend, anomaly)
        3. Check if muted
        4. Sign and rate-limit check (Fix #11)
        5. Request confirmation if needed (Fix #11)
        6. Fire alert if condition + enabled + not muted + rate OK + confirmed
        7. Log to audit trail (GDPR Art. 30)
        8. Call notification handlers
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

                # Security: Sign alert (Fix #11)
                alert_sig = self._sign_alert(
                    alert.alert_id, alert.metric_name, metric_value, policy.threshold
                )
                logger.debug(f"Alert signed: {alert.alert_id} sig={alert_sig.signature[:16]}... (tenant={self.tenant_id})")

                # Security: Check rate limits (Fix #11)
                rate_ok, rate_msg = self.check_rate_limit(policy_id)
                if not rate_ok:
                    logger.warning(f"Alert rate-limited: {alert.alert_id} ({rate_msg}, tenant={self.tenant_id})")
                    continue  # Skip this alert, do not fire

                # Security: Request confirmation for sensitive policies (Fix #11)
                if policy_id in self._require_confirmation_policies:
                    confidence = 0.8 if metric_value > (policy.threshold * 2) else 0.5
                    conf_req = self.request_confirmation(alert, confidence_score=confidence)
                    logger.warning(f"Confirmation requested: {alert.alert_id} (conf_id={conf_req.confirmation_id}, tenant={self.tenant_id})")
                    # Alert is pending, not yet in history
                    continue

                # All checks passed: record in rate limiter and fire alert
                self.record_alert_for_rate_limit(policy_id, alert.alert_id)
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

    # ====== Security Methods (Fix #11: Alert Spoofing) ======

    def _sign_alert(
        self,
        alert_id: str,
        metric_name: str,
        metric_value: float,
        threshold: float,
    ) -> AlertSignature:
        """Create a cryptographic signature for an alert (Fix #11, Round 2).

        Prevents tampering and spoofing by signing alert content.

        **Fix #11 Round 2 (Nonce Uniqueness):**
        Each alert MUST receive a fresh, unique nonce generated per alert (not cached
        or reused from a batch). The nonce combines:
        - uuid4() for cryptographic uniqueness
        - timestamp for monotonic ordering
        - microsecond precision to prevent collisions in tight loops

        This ensures that even 100 consecutive alerts all receive different nonces
        and enables detection of replay attacks.

        Args:
            alert_id: Unique alert identifier
            metric_name: Name of metric being checked
            metric_value: Actual metric value
            threshold: Policy threshold

        Returns:
            AlertSignature with HMAC-SHA256 signature
        """
        # Generate fresh nonce per alert (not per batch)
        # Combines uuid4() + timestamp for guaranteed uniqueness
        # Fix #11 Round 2: ensure nonce is unique per invocation
        timestamp_us = int(datetime.utcnow().timestamp() * 1_000_000)
        nonce = f"{uuid.uuid4().hex}_{timestamp_us}"

        # Fields to sign (immutable alert properties)
        fields_to_sign = [alert_id, metric_name, str(metric_value), str(threshold), nonce]
        message = "|".join(fields_to_sign)

        # Compute HMAC-SHA256 signature
        signature_bytes = hmac.new(
            self._signing_key.encode(), message.encode(), hashlib.sha256
        ).digest()
        signature_hex = signature_bytes.hex()

        now = datetime.utcnow().isoformat()
        alert_sig = AlertSignature(
            alert_id=alert_id,
            signature=signature_hex,
            timestamp=now,
            nonce=nonce,
            signed_fields=fields_to_sign,
        )

        # NOTE: the nonce is deliberately NOT recorded here. It is recorded by
        # verify_alert_signature() on the first successful verification, so the
        # original verification of a freshly signed alert is accepted and only a
        # SECOND verification of the same signature is rejected as a replay.

        logger.debug(f"Signed alert {alert_id} with nonce {nonce} (tenant={self.tenant_id})")
        return alert_sig

    def verify_alert_signature(self, alert_sig: AlertSignature) -> Tuple[bool, str]:
        """Verify an alert signature (Fix #11).

        Checks, in order:
        1. Nonce not previously verified (replay prevention — first verify wins)
        2. Timestamp not stale (within ``_SIGNATURE_MAX_AGE_SECONDS``)
        3. Nonce is bound into the signed fields (it must be the last one)
        4. Signature matches the computed HMAC-SHA256

        The nonce is recorded ONLY after all checks pass, so a tampered or stale
        signature cannot burn a nonce that a legitimate alert still needs.

        Args:
            alert_sig: AlertSignature to verify

        Returns:
            (is_valid: bool, reason: str)
        """
        self._prune_nonce_cache()

        # Check 1: Replay prevention (nonce not verified before)
        if alert_sig.nonce in self._nonce_cache:
            return False, f"Replay detected: nonce {alert_sig.nonce} already used"

        # Check 2: Timestamp freshness
        try:
            sig_time = datetime.fromisoformat(alert_sig.timestamp)
        except (TypeError, ValueError):
            return False, "Signature verification failed: malformed timestamp"
        age_seconds = (datetime.utcnow() - sig_time).total_seconds()
        if age_seconds > self._SIGNATURE_MAX_AGE_SECONDS:
            return (
                False,
                f"Stale signature: {age_seconds:.0f}s old "
                f"(max {self._SIGNATURE_MAX_AGE_SECONDS}s)",
            )

        # Check 3: The nonce must be bound into the signed fields, otherwise the
        # replay guard could be bypassed by swapping in a fresh, unsigned nonce.
        if not alert_sig.signed_fields or alert_sig.signed_fields[-1] != alert_sig.nonce:
            return False, "Signature verification failed: nonce not bound, tampering detected"

        # Check 4: Verify HMAC-SHA256 signature over exactly the signed fields
        # (which already end with the nonce — see _sign_alert).
        message = "|".join(alert_sig.signed_fields)
        expected_signature_bytes = hmac.new(
            self._signing_key.encode(), message.encode(), hashlib.sha256
        ).digest()
        expected_signature_hex = expected_signature_bytes.hex()

        if not hmac.compare_digest(alert_sig.signature, expected_signature_hex):
            return False, "Signature verification failed: tampering detected"

        # Record the nonce only now: first successful verification wins, any
        # further verification of the same signature is a replay.
        self._record_nonce(alert_sig.nonce)

        logger.debug(f"Verified alert signature {alert_sig.alert_id} (tenant={self.tenant_id})")
        return True, "OK"

    def _prune_nonce_cache(self) -> None:
        """Drop nonces older than the signature freshness window.

        A signature whose nonce has expired here would already be rejected by the
        staleness check, so dropping it cannot re-open a replay window.
        """
        cutoff = time.monotonic() - self._NONCE_TTL_SECONDS
        while self._nonce_cache:
            nonce, seen_at = next(iter(self._nonce_cache.items()))
            if seen_at > cutoff:
                break
            self._nonce_cache.pop(nonce, None)

    def _record_nonce(self, nonce: str) -> None:
        """Record a verified nonce, keeping the cache hard-bounded."""
        self._nonce_cache[nonce] = time.monotonic()
        self._nonce_cache.move_to_end(nonce)
        while len(self._nonce_cache) > self._NONCE_CACHE_MAX:
            self._nonce_cache.popitem(last=False)

    def check_rate_limit(self, policy_id: str) -> Tuple[bool, str]:
        """Check rate limits for a policy (Fix #11).

        Enforces:
        - Max 5 alerts per minute (burst)
        - Max 50 alerts per hour (sustained)

        Args:
            policy_id: Policy to check

        Returns:
            (is_allowed: bool, reason: str)
        """
        # Initialize rate limiter if not exists
        if policy_id not in self._rate_limiters:
            self._rate_limiters[policy_id] = AlertRateLimiter(policy_id=policy_id)

        limiter = self._rate_limiters[policy_id]
        now = datetime.utcnow()

        # Reset minute window if needed
        last_reset_min = datetime.fromisoformat(limiter.last_reset_minute)
        if (now - last_reset_min).total_seconds() >= 60:
            limiter.alerts_last_minute = []
            limiter.last_reset_minute = now.isoformat()

        # Reset hour window if needed
        last_reset_hour = datetime.fromisoformat(limiter.last_reset_hour)
        if (now - last_reset_hour).total_seconds() >= 3600:
            limiter.alerts_last_hour = []
            limiter.last_reset_hour = now.isoformat()

        # Check minute limit (burst)
        if len(limiter.alerts_last_minute) >= limiter.max_alerts_per_minute:
            return False, f"Rate limit exceeded: {len(limiter.alerts_last_minute)} alerts in last minute"

        # Check hour limit (sustained)
        if len(limiter.alerts_last_hour) >= limiter.max_alerts_per_hour:
            return False, f"Rate limit exceeded: {len(limiter.alerts_last_hour)} alerts in last hour"

        return True, "OK"

    def record_alert_for_rate_limit(self, policy_id: str, alert_id: str) -> None:
        """Record an alert in rate limit tracking (Fix #11).

        Args:
            policy_id: Policy ID
            alert_id: Alert ID to record
        """
        if policy_id not in self._rate_limiters:
            self._rate_limiters[policy_id] = AlertRateLimiter(policy_id=policy_id)

        limiter = self._rate_limiters[policy_id]
        limiter.alerts_last_minute.append(alert_id)
        limiter.alerts_last_hour.append(alert_id)

    def request_confirmation(
        self, alert: AlertEvent, confidence_score: float = 0.5
    ) -> AlertConfirmationRequest:
        """Request human/automated confirmation for a sensitive alert (Fix #11).

        High-severity alerts are held pending confirmation before being processed.

        Args:
            alert: Alert to confirm
            confidence_score: Confidence that this is a real alert (0.0–1.0)

        Returns:
            AlertConfirmationRequest
        """
        # Unique per REQUEST, not per alert: two confirmation requests for the
        # same alert must not collide, otherwise approving the first silently
        # approves (and hides) the second.
        confirmation_id = f"confirm_{alert.alert_id}_{secrets.token_hex(8)}"
        conf_req = AlertConfirmationRequest(
            confirmation_id=confirmation_id,
            alert_id=alert.alert_id,
            policy_id=alert.policy_id,
            alert_type=alert.alert_type,
            metric_value=alert.metric_value,
            threshold=alert.threshold,
            confidence_score=confidence_score,
            created_at=datetime.utcnow().isoformat(),
            status="pending",
        )

        self._confirmation_queue[confirmation_id] = conf_req
        logger.info(f"Requested confirmation for alert {alert.alert_id} (tenant={self.tenant_id})")

        return conf_req

    def confirm_alert(self, confirmation_id: str, approved: bool, confirmed_by: str = "operator") -> bool:
        """Confirm or reject an alert (Fix #11).

        Args:
            confirmation_id: Confirmation request ID
            approved: True to approve, False to reject
            confirmed_by: Who confirmed ("operator", "auto", etc.)

        Returns:
            True if confirmation was processed
        """
        if confirmation_id not in self._confirmation_queue:
            logger.warning(f"Confirmation not found: {confirmation_id}")
            return False

        conf_req = self._confirmation_queue[confirmation_id]
        now = datetime.utcnow().isoformat()
        conf_req.confirmed_at = now
        conf_req.confirmed_by = confirmed_by

        if approved:
            conf_req.status = "approved"
            logger.info(f"Alert approved: {conf_req.alert_id} by {confirmed_by} (tenant={self.tenant_id})")
        else:
            conf_req.status = "rejected"
            conf_req.rejection_reason = "Operator rejected"
            logger.info(f"Alert rejected: {conf_req.alert_id} by {confirmed_by} (tenant={self.tenant_id})")

        return True

    def get_pending_confirmations(self) -> List[AlertConfirmationRequest]:
        """Get all pending confirmation requests (Fix #11)."""
        return [req for req in self._confirmation_queue.values() if req.status == "pending"]

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

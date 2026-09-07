"""Test Suite for Security Fix #11: Alert Spoofing Prevention

Tests the alert dispatcher's security hardening against alert spoofing attacks.

Finding #11: "Alert Spoofing: Fake CRITICAL alert disables learning"

Mitigations tested:
  1. Alert Signatures (HMAC-SHA256) - prevent forging alerts
  2. Rate Limiting (1 CRITICAL per 5 minutes) - prevent alert storms
  3. Manual Confirmation (CRITICAL alerts) - require operator approval
  4. Audit Trail - log every alert + signature check + action
"""

import asyncio
import hashlib
import hmac
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.learning.alert_dispatcher import (
    AlertDispatcher,
    AlertSignatureVerifier,
    AlertSignatureStatus,
    AlertAction,
    CriticalAlertRateLimiter,
    ManualConfirmationManager,
    SecureAlert,
    AlertSignature,
    get_alert_dispatcher,
    set_alert_dispatcher,
)


# ============================================================================
# FIXTURE: Test Tenant Key
# ============================================================================

@pytest.fixture
def test_tenant_key():
    """Test tenant key for signing alerts."""
    return "test-tenant-secret-key-12345"


@pytest.fixture
def test_tenant_id():
    """Test tenant identifier."""
    return "test-tenant"


# ============================================================================
# TEST GROUP 1: Alert Signature Verification
# ============================================================================

class TestAlertSignatureValid:
    """Test valid alert signatures are accepted."""

    def test_alert_signature_valid_verification_passes(self, test_tenant_key, test_tenant_id):
        """Test: Valid signature verifies successfully."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        alert_id = "alert_001"
        component_id = "learning_core"
        severity = "critical"
        message = "Loss divergence detected"

        # Create a valid signature
        signature = verifier.sign_alert(
            alert_id=alert_id,
            component_id=component_id,
            severity=severity,
            message=message,
            tenant_id=test_tenant_id,
        )

        # Verify it passes
        result = verifier.verify_alert(
            alert_id=alert_id,
            component_id=component_id,
            severity=severity,
            message=message,
            tenant_id=test_tenant_id,
            signature_hex=signature.signature_hex,
        )

        assert result == AlertSignatureStatus.VALID

    def test_alert_signature_multiple_alerts_different_signatures(self, test_tenant_key, test_tenant_id):
        """Test: Different alerts produce different signatures."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        # Create two different alerts
        sig1 = verifier.sign_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message="Loss divergence",
            tenant_id=test_tenant_id,
        )

        sig2 = verifier.sign_alert(
            alert_id="alert_002",
            component_id="learning_core",
            severity="critical",
            message="Loss divergence",
            tenant_id=test_tenant_id,
        )

        # Signatures should be different (different alert_id)
        assert sig1.signature_hex != sig2.signature_hex

    def test_alert_signature_same_alert_same_signature(self, test_tenant_key, test_tenant_id):
        """Test: Same alert produces same signature (deterministic)."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        # Create same alert twice
        sig1 = verifier.sign_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message="Loss divergence",
            tenant_id=test_tenant_id,
        )

        sig2 = verifier.sign_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message="Loss divergence",
            tenant_id=test_tenant_id,
        )

        # Signatures should be identical
        assert sig1.signature_hex == sig2.signature_hex


# ============================================================================
# TEST GROUP 2: Invalid Alert Signatures (Spoofing Attempts)
# ============================================================================

class TestAlertSignatureInvalid:
    """Test invalid/forged alert signatures are rejected."""

    def test_alert_signature_invalid_wrong_signature_rejected(self, test_tenant_key, test_tenant_id):
        """Test: Alert with wrong signature is rejected."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        alert_id = "alert_001"
        component_id = "learning_core"
        severity = "critical"
        message = "Loss divergence"

        # Create a signature with wrong key
        wrong_key = "wrong-tenant-key"
        wrong_hmac = hmac.new(
            wrong_key.encode("utf-8"),
            f"{alert_id}|{component_id}|{severity}|{message}|{test_tenant_id}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Verify should reject
        result = verifier.verify_alert(
            alert_id=alert_id,
            component_id=component_id,
            severity=severity,
            message=message,
            tenant_id=test_tenant_id,
            signature_hex=wrong_hmac,
        )

        assert result == AlertSignatureStatus.INVALID

    def test_alert_signature_tampered_message_rejected(self, test_tenant_key, test_tenant_id):
        """Test: Alert with tampered message is rejected."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        # Create valid signature for original message
        original_message = "Loss divergence"
        signature = verifier.sign_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message=original_message,
            tenant_id=test_tenant_id,
        )

        # Try to verify with tampered message
        tampered_message = "Safe to disable learning"
        result = verifier.verify_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message=tampered_message,  # Different message
            tenant_id=test_tenant_id,
            signature_hex=signature.signature_hex,
        )

        assert result == AlertSignatureStatus.INVALID

    def test_alert_signature_random_signature_rejected(self, test_tenant_key, test_tenant_id):
        """Test: Completely fake signature is rejected."""
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)

        # Use a random signature
        fake_signature = "0" * 64  # SHA256 is 64 hex chars

        result = verifier.verify_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="critical",
            message="Loss divergence",
            tenant_id=test_tenant_id,
            signature_hex=fake_signature,
        )

        assert result == AlertSignatureStatus.INVALID


# ============================================================================
# TEST GROUP 3: Rate Limiting for CRITICAL Alerts
# ============================================================================

class TestAlertRateLimiting:
    """Test rate limiting prevents alert storms."""

    def test_alert_rate_limiting_first_critical_allowed(self):
        """Test: First CRITICAL alert is allowed."""
        limiter = CriticalAlertRateLimiter()
        component_id = "learning_core"

        # First alert should be allowed
        assert limiter.can_send_critical(component_id) is True

    def test_alert_rate_limiting_second_critical_blocked(self):
        """Test: Second CRITICAL alert within 5 minutes is blocked."""
        limiter = CriticalAlertRateLimiter()
        component_id = "learning_core"

        # Allow first
        assert limiter.can_send_critical(component_id) is True
        limiter.record_critical_alert(component_id)

        # Second should be blocked
        assert limiter.can_send_critical(component_id) is False

    def test_alert_rate_limiting_different_components_independent(self):
        """Test: Rate limits are per-component (independent)."""
        limiter = CriticalAlertRateLimiter()

        # Component 1: send one CRITICAL
        assert limiter.can_send_critical("component_1") is True
        limiter.record_critical_alert("component_1")

        # Component 1: second CRITICAL blocked
        assert limiter.can_send_critical("component_1") is False

        # Component 2: should still be allowed (different component)
        assert limiter.can_send_critical("component_2") is True
        limiter.record_critical_alert("component_2")

    def test_alert_rate_limiting_expires_after_window(self):
        """Test: Rate limit expires after 5-minute window."""
        limiter = CriticalAlertRateLimiter()
        component_id = "learning_core"

        # Record first alert
        limiter.record_critical_alert(component_id)
        assert limiter.can_send_critical(component_id) is False

        # Simulate time passage by manipulating internal state
        # (In real scenario, this would be 5 minutes later)
        old_timestamp = datetime.utcnow() - timedelta(minutes=6)
        limiter.critical_alerts[component_id] = [old_timestamp]

        # Now it should be allowed (old alert is outside window)
        assert limiter.can_send_critical(component_id) is True

    def test_alert_rate_limiting_multiple_components_storm(self):
        """Test: Alert storm from multiple components is limited independently."""
        limiter = CriticalAlertRateLimiter()

        # Attacker tries to send CRITICAL from many components
        for i in range(10):
            component = f"component_{i}"
            # First alert allowed
            assert limiter.can_send_critical(component) is True
            limiter.record_critical_alert(component)

        # All should now be limited
        for i in range(10):
            component = f"component_{i}"
            assert limiter.can_send_critical(component) is False


# ============================================================================
# TEST GROUP 4: Manual Confirmation Required for CRITICAL Alerts
# ============================================================================

class TestAlertCriticalRequiresConfirmation:
    """Test CRITICAL alerts require manual confirmation."""

    def test_alert_critical_creates_pending_confirmation(self):
        """Test: CRITICAL alert creates pending confirmation."""
        manager = ManualConfirmationManager()

        confirmation = manager.create_confirmation(
            alert_id="alert_001",
            action=AlertAction.DISABLE_LEARNING,
            component_id="learning_core",
        )

        assert confirmation is not None
        assert confirmation.alert_id == "alert_001"
        assert confirmation.confirmed is False
        assert confirmation.confirmed_by is None

    def test_alert_critical_confirmation_has_ttl(self):
        """Test: Pending confirmation has 15-minute TTL."""
        manager = ManualConfirmationManager()

        confirmation = manager.create_confirmation(
            alert_id="alert_001",
            action=AlertAction.DISABLE_LEARNING,
            component_id="learning_core",
        )

        # TTL should be ~15 minutes from now
        ttl_seconds = (confirmation.expires_at - confirmation.created_at).total_seconds()
        assert 14 * 60 <= ttl_seconds <= 16 * 60  # Allow 1 min margin

    def test_alert_critical_confirmation_expires(self):
        """Test: Expired confirmations cannot be approved."""
        manager = ManualConfirmationManager()

        confirmation = manager.create_confirmation(
            alert_id="alert_001",
            action=AlertAction.DISABLE_LEARNING,
            component_id="learning_core",
        )

        # Manually expire it
        confirmation.expires_at = datetime.utcnow() - timedelta(seconds=1)

        # Should not approve expired confirmation
        result = manager.approve_confirmation(confirmation.confirmation_id, "operator_1")
        assert result is False

    def test_alert_critical_confirmation_can_be_approved(self):
        """Test: Operator can approve pending confirmation."""
        manager = ManualConfirmationManager()

        confirmation = manager.create_confirmation(
            alert_id="alert_001",
            action=AlertAction.DISABLE_LEARNING,
            component_id="learning_core",
        )

        # Approve it
        result = manager.approve_confirmation(confirmation.confirmation_id, "operator_1")
        assert result is True

        # Check state
        confirmed = manager.pending_confirmations[confirmation.confirmation_id]
        assert confirmed.confirmed is True
        assert confirmed.confirmed_by == "operator_1"

    def test_alert_critical_confirmation_can_be_rejected(self):
        """Test: Operator can reject pending confirmation."""
        manager = ManualConfirmationManager()

        confirmation = manager.create_confirmation(
            alert_id="alert_001",
            action=AlertAction.DISABLE_LEARNING,
            component_id="learning_core",
        )

        # Reject it
        result = manager.reject_confirmation(confirmation.confirmation_id)
        assert result is True

        # Should be removed
        assert confirmation.confirmation_id not in manager.pending_confirmations


# ============================================================================
# TEST GROUP 5: Audit Logging for Alert Actions
# ============================================================================

class TestAlertConfirmationAuditLogged:
    """Test alert actions are properly audited."""

    @pytest.mark.asyncio
    async def test_alert_audit_signature_check_logged(self, test_tenant_id, test_tenant_key):
        """Test: Signature check results are audited."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Create a valid signature
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="warning",
            message="Test alert",
            tenant_id=test_tenant_id,
        )

        # Process alert (will succeed)
        result = await dispatcher.process_alert(
            alert_id="alert_001",
            component_id="learning_core",
            severity="warning",
            message="Test alert",
            action=AlertAction.LOG_ONLY,
            signature_hex=signature.signature_hex,
        )

        # Check audit event was logged
        audit_events = dispatcher.get_audit_events()
        sig_check_events = [e for e in audit_events if e.event_type == "alert_signature_check"]

        assert len(sig_check_events) >= 1
        assert sig_check_events[0].status == "valid"

    @pytest.mark.asyncio
    async def test_alert_audit_invalid_signature_logged(self, test_tenant_id, test_tenant_key):
        """Test: Invalid signature rejection is audited."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Try with invalid signature
        result = await dispatcher.process_alert(
            alert_id="alert_002",
            component_id="learning_core",
            severity="warning",
            message="Test alert",
            action=AlertAction.LOG_ONLY,
            signature_hex="fake_signature_0000000000000000000000000000000000000000000000000000000000000000",
        )

        # Should be rejected
        assert result is False

        # Check audit event
        audit_events = dispatcher.get_audit_events()
        sig_check_events = [e for e in audit_events if e.event_type == "alert_signature_check"]

        assert len(sig_check_events) >= 1
        assert sig_check_events[0].status == "invalid"

    @pytest.mark.asyncio
    async def test_alert_audit_confirmation_required_logged(self, test_tenant_id, test_tenant_key):
        """Test: Pending confirmation is audited."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Create valid CRITICAL alert
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_003",
            component_id="learning_core",
            severity="critical",
            message="Critical issue",
            tenant_id=test_tenant_id,
        )

        # Process CRITICAL alert
        result = await dispatcher.process_alert(
            alert_id="alert_003",
            component_id="learning_core",
            severity="critical",
            message="Critical issue",
            action=AlertAction.DISABLE_LEARNING,
            signature_hex=signature.signature_hex,
        )

        # Should be pending confirmation (result True)
        assert result is True

        # Check audit event
        audit_events = dispatcher.get_audit_events()
        conf_events = [e for e in audit_events if e.event_type == "alert_confirmation_required"]

        assert len(conf_events) >= 1
        assert conf_events[0].status == "pending_confirmation"

    @pytest.mark.asyncio
    async def test_alert_audit_action_execution_logged(self, test_tenant_id, test_tenant_key):
        """Test: Action execution is audited."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Mock action callback
        callback = AsyncMock()
        dispatcher.register_action_callback(AlertAction.LOG_ONLY, callback)

        # Create and process alert (not CRITICAL, so executes immediately)
        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_004",
            component_id="learning_core",
            severity="warning",
            message="Warning alert",
            tenant_id=test_tenant_id,
        )

        result = await dispatcher.process_alert(
            alert_id="alert_004",
            component_id="learning_core",
            severity="warning",
            message="Warning alert",
            action=AlertAction.LOG_ONLY,
            signature_hex=signature.signature_hex,
        )

        # Should execute and audit
        assert result is True

        # Check audit event
        audit_events = dispatcher.get_audit_events()
        exec_events = [e for e in audit_events if e.event_type == "alert_action_executed"]

        assert len(exec_events) >= 1
        assert exec_events[0].status == "success"


# ============================================================================
# TEST GROUP 6: Spoofing Attack Prevention (Integration Tests)
# ============================================================================

class TestAlertSpoofingBlocked:
    """Test complete spoofing attack scenarios are prevented."""

    @pytest.mark.asyncio
    async def test_alert_spoofing_fake_disable_learning_blocked(self, test_tenant_id, test_tenant_key):
        """Test: Attacker cannot disable learning with fake alert.

        Attack scenario:
          1. Attacker crafts a CRITICAL alert: "disable learning"
          2. Attacker forges a signature using wrong key
          3. Dispatcher rejects the alert
        """
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Attacker tries to forge an alert with wrong key
        wrong_key = "attacker-secret-key"
        alert_body = f"alert_999|learning_core|critical|Disable learning|{test_tenant_id}"
        fake_signature = hmac.new(
            wrong_key.encode("utf-8"),
            alert_body.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

        # Process should reject
        result = await dispatcher.process_alert(
            alert_id="alert_999",
            component_id="learning_core",
            severity="critical",
            message="Disable learning",
            action=AlertAction.DISABLE_LEARNING,
            signature_hex=fake_signature,
        )

        assert result is False

        # Check that no pending confirmation was created
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 0

        # Check audit shows rejection
        audit_events = dispatcher.get_audit_events()
        sig_check_events = [e for e in audit_events if e.event_type == "alert_signature_check"]
        assert any(e.status == "invalid" for e in sig_check_events)

    @pytest.mark.asyncio
    async def test_alert_spoofing_alert_storm_limited(self, test_tenant_id, test_tenant_key):
        """Test: High-frequency alert storm is rate-limited.

        Attack scenario:
          1. Attacker sends many CRITICAL alerts from same component
          2. Dispatcher rate-limits them (max 1 per 5 minutes)
          3. Only first is allowed, rest are queued/blocked
        """
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        component_id = "learning_core"

        # Attacker sends 5 CRITICAL alerts rapidly
        results = []
        for i in range(5):
            signature = verifier.sign_alert(
                alert_id=f"alert_spam_{i}",
                component_id=component_id,
                severity="critical",
                message="Disable learning",
                tenant_id=test_tenant_id,
            )

            result = await dispatcher.process_alert(
                alert_id=f"alert_spam_{i}",
                component_id=component_id,
                severity="critical",
                message="Disable learning",
                action=AlertAction.DISABLE_LEARNING,
                signature_hex=signature.signature_hex,
            )

            results.append(result)

        # First should succeed (pending confirmation)
        assert results[0] is True

        # Rest should be blocked by rate limiter
        assert results[1] is False
        assert results[2] is False
        assert results[3] is False
        assert results[4] is False

        # Check audit events show rate limit blocks
        audit_events = dispatcher.get_audit_events()
        rate_limit_events = [e for e in audit_events if e.event_type == "alert_rate_limit_check"]
        assert len(rate_limit_events) >= 4  # At least the blocked attempts

    @pytest.mark.asyncio
    async def test_alert_spoofing_requires_operator_approval(self, test_tenant_id, test_tenant_key):
        """Test: Even valid CRITICAL alert requires operator approval.

        Scenario:
          1. A valid CRITICAL alert is received
          2. Dispatcher creates pending confirmation
          3. Alert does NOT execute until operator approves
          4. Operator can review and reject if needed
        """
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        # Mock action callback to track execution
        callback = AsyncMock()
        dispatcher.register_action_callback(AlertAction.DISABLE_LEARNING, callback)

        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_critical",
            component_id="learning_core",
            severity="critical",
            message="Disable learning",
            tenant_id=test_tenant_id,
        )

        # Process CRITICAL alert
        result = await dispatcher.process_alert(
            alert_id="alert_critical",
            component_id="learning_core",
            severity="critical",
            message="Disable learning",
            action=AlertAction.DISABLE_LEARNING,
            signature_hex=signature.signature_hex,
        )

        # Alert is pending confirmation
        assert result is True

        # Callback should NOT be called yet
        callback.assert_not_called()

        # Get pending confirmation
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 1
        confirmation = pending[0]

        # Operator can reject
        reject_result = dispatcher.confirmation_manager.reject_confirmation(confirmation.confirmation_id)
        assert reject_result is True

        # Callback should still not be called
        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_spoofing_high_frequency_from_multiple_sources(self, test_tenant_id, test_tenant_key):
        """Test: Coordinated multi-source attack is limited.

        Attack scenario:
          1. Attacker controls multiple monitoring components
          2. Sends CRITICAL alerts from each component
          3. Each component is rate-limited independently
          4. But overall alert load is manageable
        """
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        components = ["component_1", "component_2", "component_3"]

        # Attacker sends from 3 different components, 2 each
        for component in components:
            for i in range(2):
                signature = verifier.sign_alert(
                    alert_id=f"alert_{component}_{i}",
                    component_id=component,
                    severity="critical",
                    message="Disable learning",
                    tenant_id=test_tenant_id,
                )

                result = await dispatcher.process_alert(
                    alert_id=f"alert_{component}_{i}",
                    component_id=component,
                    severity="critical",
                    message="Disable learning",
                    action=AlertAction.DISABLE_LEARNING,
                    signature_hex=signature.signature_hex,
                )

                # First from each component succeeds
                if i == 0:
                    assert result is True
                # Second from each component is blocked
                else:
                    assert result is False

        # Check pending confirmations (should be 3, not 6)
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 3


# ============================================================================
# INTEGRATION TEST: End-to-End Alert Processing
# ============================================================================

class TestAlertDispatcherIntegration:
    """Integration tests for full alert processing flow."""

    @pytest.mark.asyncio
    async def test_alert_dispatcher_full_flow_info_alert(self, test_tenant_id, test_tenant_key):
        """Test: Complete flow for INFO alert (no confirmation needed)."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        callback = AsyncMock()
        dispatcher.register_action_callback(AlertAction.LOG_ONLY, callback)

        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_info",
            component_id="learning_core",
            severity="info",
            message="Info message",
            tenant_id=test_tenant_id,
        )

        # Process INFO alert (should execute immediately)
        result = await dispatcher.process_alert(
            alert_id="alert_info",
            component_id="learning_core",
            severity="info",
            message="Info message",
            action=AlertAction.LOG_ONLY,
            signature_hex=signature.signature_hex,
        )

        # Should execute
        assert result is True
        callback.assert_called_once()

        # No pending confirmations
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 0

    @pytest.mark.asyncio
    async def test_alert_dispatcher_full_flow_critical_alert_with_approval(self, test_tenant_id, test_tenant_key):
        """Test: Complete flow for CRITICAL alert with operator approval."""
        dispatcher = AlertDispatcher(
            tenant_id=test_tenant_id,
            tenant_key=test_tenant_key,
        )

        callback = AsyncMock()
        dispatcher.register_action_callback(AlertAction.DISABLE_LEARNING, callback)

        verifier = AlertSignatureVerifier(tenant_key=test_tenant_key)
        signature = verifier.sign_alert(
            alert_id="alert_critical",
            component_id="learning_core",
            severity="critical",
            message="Disable learning",
            tenant_id=test_tenant_id,
        )

        # Process CRITICAL alert
        result = await dispatcher.process_alert(
            alert_id="alert_critical",
            component_id="learning_core",
            severity="critical",
            message="Disable learning",
            action=AlertAction.DISABLE_LEARNING,
            signature_hex=signature.signature_hex,
        )

        # Pending
        assert result is True
        callback.assert_not_called()

        # Get pending confirmation
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 1
        confirmation_id = pending[0].confirmation_id

        # Operator approves
        approve_result = await dispatcher.confirm_action(
            confirmation_id=confirmation_id,
            approved_by="operator_1",
        )

        assert approve_result is True

        # Now callback should be called
        callback.assert_called_once()

        # No more pending
        pending = dispatcher.get_pending_confirmations()
        assert len(pending) == 0

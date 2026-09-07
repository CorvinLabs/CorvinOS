"""Tests for Fix #11: Alert Spoofing Prevention

Tests cover:
1. Alert Signature Verification (HMAC-SHA256)
2. Rate Limiting (Burst + Sustained)
3. Confirmation Workflow

Security Fix: ADR-0636 extension + Fix #11 implementation
Compliance: GDPR Art. 32 (security), Fail-closed design
"""

import time
from datetime import datetime, timedelta

import pytest

from core.learning.alert_policy import (
    AlertConfirmationRequest,
    AlertEvent,
    AlertLevel,
    AlertPolicy,
    AlertPolicyManager,
    AlertRateLimiter,
    AlertSignature,
    AlertType,
)


class TestAlertSignatureVerification:
    """Test alert signature verification (Fix #11) — HMAC-SHA256."""

    @pytest.fixture
    def manager(self):
        """Create alert manager with signing key."""
        return AlertPolicyManager(tenant_id="_default", signing_key="test_signing_key_12345")

    def test_sign_alert_creates_valid_signature(self, manager):
        """Test that signing an alert creates a valid HMAC-SHA256 signature."""
        alert_sig = manager._sign_alert(
            alert_id="test_alert_001",
            metric_name="loss_total",
            metric_value=0.015,
            threshold=0.01,
        )

        assert alert_sig.alert_id == "test_alert_001"
        assert alert_sig.signature != ""
        assert len(alert_sig.signature) == 64  # HMAC-SHA256 hex is 64 chars
        assert alert_sig.nonce != ""
        assert alert_sig.timestamp != ""
        assert len(alert_sig.signed_fields) == 5
        # signed_fields: [alert_id, metric_name, metric_value, threshold, nonce]
        assert alert_sig.signed_fields[0] == "test_alert_001"
        assert alert_sig.signed_fields[1] == "loss_total"
        assert alert_sig.signed_fields[2] == "0.015"
        assert alert_sig.signed_fields[3] == "0.01"

    def test_verify_valid_signature(self, manager):
        """Test that a valid signature passes verification."""
        # Create and sign an alert
        alert_sig = manager._sign_alert(
            alert_id="test_alert_002",
            metric_name="gradient_l2",
            metric_value=0.25,
            threshold=0.1,
        )

        # Verify it
        is_valid, reason = manager.verify_alert_signature(alert_sig)

        assert is_valid is True
        assert reason == "OK"

    def test_verify_invalid_signature_tampering_detected(self, manager):
        """Test that tampering is detected (signature verification fails)."""
        # Create and sign an alert
        alert_sig = manager._sign_alert(
            alert_id="test_alert_003",
            metric_name="gradient_l2",
            metric_value=0.25,
            threshold=0.1,
        )

        # Tamper with the signature
        alert_sig.signature = "0" * 64  # Replace with invalid signature

        # Verify should fail
        is_valid, reason = manager.verify_alert_signature(alert_sig)

        assert is_valid is False
        assert "tampering detected" in reason.lower()

    def test_verify_replay_attack_prevented(self, manager):
        """Test that nonce reuse is detected (replay prevention)."""
        # Create and verify an alert (caches the nonce)
        alert_sig = manager._sign_alert(
            alert_id="test_alert_004",
            metric_name="gradient_l2",
            metric_value=0.25,
            threshold=0.1,
        )
        is_valid, _ = manager.verify_alert_signature(alert_sig)
        assert is_valid is True

        # Try to use the same nonce again (replay attack)
        is_valid, reason = manager.verify_alert_signature(alert_sig)

        assert is_valid is False
        assert "replay detected" in reason.lower() or "nonce" in reason.lower()

    def test_verify_stale_signature_rejected(self, manager):
        """Test that stale signatures (>5 min old) are rejected."""
        # Manually create a signature with stale timestamp
        alert_sig = AlertSignature(
            alert_id="test_alert_005",
            signature="0" * 64,
            timestamp=(datetime.utcnow() - timedelta(minutes=10)).isoformat(),
            nonce="fake_nonce",
            signed_fields=["test_alert_005", "metric", "0.1", "0.05", "fake_nonce"],
        )

        # Verify should fail due to staleness
        is_valid, reason = manager.verify_alert_signature(alert_sig)

        assert is_valid is False
        assert "stale" in reason.lower()


class TestAlertRateLimiting:
    """Test alert rate limiting (Fix #11) — Burst + Sustained."""

    @pytest.fixture
    def manager(self):
        """Create alert manager."""
        return AlertPolicyManager(tenant_id="_default", signing_key="test_key")

    def test_rate_limit_burst_check(self, manager):
        """Test burst rate limit (max 5 alerts per minute)."""
        policy_id = "test_policy_burst"

        # Check rate limits for first 5 alerts (should all pass)
        for i in range(5):
            is_allowed, reason = manager.check_rate_limit(policy_id)
            assert is_allowed is True, f"Alert {i+1} should be allowed"
            manager.record_alert_for_rate_limit(policy_id, f"alert_{i}")

        # 6th alert should be rate-limited
        is_allowed, reason = manager.check_rate_limit(policy_id)
        assert is_allowed is False
        assert "rate limit exceeded" in reason.lower()

    def test_rate_limit_burst_reset(self, manager):
        """Test burst rate limit resets after 60 seconds."""
        policy_id = "test_policy_reset"

        # Fill up the burst limit
        for i in range(5):
            manager.check_rate_limit(policy_id)
            manager.record_alert_for_rate_limit(policy_id, f"alert_{i}")

        # 6th alert is blocked
        is_allowed, _ = manager.check_rate_limit(policy_id)
        assert is_allowed is False

        # Fast-forward time by manually updating the last_reset_minute
        limiter = manager._rate_limiters[policy_id]
        limiter.last_reset_minute = (datetime.utcnow() - timedelta(seconds=61)).isoformat()

        # Now the burst limit should reset and allow more alerts
        is_allowed, reason = manager.check_rate_limit(policy_id)
        assert is_allowed is True

    def test_rate_limit_sustained_check(self, manager):
        """Test sustained rate limit (max 50 alerts per hour)."""
        policy_id = "test_policy_sustained"

        # Simulate 50 alerts (approaching hour limit)
        for i in range(50):
            manager.check_rate_limit(policy_id)
            manager.record_alert_for_rate_limit(policy_id, f"alert_{i}")

        # 51st alert should be rate-limited
        is_allowed, reason = manager.check_rate_limit(policy_id)
        assert is_allowed is False
        assert "rate limit exceeded" in reason.lower()

    def test_rate_limit_independent_per_policy(self, manager):
        """Test that rate limits are independent per policy."""
        policy_1 = "policy_1"
        policy_2 = "policy_2"

        # Fill up policy_1
        for i in range(5):
            manager.check_rate_limit(policy_1)
            manager.record_alert_for_rate_limit(policy_1, f"p1_alert_{i}")

        # policy_1 should be rate-limited
        is_allowed, _ = manager.check_rate_limit(policy_1)
        assert is_allowed is False

        # policy_2 should still be allowed
        is_allowed, _ = manager.check_rate_limit(policy_2)
        assert is_allowed is True


class TestAlertConfirmationWorkflow:
    """Test alert confirmation workflow (Fix #11) — Request + Approval/Rejection."""

    @pytest.fixture
    def manager(self):
        """Create alert manager."""
        return AlertPolicyManager(tenant_id="_default", signing_key="test_key")

    @pytest.fixture
    def sample_alert(self, manager):
        """Create a sample alert for testing."""
        return AlertEvent(
            alert_id="confirm_test_001",
            policy_id="loss_divergence_critical",
            alert_type=AlertType.LOSS_DIVERGENCE,
            level=AlertLevel.CRITICAL,
            metric_name="loss_total",
            metric_value=0.025,
            threshold=0.01,
            message="Test alert for confirmation",
            timestamp=datetime.utcnow().isoformat(),
            tenant_id="_default",
            muted=False,
        )

    def test_request_confirmation(self, manager, sample_alert):
        """Test requesting confirmation for an alert."""
        conf_req = manager.request_confirmation(sample_alert, confidence_score=0.75)

        assert conf_req.confirmation_id is not None
        assert conf_req.alert_id == sample_alert.alert_id
        assert conf_req.policy_id == sample_alert.policy_id
        assert conf_req.status == "pending"
        assert conf_req.confidence_score == 0.75
        assert conf_req.confirmed_at is None
        assert conf_req.confirmed_by is None

    def test_confirm_alert_approval(self, manager, sample_alert):
        """Test approving a confirmation request."""
        conf_req = manager.request_confirmation(sample_alert, confidence_score=0.8)
        confirmation_id = conf_req.confirmation_id

        # Approve the confirmation
        success = manager.confirm_alert(confirmation_id, approved=True, confirmed_by="operator")

        assert success is True
        conf_req_after = manager._confirmation_queue[confirmation_id]
        assert conf_req_after.status == "approved"
        assert conf_req_after.confirmed_by == "operator"
        assert conf_req_after.confirmed_at is not None

    def test_confirm_alert_rejection(self, manager, sample_alert):
        """Test rejecting a confirmation request."""
        conf_req = manager.request_confirmation(sample_alert, confidence_score=0.3)
        confirmation_id = conf_req.confirmation_id

        # Reject the confirmation
        success = manager.confirm_alert(confirmation_id, approved=False, confirmed_by="auto")

        assert success is True
        conf_req_after = manager._confirmation_queue[confirmation_id]
        assert conf_req_after.status == "rejected"
        assert conf_req_after.confirmed_by == "auto"
        assert conf_req_after.rejection_reason is not None

    def test_get_pending_confirmations(self, manager, sample_alert):
        """Test retrieving pending confirmation requests."""
        # Create multiple confirmation requests
        conf_1 = manager.request_confirmation(sample_alert, confidence_score=0.7)
        conf_2 = manager.request_confirmation(sample_alert, confidence_score=0.6)

        # Approve one, leave one pending
        manager.confirm_alert(conf_1.confirmation_id, approved=True)

        # Get pending (should only have conf_2)
        pending = manager.get_pending_confirmations()

        assert len(pending) == 1
        assert pending[0].confirmation_id == conf_2.confirmation_id

    def test_confirmation_invalid_id(self, manager):
        """Test confirming a non-existent confirmation request."""
        success = manager.confirm_alert("invalid_conf_id", approved=True)

        assert success is False


class TestAlertSecurityIntegration:
    """Integration tests: Signature + Rate-Limit + Confirmation together."""

    @pytest.fixture
    def manager(self):
        """Create alert manager with security enabled."""
        return AlertPolicyManager(tenant_id="_default", signing_key="integration_test_key")

    def test_evaluate_with_rate_limiting(self, manager):
        """Test that evaluate() respects rate limits."""
        # Add a policy
        policy = manager.add_policy(
            alert_type=AlertType.LOSS_DIVERGENCE,
            threshold=0.01,
            level=AlertLevel.WARNING,
        )

        # Trigger many alerts that exceed rate limit
        for i in range(10):
            metrics = {"loss_total": 0.015}  # Above threshold
            alerts = manager.evaluate(metrics)

            if i < 5:
                # First 5 should succeed
                assert len(alerts) == 1, f"Alert {i} should succeed"
            else:
                # 6+ should be rate-limited
                assert len(alerts) == 0, f"Alert {i} should be rate-limited"

    def test_evaluate_with_confirmation_requirement(self, manager):
        """Test that evaluate() requests confirmation for sensitive policies."""
        # Policy is in _require_confirmation_policies by default
        metrics = {"loss_total": 0.03}  # Above threshold

        alerts = manager.evaluate(metrics)

        # Alert should be pending confirmation, not in returned alerts
        assert len(alerts) == 0

        # But confirmation request should exist
        pending = manager.get_pending_confirmations()
        assert len(pending) == 1
        assert pending[0].status == "pending"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

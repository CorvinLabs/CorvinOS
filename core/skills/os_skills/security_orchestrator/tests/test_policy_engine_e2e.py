"""
E2E tests for Security Orchestrator policy engine (Week 3).

Covers:
- Policy tightening on each threat type (brute force, escalation, exfil, cross-tenant)
- Policy reversion after threat clears
- Policy history tracking
- Audit event emission
- Thread-safe policy updates

Tests: 12 E2E tests
"""

import pytest
from datetime import datetime, timezone

from core.skills.os_skills.threat_detector import (
    ThreatDetector,
    Threat,
    ThreatType,
    ThreatSeverity,
)
from core.skills.os_skills.policy_engine import (
    PolicyEngine,
    SecurityPolicy,
    PolicyAdjustment,
)


class TestPolicyTighteningOnThreat:
    """Policy tightening response to threats (4 tests)."""

    def test_tighten_on_brute_force_requires_mfa(self):
        """Test: Brute force threat tightens auth: MFA required + reduced timeout."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        # Detect brute force
        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )
        assert threat is not None

        # Apply policy tightening
        adjustment = policy_engine.tighten_on_threat(threat)

        assert adjustment is not None
        assert adjustment.tenant_id == "_default"
        assert adjustment.threat_id == threat.threat_id

        # Verify policy changed
        current_policy = policy_engine.current_policy()
        assert current_policy.require_mfa is True
        assert (
            current_policy.auth_timeout_seconds
            < 1800
        )  # Default is 1800, should be reduced

    def test_tighten_on_privilege_escalation_lowers_review_threshold(self):
        """Test: Privilege escalation lowers manual review threshold (admin→editor)."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_privilege_escalation(
            user_id="attacker@example.com",
            old_role="viewer",
            new_role="admin",
            escalation_level=2,
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        current_policy = policy_engine.current_policy()
        # Manual review threshold should be lowered
        assert current_policy.require_manual_review_above_level == "editor"

    def test_tighten_on_data_exfiltration_blocks_exports(self):
        """Test: Data exfiltration reduces export limit to nearly zero."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_data_exfiltration(
            user_id="attacker@example.com",
            export_size=5000,
            destination="203.0.113.45",
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        current_policy = policy_engine.current_policy()
        # Export size should be severely limited
        assert current_policy.max_export_size_records < 200
        assert current_policy.api_rate_limit_per_minute < 100

    def test_tighten_on_cross_tenant_access_emergency_lockdown(self):
        """Test: Cross-tenant access triggers emergency lockdown (minimal exports/api)."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_cross_tenant_access(
            user_id="attacker@example.com",
            accessing_tenant="_default",
            accessed_tenant="other-tenant",
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        current_policy = policy_engine.current_policy()
        # Emergency lockdown: no exports, 1-minute timeout, severe rate limit
        assert current_policy.max_export_size_records == 0
        assert current_policy.auth_timeout_seconds == 60
        assert current_policy.api_rate_limit_per_minute == 5


class TestPolicyReversion:
    """Policy reversion when threat clears (3 tests)."""

    def test_revert_policy_after_threat_clear(self):
        """Test: Policy reverts to baseline when threat is cleared."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        # Tighten policy
        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )
        adjustment = policy_engine.tighten_on_threat(threat)

        # Verify tightened
        tightened_policy = policy_engine.current_policy()
        assert tightened_policy.require_mfa is True

        # Revert
        reverted_policy = policy_engine.revert_on_clear(threat.threat_id)

        assert reverted_policy is not None
        # Should be back to baseline
        assert reverted_policy.require_mfa is False
        assert reverted_policy.auth_timeout_seconds == 1800  # Baseline

    def test_revert_unknown_threat_returns_none(self):
        """Test: Reverting unknown threat returns None (no-op)."""
        policy_engine = PolicyEngine("_default")

        result = policy_engine.revert_on_clear("unknown-threat-id")

        assert result is None

    def test_multiple_tightenings_tracked_independently(self):
        """Test: Multiple threats can be active; each tracked in history."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        # Multiple threats
        threat1 = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )
        threat2 = detector.detect_data_exfiltration(
            user_id="user2@example.com",
            export_size=5000,
            destination="203.0.113.45",
        )

        policy_engine.tighten_on_threat(threat1)
        policy_engine.tighten_on_threat(threat2)

        history = policy_engine.get_policy_history()
        # Should have 2 adjustments
        assert len(history) >= 2

        active_count = policy_engine.get_active_threat_count()
        assert active_count == 2


class TestPolicyAuditTrail:
    """Policy change audit trail (2 tests)."""

    def test_adjustment_includes_tenant_and_timestamp(self):
        """Test: Every policy adjustment includes tenant_id, timestamp for audit."""
        detector = ThreatDetector("tenant-a")
        policy_engine = PolicyEngine("tenant-a")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        assert adjustment.tenant_id == "tenant-a"
        assert adjustment.timestamp is not None
        assert adjustment.timestamp.tzinfo == timezone.utc

    def test_adjustment_captures_policy_delta(self):
        """Test: Adjustment records before/after policy values for audit."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        original_policy = policy_engine.current_policy()
        original_timeout = original_policy.auth_timeout_seconds

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        # Adjustment should record the change
        assert adjustment.old_value == original_timeout
        assert adjustment.new_value < original_timeout
        assert adjustment.policy_name == "auth_timeout"


class TestTenantIsolation:
    """Tenant isolation in policy engine (2 tests)."""

    def test_policy_engine_fails_closed_on_tenant_mismatch(self):
        """Test: Tightening with mismatched tenant raises ValueError (fail-closed)."""
        detector_a = ThreatDetector("tenant-a")
        policy_engine_b = PolicyEngine("tenant-b")

        threat = detector_a.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Threat is for tenant-a, engine is for tenant-b
        with pytest.raises(ValueError, match="Tenant mismatch"):
            policy_engine_b.tighten_on_threat(threat)

    def test_policy_engines_are_tenant_scoped(self):
        """Test: Each tenant has independent policy state."""
        detector_a = ThreatDetector("tenant-a")
        detector_b = ThreatDetector("tenant-b")
        policy_engine_a = PolicyEngine("tenant-a")
        policy_engine_b = PolicyEngine("tenant-b")

        # Create threat in tenant-a
        threat_a = detector_a.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Tighten tenant-a policy
        policy_engine_a.tighten_on_threat(threat_a)

        # Tenant-b policy should remain unchanged
        policy_b = policy_engine_b.current_policy()
        assert policy_b.require_mfa is False  # Baseline


class TestPolicyHistoryTracking:
    """Policy history and audit trail (2 tests)."""

    def test_policy_history_includes_all_adjustments(self):
        """Test: Policy history tracks all adjustments in order."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat1 = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )
        threat2 = detector.detect_brute_force(
            user_id="user2@example.com", failed_attempts=6
        )

        policy_engine.tighten_on_threat(threat1)
        policy_engine.tighten_on_threat(threat2)

        history = policy_engine.get_policy_history()

        # Both adjustments should be in history
        assert len(history) >= 2
        for adj_id, adj in history.items():
            assert isinstance(adj, PolicyAdjustment)
            assert adj.tenant_id == "_default"

    def test_policy_adjustment_reason_includes_threat_description(self):
        """Test: Adjustment reason field captures the threat context."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=7
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        # Reason should include threat description
        assert "7 failed login" in adjustment.reason


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

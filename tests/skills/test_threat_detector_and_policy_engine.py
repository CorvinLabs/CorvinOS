"""
Threat Detector and Policy Engine Tests — SecurityOrchestratorSkill (Blocker 5)

Test coverage:
- ThreatDetector (all threat types)
- PolicyEngine (policy tightening + revert)
- Audit integration
- Tenant isolation
"""

from datetime import datetime, timezone
import pytest

from core.skills.os_skills.threat_detector import (
    Threat,
    ThreatType,
    ThreatSeverity,
    ThreatDetector,
)
from core.skills.os_skills.policy_engine import (
    SecurityPolicy,
    PolicyAdjustment,
    PolicyEngine,
)


class TestThreatDetector:
    """Test ThreatDetector threat pattern detection."""

    def test_detector_creation(self):
        """Test creating threat detector."""
        detector = ThreatDetector(tenant_id="_default")
        assert detector.tenant_id == "_default"
        assert detector.get_active_threats() == []

    def test_detector_requires_tenant_id(self):
        """Test that detector fails without tenant_id (fail-closed)."""
        with pytest.raises(ValueError):
            ThreatDetector(tenant_id="")

    def test_brute_force_detection_threshold_not_met(self):
        """Test brute force detection when threshold not met."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(
            user_id="user1",
            failed_attempts=3,  # Below threshold of 5
        )

        assert threat is None
        assert detector.get_active_threats() == []

    def test_brute_force_detection_threshold_met(self):
        """Test brute force detection when threshold is met."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(
            user_id="user1",
            failed_attempts=5,  # At threshold
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.BRUTE_FORCE
        assert threat.severity == ThreatSeverity.HIGH
        assert threat.confidence == 0.95
        assert "user1" in threat.description

        # Verify it's in active threats
        active = detector.get_active_threats()
        assert len(active) == 1
        assert active[0].threat_id == threat.threat_id

    def test_brute_force_threat_properties(self):
        """Test brute force threat immutability and properties."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(
            user_id="attacker",
            failed_attempts=10,
            confidence=0.98,
        )

        assert threat.threat_id.startswith("threat-brute-force-")
        assert threat.tenant_id == "_default"
        assert threat.confidence == 0.98
        assert threat.evidence["failed_attempts"] == 10
        assert not threat.is_expired()

    def test_privilege_escalation_detection_no_escalation(self):
        """Test privilege escalation detection when roles are same."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_privilege_escalation(
            user_id="user1",
            old_role="editor",
            new_role="editor",
        )

        assert threat is None

    def test_privilege_escalation_detection_small_escalation(self):
        """Test privilege escalation detection when escalation < threshold."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_privilege_escalation(
            user_id="user1",
            old_role="editor",
            new_role="admin",
            escalation_level=2,  # Threshold
        )

        assert threat is None  # Only 1 level (editor → admin)

    def test_privilege_escalation_detection_large_escalation(self):
        """Test privilege escalation detection when threshold exceeded."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_privilege_escalation(
            user_id="user1",
            old_role="viewer",
            new_role="superadmin",
            escalation_level=2,
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.PRIVILEGE_ESCALATION
        assert threat.severity == ThreatSeverity.HIGH
        assert "viewer" in threat.description
        assert "superadmin" in threat.description

    def test_data_exfiltration_detection_not_external(self):
        """Test data exfiltration detection when destination is internal."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_data_exfiltration(
            user_id="user1",
            export_size=5000,
            destination="internal-service",  # Internal
        )

        assert threat is None

    def test_data_exfiltration_detection_small_export(self):
        """Test data exfiltration detection when export is small."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_data_exfiltration(
            user_id="user1",
            export_size=500,  # Below threshold of 1000
            destination="external.com",
        )

        assert threat is None

    def test_data_exfiltration_detection_large_external_export(self):
        """Test data exfiltration detection for bulk external export."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_data_exfiltration(
            user_id="user1",
            export_size=50000,
            destination="attacker.com",
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.DATA_EXFILTRATION
        assert threat.severity == ThreatSeverity.CRITICAL
        assert threat.confidence == 0.92
        assert "50000" in str(threat.evidence["export_size"])

    def test_cross_tenant_access_detection_same_tenant(self):
        """Test cross-tenant detection when access is within same tenant."""
        detector = ThreatDetector(tenant_id="tenant-a")

        threat = detector.detect_cross_tenant_access(
            user_id="user1",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-a",
        )

        assert threat is None

    def test_cross_tenant_access_detection_different_tenants(self):
        """Test cross-tenant detection for cross-tenant access."""
        detector = ThreatDetector(tenant_id="tenant-a")

        threat = detector.detect_cross_tenant_access(
            user_id="user1",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-b",
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.CROSS_TENANT_ACCESS
        assert threat.severity == ThreatSeverity.CRITICAL
        assert threat.confidence == 0.99
        assert "tenant-b" in threat.description

    def test_threat_expiry(self):
        """Test that threats expire after TTL."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(
            user_id="user1",
            failed_attempts=5,
        )

        # Threat should be active
        assert len(detector.get_active_threats()) == 1
        assert not threat.is_expired()

        # Manually manipulate threat to simulate TTL expiry
        # (Can't wait in unit tests)
        old_threat = threat
        new_threat = Threat(
            threat_id=threat.threat_id,
            threat_type=threat.threat_type,
            severity=threat.severity,
            tenant_id=threat.tenant_id,
            detected_at=datetime.now(timezone.utc),  # Just now
            description=threat.description,
            evidence=threat.evidence,
            confidence=threat.confidence,
            recommended_action=threat.recommended_action,
            ttl_minutes=0,  # Expired immediately
        )
        assert new_threat.is_expired()

    def test_threat_immutability(self):
        """Test that threats are immutable (frozen dataclass)."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(
            user_id="user1",
            failed_attempts=5,
        )

        # Try to modify threat
        with pytest.raises(AttributeError):
            threat.confidence = 0.5  # type: ignore

    def test_multiple_threats_same_detector(self):
        """Test detector tracking multiple threats."""
        detector = ThreatDetector(tenant_id="_default")

        threat1 = detector.detect_brute_force(user_id="user1", failed_attempts=5)
        threat2 = detector.detect_privilege_escalation(
            user_id="user2", old_role="viewer", new_role="superadmin", escalation_level=2
        )
        threat3 = detector.detect_data_exfiltration(
            user_id="user3", export_size=50000, destination="external.com"
        )

        active = detector.get_active_threats()
        assert len(active) == 3

        # Verify sorted by severity (most critical first)
        assert active[0].severity == ThreatSeverity.CRITICAL
        assert active[1].severity in [ThreatSeverity.HIGH, ThreatSeverity.CRITICAL]

    def test_clear_threat(self):
        """Test clearing a threat."""
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(user_id="user1", failed_attempts=5)
        threat_id = threat.threat_id

        # Clear the threat
        cleared = detector.clear_threat(threat_id)
        assert cleared is not None
        assert cleared.threat_id == threat_id

        # Should not be in active threats
        assert len(detector.get_active_threats()) == 0

    def test_clear_expired_threats(self):
        """Test cleaning up expired threats."""
        detector = ThreatDetector(tenant_id="_default")

        # Create threats with different TTLs
        threat1 = Threat(
            threat_id="threat-1",
            threat_type=ThreatType.BRUTE_FORCE,
            severity=ThreatSeverity.HIGH,
            tenant_id="_default",
            detected_at=datetime.now(timezone.utc),
            description="Test",
            evidence={},
            confidence=0.9,
            recommended_action="Block",
            ttl_minutes=0,  # Expired
        )

        threat2 = Threat(
            threat_id="threat-2",
            threat_type=ThreatType.BRUTE_FORCE,
            severity=ThreatSeverity.HIGH,
            tenant_id="_default",
            detected_at=datetime.now(timezone.utc),
            description="Test",
            evidence={},
            confidence=0.9,
            recommended_action="Block",
            ttl_minutes=60,  # Active
        )

        # Manually add threats to detector
        detector._detected_threats["threat-1"] = threat1
        detector._detected_threats["threat-2"] = threat2

        # Clear expired
        expired = detector.clear_expired_threats()

        assert len(expired) == 1
        assert "threat-1" in expired
        assert len(detector.get_active_threats()) == 1


class TestPolicyEngine:
    """Test PolicyEngine security policy management."""

    def test_engine_creation(self):
        """Test creating policy engine."""
        engine = PolicyEngine(tenant_id="_default")
        assert engine.tenant_id == "_default"
        assert engine.get_active_threat_count() == 0

    def test_engine_requires_tenant_id(self):
        """Test that engine fails without tenant_id (fail-closed)."""
        with pytest.raises(ValueError):
            PolicyEngine(tenant_id="")

    def test_baseline_policy(self):
        """Test default policy configuration."""
        engine = PolicyEngine(tenant_id="_default")
        policy = engine.current_policy()

        assert policy.auth_timeout_seconds == 1800
        assert policy.require_mfa is False
        assert policy.login_attempt_limit == 5

    def test_tighten_on_brute_force(self):
        """Test policy tightening on brute force threat."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(user_id="attacker", failed_attempts=10)
        adjustment = engine.tighten_on_threat(threat)

        # Verify adjustment recorded
        assert adjustment.threat_id == threat.threat_id
        assert "auth_timeout" in adjustment.policy_name or "login_attempt" in adjustment.policy_name

        # Verify policy tightened
        new_policy = engine.current_policy()
        assert new_policy.auth_timeout_seconds < 1800  # Reduced
        assert new_policy.require_mfa is True  # Enabled
        assert new_policy.login_attempt_limit < 5  # Reduced

    def test_tighten_on_privilege_escalation(self):
        """Test policy tightening on privilege escalation."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_privilege_escalation(
            user_id="user1",
            old_role="viewer",
            new_role="superadmin",
            escalation_level=2,
        )
        adjustment = engine.tighten_on_threat(threat)

        new_policy = engine.current_policy()
        assert new_policy.require_mfa is True
        assert "user1" in new_policy.blocked_users

    def test_tighten_on_data_exfiltration(self):
        """Test policy tightening on data exfiltration (most severe)."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_data_exfiltration(
            user_id="thief",
            export_size=50000,
            destination="attacker.com",
        )
        adjustment = engine.tighten_on_threat(threat)

        new_policy = engine.current_policy()
        assert new_policy.auth_timeout_seconds == 300  # 5 minutes
        assert new_policy.max_export_size_records == 100  # Tiny limit
        assert new_policy.ip_whitelist_enabled is True
        assert "thief" in new_policy.blocked_users

    def test_tighten_on_cross_tenant_access(self):
        """Test policy tightening on cross-tenant access (emergency)."""
        engine = PolicyEngine(tenant_id="tenant-a")
        detector = ThreatDetector(tenant_id="tenant-a")

        threat = detector.detect_cross_tenant_access(
            user_id="insider",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-b",
        )
        adjustment = engine.tighten_on_threat(threat)

        new_policy = engine.current_policy()
        assert new_policy.auth_timeout_seconds == 60  # 1 minute (emergency)
        assert new_policy.max_export_size_records == 0  # No exports
        assert new_policy.login_attempt_limit == 1  # One strike
        assert "insider" in new_policy.blocked_users

    def test_revert_on_threat_clear(self):
        """Test policy reversion when threat clears."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        threat = detector.detect_brute_force(user_id="attacker", failed_attempts=10)
        engine.tighten_on_threat(threat)

        tightened_policy = engine.current_policy()
        assert tightened_policy.auth_timeout_seconds < 1800  # Tightened

        # Revert
        reverted = engine.revert_on_clear(threat.threat_id)
        assert reverted is not None
        assert reverted.auth_timeout_seconds == 1800  # Back to default

    def test_policy_history(self):
        """Test policy change history tracking."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        threat1 = detector.detect_brute_force(user_id="user1", failed_attempts=5)
        threat2 = detector.detect_privilege_escalation(
            user_id="user2", old_role="viewer", new_role="superadmin", escalation_level=2
        )

        engine.tighten_on_threat(threat1)
        engine.tighten_on_threat(threat2)

        history = engine.get_policy_history()
        assert len(history) == 2

        # Each adjustment should be recorded
        for threat_id, adjustment in history.items():
            assert adjustment.threat_id in [threat1.threat_id, threat2.threat_id]

    def test_active_threat_count(self):
        """Test tracking active threat count."""
        engine = PolicyEngine(tenant_id="_default")
        detector = ThreatDetector(tenant_id="_default")

        assert engine.get_active_threat_count() == 0

        threat1 = detector.detect_brute_force(user_id="user1", failed_attempts=5)
        engine.tighten_on_threat(threat1)
        assert engine.get_active_threat_count() == 1

        threat2 = detector.detect_privilege_escalation(
            user_id="user2", old_role="viewer", new_role="superadmin", escalation_level=2
        )
        engine.tighten_on_threat(threat2)
        assert engine.get_active_threat_count() == 2

    def test_tenant_mismatch_error(self):
        """Test that tightening fails if threat is from different tenant."""
        engine = PolicyEngine(tenant_id="tenant-a")
        detector = ThreatDetector(tenant_id="tenant-b")

        threat = detector.detect_brute_force(user_id="user1", failed_attempts=5)

        with pytest.raises(ValueError, match="Tenant mismatch"):
            engine.tighten_on_threat(threat)

    def test_policy_immutability(self):
        """Test that returned policies are immutable."""
        engine = PolicyEngine(tenant_id="_default")
        policy = engine.current_policy()

        with pytest.raises(AttributeError):
            policy.auth_timeout_seconds = 500  # type: ignore


class TestIntegration:
    """Integration tests for ThreatDetector + PolicyEngine."""

    def test_full_threat_response_cycle(self):
        """Test complete threat detection → policy adjustment → revert cycle."""
        detector = ThreatDetector(tenant_id="_default")
        engine = PolicyEngine(tenant_id="_default")

        # Step 1: Detect threat
        threat = detector.detect_data_exfiltration(
            user_id="attacker",
            export_size=50000,
            destination="attacker.com",
        )
        assert threat is not None

        # Step 2: Tighten policy
        old_policy = engine.current_policy()
        adjustment = engine.tighten_on_threat(threat)
        new_policy = engine.current_policy()

        assert new_policy != old_policy
        assert adjustment.old_value != adjustment.new_value

        # Step 3: Clear threat
        cleared = detector.clear_threat(threat.threat_id)
        assert cleared is not None

        # Step 4: Revert policy
        reverted = engine.revert_on_clear(threat.threat_id)
        assert reverted.auth_timeout_seconds == old_policy.auth_timeout_seconds

    def test_multiple_concurrent_threats(self):
        """Test handling multiple simultaneous threats."""
        detector = ThreatDetector(tenant_id="_default")
        engine = PolicyEngine(tenant_id="_default")

        # Detect multiple threats
        threat1 = detector.detect_brute_force(user_id="user1", failed_attempts=10)
        threat2 = detector.detect_data_exfiltration(
            user_id="user2", export_size=100000, destination="external.com"
        )
        threat3 = detector.detect_cross_tenant_access(
            user_id="user3", accessing_tenant="a", accessed_tenant="b"
        )

        # Apply all threats to policy
        engine.tighten_on_threat(threat1)
        engine.tighten_on_threat(threat2)
        engine.tighten_on_threat(threat3)

        # Policy should be extremely tight due to CRITICAL threat (cross-tenant)
        final_policy = engine.current_policy()
        assert final_policy.auth_timeout_seconds <= 60
        assert final_policy.max_export_size_records == 0
        assert len(final_policy.blocked_users) == 3


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

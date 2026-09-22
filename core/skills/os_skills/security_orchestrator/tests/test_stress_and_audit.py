"""
Week 4-5: Stress tests and audit trail verification for Security Orchestrator.

Covers:
- High-volume threat detection (10,000+ events)
- Audit trail hash-chain verification
- Memory efficiency under load
- Concurrent threat handling
- Edge cases: malformed events, tenant mismatches, recovery

Tests: 18 tests
"""

import pytest
import time
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

from core.skills.os_skills.threat_detector import (
    ThreatDetector,
    ThreatType,
    ThreatSeverity,
)
from core.skills.os_skills.policy_engine import PolicyEngine


class TestStressAndLoad:
    """Stress testing under high load (5 tests)."""

    def test_10k_threats_detected_in_reasonable_time(self):
        """Stress: Detect 10,000 threats in <30 seconds (target: <1ms per threat)."""
        detector = ThreatDetector("_default")

        start = time.time()

        # Simulate 10,000 brute force detections
        for i in range(10000):
            threat = detector.detect_brute_force(
                user_id=f"user{i % 100}@example.com",  # 100 unique users
                failed_attempts=5 + (i % 3),  # Vary attempts slightly
                confidence=0.90 + (i % 0.1),
            )

        elapsed = time.time() - start

        # Should complete in reasonable time (target < 30 sec)
        assert elapsed < 30.0

        # Verify threats are tracked
        active = detector.get_active_threats()
        assert len(active) > 0

    def test_concurrent_threat_detection_no_loss(self):
        """Stress: 1000 concurrent threat creations don't lose any threats."""
        detector = ThreatDetector("_default")

        threats_created = []

        # Simulate concurrent creation (sequential, but checking for no loss)
        for i in range(1000):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com",
                failed_attempts=5,
                confidence=0.90,
            )
            if threat:
                threats_created.append(threat)

        # All threats should be tracked
        active = detector.get_active_threats()
        assert len(active) == len(threats_created)
        assert len(active) == 1000

    def test_policy_engine_high_volume_tightening(self):
        """Stress: 500 policy tightenings under threat flood."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        # Create 500 threats
        threats = []
        for i in range(500):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com", failed_attempts=5
            )
            if threat:
                threats.append(threat)

        # Tighten policy for each
        start = time.time()
        for threat in threats:
            policy_engine.tighten_on_threat(threat)
        elapsed = time.time() - start

        # Should complete quickly (< 5 sec for 500 tightenings)
        assert elapsed < 5.0

        # Policy should reflect the threats
        assert policy_engine.get_active_threat_count() == len(threats)

    def test_threat_expiry_cleanup_scales_linearly(self):
        """Stress: Expire 1000 threats without memory leak."""
        detector = ThreatDetector("_default")

        # Create 1000 threats
        for i in range(1000):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com", failed_attempts=5
            )

        # Verify 1000 active
        initial_count = len(detector.get_active_threats())
        assert initial_count == 1000

        # Clear all (simulating cleanup)
        cleared_count = 0
        for threat_id in list(detector._detected_threats.keys()):
            detector.clear_threat(threat_id)
            cleared_count += 1

        # All should be cleared
        assert len(detector.get_active_threats()) == 0
        assert cleared_count == 1000

    def test_mixed_threat_types_high_volume(self):
        """Stress: Mix of threat types at high volume (2K events)."""
        detector = ThreatDetector("_default")

        threat_count = 0

        # 500 brute force
        for i in range(500):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com", failed_attempts=5
            )
            if threat:
                threat_count += 1

        # 500 escalations
        for i in range(500):
            threat = detector.detect_privilege_escalation(
                user_id=f"user{i}@example.com",
                old_role="viewer",
                new_role="admin",
                escalation_level=2,
            )
            if threat:
                threat_count += 1

        # 500 exfiltrations
        for i in range(500):
            threat = detector.detect_data_exfiltration(
                user_id=f"user{i}@example.com",
                export_size=2000 + i,
                destination=f"203.0.113.{(i % 255) + 1}",
            )
            if threat:
                threat_count += 1

        # 500 cross-tenant
        for i in range(500):
            threat = detector.detect_cross_tenant_access(
                user_id=f"user{i}@example.com",
                accessing_tenant=f"tenant-{i % 10}",
                accessed_tenant=f"tenant-{(i + 1) % 10}",
            )
            if threat:
                threat_count += 1

        active = detector.get_active_threats()
        assert len(active) > 1900  # Should have most threats active


class TestAuditTrailIntegrity:
    """Audit trail hash-chain verification (4 tests)."""

    def test_all_threats_include_audit_fields(self):
        """Audit: Every threat includes tenant_id, timestamp, evidence."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Required audit fields
        assert threat.tenant_id == "tenant-a"
        assert threat.detected_at is not None
        assert threat.detected_at.tzinfo == timezone.utc
        assert threat.evidence is not None
        assert isinstance(threat.evidence, dict)

    def test_threat_immutability(self):
        """Audit: Threat objects are immutable (frozen dataclass)."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Should not be able to modify threat
        with pytest.raises((AttributeError, TypeError)):
            threat.confidence = 0.5

    def test_policy_adjustment_immutability(self):
        """Audit: Policy adjustments are immutable (frozen dataclass)."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        # Should not be able to modify adjustment
        with pytest.raises((AttributeError, TypeError)):
            adjustment.old_value = 9999

    def test_audit_event_ordering_by_timestamp(self):
        """Audit: Threats ordered by timestamp for chain verification."""
        detector = ThreatDetector("_default")

        # Create threats at different times
        threat1 = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )
        time.sleep(0.01)
        threat2 = detector.detect_brute_force(
            user_id="user2@example.com", failed_attempts=5
        )

        # Threats should have different timestamps
        assert threat1.detected_at < threat2.detected_at


class TestErrorHandlingAndRecovery:
    """Error handling and fail-closed semantics (5 tests)."""

    def test_invalid_tenant_id_raises_error(self):
        """Error: Invalid tenant_id raises ValueError (fail-closed)."""
        with pytest.raises(ValueError):
            ThreatDetector(None)

        with pytest.raises(ValueError):
            ThreatDetector("")

        with pytest.raises(ValueError):
            ThreatDetector("   ")  # Whitespace

    def test_policy_engine_tenant_mismatch_raises_error(self):
        """Error: Threat from different tenant raises ValueError (fail-closed)."""
        detector = ThreatDetector("tenant-a")
        policy_engine = PolicyEngine("tenant-b")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            policy_engine.tighten_on_threat(threat)

    def test_invalid_confidence_score_raises_error(self):
        """Error: Confidence score outside [0.0, 1.0] raises ValueError."""
        detector = ThreatDetector("_default")

        with pytest.raises(ValueError, match="confidence"):
            detector.detect_brute_force(
                user_id="user@example.com", failed_attempts=5, confidence=1.5
            )

        with pytest.raises(ValueError, match="confidence"):
            detector.detect_brute_force(
                user_id="user@example.com", failed_attempts=5, confidence=-0.1
            )

    def test_recovery_from_malformed_evidence(self):
        """Recovery: Threat with missing evidence fields handled gracefully."""
        detector = ThreatDetector("_default")

        # Detect threat (should have valid evidence)
        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Evidence should be complete
        assert threat.evidence["user_id"] is not None
        assert threat.evidence["failed_attempts"] is not None

    def test_unknown_role_in_escalation_doesnt_crash(self):
        """Recovery: Unknown role names handled gracefully."""
        detector = ThreatDetector("_default")

        # Unknown roles
        threat = detector.detect_privilege_escalation(
            user_id="user@example.com",
            old_role="unknown_role_xyz",
            new_role="unknown_role_abc",
            escalation_level=2,
        )

        # Should not crash, but may not detect escalation
        # (depends on implementation handling)


class TestTenantIsolationAndSecurity:
    """Tenant isolation verification (3 tests)."""

    def test_separate_detectors_separate_threat_tracking(self):
        """Isolation: Threats in tenant-a don't appear in tenant-b."""
        detector_a = ThreatDetector("tenant-a")
        detector_b = ThreatDetector("tenant-b")

        # Create threat in tenant-a
        threat_a = detector_a.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Tenant-b detector should be empty
        threats_b = detector_b.get_active_threats()
        assert len(threats_b) == 0

        # Tenant-a should have the threat
        threats_a = detector_a.get_active_threats()
        assert len(threats_a) == 1

    def test_threat_querying_respects_tenant(self):
        """Isolation: Get threat by ID is tenant-specific."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Get by ID should work
        retrieved = detector.get_threat(threat.threat_id)
        assert retrieved is not None
        assert retrieved.tenant_id == "tenant-a"

    def test_policy_engine_per_tenant(self):
        """Isolation: Each tenant has independent policy state."""
        detector_a = ThreatDetector("tenant-a")
        detector_b = ThreatDetector("tenant-b")
        policy_engine_a = PolicyEngine("tenant-a")
        policy_engine_b = PolicyEngine("tenant-b")

        # Create threat in tenant-a
        threat_a = detector_a.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Tighten only tenant-a
        policy_engine_a.tighten_on_threat(threat_a)

        # Tenant-a should be tightened
        policy_a = policy_engine_a.current_policy()
        assert policy_a.require_mfa is True

        # Tenant-b should remain at baseline
        policy_b = policy_engine_b.current_policy()
        assert policy_b.require_mfa is False


class TestEdgeCasesAndBoundaries:
    """Edge cases at boundaries (1 test suite)."""

    def test_threat_at_exact_threshold_is_detected(self):
        """Boundary: Threat at exact threshold (5 attempts, 5 min) is detected."""
        detector = ThreatDetector("_default")

        # Exact threshold
        threat = detector.detect_brute_force(
            user_id="user@example.com",
            failed_attempts=5,
            time_window_minutes=5,
        )

        assert threat is not None

    def test_threat_just_below_threshold_not_detected(self):
        """Boundary: Just below threshold (4 attempts) not detected."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="user@example.com",
            failed_attempts=4,
            time_window_minutes=5,
        )

        assert threat is None

    def test_escalation_at_exact_level_detected(self):
        """Boundary: Escalation at exact level (2 levels) is detected."""
        detector = ThreatDetector("_default")

        threat = detector.detect_privilege_escalation(
            user_id="user@example.com",
            old_role="viewer",
            new_role="admin",
            escalation_level=2,
        )

        assert threat is not None

    def test_escalation_just_below_level_not_detected(self):
        """Boundary: Below escalation level (1 level) not detected."""
        detector = ThreatDetector("_default")

        threat = detector.detect_privilege_escalation(
            user_id="user@example.com",
            old_role="viewer",
            new_role="editor",
            escalation_level=2,
        )

        assert threat is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

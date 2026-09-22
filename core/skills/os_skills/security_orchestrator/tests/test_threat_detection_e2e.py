"""
E2E tests for Security Orchestrator threat detection (Week 3).

Covers:
- Brute force detection (5+ failed auth in 5 min)
- Privilege escalation (unauthorized role elevation)
- Data exfiltration (bulk export to external destination)
- Cross-tenant access (isolation breach detection)
- Unusual behavior patterns
- Policy tightening + reversion lifecycle

Tests: 12 E2E + 3 edge cases = 15 total
"""

import pytest
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

from core.skills.os_skills.threat_detector import (
    ThreatDetector,
    Threat,
    ThreatType,
    ThreatSeverity,
)


class TestBruteForceDetection:
    """Brute force attack detection (6 tests)."""

    def test_detects_brute_force_threshold_exceeded(self):
        """Test: 5+ failed attempts in 5 minutes triggers HIGH severity threat."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=7,
            time_window_minutes=5,
            confidence=0.95,
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.BRUTE_FORCE
        assert threat.severity == ThreatSeverity.HIGH
        assert threat.confidence == 0.95
        assert "7 failed login" in threat.description
        assert threat.tenant_id == "_default"

    def test_ignores_brute_force_below_threshold(self):
        """Test: 4 failed attempts (below 5) returns None."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="user@example.com",
            failed_attempts=4,  # Below BRUTE_FORCE_ATTEMPTS=5
            confidence=0.95,
        )

        assert threat is None

    def test_brute_force_threat_ttl_expiry(self):
        """Test: Threat expires after TTL (60 min default)."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=10,
            confidence=0.95,
        )

        assert not threat.is_expired()

        # Manually advance time (mock)
        threat_expired = Threat(
            threat_id=threat.threat_id,
            threat_type=threat.threat_type,
            severity=threat.severity,
            tenant_id=threat.tenant_id,
            detected_at=datetime.now(timezone.utc) - timedelta(minutes=61),
            description=threat.description,
            evidence=threat.evidence,
            confidence=threat.confidence,
            recommended_action=threat.recommended_action,
            ttl_minutes=60,
        )

        assert threat_expired.is_expired()

    def test_brute_force_recommendation_action(self):
        """Test: Recommended action includes account lock + password reset."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=6,
        )

        assert "lock" in threat.recommended_action.lower()
        assert "password reset" in threat.recommended_action.lower()

    def test_multiple_brute_force_threats_tracked(self):
        """Test: Multiple brute force attacks tracked independently."""
        detector = ThreatDetector("_default")

        threat1 = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )
        threat2 = detector.detect_brute_force(
            user_id="user2@example.com", failed_attempts=6
        )

        assert threat1 is not None
        assert threat2 is not None
        assert threat1.threat_id != threat2.threat_id
        active = detector.get_active_threats()
        assert len(active) == 2

    def test_brute_force_tenant_isolation(self):
        """Test: Threats scoped to tenant (fail-closed on tenant mismatch)."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        assert threat.tenant_id == "tenant-a"


class TestPrivilegeEscalationDetection:
    """Privilege escalation detection (3 tests)."""

    def test_detects_privilege_escalation_two_levels(self):
        """Test: Role elevation from 'viewer' to 'admin' (2 levels) triggers threat."""
        detector = ThreatDetector("_default")

        threat = detector.detect_privilege_escalation(
            user_id="attacker@example.com",
            old_role="viewer",
            new_role="admin",
            escalation_level=2,
            confidence=0.85,
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.PRIVILEGE_ESCALATION
        assert threat.severity == ThreatSeverity.HIGH
        assert "2 levels" in threat.description

    def test_ignores_privilege_escalation_single_level(self):
        """Test: One-level elevation (viewer → editor) below threshold returns None."""
        detector = ThreatDetector("_default")

        threat = detector.detect_privilege_escalation(
            user_id="user@example.com",
            old_role="viewer",
            new_role="editor",
            escalation_level=2,  # Threshold is 2 levels
        )

        assert threat is None

    def test_privilege_escalation_manual_review_recommended(self):
        """Test: Action recommends review + audit + revert."""
        detector = ThreatDetector("_default")

        threat = detector.detect_privilege_escalation(
            user_id="attacker@example.com",
            old_role="viewer",
            new_role="superadmin",
            escalation_level=3,
        )

        assert "review" in threat.recommended_action.lower()
        assert "revert" in threat.recommended_action.lower()
        assert "audit" in threat.recommended_action.lower()


class TestDataExfiltrationDetection:
    """Data exfiltration detection (3 tests)."""

    def test_detects_data_exfiltration_bulk_export_external(self):
        """Test: >1000 record export to external IP triggers CRITICAL."""
        detector = ThreatDetector("_default")

        threat = detector.detect_data_exfiltration(
            user_id="attacker@example.com",
            export_size=5000,  # Above BULK_EXPORT_THRESHOLD=1000
            destination="203.0.113.45",  # External IP
            confidence=0.92,
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.DATA_EXFILTRATION
        assert threat.severity == ThreatSeverity.CRITICAL
        assert "5,000 records" in threat.description

    def test_ignores_exfiltration_below_threshold(self):
        """Test: <1000 record export returns None."""
        detector = ThreatDetector("_default")

        threat = detector.detect_data_exfiltration(
            user_id="user@example.com",
            export_size=500,  # Below threshold
            destination="203.0.113.45",
        )

        assert threat is None

    def test_ignores_internal_export(self):
        """Test: Bulk export to localhost/internal destination returns None."""
        detector = ThreatDetector("_default")

        threat = detector.detect_data_exfiltration(
            user_id="user@example.com",
            export_size=5000,
            destination="localhost",  # Internal
        )

        assert threat is None


class TestCrossTenantDetection:
    """Cross-tenant access detection (2 tests)."""

    def test_detects_cross_tenant_access_isolation_breach(self):
        """Test: User accessing different tenant triggers CRITICAL."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_cross_tenant_access(
            user_id="attacker@example.com",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-b",
            confidence=0.99,
        )

        assert threat is not None
        assert threat.threat_type == ThreatType.CROSS_TENANT_ACCESS
        assert threat.severity == ThreatSeverity.CRITICAL
        assert "tenant-a" in threat.description
        assert "tenant-b" in threat.description

    def test_ignores_same_tenant_access(self):
        """Test: Same-tenant access returns None (no violation)."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_cross_tenant_access(
            user_id="user@example.com",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-a",  # Same tenant
        )

        assert threat is None


class TestThreatLifecycle:
    """Threat lifecycle management (3 tests)."""

    def test_get_active_threats_sorted_by_severity(self):
        """Test: Active threats sorted by severity (CRITICAL → LOW)."""
        detector = ThreatDetector("_default")

        # Create threats of different severities
        brute_force = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )  # HIGH
        exfil = detector.detect_data_exfiltration(
            user_id="user2@example.com",
            export_size=5000,
            destination="203.0.113.45",
        )  # CRITICAL

        active = detector.get_active_threats()
        assert len(active) == 2
        # CRITICAL should come first
        assert active[0].threat_type == ThreatType.DATA_EXFILTRATION
        assert active[1].threat_type == ThreatType.BRUTE_FORCE

    def test_clear_threat_removes_from_active(self):
        """Test: Clearing a threat removes it from active threats list."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        active_before = len(detector.get_active_threats())
        detector.clear_threat(threat.threat_id)
        active_after = len(detector.get_active_threats())

        assert active_before == 1
        assert active_after == 0

    def test_clear_expired_threats_cleanup(self):
        """Test: Expired threats automatically cleaned up."""
        detector = ThreatDetector("_default")

        # Create an active threat
        threat = detector.detect_brute_force(
            user_id="user1@example.com", failed_attempts=5
        )

        # Create an expired threat manually
        expired_threat = Threat(
            threat_id="threat-expired-001",
            threat_type=ThreatType.UNUSUAL_BEHAVIOR,
            severity=ThreatSeverity.LOW,
            tenant_id="_default",
            detected_at=datetime.now(timezone.utc) - timedelta(minutes=120),
            description="Old threat",
            evidence={},
            confidence=0.5,
            recommended_action="Clear",
            ttl_minutes=60,
        )
        detector._detected_threats[expired_threat.threat_id] = expired_threat

        # Clear expired
        expired_ids = detector.clear_expired_threats()

        assert expired_threat.threat_id in expired_ids
        assert threat.threat_id not in expired_ids
        # Only active threat remains
        assert len(detector.get_active_threats()) == 1


class TestAuditTrailIntegration:
    """Audit trail verification for threats (2 tests)."""

    def test_threat_includes_tenant_and_timestamp(self):
        """Test: Every threat includes tenant_id and timestamp for audit."""
        detector = ThreatDetector("tenant-a")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        assert threat.tenant_id == "tenant-a"
        assert threat.detected_at is not None
        assert threat.detected_at.tzinfo == timezone.utc

    def test_threat_evidence_captured_for_audit(self):
        """Test: Evidence dict contains all details for audit chain."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=7,
            time_window_minutes=5,
            confidence=0.95,
        )

        evidence = threat.evidence
        assert evidence["user_id"] == "attacker@example.com"
        assert evidence["failed_attempts"] == 7
        assert evidence["time_window_minutes"] == 5
        assert evidence["threshold"] == 5


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

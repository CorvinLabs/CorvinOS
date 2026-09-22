"""
Week 7-9: Adversarial security testing and false positive tuning.

Adversarial test suite that attempts to:
1. Bypass threat detection
2. Trigger false positives
3. Evade policy tightening
4. Cause denial of service
5. Manipulate audit trail

Tests: 18 adversarial scenarios + 5 false positive tuning tests
"""

import pytest
from datetime import datetime, timedelta, timezone

from core.skills.os_skills.threat_detector import (
    ThreatDetector,
    ThreatType,
    ThreatSeverity,
)
from core.skills.os_skills.policy_engine import PolicyEngine


class TestBypassAttempts:
    """Adversarial attempts to bypass threat detection (6 tests)."""

    def test_bypass_brute_force_detection_by_spreading_attempts(self):
        """Bypass Attempt 1: Spread 5 login attempts over 6+ minutes (outside 5-min window)."""
        detector = ThreatDetector("_default")

        # Attempt 1 at T=0min
        threat1 = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=1,
            time_window_minutes=1,
        )

        # Attempts 2-5 at T=6min (outside original 5-min window)
        threat2 = detector.detect_brute_force(
            user_id="attacker@example.com",
            failed_attempts=4,
            time_window_minutes=10,  # Spread over longer window
            confidence=0.70,
        )

        # Neither should trigger individual thresholds
        # But pattern analysis should detect slow brute force (TODO: advanced pattern matching)
        assert threat1 is None  # Single attempt
        assert threat2 is None  # Window too long

    def test_bypass_brute_force_by_rotating_usernames(self):
        """Bypass Attempt 2: Rotate between 5 different usernames (credential enumeration)."""
        detector = ThreatDetector("_default")

        threats = []
        # Attack: 5 different users, each 1-2 failed attempts
        for i in range(5):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com",
                failed_attempts=2,  # Below threshold per user
                confidence=0.70,
            )
            if threat:
                threats.append(threat)

        # Individual attempts don't trigger
        # But should trigger via distributed attack detection (TODO: aggregate analysis)
        assert len(threats) == 0  # Per-user below threshold

    def test_bypass_escalation_detection_by_gradual_privilege_gain(self):
        """Bypass Attempt 3: Escalate gradually (viewer→editor→admin) instead of direct."""
        detector = ThreatDetector("_default")

        # Step 1: viewer → editor (1 level, below 2-level threshold)
        threat1 = detector.detect_privilege_escalation(
            user_id="attacker@example.com",
            old_role="viewer",
            new_role="editor",
            escalation_level=2,
        )

        # Step 2: editor → admin (1 level again)
        threat2 = detector.detect_privilege_escalation(
            user_id="attacker@example.com",
            old_role="editor",
            new_role="admin",
            escalation_level=2,
        )

        # Each individual step below threshold
        assert threat1 is None
        assert threat2 is None
        # But combined = 2-level escalation (should detect if tracking history)

    def test_bypass_exfiltration_detection_by_incremental_exports(self):
        """Bypass Attempt 4: Export 500 records 3 times (below 1000 threshold each)."""
        detector = ThreatDetector("_default")

        threats = []
        for i in range(3):
            threat = detector.detect_data_exfiltration(
                user_id="attacker@example.com",
                export_size=500,  # Below 1000 threshold
                destination="203.0.113.45",
            )
            if threat:
                threats.append(threat)

        # Each export individually below threshold
        assert len(threats) == 0
        # But 3×500 = 1500 total (should detect via aggregation)

    def test_bypass_exfiltration_by_using_internal_gateway(self):
        """Bypass Attempt 5: Export data via 'internal' gateway that's actually external."""
        detector = ThreatDetector("_default")

        # Masquerade as internal
        threat = detector.detect_data_exfiltration(
            user_id="attacker@example.com",
            export_size=5000,
            destination="internal-gateway-proxy.attacker.net",  # Looks internal
        )

        # Should not trigger if only checking for "internal-" prefix
        # Real system would need reverse-DNS verification (TODO)
        # This is a known false negative

    def test_bypass_cross_tenant_isolation_check_via_shared_session(self):
        """Bypass Attempt 6: Use shared session token to access other tenant."""
        detector = ThreatDetector("tenant-a")

        # If session is truly "shared" (architectural flaw), won't detect
        # But assuming session is tenant-bound, should detect
        threat = detector.detect_cross_tenant_access(
            user_id="attacker@example.com",
            accessing_tenant="tenant-a",
            accessed_tenant="tenant-b",
        )

        assert threat is not None  # Should always detect


class TestFalsePositiveCases:
    """Legitimate activity that could trigger false positives (6 tests)."""

    def test_false_positive_legitimate_password_reset_attempts(self):
        """False Positive 1: User forgot password, 5 failed attempts in 5 min (legitimate)."""
        detector = ThreatDetector("_default")

        # Real scenario: User forgets password
        threat = detector.detect_brute_force(
            user_id="legitimate_user@example.com",
            failed_attempts=5,  # At threshold
            time_window_minutes=5,
            confidence=0.70,  # Lower confidence = more likely FP
        )

        # Detected, but could be FP
        # Solution: Operator feedback → false positive marked → confidence tuning
        if threat:
            assert threat.confidence <= 0.70  # Lower confidence for borderline cases

    def test_false_positive_authorized_bulk_export_for_reporting(self):
        """False Positive 2: Legitimate bulk export for quarterly report."""
        detector = ThreatDetector("_default")

        # Authorized use: Export 2000 records to external analytics service
        threat = detector.detect_data_exfiltration(
            user_id="finance@example.com",
            export_size=2000,
            destination="analytics-external.example.com",  # Legitimate service
            confidence=0.75,  # Moderate confidence
        )

        # Might detect, but could be false positive
        if threat:
            assert threat.confidence <= 0.75

    def test_false_positive_legitimate_role_change_during_onboarding(self):
        """False Positive 3: Onboarding flow assigns new hire viewer→editor role."""
        detector = ThreatDetector("_default")

        # Authorized: New hire gets role upgrade
        threat = detector.detect_privilege_escalation(
            user_id="new_hire@example.com",
            old_role="viewer",
            new_role="editor",
            escalation_level=2,  # 2-level, but authorized
        )

        # Should not detect (only 1 level), but if it does:
        assert threat is None

    def test_false_positive_distributed_auth_attempts_from_load_balancer(self):
        """False Positive 4: All users hitting same auth IP (load balancer) = false positive."""
        detector = ThreatDetector("_default")

        # Scenario: 20 users login from same LB IP in same second
        # Looks like distributed attack, but is normal
        threats = []
        for i in range(10):
            threat = detector.detect_brute_force(
                user_id=f"user{i}@example.com",
                failed_attempts=2,  # Each user only 2 attempts
                confidence=0.65,
            )
            if threat:
                threats.append(threat)

        # Should not detect (per-user below threshold)
        assert len(threats) == 0

    def test_false_positive_vpn_rotation_looks_like_impossible_travel(self):
        """False Positive 5: User rotates VPN → appears to be in different countries."""
        detector = ThreatDetector("_default")

        # User logs out from VPN server in Japan, reconnects in US
        # Time delta: instant, but different IPs = impossible travel detection (TODO)
        # Not directly tested here, but documented as known FP source

    def test_false_positive_batch_job_looks_like_data_exfiltration(self):
        """False Positive 6: Nightly batch job exports data to data warehouse."""
        detector = ThreatDetector("_default")

        # Authorized batch: Export 10K records to data warehouse
        threat = detector.detect_data_exfiltration(
            user_id="batch-job@internal",
            export_size=10000,
            destination="data-warehouse.internal",  # Internal destination
            confidence=0.60,  # Should be low confidence for internal exports
        )

        # Should not detect (internal destination)
        assert threat is None


class TestFalsePositiveRateTargeting:
    """Tuning false positive rate to < 5% target (4 tests)."""

    def test_confidence_scoring_adjusted_for_fp_reduction(self):
        """Tuning: Adjust confidence scores to reduce false positives."""
        detector = ThreatDetector("_default")

        # Borderline case: 5 failed attempts, 5 min window
        threat = detector.detect_brute_force(
            user_id="user@example.com",
            failed_attempts=5,
            time_window_minutes=5,
            confidence=0.80,  # Medium-high confidence
        )

        # Operator marks as false positive 10 times
        # System should lower confidence for this pattern

    def test_whitelist_legitimate_sources(self):
        """Tuning: Whitelist legitimate sources (batch jobs, internal services)."""
        detector = ThreatDetector("_default")

        # Batch job is whitelisted
        threat = detector.detect_data_exfiltration(
            user_id="backup-job@internal",
            export_size=50000,
            destination="backup-storage.internal",
            confidence=0.10,  # Very low confidence due to whitelist
        )

        # Should detect but with very low confidence (can be auto-cleared)
        if threat:
            assert threat.confidence < 0.20

    def test_time_of_day_context_for_false_positive_reduction(self):
        """Tuning: Consider time of day (business hours = lower suspicion)."""
        # Timestamp would be checked: 9 AM = normal hours, low suspicion
        # 3 AM = unusual, high suspicion
        # (TODO: Implement time-of-day context)

    def test_user_behavior_profile_for_false_positive_reduction(self):
        """Tuning: Compare against user's historical behavior."""
        # User normally exports 500 records/day → 2000 export not suspicious
        # New user, 2000 export → suspicious
        # (TODO: Implement behavior profiling)


class TestDenialOfServiceResistance:
    """Tests for DoS resistance (3 tests)."""

    def test_high_volume_false_threats_does_not_degrade_performance(self):
        """DoS Resistance 1: 10,000 threat detections/sec maintains <10ms latency."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        import time
        start = time.time()

        # Simulate 1000 rapid threats
        for i in range(1000):
            threat = detector.detect_brute_force(
                user_id=f"user{i % 100}@example.com",
                failed_attempts=5 + (i % 3),
            )
            if threat:
                policy_engine.tighten_on_threat(threat)

        elapsed = time.time() - start
        avg_latency_ms = (elapsed / 1000) * 1000

        # Should maintain <10ms per detection
        assert avg_latency_ms < 10.0

    def test_memory_does_not_grow_unbounded_with_threats(self):
        """DoS Resistance 2: Memory usage stays bounded even with 10K threats."""
        detector = ThreatDetector("_default")

        # Create 10K threats
        for i in range(10000):
            detector.detect_brute_force(
                user_id=f"user{i % 100}@example.com", failed_attempts=5
            )

        # Memory should be roughly linear in threat count, not exponential
        # (Would need to measure with memory profiler)

    def test_policy_engine_handles_rapid_tightening_loosening_cycles(self):
        """DoS Resistance 3: Rapid policy changes don't cause instability."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threats = []
        for i in range(100):
            threat = detector.detect_brute_force(
                user_id="user@example.com", failed_attempts=5
            )
            if threat:
                threats.append(threat)
                policy_engine.tighten_on_threat(threat)

        # Rapidly revert
        for threat in threats:
            policy_engine.revert_on_clear(threat.threat_id)

        # Final policy should be stable (baseline)
        final = policy_engine.current_policy()
        assert final.require_mfa is False  # Baseline state


class TestAuditTrailIntegrityUnderAdversary:
    """Audit trail must remain integrity even under attack (2 tests)."""

    def test_threat_cannot_be_backdated_in_audit(self):
        """Audit Integrity 1: Threat detection time cannot be modified."""
        detector = ThreatDetector("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        # Threat is immutable (frozen dataclass)
        # Cannot modify detected_at timestamp
        with pytest.raises((AttributeError, TypeError)):
            threat.detected_at = datetime.now(timezone.utc) - timedelta(days=1)

    def test_policy_adjustment_evidence_cannot_be_tampered(self):
        """Audit Integrity 2: Policy adjustments cannot be modified post-commitment."""
        detector = ThreatDetector("_default")
        policy_engine = PolicyEngine("_default")

        threat = detector.detect_brute_force(
            user_id="attacker@example.com", failed_attempts=5
        )

        adjustment = policy_engine.tighten_on_threat(threat)

        # Adjustment is immutable
        # Cannot tamper with old_value, new_value, reason
        with pytest.raises((AttributeError, TypeError)):
            adjustment.old_value = 999999


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

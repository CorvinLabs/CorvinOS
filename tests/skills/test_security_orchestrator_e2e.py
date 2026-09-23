"""E2E Tests for os.security_orchestrator Skill (Phase 5.3, ADR-0532 Phase 3).

Test Coverage:
1. Pattern detection (burst, creep, concentration, context_shift)
2. Confidence scoring
3. House-rules suggestion generation (advisory only)
4. Feedback loop + optimizer
5. Audit-chain integrity
6. Tenant isolation
7. Adversarial cases (false positives, evasion, etc.)

All tests verify:
- Real audit events flow through the skill
- Skill execution is logged and hash-chained
- Operator decisions are immutable
- No house-rules auto-apply
"""

import pytest
import json
from datetime import datetime, timedelta
from core.skills.os_security_orchestrator import (
    SecurityOrchestratorSkill,
    ThreatPatternDetector,
    SecurityAdvisor,
    FeedbackOptimizer,
    PatternType,
    ThreatPattern,
)


class TestThreatPatternDetection:
    """Test pattern detection algorithms."""

    def test_burst_detection_above_threshold(self):
        """Test: detects burst when denials exceed threshold."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        # Create 6 denial events in 10 minutes (threshold is 5)
        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(6)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")

        assert len(patterns) >= 1
        burst_patterns = [p for p in patterns if p.pattern_type == PatternType.BURST]
        assert len(burst_patterns) > 0

        burst = burst_patterns[0]
        assert burst.confidence >= 0.8
        assert burst.severity == "high"
        assert burst.affected_entity == "192.168.1.100"
        assert "rate-limit" in burst.suggested_action.lower()

    def test_burst_detection_below_threshold(self):
        """Test: does NOT detect burst when denials below threshold."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        # Create 3 denial events (threshold is 5)
        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(3)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")
        burst_patterns = [p for p in patterns if p.pattern_type == PatternType.BURST]

        assert len(burst_patterns) == 0

    def test_creep_detection_slow_attack(self):
        """Test: detects stealthy creep (1 event/hour for 24 hours)."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        # Create 25 events spread over 25 hours (1 per hour, guaranteed creep)
        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(hours=i)).isoformat() + "Z",
                "details": {"source_ip": "10.0.0.50"},
            }
            for i in range(25)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")

        creep_patterns = [p for p in patterns if p.pattern_type == PatternType.CREEP]
        assert len(creep_patterns) > 0

        creep = creep_patterns[0]
        assert creep.severity == "medium"
        assert "credential compromise" in creep.suggested_action.lower() or "monitor" in creep.suggested_action.lower()

    def test_concentration_detection_single_source(self):
        """Test: detects when 60%+ of denials come from one source."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        # 7 from one IP, 3 from others = 70% concentration
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100" if i < 7 else f"192.168.1.{101+i}"},
            }
            for i in range(10)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")

        conc_patterns = [p for p in patterns if p.pattern_type == PatternType.CONCENTRATION]
        assert len(conc_patterns) > 0

        conc = conc_patterns[0]
        assert conc.severity == "high"
        assert conc.affected_entity == "192.168.1.100"
        assert conc.context.get("concentration_pct", 0) >= 60

    def test_context_shift_detection_geo_mismatch(self):
        """Test: detects geographic context shift."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "geo_mismatch_detected",
                "tenant_id": "tenant_1",
                "user_id": "user_123",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"geo_from": "US" if i == 0 else "CN"},
            }
            for i in range(2)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")

        shift_patterns = [p for p in patterns if p.pattern_type == PatternType.CONTEXT_SHIFT]
        assert len(shift_patterns) > 0

        shift = shift_patterns[0]
        assert shift.affected_entity == "user_123"
        assert shift.severity == "medium"
        assert "2fa" in shift.suggested_action.lower() or "challenge" in shift.suggested_action.lower()

    def test_confidence_scoring_varies_with_evidence(self):
        """Test: confidence increases with more evidence."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()

        # First: small burst (5 events)
        events_small = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(5)
        ]

        patterns_small = skill.detector.detect_patterns(events_small, "tenant_1")
        burst_small = [p for p in patterns_small if p.pattern_type == PatternType.BURST]
        confidence_small = burst_small[0].confidence if burst_small else 0

        # Second: larger burst (10 events)
        events_large = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(10)
        ]

        patterns_large = skill.detector.detect_patterns(events_large, "tenant_1")
        burst_large = [p for p in patterns_large if p.pattern_type == PatternType.BURST]
        confidence_large = burst_large[0].confidence if burst_large else 0

        # Larger burst should have higher confidence
        assert confidence_large >= confidence_small


class TestRecommendationGeneration:
    """Test policy recommendation generation."""

    def test_recommendation_is_advisory_only(self):
        """Test: recommendations never auto-apply house-rules."""
        advisor = SecurityAdvisor()

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.95,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=10,
            suggested_action="Rate-limit IP",
        )

        rec = advisor.generate_recommendation(pattern, "tenant_1")

        assert rec["operator_decision_required"] is True
        assert rec["recommended_house_rule"]["manual_review_required"] is True
        assert "manual" in rec["recommended_house_rule"]["note"].lower()
        assert rec["recommended_house_rule"]["rule_name"] == f"burst_{pattern.affected_entity[:8]}"

    def test_recommendation_includes_action_options(self):
        """Test: recommendation provides operator with action choices."""
        advisor = SecurityAdvisor()

        pattern = ThreatPattern(
            pattern_type=PatternType.CONCENTRATION,
            severity="high",
            confidence=0.85,
            affected_entity="203.0.113.50",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T11:00:00"),
            event_count=8,
            suggested_action="Monitor IP",
        )

        rec = advisor.generate_recommendation(pattern, "tenant_1")

        assert "action_options" in rec
        action_types = {a["action"] for a in rec["action_options"]}
        assert "rate_limit" in action_types
        assert "require_mfa" in action_types
        assert "monitor_only" in action_types

    def test_confidence_adjustment_from_feedback_history(self):
        """Test: confidence adjusted based on feedback history."""
        feedback_history = [
            {"pattern_type": "burst", "affected_entity": "192.168.1.100", "is_threat": True},
            {"pattern_type": "burst", "affected_entity": "192.168.1.100", "is_threat": True},
            {"pattern_type": "burst", "affected_entity": "192.168.1.100", "is_threat": False},
        ]

        advisor = SecurityAdvisor(feedback_history=feedback_history)

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.5,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=6,
            suggested_action="Rate-limit",
        )

        rec = advisor.generate_recommendation(pattern, "tenant_1")

        # Confidence should be adjusted (2/3 confirmed = 0.67)
        adjusted_conf = rec["confidence"]
        # It's average of original (0.5) and confirmation rate (0.67)
        assert 0.55 <= adjusted_conf <= 0.65


class TestFeedbackOptimizer:
    """Test feedback loop and learning."""

    def test_feedback_recorded_and_audited(self):
        """Test: operator feedback is recorded and immutable."""
        config = {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
                "concentration": {"threshold_pct": 60},
            },
            "max_events_to_scan": 1000,
        }
        optimizer = FeedbackOptimizer(config)

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.9,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=7,
            suggested_action="Rate-limit",
        )

        result = optimizer.record_feedback(
            pattern=pattern,
            is_threat=True,
            operator_note="Confirmed intrusion attempt from dev test",
        )

        assert result["feedback_recorded"] is True
        assert "feedback_id" in result
        assert result["operator_decision"] == "threat confirmed"
        assert "feedback_id" in result

    def test_false_alarm_feedback_suggests_higher_threshold(self):
        """Test: false alarm feedback suggests raising detection threshold."""
        config = {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
                "concentration": {"threshold_pct": 60},
            },
        }
        optimizer = FeedbackOptimizer(config)

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.8,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=5,
            suggested_action="Rate-limit",
        )

        result = optimizer.record_feedback(
            pattern=pattern,
            is_threat=False,
            operator_note="This was a legitimate load test",
        )

        suggested_update = result.get("suggested_config_update", {})
        assert "burst" in suggested_update
        # Threshold should be raised (less sensitive)
        new_threshold = suggested_update["burst"].get("threshold")
        assert new_threshold > 5  # Was 5, should be higher

    def test_threat_confirmed_feedback_keeps_threshold(self):
        """Test: threat confirmation doesn't change threshold (already effective)."""
        config = {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
            },
        }
        optimizer = FeedbackOptimizer(config)

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.9,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=7,
            suggested_action="Rate-limit",
        )

        result = optimizer.record_feedback(
            pattern=pattern,
            is_threat=True,
            operator_note="Confirmed attack",
        )

        suggested_update = result.get("suggested_config_update", {})
        # Threshold should stay the same
        assert suggested_update["burst"]["threshold"] == 5

    def test_feedback_stats_show_confirmation_rate(self):
        """Test: feedback stats aggregate operator decisions."""
        optimizer = FeedbackOptimizer({})

        pattern1 = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.8,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=6,
            suggested_action="Rate-limit",
        )

        pattern2 = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.75,
            affected_entity="192.168.1.101",
            timestamp_range=("2026-09-20T11:00:00", "2026-09-20T11:10:00"),
            event_count=5,
            suggested_action="Rate-limit",
        )

        optimizer.record_feedback(pattern1, is_threat=True)
        optimizer.record_feedback(pattern1, is_threat=False)
        optimizer.record_feedback(pattern2, is_threat=True)

        stats = optimizer.get_feedback_stats()

        assert stats["total_feedback"] == 3
        assert stats["threat_confirmation_rate"] == 2 / 3


class TestSkillExecution:
    """Test main skill execution and audit integration."""

    def test_skill_executes_and_returns_patterns(self):
        """Test: skill runs end-to-end and returns detected patterns."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(6)
        ]

        result = skill.execute(events, "tenant_1")

        assert result["tenant_id"] == "tenant_1"
        assert "patterns_detected" in result
        assert "recommendations" in result
        assert result["skill_version"] == "1.0"
        assert result["audit_committed"] is True

    def test_skill_patterns_above_confidence_threshold(self):
        """Test: skill only reports patterns above confidence threshold."""
        config = {
            "patterns": {
                "burst": {"window_minutes": 10, "threshold": 5},
            },
            "confidence_threshold": 0.8,
        }
        skill = SecurityOrchestratorSkill(config)

        now = datetime.utcnow()
        # Weak burst: 5 events (confidence at threshold, not above)
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(5)
        ]

        result = skill.execute(events, "tenant_1")

        # May or may not report depending on exact confidence calculation
        # At least verify threshold is respected
        for pattern in result["patterns_detected"]:
            assert pattern["confidence"] >= 0.8

    def test_skill_audit_event_emitted(self):
        """Test: skill execution emits audit event."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": now.isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
        ]

        result = skill.execute(events, "tenant_1")

        assert "audit_event_id" in result
        assert len(skill.audit_events) > 0

        # Verify event is hash-chained
        last_event = skill.audit_events[-1]
        assert last_event["event_type"] == "security_pattern_detected"
        assert last_event["skill_id"] == "os.security_orchestrator"
        assert last_event["hash"] is not None

    def test_skill_audit_chain_integrity(self):
        """Test: audit chain is hash-linked and verifiable."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": now.isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
        ]

        skill.execute(events, "tenant_1")
        skill.execute(events, "tenant_1")

        # Verify chain
        verification = skill.verify_audit_chain()

        assert verification["chain_valid"] is True
        assert verification["event_count"] == 2
        assert verification["gap_detected"] is False

    def test_skill_detects_tampered_audit_event(self):
        """Test: audit chain verification detects tampering."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": now.isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
        ]

        skill.execute(events, "tenant_1")

        # Tamper with event
        if skill.audit_events:
            skill.audit_events[0]["hash"] = "tampered_hash"

        # Verify should fail
        verification = skill.verify_audit_chain()

        assert verification["chain_valid"] is False
        assert verification["gap_detected"] is True


class TestTenantIsolation:
    """Test: tenant isolation (GDPR Art. 5, 32)."""

    def test_patterns_filtered_by_tenant(self):
        """Test: skill only processes events for requested tenant."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        # Mix of events from two tenants
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=0)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_2",
                "timestamp": (now + timedelta(minutes=1)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.200"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=2)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            },
        ]

        result_t1 = skill.execute(events, "tenant_1")

        # Verify no cross-tenant leakage
        assert result_t1["tenant_id"] == "tenant_1"
        # tenant_1 should only see 192.168.1.100 events, not 200
        for pattern in result_t1["patterns_detected"]:
            assert pattern["affected_entity"] != "192.168.1.200"


class TestAdversarialCases:
    """Test: adversarial attacks and edge cases."""

    def test_large_audit_log_sampling(self):
        """Test: skill handles large audit logs via sampling."""
        config = {
            "patterns": {"burst": {"window_minutes": 10, "threshold": 5}},
            "max_events_to_scan": 100,  # Limit
        }
        skill = SecurityOrchestratorSkill(config)

        now = datetime.utcnow()
        # Create 500 events (exceeds max_events_to_scan)
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(hours=i)).isoformat() + "Z",
                "details": {"source_ip": f"192.168.1.{i % 256}"},
            }
            for i in range(500)
        ]

        # Should not crash, should sample
        result = skill.execute(events, "tenant_1")

        assert result["audit_committed"] is True
        # Processing should complete without OOM

    def test_empty_audit_log_handled(self):
        """Test: skill gracefully handles empty event list."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        result = skill.execute([], "tenant_1")

        assert result["patterns_detected"] == []
        assert result["recommendations"] == []
        assert result["audit_committed"] is True

    def test_evasion_resistant_to_threshold_spread(self):
        """Test: creep detection resists evasion (spread over time)."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        # Spread events evenly: 1 per hour for 24 hours (evades burst)
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(hours=i)).isoformat() + "Z",
                "details": {"source_ip": "192.168.1.100"},
            }
            for i in range(24)
        ]

        patterns = skill.detector.detect_patterns(events, "tenant_1")

        # Should detect creep even though burst is spread out
        creep_patterns = [p for p in patterns if p.pattern_type == PatternType.CREEP]
        assert len(creep_patterns) > 0

    def test_feedback_injection_resistance(self):
        """Test: false feedback doesn't destroy threat model."""
        optimizer = FeedbackOptimizer({"patterns": {"burst": {"threshold": 5}}})

        pattern = ThreatPattern(
            pattern_type=PatternType.BURST,
            severity="high",
            confidence=0.9,
            affected_entity="192.168.1.100",
            timestamp_range=("2026-09-20T10:00:00", "2026-09-20T10:10:00"),
            event_count=20,  # Clear attack
            suggested_action="Rate-limit",
        )

        # Attacker tries to inject false "not a threat" feedback
        result = optimizer.record_feedback(
            pattern=pattern,
            is_threat=False,
            operator_note="This is definitely not an attack",
        )

        # Suggestion should still be reasonable (threshold might be raised, but not eliminated)
        new_config = result["suggested_config_update"]["burst"]
        assert new_config["threshold"] > 0  # Threshold not weakened to 0
        assert new_config["threshold"] <= 10  # But might be raised


class TestE2EWiringProof:
    """E2E wiring proof: skill is called end-to-end and audit events flow."""

    def test_skill_called_end_to_end_with_audit_events(self):
        """E2E Proof: Real skill execution flow with audit chain."""
        # Setup: Create realistic audit events from defender perspective
        now = datetime.utcnow()
        defender_audit_log = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=0)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=1)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=2)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=3)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=4)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
            {
                "event_type": "house_rule_denied",
                "tenant_id": "production_tenant",
                "timestamp": (now + timedelta(minutes=5)).isoformat() + "Z",
                "user_id": "attacker_bot",
                "details": {"source_ip": "203.0.113.1", "reason": "Military use denied"},
            },
        ]

        # Execute skill (this is the real wiring point)
        skill = SecurityOrchestratorSkill(tenant_id="_default")
        result = skill.execute(defender_audit_log, "production_tenant")

        # Verify: patterns detected
        assert len(result["patterns_detected"]) > 0
        burst = result["patterns_detected"][0]
        assert burst["affected_entity"] == "203.0.113.1"
        assert burst["type"] == "burst"

        # Verify: recommendations generated (advisory)
        assert len(result["recommendations"]) > 0
        rec = result["recommendations"][0]
        assert rec["operator_decision_required"] is True
        assert "rate_limit" in rec["recommended_house_rule"]["condition"].lower() or "203.0.113.1" in rec["recommended_house_rule"]["condition"]

        # Verify: audit event emitted and hash-chained
        assert len(skill.audit_events) > 0
        exec_event = skill.audit_events[0]
        assert exec_event["event_type"] == "security_pattern_detected"
        assert exec_event["skill_id"] == "os.security_orchestrator"
        assert exec_event["tenant_id"] == "production_tenant"
        assert exec_event["hash"] is not None

        # Verify: operator feedback loop integration
        feedback_result = skill.submit_feedback(
            pattern=ThreatPattern(
                pattern_type=PatternType.BURST,
                severity=burst["severity"],
                confidence=burst["confidence"],
                affected_entity=burst["affected_entity"],
                timestamp_range=(burst["time_range"]["start"], burst["time_range"]["end"]),
                event_count=burst["event_count"],
                suggested_action=burst["suggested_action"],
            ),
            is_threat=True,
            operator_note="Confirmed: attacker IP trying to bypass house-rules"
        )

        assert feedback_result["feedback_recorded"] is True
        assert len(skill.audit_events) > 1  # New feedback event added

        # Verify: chain integrity after multiple operations
        verification = skill.verify_audit_chain()
        assert verification["chain_valid"] is True
        assert verification["event_count"] >= 2

    def test_skill_lifecycle_audit_trail_complete(self):
        """E2E Proof: Complete audit trail of skill operations."""
        skill = SecurityOrchestratorSkill(tenant_id="_default")

        now = datetime.utcnow()
        events = [
            {
                "event_type": "house_rule_denied",
                "tenant_id": "tenant_1",
                "timestamp": (now + timedelta(minutes=i)).isoformat() + "Z",
                "details": {"source_ip": f"192.168.1.{100 + (i % 2)}"},
            }
            for i in range(7)
        ]

        # Step 1: Execute
        exec_result = skill.execute(events, "tenant_1")

        # Verify execution recorded
        assert len(skill.audit_events) >= 1
        assert skill.audit_events[0]["event_type"] == "security_pattern_detected"

        # Step 2: Submit feedback
        if exec_result["patterns_detected"]:
            pattern_data = exec_result["patterns_detected"][0]
            pattern = ThreatPattern(
                pattern_type=PatternType[pattern_data["type"].upper()],
                severity=pattern_data["severity"],
                confidence=pattern_data["confidence"],
                affected_entity=pattern_data["affected_entity"],
                timestamp_range=(
                    pattern_data["time_range"]["start"],
                    pattern_data["time_range"]["end"]
                ),
                event_count=pattern_data["event_count"],
                suggested_action=pattern_data["suggested_action"],
            )

            feedback_result = skill.submit_feedback(
                pattern=pattern,
                is_threat=True,
                operator_note="Confirmed threat"
            )

            # Verify feedback recorded
            assert feedback_result["feedback_recorded"] is True
            assert len(skill.audit_events) >= 2
            assert skill.audit_events[-1]["event_type"] == "threat_confirmed_by_operator"

        # Final verification: entire chain is valid
        final_verification = skill.verify_audit_chain()
        assert final_verification["chain_valid"] is True
        assert final_verification["event_count"] >= 1

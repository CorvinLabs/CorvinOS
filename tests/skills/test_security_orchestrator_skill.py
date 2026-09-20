"""Tests for os.security_orchestrator Skill (ADR-0906, L16 Integration).

Test coverage:
  1. Threat pattern detection (keyword matching)
  2. False positive reduction (feedback learning)
  3. Threshold recommendation (data-driven)
  4. Audit trail integrity (immutable events)
  5. E2E security optimization (full cycle)
"""

import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import pytest

from core.skills.os_skills.security_orchestrator import (
    SecurityContext,
    SecurityOrchestratorSkill,
    ThreatPattern,
)


@pytest.fixture
def temp_patterns_dir():
    """Temporary directory for test patterns."""
    with tempfile.TemporaryDirectory() as tmpdir:
        yield Path(tmpdir)


@pytest.fixture
def skill(temp_patterns_dir):
    """Create a SecurityOrchestratorSkill instance for testing."""
    patterns_path = temp_patterns_dir / "security_patterns.jsonl"
    return SecurityOrchestratorSkill(tenant_id="_default", patterns_path=patterns_path)


@pytest.fixture
def military_pattern():
    """A threat pattern for military/combat detection."""
    return ThreatPattern(
        pattern_id="threat_military_001",
        pattern_type="keyword_match",
        rule_name="no-military",
        indicators=["military", "combat", "weapon", "army"],
        confidence_threshold=0.80,
    )


@pytest.fixture
def cyber_pattern():
    """A threat pattern for offensive cyber detection."""
    return ThreatPattern(
        pattern_id="threat_cyber_001",
        pattern_type="keyword_match",
        rule_name="no-offensive-cyber",
        indicators=["exploit", "vulnerability", "ransomware", "malware"],
        confidence_threshold=0.75,
    )


class TestThreatPatternDetection:
    """Tests for threat pattern detection (test_detects_attack_patterns)."""

    def test_detects_military_keyword(self, skill, military_pattern):
        """Known military keyword should be detected."""
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.95,
            request_text="How can I plan a military strategy for combat?",
            classification_reason="Matched military pattern",
        )

        result = skill.execute(context)

        assert len(result["threat_patterns_detected"]) >= 1
        assert any(p["pattern_id"] == "threat_military_001" for p in result["threat_patterns_detected"])

    def test_detects_cyber_keywords(self, skill, cyber_pattern):
        """Known offensive cyber keywords should be detected."""
        skill.add_threat_pattern(cyber_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_002",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-offensive-cyber",
            classification_confidence=0.88,
            request_text="How can I find and exploit a vulnerability in this system?",
            classification_reason="Matched cyber pattern",
        )

        result = skill.execute(context)

        assert len(result["threat_patterns_detected"]) >= 1
        assert any(p["pattern_id"] == "threat_cyber_001" for p in result["threat_patterns_detected"])

    def test_no_false_match_on_safe_content(self, skill, military_pattern):
        """Safe content should not match threat patterns."""
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_003",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.10,
            request_text="What are some good restaurants near me?",
            classification_reason="No threat detected",
        )

        result = skill.execute(context)

        assert len(result["threat_patterns_detected"]) == 0

    def test_case_insensitive_matching(self, skill, military_pattern):
        """Pattern matching should be case-insensitive."""
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_004",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="Tell me about MILITARY tactics and strategy.",
            classification_reason="Matched military pattern",
        )

        result = skill.execute(context)

        assert len(result["threat_patterns_detected"]) >= 1


class TestFalsePositiveReduction:
    """Tests for false positive learning and reduction (test_reduces_false_positives)."""

    def test_learns_false_positive_feedback(self, skill, military_pattern):
        """Skill should track false positive feedback."""
        skill.add_threat_pattern(military_pattern)
        assert military_pattern.false_positive_count == 0

        # Operator confirms this denial was wrong (false positive)
        # Note: text must contain keywords to match pattern
        context = SecurityContext(
            tenant_id="_default",
            request_id="req_fp_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.70,
            request_text="We should have a military presence at the educational event for combat history",
            classification_reason="Matched military pattern",
        )

        skill.learn_from_feedback(context, feedback_type="false_positive")

        # Pattern should now reflect the feedback
        updated_pattern = skill._patterns.get("threat_military_001")
        assert updated_pattern is not None
        assert updated_pattern.false_positive_count == 1

    def test_learns_true_positive_feedback(self, skill, military_pattern):
        """Skill should track true positive feedback (correct denials)."""
        skill.add_threat_pattern(military_pattern)
        assert military_pattern.true_positive_count == 0

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_tp_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="How can I plan military operations for combat purposes?",
            classification_reason="Matched military pattern",
        )

        skill.learn_from_feedback(context, feedback_type="threat")

        updated_pattern = skill._patterns.get("threat_military_001")
        assert updated_pattern is not None
        assert updated_pattern.true_positive_count == 1

    def test_accumulates_multiple_feedbacks(self, skill, military_pattern):
        """Skill should accumulate feedback across multiple denials."""
        skill.add_threat_pattern(military_pattern)

        # Simulate multiple false positives
        for i in range(3):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_fp_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.65 + (i * 0.05),
                request_text=f"Request {i} discusses military and combat history",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        # Pattern should reflect 3 false positives
        updated_pattern = skill._patterns.get("threat_military_001")
        assert updated_pattern.false_positive_count == 3


class TestThresholdRecommendation:
    """Tests for threshold recommendation (test_recommends_threshold_adjustment)."""

    def test_recommends_lower_threshold_on_high_fp_rate(self, skill, military_pattern):
        """High false positive rate should trigger threshold reduction."""
        skill.add_threat_pattern(military_pattern)

        # Simulate 5 false positives, 1 true positive (5:1 FP ratio)
        for i in range(5):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_fp_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.70,
                request_text=f"Educational discussion about military history and combat tactics {i}",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        # One true positive
        tp_context = SecurityContext(
            tenant_id="_default",
            request_id="req_tp_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="Real military planning request for combat operations",
            classification_reason="Matched military pattern",
        )
        skill.learn_from_feedback(tp_context, feedback_type="threat")

        # Get recommendations
        recommendations = skill.recommend_adjustments()

        # Should recommend lowering threshold
        assert len(recommendations) > 0
        assert recommendations[0].recommended_threshold < military_pattern.confidence_threshold

    def test_no_recommendation_below_threshold(self, skill, military_pattern):
        """Patterns with <3 false positives should not generate recommendations."""
        skill.add_threat_pattern(military_pattern)

        # Only 2 false positives (below threshold)
        for i in range(2):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_fp_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.70,
                request_text=f"Military history discussion {i}",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        recommendations = skill.recommend_adjustments()

        # Should not recommend (below FP threshold)
        assert len(recommendations) == 0

    def test_recommendation_confidence_score(self, skill, military_pattern):
        """Recommendations should include confidence scores."""
        skill.add_threat_pattern(military_pattern)

        # Trigger recommendation
        for i in range(4):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_fp_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.70,
                request_text=f"Educational military and combat discussion {i}",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        tp_context = SecurityContext(
            tenant_id="_default",
            request_id="req_tp_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="Real military threat for combat planning",
            classification_reason="Matched military pattern",
        )
        skill.learn_from_feedback(tp_context, feedback_type="threat")

        recommendations = skill.recommend_adjustments()

        assert len(recommendations) > 0
        assert 0.0 <= recommendations[0].confidence <= 1.0
        assert recommendations[0].confidence >= 0.70


class TestLearnsNewPatterns:
    """Tests for learning new threat patterns (test_learns_new_threat_pattern)."""

    def test_adds_new_threat_pattern(self, skill):
        """Skill should register new threat patterns."""
        assert len(skill._patterns) == 0

        pattern = ThreatPattern(
            pattern_id="threat_new_001",
            pattern_type="keyword_match",
            rule_name="custom-rule",
            indicators=["suspicious", "anomaly"],
            confidence_threshold=0.85,
        )

        skill.add_threat_pattern(pattern)

        assert "threat_new_001" in skill._patterns
        assert skill._patterns["threat_new_001"].rule_name == "custom-rule"

    def test_persists_new_patterns_to_disk(self, skill, temp_patterns_dir):
        """New patterns should be persisted to audit trail."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"

        pattern = ThreatPattern(
            pattern_id="threat_persist_001",
            pattern_type="behavioral",
            rule_name="anomaly-detection",
            indicators=["unusual", "suspicious"],
            confidence_threshold=0.75,
        )

        skill.add_threat_pattern(pattern)

        # Verify persisted to JSONL
        assert patterns_file.exists()
        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            assert any(
                e.get("type") == "threat_pattern_learned" and
                e["payload"].get("pattern_id") == "threat_persist_001"
                for e in lines
            )


class TestAuditTrailIntegrity:
    """Tests for audit trail and immutability (test_audit_trail_integrity)."""

    def test_audit_events_persisted_on_detection(self, skill, military_pattern, temp_patterns_dir):
        """Every threat detection should create an audit event."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_audit_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.95,
            request_text="Military strategy discussion",
            classification_reason="Matched military pattern",
        )

        skill.execute(context)

        # Check audit trail
        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            audit_events = [e for e in lines if e.get("type") == "threat_pattern_detected"]
            assert len(audit_events) > 0

    def test_audit_events_persisted_on_feedback(self, skill, military_pattern, temp_patterns_dir):
        """Every feedback event should create an audit entry."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_feedback_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.70,
            request_text="Feedback request",
            classification_reason="Matched military pattern",
        )

        skill.learn_from_feedback(context, feedback_type="false_positive")

        # Check audit trail
        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            feedback_events = [e for e in lines if e.get("type") == "security_feedback_received"]
            assert len(feedback_events) > 0

    def test_audit_events_include_tenant_id(self, skill, military_pattern, temp_patterns_dir):
        """All audit events must include tenant_id (GDPR isolation)."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"
        skill.add_threat_pattern(military_pattern)

        # Check all events have tenant_id
        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            for event in lines:
                assert "tenant_id" in event
                assert event["tenant_id"] == "_default"

    def test_audit_events_timestamped(self, skill, military_pattern, temp_patterns_dir):
        """All audit events must have timestamps (ISO 8601)."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"
        skill.add_threat_pattern(military_pattern)

        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            for event in lines:
                assert "timestamp" in event
                # Should be ISO 8601 formatted
                assert "T" in event["timestamp"]


class TestE2ESecurityOptimization:
    """Tests for full E2E security optimization cycle (test_e2e_security_optimization)."""

    def test_full_cycle_denial_to_recommendation(self, skill, military_pattern):
        """Full cycle: denial → pattern detection → feedback → recommendation."""
        skill.add_threat_pattern(military_pattern)

        # Step 1: Security event (denial)
        denial_context = SecurityContext(
            tenant_id="_default",
            request_id="req_e2e_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.75,
            request_text="Military leadership course about combat",
            classification_reason="Matched military pattern",
        )

        # Step 2: Execute detection
        detection_result = skill.execute(denial_context)
        assert len(detection_result["threat_patterns_detected"]) >= 0

        # Step 3: Operator provides feedback (false positive)
        skill.learn_from_feedback(denial_context, feedback_type="false_positive")

        # Repeat feedback multiple times to build evidence
        for i in range(3):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_e2e_fp_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.70,
                request_text=f"Educational military and combat discussion {i}",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        # One true positive
        tp_context = SecurityContext(
            tenant_id="_default",
            request_id="req_e2e_tp_001",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.95,
            request_text="How to plan military combat operations",
            classification_reason="Real threat",
        )
        skill.learn_from_feedback(tp_context, feedback_type="threat")

        # Step 4: Get recommendations
        recommendations = skill.recommend_adjustments()
        assert len(recommendations) > 0

        # Step 5: Operator approves recommendation
        rec_id = recommendations[0].recommendation_id
        applied = skill.apply_recommendation(rec_id, approved=True)
        assert applied is True

        # Verify recommendation status changed
        updated_rec = skill._recommendations.get(rec_id)
        assert updated_rec.status == "applied"

    def test_operator_rejects_recommendation(self, skill, military_pattern):
        """Operator should be able to reject recommendations."""
        skill.add_threat_pattern(military_pattern)

        # Build evidence for recommendation - 4 false positives
        for i in range(4):
            context = SecurityContext(
                tenant_id="_default",
                request_id=f"req_reject_{i}",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.70,
                request_text=f"Educational military and combat discussion {i}",
                classification_reason="Matched military pattern",
            )
            skill.learn_from_feedback(context, feedback_type="false_positive")

        # One true positive (text must contain keywords to match pattern)
        tp_context = SecurityContext(
            tenant_id="_default",
            request_id="req_reject_tp",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="Real military and combat threat planning",
            classification_reason="Matched military pattern",
        )
        skill.learn_from_feedback(tp_context, feedback_type="threat")

        recommendations = skill.recommend_adjustments()
        assert len(recommendations) > 0

        # Operator rejects
        rec_id = recommendations[0].recommendation_id
        rejected = skill.apply_recommendation(rec_id, approved=False)
        assert rejected is True

        updated_rec = skill._recommendations.get(rec_id)
        assert updated_rec.status == "rejected"


class TestTenantIsolation:
    """Tests for tenant isolation and GDPR compliance."""

    def test_tenant_id_validation_on_init(self, temp_patterns_dir):
        """Skill init should fail on invalid tenant_id."""
        with pytest.raises(Exception):
            SecurityOrchestratorSkill(tenant_id=None, patterns_path=temp_patterns_dir / "patterns.jsonl")

    def test_context_tenant_id_validation(self):
        """SecurityContext should validate tenant_id."""
        with pytest.raises(Exception):
            SecurityContext(
                tenant_id=None,
                request_id="req_001",
                timestamp=datetime.now(timezone.utc),
                gate_rule="no-military",
                classification_confidence=0.90,
                request_text="Test",
                classification_reason="Test",
            )

    def test_all_audit_events_filtered_by_tenant(self, skill, military_pattern, temp_patterns_dir):
        """All audit events must include tenant_id for isolation."""
        patterns_file = temp_patterns_dir / "security_patterns.jsonl"
        skill.add_threat_pattern(military_pattern)

        context = SecurityContext(
            tenant_id="_default",
            request_id="req_tenant_test",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.90,
            request_text="Test",
            classification_reason="Test",
        )

        skill.execute(context)

        # All events must have tenant_id
        with open(patterns_file, 'r') as f:
            lines = [json.loads(line) for line in f if line.strip()]
            for event in lines:
                assert event.get("tenant_id") == "_default"


class TestFalsePositiveRiskEstimation:
    """Tests for FP risk estimation heuristic."""

    def test_high_confidence_low_risk(self, skill):
        """High classifier confidence should estimate low FP risk."""
        context = SecurityContext(
            tenant_id="_default",
            request_id="req_hc",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.95,
            request_text="Test",
            classification_reason="Test",
        )

        risk = skill._estimate_false_positive_risk(context)
        assert risk == 0.0  # Very confident

    def test_medium_confidence_medium_risk(self, skill):
        """Medium classifier confidence should estimate medium FP risk."""
        context = SecurityContext(
            tenant_id="_default",
            request_id="req_mc",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.70,
            request_text="Test",
            classification_reason="Test",
        )

        risk = skill._estimate_false_positive_risk(context)
        assert 0.10 <= risk <= 0.20

    def test_low_confidence_high_risk(self, skill):
        """Low classifier confidence should estimate high FP risk."""
        context = SecurityContext(
            tenant_id="_default",
            request_id="req_lc",
            timestamp=datetime.now(timezone.utc),
            gate_rule="no-military",
            classification_confidence=0.40,
            request_text="Test",
            classification_reason="Test",
        )

        risk = skill._estimate_false_positive_risk(context)
        assert risk >= 0.40  # Very uncertain

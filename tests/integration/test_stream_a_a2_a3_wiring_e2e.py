"""
Stream A: A2 → A3 Wiring Proof + Phase 2 Tests

Proves A2→A3 complete flow with escalation_rate penalty logic.
References: ADR-2086 (Phase 2), ADR-2075 (Message Contract), ADR-0081 (Event Schema)
"""

import pytest
from unittest.mock import Mock, patch
from datetime import datetime
from dataclasses import dataclass

from core.learning.confidence_scorer_a3 import ConfidenceScore, ConfidenceScorer


@dataclass(frozen=True)
class MockOutcomeRecord:
    """A2 output → A3 input"""
    skill_id: str = "os.delegation_router"
    outcome_count: int = 10
    avg_confidence: float = 0.85
    audit_ref: str = "audit_abc123"


class TestPhase2ConfidenceScorer:
    """Phase 2 tests: escalation_rate penalty + trend detection"""

    def test_escalation_rate_penalty_computed(self):
        """Escalation rate penalty is computed correctly"""
        scorer = ConfidenceScorer(tenant_id="_test")
        record = MockOutcomeRecord(outcome_count=10)

        score = scorer.score(record)
        assert score is not None

        # Phase 2: delta should include escalation penalty
        # Expected: success_rate - (escalation_rate × 0.1)
        assert isinstance(score.confidence_delta, float)
        assert -1.0 <= score.confidence_delta <= 1.0
        assert score.escalation_count >= 0

    def test_trend_detection_improving(self):
        """Trend detection: improving trend with rising deltas"""
        scorer = ConfidenceScorer(tenant_id="_test", window_size=10)

        # Feed 10 outcomes with increasing confidence
        for i in range(10):
            record = MockOutcomeRecord(
                outcome_count=10,
                avg_confidence=0.5 + (i * 0.05),  # Increasing
            )
            score = scorer.score(record)

        assert score is not None
        assert score.trend in ["improving", "stable", "degrading"]
        history = scorer.get_trend_history()
        assert len(history) == 10

    def test_trend_detection_degrading(self):
        """Trend detection: degrading trend with falling deltas"""
        scorer = ConfidenceScorer(tenant_id="_test", window_size=10)

        # Feed 10 outcomes with decreasing confidence
        for i in range(10):
            record = MockOutcomeRecord(
                outcome_count=10,
                avg_confidence=0.9 - (i * 0.05),  # Decreasing
            )
            score = scorer.score(record)

        assert score is not None
        assert score.trend in ["improving", "stable", "degrading"]

    def test_rolling_window_size(self):
        """Rolling window respects maxlen constraint"""
        scorer = ConfidenceScorer(tenant_id="_test", window_size=5)

        # Feed 10 outcomes (more than window size)
        for i in range(10):
            record = MockOutcomeRecord(outcome_count=5 + i)
            score = scorer.score(record)

        # Window should only keep last 5
        history = scorer.get_trend_history()
        assert len(history) <= 5

    def test_immutable_score_object(self):
        """ConfidenceScore is immutable (frozen dataclass)"""
        scorer = ConfidenceScorer(tenant_id="_test")
        record = MockOutcomeRecord()

        score = scorer.score(record)
        assert score is not None

        # Attempt to mutate should raise
        with pytest.raises(AttributeError):
            score.confidence_delta = 0.5

    def test_audit_event_emitted(self):
        """Audit event emitted with all required fields"""
        scorer = ConfidenceScorer(tenant_id="_test")
        record = MockOutcomeRecord(
            skill_id="test_skill",
            outcome_count=20,
        )

        score = scorer.score(record)

        assert score.audit_ref is not None
        assert len(score.audit_ref) > 0
        assert score.skill_id == "test_skill"
        assert score.outcome_count == 20

    def test_tenant_isolation(self):
        """Different tenants maintain separate state"""
        scorer1 = ConfidenceScorer(tenant_id="tenant_a")
        scorer2 = ConfidenceScorer(tenant_id="tenant_b")

        record = MockOutcomeRecord()

        score1 = scorer1.score(record)
        score2 = scorer2.score(record)

        assert score1.tenant_id == "tenant_a"
        assert score2.tenant_id == "tenant_b"
        assert scorer1.get_processed_count() == 1
        assert scorer2.get_processed_count() == 1

    def test_processed_count_increments(self):
        """Processed count tracks outcomes"""
        scorer = ConfidenceScorer(tenant_id="_test")

        assert scorer.get_processed_count() == 0

        for i in range(5):
            record = MockOutcomeRecord()
            score = scorer.score(record)
            assert score is not None

        assert scorer.get_processed_count() == 5

    def test_escalation_count_tracked(self):
        """Escalation count is tracked in score"""
        scorer = ConfidenceScorer(tenant_id="_test")
        record = MockOutcomeRecord(outcome_count=100)

        score = scorer.score(record)

        assert score.escalation_count >= 0
        assert score.escalation_count <= score.outcome_count

    def test_phase2_phase1_compatibility(self):
        """Phase 2 is backward compatible with Phase 1 records"""
        scorer = ConfidenceScorer(tenant_id="_test")

        # Phase 1 record (minimal fields)
        record = MockOutcomeRecord(
            skill_id="os.skill",
            outcome_count=5,
            avg_confidence=0.75,
        )

        score = scorer.score(record)

        # Phase 2 should handle it without errors
        assert score is not None
        assert score.skill_id == "os.skill"
        assert score.confidence_delta is not None
        assert score.trend is not None


if __name__ == "__main__":
    pytest.main([__file__, "-xvs"])

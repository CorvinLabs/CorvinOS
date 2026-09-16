"""Unit tests for SkillLearningLoop k=1 (Event schema + collection)."""

import pytest
from datetime import datetime
from core.skills.skill_learning_loop import SkillLearningLoop
from core.skills.models.learning_event import (
    LearningEventType, FeedbackOutcome, SkillExecutedEvent, 
    OutcomeFeedbackEvent, ConfidenceScoreEvent
)


class TestSkillExecutionEvents:
    """k=1: Skill execution event collection."""
    
    def test_record_execution_success(self):
        """Test recording a successful skill execution."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_execution(
            input_data={"query": "test"},
            output_data={"result": "success"},
            latency_ms=42.5,
            lom="test:record_execution_success:L20"
        )
        
        assert event.skill_id == "test-skill"
        assert event.tenant_id == "_default"
        assert event.latency_ms == 42.5
        assert event.error is None
        assert isinstance(event, SkillExecutedEvent)
    
    def test_record_execution_with_error(self):
        """Test recording a skill execution that failed."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_execution(
            input_data={"query": "bad"},
            output_data={},
            latency_ms=1.2,
            error="RuntimeError: skill failed",
            lom="test:record_execution_with_error:L40"
        )
        
        assert event.error == "RuntimeError: skill failed"
        assert event.output_data == {}
    
    def test_execution_events_chained(self):
        """Test that execution events are hash-chained."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event1 = loop.record_execution(
            input_data={"q": "1"},
            output_data={"r": "1"},
            latency_ms=10.0,
            lom="test:L1"
        )
        event2 = loop.record_execution(
            input_data={"q": "2"},
            output_data={"r": "2"},
            latency_ms=20.0,
            lom="test:L2"
        )
        
        # Both should have chain_hash set
        assert event1.chain_hash != ""
        assert event2.chain_hash != ""
        # Event2's prev_hash should point to Event1
        assert event2.prev_hash == event1.chain_hash
    
    def test_execution_event_count(self):
        """Test counting execution events."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        for i in range(5):
            loop.record_execution(
                input_data={"i": i},
                output_data={"i": i},
                latency_ms=float(i),
                lom=f"test:L{i}"
            )
        
        assert loop.event_count(LearningEventType.SKILL_EXECUTED) == 5


class TestOutcomeFeedback:
    """k=1: Outcome feedback collection (never stores reason, GDPR Art. 5)."""
    
    def test_record_correct_feedback(self):
        """Test recording correct outcome feedback."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_outcome_feedback(
            outcome=FeedbackOutcome.CORRECT,
            reason="Model output matched expected result",
            lom="test:record_correct_feedback:L80"
        )
        
        assert event.outcome == FeedbackOutcome.CORRECT
        # GDPR: reason never stored in event
        assert event.reason == ""
        assert event.event_type == LearningEventType.OUTCOME_FEEDBACK
    
    def test_record_incorrect_feedback(self):
        """Test recording incorrect outcome feedback."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_outcome_feedback(
            outcome=FeedbackOutcome.INCORRECT,
            reason="Output was completely wrong",
            lom="test:L90"
        )
        
        assert event.outcome == FeedbackOutcome.INCORRECT
    
    def test_record_partial_feedback(self):
        """Test recording partial feedback."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_outcome_feedback(
            outcome=FeedbackOutcome.PARTIAL,
            lom="test:L105"
        )
        
        assert event.outcome == FeedbackOutcome.PARTIAL
    
    def test_feedback_event_count(self):
        """Test counting feedback events."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_outcome_feedback(FeedbackOutcome.CORRECT)
        loop.record_outcome_feedback(FeedbackOutcome.INCORRECT)
        loop.record_outcome_feedback(FeedbackOutcome.PARTIAL)
        
        assert loop.event_count(LearningEventType.OUTCOME_FEEDBACK) == 3


class TestConfidenceScores:
    """k=1: Confidence score observation and tracking."""
    
    def test_record_confidence_score(self):
        """Test recording a confidence score."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        event = loop.record_confidence_score(
            confidence=0.75,
            basis="success_rate=0.8, feedback_engagement=0.7",
            lom="test:record_confidence_score:L135"
        )
        
        assert event.confidence == 0.75
        assert event.basis == "success_rate=0.8, feedback_engagement=0.7"
        assert isinstance(event, ConfidenceScoreEvent)
    
    def test_confidence_score_validation(self):
        """Test that confidence must be 0.0-1.0."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        with pytest.raises(ValueError):
            loop.record_confidence_score(confidence=1.5)
        
        with pytest.raises(ValueError):
            loop.record_confidence_score(confidence=-0.1)
    
    def test_confidence_history_tracking(self):
        """Test that confidence history is tracked."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        assert loop.current_confidence() == 0.5  # baseline
        
        loop.record_confidence_score(0.6)
        assert loop.current_confidence() == 0.6
        
        loop.record_confidence_score(0.7)
        assert loop.current_confidence() == 0.7
    
    def test_confidence_stable_short_history(self):
        """Test confidence stability check with insufficient data."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_confidence_score(0.5)
        loop.record_confidence_score(0.6)
        
        # Window=10, but only 3 observations (baseline + 2 recorded)
        assert loop.confidence_stable(window=10) is False
    
    def test_confidence_stable_within_tolerance(self):
        """Test confidence stability with stable scores."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        # Record 10 stable scores (0.75 ± 0.02)
        for _ in range(10):
            loop.record_confidence_score(0.75)
        
        assert loop.confidence_stable(window=10, tolerance=0.05) is True
    
    def test_confidence_unstable_exceeds_tolerance(self):
        """Test confidence instability detection."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        # Oscillate between 0.5 and 0.9
        for i in range(10):
            loop.record_confidence_score(0.5 if i % 2 == 0 else 0.9)
        
        assert loop.confidence_stable(window=10, tolerance=0.05) is False


class TestEventRetrievalAndTenantIsolation:
    """k=1: Event retrieval and tenant-scoped isolation (GDPR Art. 5, 6)."""
    
    def test_retrieve_all_events(self):
        """Test retrieving all events."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_execution(
            input_data={},
            output_data={},
            latency_ms=10.0,
            lom="test:L205"
        )
        loop.record_outcome_feedback(FeedbackOutcome.CORRECT)
        
        events = loop.get_events()
        assert len(events) == 2
    
    def test_retrieve_events_by_type(self):
        """Test filtering events by type."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_execution(input_data={}, output_data={}, latency_ms=10.0, lom="L1")
        loop.record_execution(input_data={}, output_data={}, latency_ms=20.0, lom="L2")
        loop.record_outcome_feedback(FeedbackOutcome.CORRECT)
        
        executed = loop.get_events(LearningEventType.SKILL_EXECUTED)
        assert len(executed) == 2
        
        feedback = loop.get_events(LearningEventType.OUTCOME_FEEDBACK)
        assert len(feedback) == 1
    
    def test_tenant_isolation_on_construction(self):
        """Test that tenant_id is immutable."""
        loop = SkillLearningLoop("test-skill", "tenant-1")
        assert loop.tenant_id == "tenant-1"
        
        # Event should carry the same tenant_id
        event = loop.record_execution(
            input_data={},
            output_data={},
            latency_ms=10.0,
            lom="test:L235"
        )
        assert event.tenant_id == "tenant-1"
    
    def test_event_store_tenant_mismatch_raises(self):
        """Test that event store rejects cross-tenant events."""
        loop = SkillLearningLoop("test-skill", "tenant-1")
        
        # Manually create an event with wrong tenant (should be rejected)
        from core.skills.models.learning_event import LearningEvent
        bad_event = LearningEvent(
            event_type=LearningEventType.SKILL_EXECUTED,
            skill_id="test-skill",
            tenant_id="tenant-2",  # MISMATCH
            timestamp=datetime.utcnow(),
        )
        
        with pytest.raises(ValueError, match="Tenant mismatch"):
            loop.event_store.append_event(bad_event)


class TestAuditLogSerialization:
    """k=1: Audit log serialization to JSONL (ADR-0232)."""
    
    def test_to_jsonl_format(self):
        """Test JSONL serialization of events."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_execution(
            input_data={"q": "test"},
            output_data={"r": "ok"},
            latency_ms=42.0,
            lom="test:L270"
        )
        
        jsonl = loop.to_audit_log()
        
        # Should be valid JSONL (one JSON per line)
        lines = jsonl.strip().split("\n")
        assert len(lines) == 1
        
        import json
        event_dict = json.loads(lines[0])
        assert event_dict["skill_id"] == "test-skill"
        assert event_dict["event_type"] == "skill_executed"
        assert event_dict["latency_ms"] == 42.0
    
    def test_jsonl_includes_chain_hash(self):
        """Test that JSONL includes chain hash for audit trail."""
        loop = SkillLearningLoop("test-skill", "_default")
        
        loop.record_execution(
            input_data={},
            output_data={},
            latency_ms=10.0,
            lom="test:L295"
        )
        
        jsonl = loop.to_audit_log()
        
        import json
        event_dict = json.loads(jsonl.strip())
        assert "chain_hash" in event_dict
        assert event_dict["chain_hash"] != ""


class TestIntegrationK1:
    """k=1 integration tests: Full learning loop with 8+ event types."""
    
    def test_full_learning_workflow(self):
        """Test complete learning workflow: execute → feedback → confidence."""
        loop = SkillLearningLoop("classifier-skill", "_default")
        
        # Execute skill 10 times
        for i in range(10):
            loop.record_execution(
                input_data={"text": f"sample {i}"},
                output_data={"label": f"class_{i % 3}"},
                latency_ms=float(10 + i),
                lom=f"test:execute:{i}"
            )
        
        # Collect 10 feedback events
        for i in range(10):
            loop.record_outcome_feedback(
                outcome=FeedbackOutcome.CORRECT if i < 8 else FeedbackOutcome.INCORRECT,
                lom=f"test:feedback:{i}"
            )
        
        # Observe confidence scores
        loop.record_confidence_score(
            confidence=0.8,  # 8/10 correct
            basis="8 correct / 10 total",
            lom="test:confidence:final"
        )
        
        # Verify event counts
        assert loop.event_count(LearningEventType.SKILL_EXECUTED) == 10
        assert loop.event_count(LearningEventType.OUTCOME_FEEDBACK) == 10
        assert loop.event_count(LearningEventType.CONFIDENCE_SCORE) == 1
        
        # Verify confidence
        assert loop.current_confidence() == 0.8
    
    def test_event_chain_integrity(self):
        """Test that event chain is properly hash-linked."""
        loop = SkillLearningLoop("skill", "_default")
        
        events = []
        for i in range(5):
            event = loop.record_execution(
                input_data={"i": i},
                output_data={"i": i},
                latency_ms=float(i * 10),
                lom=f"test:chain:{i}"
            )
            events.append(event)
        
        # Verify chain links
        for i in range(1, len(events)):
            assert events[i].prev_hash == events[i-1].chain_hash
            assert events[i].chain_hash != ""
        
        # First event should have empty prev_hash
        assert events[0].prev_hash == ""

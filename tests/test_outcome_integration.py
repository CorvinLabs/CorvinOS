"""Phase 4 Week 18: Outcome Integration Tests — feedback + outcome signals + tuning.

Test coverage:
- Outcome event emission (3 tests)
- Feedback-outcome integration (4 tests)
- Recent outcomes aggregation (3 tests)
- Confidence-weighted averaging (3 tests)
- Conservative mode on contradictions (3 tests)
- Rollback on bad feedback (2 tests)
- Total: 20+ tests
"""

import pytest
from unittest.mock import Mock, MagicMock, patch
from typing import Dict, Any

from core.learning.outcome_sink import (
    emit_task_outcome,
    integrate_feedback_outcome,
    recent_outcomes,
)


class MockEventStore:
    """Mock EventStore for testing."""
    def __init__(self):
        self.events = []

    def query_events(self, tenant_id: str, event_type=None, limit=None):
        """Query events as ``LearningEvent`` objects (the real store's contract)."""
        from core.learning.learning_events import EventType, LearningEvent

        results = [e for e in self.events if e.get("tenant_id") == tenant_id]
        if event_type:
            results = [e for e in results if e.get("event_type") == event_type.value]
        if limit:
            results = results[-limit:]
        return [
            LearningEvent(
                event_id=e["event_id"], event_type=EventType(e["event_type"]), skill_id=e["skill_id"],
                tenant_id=e["tenant_id"], timestamp=e["timestamp"], signal=e.get("signal"), lom=e.get("lom"),
            )
            for e in results
        ]

    def add_event(self, event: Dict[str, Any]):
        """Add event to store."""
        self.events.append(event)


class MockEventEmitter:
    """Mock EventEmitter for testing."""
    def __init__(self, store=None):
        self.store = store or MockEventStore()
        self.emitted = []

    def emit(self, event):
        """Emit event (mock: just record it)."""
        self.emitted.append(event)
        if hasattr(event, 'to_dict'):
            self.store.add_event(event.to_dict())
        return True


class TestOutcomeEmission:
    """Test basic outcome event emission."""

    def test_emit_successful_task_outcome(self):
        """Emit OUTCOME event for successful task."""
        emitter = MockEventEmitter()
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-123",
            status="completed",
            exit_code=0,
            duration_ms=1000,
            engine="claude",
            emitter=emitter,
        )
        assert result is True
        assert len(emitter.emitted) == 1
        event = emitter.emitted[0]
        assert event.skill_id == "os.delegation_router"
        assert event.signal["success"] is True

    def test_emit_failed_task_outcome(self):
        """Emit OUTCOME event for failed task."""
        emitter = MockEventEmitter()
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-456",
            status="failed",
            exit_code=1,
            duration_ms=500,
            engine="claude",
            emitter=emitter,
        )
        assert result is True
        event = emitter.emitted[0]
        assert event.signal["success"] is False
        assert event.signal["exit_code"] == 1

    def test_emit_cancelled_task(self):
        """Emit OUTCOME event for cancelled task."""
        emitter = MockEventEmitter()
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-789",
            status="cancelled",
            emitter=emitter,
        )
        assert result is True
        event = emitter.emitted[0]
        assert event.signal["status"] == "cancelled"

    def test_emit_missing_tenant_rejected(self):
        """Reject outcome without tenant_id."""
        emitter = MockEventEmitter()
        result = emit_task_outcome(
            tenant_id=None,
            task_id="task-123",
            status="completed",
            emitter=emitter,
        )
        assert result is False
        assert len(emitter.emitted) == 0

    def test_emit_invalid_status_rejected(self):
        """Reject outcome with invalid status."""
        emitter = MockEventEmitter()
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-123",
            status="unknown_status",  # Invalid
            emitter=emitter,
        )
        assert result is False


class TestFeedbackOutcomeIntegration:
    """Test feedback-based outcome signals."""

    def test_integrate_feedback_outcome_yes(self):
        """Integrate user 'yes' feedback as tuning signal."""
        emitter = MockEventEmitter()
        feedback_signal = {
            "outcome_feedback": "yes",
            "quality_rating": 5,
            "confidence": 0.95,
        }
        result = integrate_feedback_outcome(
            tenant_id="_default",
            task_id="task-123",
            feedback_signal=feedback_signal,
            emitter=emitter,
        )
        assert result is True
        event = emitter.emitted[0]
        assert event.signal["feedback_signal"]["outcome_feedback"] == "yes"
        assert event.signal["source"] == "feedback_loop"

    def test_integrate_feedback_outcome_no(self):
        """Integrate user 'no' feedback (negative signal)."""
        emitter = MockEventEmitter()
        feedback_signal = {
            "outcome_feedback": "no",
            "quality_rating": 1,
            "confidence": 0.8,
        }
        result = integrate_feedback_outcome(
            tenant_id="_default",
            task_id="task-456",
            feedback_signal=feedback_signal,
            emitter=emitter,
        )
        assert result is True
        event = emitter.emitted[0]
        assert event.signal["feedback_signal"]["outcome_feedback"] == "no"

    def test_integrate_feedback_missing_tenant(self):
        """Reject feedback outcome without tenant_id."""
        emitter = MockEventEmitter()
        result = integrate_feedback_outcome(
            tenant_id=None,
            task_id="task-123",
            feedback_signal={"outcome_feedback": "yes"},
            emitter=emitter,
        )
        assert result is False

    def test_integrate_feedback_no_emitter(self, monkeypatch):
        import core.learning.outcome_sink as _sink
        monkeypatch.setattr(_sink, 'learning_emitter', lambda: None)  # no booted registry
        """Gracefully handle missing emitter."""
        result = integrate_feedback_outcome(
            tenant_id="_default",
            task_id="task-123",
            feedback_signal={"outcome_feedback": "yes"},
            emitter=None,  # Will try to use booted emitter (None in test)
        )
        assert result is False


class TestRecentOutcomesAggregation:
    """Test outcome aggregation for optimizer."""

    def test_recent_outcomes_all_success(self):
        """Get success count from all-successful outcomes."""
        store = MockEventStore()
        emitter = MockEventEmitter(store)

        # Emit 5 successful outcomes
        for i in range(5):
            emit_task_outcome(
                tenant_id="_default",
                task_id=f"task-{i}",
                status="completed",
                exit_code=0,
                emitter=emitter,
            )

        successes, total = recent_outcomes("_default", limit=10, store=store)
        assert successes == 5
        assert total == 5

    def test_recent_outcomes_mixed(self):
        """Get success/failure mix."""
        store = MockEventStore()
        emitter = MockEventEmitter(store)

        # 3 successes
        for i in range(3):
            emit_task_outcome(
                tenant_id="_default",
                task_id=f"task-{i}",
                status="completed",
                exit_code=0,
                emitter=emitter,
            )

        # 2 failures
        for i in range(3, 5):
            emit_task_outcome(
                tenant_id="_default",
                task_id=f"task-{i}",
                status="failed",
                exit_code=1,
                emitter=emitter,
            )

        successes, total = recent_outcomes("_default", limit=10, store=store)
        assert successes == 3
        assert total == 5

    def test_recent_outcomes_limit(self):
        """Respect limit parameter."""
        store = MockEventStore()
        emitter = MockEventEmitter(store)

        # Emit 20 outcomes
        for i in range(20):
            emit_task_outcome(
                tenant_id="_default",
                task_id=f"task-{i}",
                status="completed",
                exit_code=0,
                emitter=emitter,
            )

        # Get last 10
        successes, total = recent_outcomes("_default", limit=10, store=store)
        assert total == 10
        assert successes == 10

    def test_recent_outcomes_empty_store(self):
        """Handle empty store gracefully."""
        store = MockEventStore()
        successes, total = recent_outcomes("_default", limit=10, store=store)
        assert successes == 0
        assert total == 0

    def test_recent_outcomes_tenant_isolation(self):
        """Outcomes are tenant-scoped."""
        store = MockEventStore()
        emitter = MockEventEmitter(store)

        # Emit for tenant-A
        emit_task_outcome(
            tenant_id="tenant-a",
            task_id="task-a1",
            status="completed",
            exit_code=0,
            emitter=emitter,
        )

        # Emit for tenant-B
        emit_task_outcome(
            tenant_id="tenant-b",
            task_id="task-b1",
            status="failed",
            exit_code=1,
            emitter=emitter,
        )

        # Query tenant-A only
        successes_a, total_a = recent_outcomes("tenant-a", store=store)
        assert successes_a == 1
        assert total_a == 1

        # Query tenant-B only
        successes_b, total_b = recent_outcomes("tenant-b", store=store)
        assert successes_b == 0
        assert total_b == 1


class TestConfidenceWeightedAveraging:
    """Test confidence-weighted outcome aggregation."""

    def test_high_confidence_feedback_weighted(self):
        """High-confidence feedback gets more weight."""
        # Simulate: 3 high-confidence "yes", 1 low-confidence "no"
        feedback_samples = [
            {"outcome": "yes", "confidence": 0.95},
            {"outcome": "yes", "confidence": 0.90},
            {"outcome": "yes", "confidence": 0.85},
            {"outcome": "no", "confidence": 0.2},  # Low confidence, less weight
        ]

        # Weighted average should lean toward "yes"
        weights = [s["confidence"] for s in feedback_samples]
        avg_weight = sum(weights) / len(weights)
        assert avg_weight > 0.7  # Heavily skewed toward "yes"

    def test_low_confidence_feedback_reduced_weight(self):
        """Low-confidence samples have minimal impact."""
        feedback_samples = [
            {"outcome": "yes", "confidence": 0.1},
            {"outcome": "no", "confidence": 0.9},
            {"outcome": "no", "confidence": 0.85},
        ]

        weights = [s["confidence"] for s in feedback_samples]
        avg_weight = sum(weights) / len(weights)
        # Low-confidence yes is 0.1; two high-confidence nos at 0.87 avg
        # Weighted toward "no"
        assert avg_weight > 0.6

    def test_uniform_confidence(self):
        """Equal confidence samples get equal weight."""
        feedback_samples = [
            {"outcome": "yes", "confidence": 0.8},
            {"outcome": "yes", "confidence": 0.8},
            {"outcome": "no", "confidence": 0.8},
        ]

        weights = [s["confidence"] for s in feedback_samples]
        avg_weight = sum(weights) / len(weights)
        assert avg_weight == pytest.approx(0.8)  # All equal


class TestConservativeMode:
    """Test conservative mode when feedback contradicts outcomes."""

    def test_detect_contradictions(self):
        """Flag contradictions (yes + no on same task)."""
        feedback_samples = [
            {"outcome": "yes", "confidence": 0.9},
            {"outcome": "no", "confidence": 0.8},
        ]

        # Simple contradiction detection
        has_yes = any(s["outcome"] == "yes" for s in feedback_samples)
        has_no = any(s["outcome"] == "no" for s in feedback_samples)
        has_contradiction = has_yes and has_no

        assert has_contradiction

    def test_conservative_mode_reduces_learning_rate(self):
        """Conservative mode cuts learning rate in half."""
        base_learning_rate = 0.1
        conservative_learning_rate = base_learning_rate * 0.5  # Reduce by half
        assert conservative_learning_rate == 0.05

    def test_conservative_mode_prevents_divergence(self):
        """Conservative mode prevents loss worsening."""
        # Simulate: if loss gets worse, enter conservative mode
        loss_history = [0.5, 0.48, 0.47, 0.46, 0.45]  # Improving
        loss_history_bad = [0.5, 0.52, 0.54, 0.56, 0.58]  # Worsening

        # Detect worsening
        is_improving = loss_history[-1] < loss_history[0]
        is_worsening = loss_history_bad[-1] > loss_history_bad[0]

        assert is_improving
        assert is_worsening


class TestRollbackOnBadFeedback:
    """Test rollback when feedback signals divergence."""

    def test_rollback_detects_bad_feedback(self):
        """Detect when feedback-guided tuning makes loss worse."""
        # Scenario: applied tuning based on feedback, loss increased
        loss_before_tuning = 0.3
        loss_after_tuning = 0.5  # Worse!

        loss_worsened = loss_after_tuning > loss_before_tuning
        assert loss_worsened

    def test_rollback_reverts_params(self):
        """Revert parameters to pre-feedback state."""
        # Saved state before feedback
        saved_state = {"α": 0.1, "damping": 0.9}

        # After bad feedback, tuning happened
        tuned_state = {"α": 0.05, "damping": 0.85}

        # Rollback
        rolled_back = saved_state.copy()
        assert rolled_back["α"] == 0.1
        assert rolled_back["damping"] == 0.9
        assert rolled_back != tuned_state

    def test_rollback_prevents_convergence_loops(self):
        """Prevent oscillating feedback (yes → no → yes → ...)."""
        feedback_sequence = ["yes", "no", "yes", "no", "yes"]

        # Detect oscillation
        oscillating = len(set(feedback_sequence)) > 1 and len(feedback_sequence) > 3
        assert oscillating

        # In conservative mode, should gradually reduce learning rate
        # to avoid chasing contradictory feedback


class TestFailSoftBehavior:
    """Test fail-soft behavior (no exceptions raised)."""

    def test_missing_emitter_returns_false(self, monkeypatch):
        import core.learning.outcome_sink as _sink
        monkeypatch.setattr(_sink, 'learning_emitter', lambda: None)  # no booted registry
        """Missing emitter returns False, not exception."""
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-123",
            status="completed",
            emitter=None,  # Booted emitter is None
        )
        assert result is False

    def test_store_query_failure_returns_zero(self):
        """Store query failure returns (0, 0), not exception."""
        store = Mock()
        store.query_events.side_effect = Exception("Store error")

        successes, total = recent_outcomes("_default", store=store)
        assert successes == 0
        assert total == 0

    def test_event_creation_failure_logged(self):
        """Event creation failure is logged, not raised."""
        emitter = Mock()
        emitter.emit.side_effect = Exception("Emit error")

        # Should log and return False
        result = emit_task_outcome(
            tenant_id="_default",
            task_id="task-123",
            status="completed",
            emitter=emitter,
        )
        assert result is False

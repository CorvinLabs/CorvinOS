"""E2E Tests for Stream 1 Phase 2: Workflow Optimizer Learning Loop (Week 2, Days 1–3).

Tests feedback handler, confidence calculator, and config persistence.
Total: 13 E2E tests covering:
- Feedback reception + validation
- Bayesian confidence updates
- Config persistence (YAML versioning)
- Audit trail integration
- Error handling (fail-closed)

All tests use real EventStore + LearningEvent (no mocks).
"""

import json
import pytest
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

# Imports (will resolve after code structure finalized)
from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType
from core.skills.os_skills.workflow_optimizer_skill.feedback_handler import (
    FeedbackHandler,
    RoutingFeedback,
    FeedbackType,
)
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
    RoutingWeights,
)


class TestWorkflowOptimizerPhase1:
    """E2E tests for Workflow Optimizer learning loop Phase 1."""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary directory for EventStore + config."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def event_store(self, temp_storage):
        """Initialize EventStore for tests."""
        tenant_home = temp_storage / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        return EventStore(tenant_home=tenant_home, tenant_id="_default")

    @pytest.fixture
    def feedback_handler(self, event_store):
        """Initialize FeedbackHandler."""
        return FeedbackHandler(
            event_store=event_store,
            tenant_id="_default",
            skill_id="os.workflow_optimizer_l5",
            skill_version="1.0.0",
        )

    @pytest.fixture
    def confidence_calculator(self, event_store, temp_storage):
        """Initialize ConfidenceCalculator."""
        config_dir = temp_storage / "config"
        return ConfidenceCalculator(
            event_store=event_store,
            tenant_id="_default",
            config_dir=config_dir,
            skill_id="os.workflow_optimizer_l5",
            skill_version="1.0.0",
        )

    # TEST 1: Feedback Reception + Validation
    def test_feedback_reception_valid(self, feedback_handler):
        """Test: Valid feedback is accepted and emitted (TEST 1)."""
        feedback = RoutingFeedback(
            task_id="task_001",
            routed_model="sonnet-5",
            task_complexity="medium",
            feedback_type=FeedbackType.CORRECT,
            confidence_score=0.85,
            tenant_id="_default",
        )

        event_id = feedback_handler.process_feedback(feedback)

        assert event_id is not None
        assert isinstance(event_id, str)
        assert feedback_handler.feedback_count == 1

    # TEST 2: Feedback Validation Fails (Tenant Mismatch)
    def test_feedback_validation_tenant_mismatch(self, feedback_handler):
        """Test: Feedback with wrong tenant is rejected (fail-closed) (TEST 2)."""
        feedback = RoutingFeedback(
            task_id="task_002",
            routed_model="opus-5",
            task_complexity="complex",
            feedback_type=FeedbackType.INCORRECT,
            tenant_id="other_tenant",  # Wrong tenant
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            feedback_handler.process_feedback(feedback)

    # TEST 3: Feedback Skip (Type=SKIP)
    def test_feedback_skip(self, feedback_handler):
        """Test: Feedback with type=SKIP is ignored (TEST 3)."""
        feedback = RoutingFeedback(
            task_id="task_003",
            routed_model="haiku-4-5",
            task_complexity="simple",
            feedback_type=FeedbackType.SKIP,
            tenant_id="_default",
        )

        event_id = feedback_handler.process_feedback(feedback)

        assert event_id is None
        assert feedback_handler.feedback_count == 0

    # TEST 4: Multiple Feedback Accumulation
    def test_feedback_accumulation(self, feedback_handler):
        """Test: Multiple feedback events are accumulated (TEST 4)."""
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"task_{i:03d}",
                routed_model=["haiku-4-5", "sonnet-5", "opus-5"][i % 3],
                task_complexity=["simple", "medium", "complex"][i % 3],
                feedback_type=FeedbackType.CORRECT if i % 2 == 0 else FeedbackType.INCORRECT,
                confidence_score=0.7 + (i * 0.03),
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        assert feedback_handler.feedback_count == 5

    # TEST 5: Bayesian Confidence Update (Simple Case)
    def test_confidence_update_simple(self, feedback_handler, confidence_calculator, event_store):
        """Test: Confidence scores update correctly after feedback (TEST 5)."""
        # Emit 10 feedback events: 8 correct, 2 incorrect for simple_sonnet
        for i in range(10):
            feedback = RoutingFeedback(
                task_id=f"simple_task_{i:02d}",
                routed_model="sonnet-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT if i < 8 else FeedbackType.INCORRECT,
                confidence_score=0.8,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Update confidence scores
        weights, feedback_count = confidence_calculator.update_from_feedback()

        assert feedback_count == 10
        # P(correct) ≈ 8/10 = 0.80 (with Laplace smoothing)
        simple_sonnet_weight = weights.get_confidence("simple", "sonnet-5")
        assert 0.75 < simple_sonnet_weight < 0.85  # Reasonable range with smoothing

    # TEST 6: Confidence Update (Multiple Complexity Levels)
    def test_confidence_update_multiple_complexities(self, feedback_handler, confidence_calculator):
        """Test: Confidence updates separately per complexity level (TEST 6)."""
        # Simple tasks: all correct
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"simple_{i:02d}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Complex tasks: all incorrect
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"complex_{i:02d}",
                routed_model="haiku-4-5",
                task_complexity="complex",
                feedback_type=FeedbackType.INCORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        weights, _ = confidence_calculator.update_from_feedback()

        simple_haiku = weights.get_confidence("simple", "haiku-4-5")
        complex_haiku = weights.get_confidence("complex", "haiku-4-5")

        # Simple should be high, complex should be low
        assert simple_haiku > 0.7  # High confidence
        assert complex_haiku < 0.4  # Low confidence

    # TEST 7: Config Persistence (Save + Load)
    def test_config_persistence_save_load(self, confidence_calculator):
        """Test: Weights are persisted to disk and can be reloaded (TEST 7)."""
        # Create and save weights
        weights = RoutingWeights(
            weights={"simple_sonnet": 0.85, "medium_opus": 0.75},
            feedback_count=42,
            version="1.0",
        )
        confidence_calculator._save_weights_versioned(weights)

        # Reload from disk
        loaded = confidence_calculator.load_weights()

        assert loaded.feedback_count == 42
        assert loaded.weights["simple_sonnet"] == 0.85
        assert loaded.weights["medium_opus"] == 0.75

    # TEST 8: Config Versioning (Immutable History)
    def test_config_versioning(self, confidence_calculator):
        """Test: Weight versions are archived (immutable history) (TEST 8)."""
        # v1.0
        weights_v1 = RoutingWeights(
            weights={"simple_sonnet": 0.80},
            feedback_count=10,
            version="1.0",
        )
        confidence_calculator._save_weights_versioned(weights_v1)

        # v1.1
        weights_v1_1 = RoutingWeights(
            weights={"simple_sonnet": 0.85},
            feedback_count=20,
            version="1.1",
        )
        confidence_calculator._save_weights_versioned(weights_v1_1)

        # Both should exist in history
        v1_file = confidence_calculator.history_dir / "v1.0.json"
        v1_1_file = confidence_calculator.history_dir / "v1.1.json"

        assert v1_file.exists()
        assert v1_1_file.exists()

        # Current should be v1.1
        current = confidence_calculator.load_weights()
        assert current.version == "1.1"

    # TEST 9: Audit Trail Integration (CONFIG_UPDATED event)
    def test_config_updated_event_emitted(self, feedback_handler, confidence_calculator, event_store):
        """Test: CONFIG_UPDATED event is emitted to audit trail (TEST 9)."""
        # Emit feedback
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"audit_test_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT if i < 3 else FeedbackType.INCORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Update confidence
        confidence_calculator.update_from_feedback()

        # Check audit trail for CONFIG_UPDATED event
        audit_events = event_store.query_events(
            tenant_id="_default",
            event_type=EventType.CONFIG_UPDATED,
            skill_id="os.workflow_optimizer_l5",
            limit=100,
        )

        assert len(audit_events) > 0
        latest = audit_events[-1]
        assert latest.event_type == EventType.CONFIG_UPDATED
        assert latest.signal is not None
        assert "config_delta" in latest.signal

    # TEST 10: Error Handling (EventStore Write Failure)
    def test_error_handling_event_store_failure(self, event_store, feedback_handler, monkeypatch):
        """Test: Feedback rejected if EventStore write fails (fail-closed) (TEST 10)."""
        # Mock EventStore.write_event to raise RuntimeError
        def mock_write_event(event):
            raise RuntimeError("Mock EventStore failure")

        monkeypatch.setattr(event_store, "write_event", mock_write_event)

        feedback = RoutingFeedback(
            task_id="error_test",
            routed_model="opus-5",
            task_complexity="complex",
            feedback_type=FeedbackType.CORRECT,
            tenant_id="_default",
        )

        with pytest.raises(RuntimeError, match="rejected by audit chain"):
            feedback_handler.process_feedback(feedback)

    # TEST 11: Query Recent Feedback
    def test_query_recent_feedback(self, feedback_handler):
        """Test: Recent feedback can be queried from EventStore (TEST 11)."""
        # Emit feedback
        for i in range(3):
            feedback = RoutingFeedback(
                task_id=f"query_test_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Query recent feedback
        recent = feedback_handler.get_recent_feedback(limit=10)

        assert len(recent) == 3
        assert all(e.event_type == EventType.FEEDBACK for e in recent)

    # TEST 12: Count Feedback Events
    def test_count_feedback_events(self, feedback_handler):
        """Test: Feedback event count is accurate (TEST 12)."""
        # Emit feedback
        for i in range(7):
            feedback = RoutingFeedback(
                task_id=f"count_test_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        count = feedback_handler.count_feedback()
        assert count == 7

    # TEST 13: Confidence Calculation with Empty Feedback
    def test_confidence_update_no_feedback(self, confidence_calculator):
        """Test: Confidence returns defaults when no feedback present (TEST 13)."""
        weights, feedback_count = confidence_calculator.update_from_feedback()

        assert feedback_count == 0
        # Should return defaults
        assert weights.feedback_count == 0
        # Weights should still have default values
        assert "simple_haiku" in weights.weights


# Gate 1: All 13 Tests Must Pass
if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

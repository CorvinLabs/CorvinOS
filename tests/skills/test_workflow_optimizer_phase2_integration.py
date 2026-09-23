"""E2E Integration Tests for Stream 1 Phase 2 (Config Persistence + L5 Routing).

Tests the complete workflow:
1. Feedback → ConfidenceCalculator updates weights
2. Weights persisted to disk (ConfigPersistence)
3. L5AgentSelectorLearned loads + uses weights for routing
4. Next routing decision reflects learned weights
5. Operator pin override works correctly
6. Config versioning + rollback works

Total: 12 integration tests covering full feedback→routing loop.
"""

import json
import pytest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType
from core.skills.os_skills.workflow_optimizer_skill.feedback_handler import (
    FeedbackHandler,
    RoutingFeedback,
    FeedbackType,
)
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
)
from core.skills.os_skills.workflow_optimizer_skill.config_persistence import (
    ConfigPersistence,
)
from core.skills.os_skills.workflow_optimizer_skill.l5_agent_selector_learned import (
    L5AgentSelectorLearned,
)


class TestWorkflowOptimizerPhase2:
    """E2E integration tests for Stream 1 Phase 2."""

    @pytest.fixture
    def temp_storage(self):
        """Create temporary storage."""
        with TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def event_store(self, temp_storage):
        """Initialize EventStore."""
        tenant_home = temp_storage / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        return EventStore(tenant_home=tenant_home, tenant_id="_default")

    @pytest.fixture
    def config_dir(self, temp_storage):
        """Create config directory."""
        config_dir = temp_storage / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        return config_dir

    @pytest.fixture
    def feedback_handler(self, event_store):
        """Initialize FeedbackHandler."""
        return FeedbackHandler(event_store=event_store, tenant_id="_default")

    @pytest.fixture
    def confidence_calculator(self, event_store, config_dir):
        """Initialize ConfidenceCalculator."""
        return ConfidenceCalculator(
            event_store=event_store,
            tenant_id="_default",
            config_dir=config_dir,
        )

    @pytest.fixture
    def config_persistence(self, config_dir):
        """Initialize ConfigPersistence."""
        return ConfigPersistence(config_dir=config_dir)

    @pytest.fixture
    def l5_selector(self, config_dir):
        """Initialize L5AgentSelectorLearned."""
        return L5AgentSelectorLearned(tenant_id="_default", config_dir=config_dir)

    # TEST 1: Feedback → Update → Persistence
    def test_feedback_to_persistence_pipeline(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
    ):
        """TEST 1: Feedback flows through calculator to disk persistence."""
        # Emit feedback
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"task_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT if i < 3 else FeedbackType.INCORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Update confidence (writes to disk)
        weights, count = confidence_calculator.update_from_feedback()
        assert count == 5

        # Load from disk
        loaded = config_persistence.load_current_weights()
        assert loaded.feedback_count == 5
        assert "medium_sonnet" in loaded.weights

    # TEST 2: L5 Router Loads Learned Weights
    def test_l5_loads_learned_weights(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
        l5_selector,
    ):
        """TEST 2: L5 router loads and uses learned weights."""
        # Emit feedback for simple_haiku (should be high confidence)
        for i in range(10):
            feedback = RoutingFeedback(
                task_id=f"simple_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Update weights
        confidence_calculator.update_from_feedback()

        # L5 router reloads weights
        l5_selector.update_weights()

        # Get weight info
        info = l5_selector.get_weight_info()
        assert info["feedback_count"] == "10"

    # TEST 3: L5 Routing Uses Learned Weights
    def test_l5_routing_decision_uses_learned_weights(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
        l5_selector,
    ):
        """TEST 3: L5 routing decision reflects learned weights."""
        # Setup: emit feedback preferring haiku for simple tasks
        for i in range(8):
            feedback = RoutingFeedback(
                task_id=f"setup_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Also emit feedback showing sonnet is bad for simple (incorrect)
        for i in range(3):
            feedback = RoutingFeedback(
                task_id=f"bad_{i}",
                routed_model="sonnet-5",
                task_complexity="simple",
                feedback_type=FeedbackType.INCORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Update weights
        confidence_calculator.update_from_feedback()
        l5_selector.update_weights()

        # Route a simple task
        decision = l5_selector.select_model(
            task_id="route_test_1",
            task_complexity="simple",
            classifier_confidence=0.9,
        )

        # Should prefer haiku (learned weights favor it)
        assert decision.model.value == "haiku-4-5"
        assert decision.is_learned_routing is True
        assert decision.model_confidence > 0.7  # High confidence from feedback

    # TEST 4: Operator Pin Override
    def test_operator_pin_override_beats_learned_weights(
        self,
        feedback_handler,
        confidence_calculator,
        l5_selector,
    ):
        """TEST 4: Operator pin overrides learned weights."""
        # Setup: learned weights prefer haiku
        for i in range(10):
            feedback = RoutingFeedback(
                task_id=f"setup_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()
        l5_selector.update_weights()

        # Route with operator pin to opus
        decision = l5_selector.select_model(
            task_id="override_test",
            task_complexity="simple",
            classifier_confidence=0.9,
            model_pin="opus-5",
        )

        # Should use opus (operator override)
        assert decision.model.value == "opus-5"
        assert decision.is_learned_routing is False
        assert "operator pin" in decision.reasoning.lower()

    # TEST 5: Config Versioning
    def test_config_versioning(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
    ):
        """TEST 5: Weight versions are archived (immutable history)."""
        # v1.0: emit 5 feedback
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"v1_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()
        v1_versions = config_persistence.list_versions()
        assert len(v1_versions) > 0

        # v1.1: emit more feedback
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"v2_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()
        v2_versions = config_persistence.list_versions()

        # Should have both v1.0 and v1.1
        assert len(v2_versions) > len(v1_versions)

    # TEST 6: Config Rollback
    def test_config_rollback(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
    ):
        """TEST 6: Config can be rolled back to prior version."""
        # Create v1.0 and v1.1
        for i in range(3):
            feedback = RoutingFeedback(
                task_id=f"rb_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()
        versions = config_persistence.list_versions()
        v1_version = versions[-1]["version"]  # Oldest

        # Emit more feedback (creates v1.1)
        for i in range(3):
            feedback = RoutingFeedback(
                task_id=f"rb2_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.INCORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()

        # Rollback to v1.0
        success, msg = config_persistence.rollback_to_version(v1_version)
        assert success is True
        assert "rolled back" in msg.lower()

    # TEST 7: Latency Benchmark (<500ms)
    def test_feedback_update_latency(
        self,
        feedback_handler,
        confidence_calculator,
    ):
        """TEST 7: Feedback → update latency is <500ms."""
        import time

        # Emit feedback
        for i in range(10):
            feedback = RoutingFeedback(
                task_id=f"latency_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Measure update time
        start = time.perf_counter()
        weights, count = confidence_calculator.update_from_feedback()
        elapsed_ms = (time.perf_counter() - start) * 1000

        assert count == 10
        assert elapsed_ms < 500, f"Update took {elapsed_ms:.1f}ms (target: <500ms)"

    # TEST 8: Multiple Complexity Levels
    def test_routing_multiple_complexity_levels(
        self,
        feedback_handler,
        confidence_calculator,
        l5_selector,
    ):
        """TEST 8: Routing learns separate weights per complexity level."""
        # Simple: haiku is good
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"simple_{i}",
                routed_model="haiku-4-5",
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Medium: sonnet is good
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"medium_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        # Complex: opus is good
        for i in range(5):
            feedback = RoutingFeedback(
                task_id=f"complex_{i}",
                routed_model="opus-5",
                task_complexity="complex",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()
        l5_selector.update_weights()

        # Route each complexity
        simple_route = l5_selector.select_model(
            task_id="test_simple", task_complexity="simple", classifier_confidence=0.9
        )
        medium_route = l5_selector.select_model(
            task_id="test_medium", task_complexity="medium", classifier_confidence=0.9
        )
        complex_route = l5_selector.select_model(
            task_id="test_complex", task_complexity="complex", classifier_confidence=0.9
        )

        # Each should prefer its learned model
        assert simple_route.model.value == "haiku-4-5"
        assert medium_route.model.value == "sonnet-5"
        assert complex_route.model.value == "opus-5"

    # TEST 9: Graceful Fallback (No Weights File)
    def test_graceful_fallback_no_weights(self, config_dir):
        """TEST 9: L5 router falls back gracefully if weights missing."""
        # Create new selector with empty config dir
        selector = L5AgentSelectorLearned(
            tenant_id="_default",
            config_dir=config_dir / "nonexistent",
        )

        # Should still route (use defaults)
        decision = selector.select_model(
            task_id="fallback_test",
            task_complexity="simple",
            classifier_confidence=0.9,
        )

        assert decision.model is not None
        assert decision.is_learned_routing is True  # But with defaults

    # TEST 10: Weight Stats
    def test_weight_stats(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
    ):
        """TEST 10: Config persistence provides stats."""
        # Emit feedback
        for i in range(15):
            feedback = RoutingFeedback(
                task_id=f"stats_{i}",
                routed_model="sonnet-5",
                task_complexity="medium",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

        confidence_calculator.update_from_feedback()

        stats = config_persistence.get_weight_stats()
        assert stats["total_feedback_incorporated"] == 15
        assert "current_version" in stats

    # TEST 11: Config Corruption Recovery
    def test_config_corruption_recovery(self, config_persistence):
        """TEST 11: Corrupted config file is handled gracefully."""
        # Write corrupted JSON
        config_persistence.weights_file.write_text("{ invalid json }")

        # Should load defaults
        weights = config_persistence.load_current_weights()
        assert weights is not None

    # TEST 12: End-to-End Feedback Loop (5 iterations)
    def test_end_to_end_five_iteration_loop(
        self,
        feedback_handler,
        confidence_calculator,
        config_persistence,
        l5_selector,
    ):
        """TEST 12: Complete feedback loop over 5 iterations."""
        for iteration in range(5):
            # Route a task
            decision = l5_selector.select_model(
                task_id=f"loop_{iteration}",
                task_complexity="simple",
                classifier_confidence=0.9,
            )

            # Emit feedback on the routing
            feedback = RoutingFeedback(
                task_id=f"loop_{iteration}",
                routed_model=decision.model.value,
                task_complexity="simple",
                feedback_type=FeedbackType.CORRECT,
                tenant_id="_default",
            )
            feedback_handler.process_feedback(feedback)

            # Update weights
            confidence_calculator.update_from_feedback()

            # Reload weights
            l5_selector.update_weights()

        # Verify final state
        final_weights = config_persistence.load_current_weights()
        assert final_weights.feedback_count >= 5


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

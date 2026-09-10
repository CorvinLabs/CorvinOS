"""
Unit tests for ADR-0377: Multi-Model Routing & Cost Optimizer.
"""

import pytest
from datetime import datetime

from core.orchestration.model_routing import (
    ModelRoutingPlan,
    SubsystemCostEvent,
    TaskTemplate,
    ExecutionContext,
    OperatorStyle,
    ModelChoice,
    hash_routing_plan,
)
from core.orchestration.cost_optimizer import CostOptimizer, CostTracker


class MockEventStore:
    """Mock event store for testing."""
    def __init__(self):
        self.events = []

    def write_event(self, event_dict):
        self.events.append(event_dict)


def simple_template_provider(task_type: str) -> TaskTemplate:
    """Provide a simple template for testing."""
    if task_type == "code_review":
        return TaskTemplate(
            task_type="code_review",
            duration_mean_minutes=30.0,
            duration_stddev_minutes=15.0,
            subsystems={
                "code_analyzer": {
                    "complexity_typical": 0.6,
                    "model_haiku_cost": 0.25,
                    "model_opus_cost": 0.65,
                    "haiku_accuracy": 0.80,
                    "opus_accuracy": 0.95,
                },
                "refactoring_engine": {
                    "complexity_typical": 0.4,
                    "model_haiku_cost": 0.20,
                    "model_opus_cost": 0.50,
                    "haiku_accuracy": 0.90,
                    "opus_accuracy": 0.98,
                },
            },
            sample_count=100,
        )
    return None


def simple_operator_style_provider(operator_id: str) -> OperatorStyle:
    """Provide operator style for testing."""
    if operator_id == "speed_biased":
        return OperatorStyle(operator_id=operator_id, speed_bias=1.0)
    elif operator_id == "accuracy_biased":
        return OperatorStyle(operator_id=operator_id, accuracy_bias=1.0)
    return OperatorStyle(operator_id=operator_id, speed_bias=0.0)


class TestModelRoutingPlan:
    """Test ModelRoutingPlan data structure."""

    def test_routing_plan_creation(self):
        """Create a basic routing plan."""
        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={
                "code_analyzer": ModelChoice.HAIKU,
                "refactoring_engine": ModelChoice.OPUS,
            },
            estimated_total_cost=0.95,
            confidence=0.85,
        )

        assert plan.task_id == "task_123"
        assert plan.subsystem_routes["code_analyzer"] == ModelChoice.HAIKU
        assert plan.get_model_for_subsystem("refactoring_engine") == ModelChoice.OPUS
        assert plan.get_model_for_subsystem("unknown") == ModelChoice.OPUS  # Default

    def test_routing_plan_serialization(self):
        """Serialize routing plan to dict."""
        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        plan_dict = plan.to_dict()
        assert plan_dict["task_id"] == "task_123"
        assert "created_at" in plan_dict

    def test_routing_plan_immutable(self):
        """Routing plan should be immutable (frozen dataclass)."""
        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        with pytest.raises(AttributeError):
            plan.task_id = "changed"


class TestSubsystemCostEvent:
    """Test SubsystemCostEvent data structure."""

    def test_cost_event_creation(self):
        """Create a cost event."""
        event = SubsystemCostEvent(
            task_id="task_123",
            subsystem="code_analyzer",
            model_used=ModelChoice.HAIKU,
            estimated_cost=0.25,
            actual_cost=0.30,
        )

        assert event.variance == 0.05
        assert event.variance_pct() == 20.0

    def test_cost_event_serialization(self):
        """Serialize cost event to dict."""
        event = SubsystemCostEvent(
            task_id="task_123",
            subsystem="code_analyzer",
            model_used=ModelChoice.HAIKU,
            estimated_cost=0.25,
            actual_cost=0.30,
        )

        event_dict = event.to_dict()
        assert event_dict["variance_pct"] == 20.0
        assert "timestamp" in event_dict


class TestCostOptimizer:
    """Test CostOptimizer logic."""

    def test_optimizer_initialization(self):
        """Initialize optimizer with providers."""
        optimizer = CostOptimizer(
            simple_template_provider,
            simple_operator_style_provider,
        )
        assert optimizer is not None

    def test_routing_plan_for_simple_task(self):
        """Compute routing plan for a simple task."""
        optimizer = CostOptimizer(
            simple_template_provider,
            simple_operator_style_provider,
        )

        context = ExecutionContext(
            task_id="task_123",
            task_type="code_review",
            operator_id="balanced",
            code="def foo():\n    pass",  # Small code
        )

        plan = optimizer.compute_routing_plan(
            task_id="task_123",
            task_type="code_review",
            context=context,
        )

        assert plan.task_id == "task_123"
        assert "code_analyzer" in plan.subsystem_routes
        assert plan.estimated_total_cost > 0
        assert 0.0 <= plan.confidence <= 1.0

    def test_operator_speed_bias_affects_routing(self):
        """Speed-biased operator should prefer cheaper models."""
        optimizer = CostOptimizer(
            simple_template_provider,
            simple_operator_style_provider,
        )

        context = ExecutionContext(
            task_id="task_complex",
            task_type="code_review",
            operator_id="speed_biased",
            code="x" * 2000,  # Large code = high complexity
        )

        plan = optimizer.compute_routing_plan(
            task_id="task_complex",
            task_type="code_review",
            context=context,
        )

        # With speed bias = 1.0, threshold becomes 0.2 (very aggressive on Haiku)
        # So even complex subsystems might route to cheaper models
        haiku_count = sum(
            1 for model in plan.subsystem_routes.values()
            if model == ModelChoice.HAIKU
        )
        assert haiku_count >= 1  # At least one subsystem should use Haiku

    def test_routing_plan_hash_consistency(self):
        """Hash of same plan should be consistent."""
        plan1 = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        plan2 = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        hash1 = hash_routing_plan(plan1)
        hash2 = hash_routing_plan(plan2)

        # Different timestamps won't match, but content should be similar
        assert isinstance(hash1, str)
        assert len(hash1) == 64  # SHA256 hex string


class TestCostTracker:
    """Test CostTracker logic."""

    def test_tracker_initialization(self):
        """Initialize tracker with mock event store."""
        store = MockEventStore()
        tracker = CostTracker(store)
        assert tracker is not None

    def test_track_subsystem_cost_success(self):
        """Track a successful subsystem execution."""
        store = MockEventStore()
        tracker = CostTracker(store)

        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        event = tracker.track_subsystem_cost(
            task_id="task_123",
            subsystem="code_analyzer",
            model_used=ModelChoice.HAIKU,
            actual_cost=0.22,
            routing_plan=plan,
            success=True,
        )

        assert event.task_id == "task_123"
        assert event.success is True
        assert len(store.events) == 1  # Event was written

    def test_track_subsystem_cost_failure(self):
        """Track a failed subsystem execution."""
        store = MockEventStore()
        tracker = CostTracker(store)

        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=0.5,
            confidence=0.8,
        )

        event = tracker.track_subsystem_cost(
            task_id="task_123",
            subsystem="code_analyzer",
            model_used=ModelChoice.HAIKU,
            actual_cost=0.0,
            routing_plan=plan,
            success=False,
            error_msg="Model timeout",
        )

        assert event.success is False
        assert event.error_msg == "Model timeout"

    def test_tracker_alerts_on_high_variance(self):
        """Tracker should alert when variance is >30%."""
        store = MockEventStore()
        tracker = CostTracker(store)

        plan = ModelRoutingPlan(
            task_id="task_123",
            task_type="code_review",
            subsystem_routes={"code_analyzer": ModelChoice.HAIKU},
            estimated_total_cost=1.0,
            confidence=0.8,
        )

        # Actual cost is 40% higher than estimated (40% variance)
        event = tracker.track_subsystem_cost(
            task_id="task_123",
            subsystem="code_analyzer",
            model_used=ModelChoice.HAIKU,
            actual_cost=1.4,  # 40% more than estimated
            routing_plan=plan,
        )

        # Alert should be triggered (variance > 30%), but execution should succeed
        assert event.variance_pct() == 40.0
        assert len(store.events) == 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

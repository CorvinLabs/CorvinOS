"""Tests for orchestration console API (ADR-0612)."""

import pytest

from core.console.routes.orchestration import create_orchestration_router
from core.skills.orchestration.learning_integration import OrchestrationLearner, get_learner


class TestOrchestrationConsoleAPI:
    """Test console API endpoints."""

    def test_router_creation(self):
        """Create router."""
        router = create_orchestration_router()
        assert router is not None
        # Router has 3 endpoints
        routes = [r.path for r in router.routes]
        assert any("history" in path for path in routes)
        assert any("recommendation" in path for path in routes)
        assert any("feedback" in path for path in routes)

    def test_history_endpoint_with_data(self):
        """History endpoint returns performance data."""
        learner = get_learner()
        
        # Populate with data
        for _ in range(10):
            learner.process_outcome("skill1", "plugin1", "cap1", 100, True, True)

        model = learner.get_model("skill1")
        assert model is not None

        # Simulate endpoint
        total_invocations = sum(s.invocations for s in model.stats.values())
        assert total_invocations == 10

    def test_recommendation_endpoint(self):
        """Recommendation endpoint works."""
        learner = get_learner()
        
        # Populate
        for _ in range(20):
            learner.process_outcome("skill2", "plugin1", "cap1", 100, True, True)
        for _ in range(5):
            learner.process_outcome("skill2", "plugin2", "cap1", 1000, False, False)

        # Simulate recommendation
        rec = learner.recommend("skill2", "cap1", ["plugin1", "plugin2"])
        assert rec is not None
        plugin_id, confidence = rec
        assert plugin_id == "plugin1"  # Should recommend best-performing

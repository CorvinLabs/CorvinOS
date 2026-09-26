"""Unit tests: Tier 1 Router (k=2 Model Selector)."""
import pytest
from core.skills.os_skills.model_selector import (
    Tier1Router,
    ModelSelectionDecision,
    Complexity,
    ModelTier,
)


class TestTier1Router:
    """Tier 1 Router unit tests."""
    
    @pytest.fixture
    def router(self):
        """Instantiate router."""
        return Tier1Router()
    
    def test_route_simple_complexity(self, router):
        """Route simple task → Haiku/Sonnet (40% / 60%)."""
        decisions = []
        for i in range(100):
            task_id = f"simple_task_{i:03d}"
            decision = router.route(task_id, "simple")
            decisions.append(decision)
        
        haiku_count = sum(1 for d in decisions if "haiku" in d.selected_model)
        sonnet_count = sum(1 for d in decisions if "sonnet" in d.selected_model)
        opus_count = sum(1 for d in decisions if "opus" in d.selected_model)
        
        # Expect: 30–40% Haiku, 60–70% Sonnet, 0% Opus
        assert 25 <= haiku_count <= 45, f"Haiku: {haiku_count}%"
        assert 55 <= sonnet_count <= 75, f"Sonnet: {sonnet_count}%"
        assert opus_count == 0, f"Opus should be 0%, got {opus_count}%"
    
    def test_route_medium_complexity(self, router):
        """Route medium task → Haiku/Sonnet/Opus (10% / 50% / 40%)."""
        decisions = []
        for i in range(100):
            task_id = f"medium_task_{i:03d}"
            decision = router.route(task_id, "medium")
            decisions.append(decision)
        
        haiku_count = sum(1 for d in decisions if "haiku" in d.selected_model)
        sonnet_count = sum(1 for d in decisions if "sonnet" in d.selected_model)
        opus_count = sum(1 for d in decisions if "opus" in d.selected_model)
        
        # Expect: 10%, 50%, 40%
        assert 5 <= haiku_count <= 15, f"Haiku: {haiku_count}%"
        assert 40 <= sonnet_count <= 60, f"Sonnet: {sonnet_count}%"
        assert 30 <= opus_count <= 50, f"Opus: {opus_count}%"
    
    def test_route_complex_complexity(self, router):
        """Route complex task → Sonnet/Opus (20% / 80%)."""
        decisions = []
        for i in range(100):
            task_id = f"complex_task_{i:03d}"
            decision = router.route(task_id, "complex")
            decisions.append(decision)
        
        haiku_count = sum(1 for d in decisions if "haiku" in d.selected_model)
        sonnet_count = sum(1 for d in decisions if "sonnet" in d.selected_model)
        opus_count = sum(1 for d in decisions if "opus" in d.selected_model)
        
        # Expect: 0% Haiku, 20% Sonnet, 80% Opus
        assert haiku_count == 0, f"Haiku should be 0%, got {haiku_count}%"
        assert 10 <= sonnet_count <= 30, f"Sonnet: {sonnet_count}%"
        assert 70 <= opus_count <= 90, f"Opus: {opus_count}%"
    
    def test_route_deterministic(self, router):
        """Route same task_id → same model."""
        task_id = "deterministic_test"
        
        decision1 = router.route(task_id, "simple")
        decision2 = router.route(task_id, "simple")
        decision3 = router.route(task_id, "simple")
        
        assert decision1.selected_model == decision2.selected_model
        assert decision2.selected_model == decision3.selected_model
        assert decision1.stratification_bucket == decision2.stratification_bucket
    
    def test_route_invalid_complexity(self, router):
        """Route with invalid complexity → ValueError."""
        with pytest.raises(ValueError, match="Unknown complexity"):
            router.route("task_123", "invalid_complexity")
    
    def test_decision_immutable(self, router):
        """ModelSelectionDecision is frozen (immutable)."""
        decision = router.route("task_123", "simple")
        
        with pytest.raises(AttributeError):
            decision.selected_model = "claude-new-model"
    
    def test_decision_fields(self, router):
        """ModelSelectionDecision has all required fields."""
        decision = router.route("task_123", "medium")
        
        assert hasattr(decision, "task_id")
        assert hasattr(decision, "complexity")
        assert hasattr(decision, "selected_model")
        assert hasattr(decision, "stratification_bucket")
        
        assert decision.task_id == "task_123"
        assert decision.complexity == "medium"
        assert isinstance(decision.stratification_bucket, float)
        assert 0.0 <= decision.stratification_bucket <= 1.0
    
    def test_different_task_ids_different_models(self, router):
        """Different task_ids can route to different models (probabilistic)."""
        decisions = [
            router.route(f"task_{i:03d}", "medium").selected_model
            for i in range(20)
        ]
        
        # Should see multiple different models (not all same)
        unique_models = set(decisions)
        assert len(unique_models) >= 2, "Expected variation across task_ids"
    
    def test_bucket_distribution_uniform(self, router):
        """Bucket hash distribution is roughly uniform."""
        buckets = [
            router._hash_to_bucket(f"task_{i:04d}")
            for i in range(1000)
        ]
        
        # Check quartiles (should be roughly 25% each)
        q1_count = sum(1 for b in buckets if b < 0.25)
        q2_count = sum(1 for b in buckets if 0.25 <= b < 0.50)
        q3_count = sum(1 for b in buckets if 0.50 <= b < 0.75)
        q4_count = sum(1 for b in buckets if b >= 0.75)
        
        # Each quartile should have ~250 ± 50 items
        for q, count in [("Q1", q1_count), ("Q2", q2_count), ("Q3", q3_count), ("Q4", q4_count)]:
            assert 200 <= count <= 300, f"{q}: {count} (expected ~250)"
    
    def test_model_names_valid(self, router):
        """All selected models are valid Claude model names."""
        valid_models = [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-5",
            "claude-opus-5-5",
        ]
        
        for complexity in ["simple", "medium", "complex"]:
            for i in range(10):
                decision = router.route(f"task_{complexity}_{i}", complexity)
                assert decision.selected_model in valid_models, \
                    f"Invalid model: {decision.selected_model}"
    
    def test_simple_selection_rate(self, router):
        """Simple complexity selects Haiku 30–40% (quality gate)."""
        haiku_rate = sum(
            1 for i in range(100)
            if "haiku" in router.route(f"simple_{i}", "simple").selected_model
        ) / 100.0
        
        # k=2 quality gate: Haiku 30–40%
        assert 0.25 <= haiku_rate <= 0.45, f"Haiku rate: {haiku_rate:.1%} (gate: 30–40%)"


pytestmark = pytest.mark.unit


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

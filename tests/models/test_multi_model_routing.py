"""
Test suite for multi-model routing (ADR-0377 Phase 3)
Tests cost estimation, model ranking, and fallback logic.
"""

import pytest
from core.models.multi_model_router import (
    MultiModelRouter,
    ModelProfile,
    ModelTier,
    MODEL_PROFILES,
    default_router,
)


class TestMultiModelRouter:
    """Test multi-model routing logic."""

    def test_router_initialization(self):
        """Test router can be initialized."""
        router = default_router()
        assert router is not None
        assert len(router.profiles) >= 7  # At least 7 models

    def test_model_profiles_exist(self):
        """Test all expected model profiles are defined."""
        expected = {
            "claude-3-5-haiku",
            "claude-3-5-sonnet",
            "claude-3-opus",
            "gemini-1.5-flash",
            "gemini-1.5-pro",
            "llama-2-7b",
        }
        assert expected.issubset(set(MODEL_PROFILES.keys()))

    def test_model_profile_structure(self):
        """Test model profiles have correct structure."""
        for model_id, profile in MODEL_PROFILES.items():
            assert profile.model_id
            assert profile.provider in ["anthropic", "google", "ollama"]
            assert profile.tier in ModelTier
            assert 0.0 <= profile.accuracy <= 1.0
            assert 0.0 <= profile.cost_per_1k_tokens
            assert profile.latency_ms > 0
            assert 0.0 <= profile.code_review_aptitude <= 1.0

    def test_rank_simple_task(self):
        """Test ranking for simple code review task."""
        router = default_router()
        ranking = router.rank_models(
            task_type="code_review",
            quality_threshold=0.85,
        )

        assert ranking.task_type == "code_review"
        assert len(ranking.ranked_models) > 0
        assert ranking.recommended_model
        assert len(ranking.fallback_chain) > 0

        # Top-ranked should be Haiku (cheap for code review)
        top_model = ranking.ranked_models[0][0]
        assert "haiku" in top_model or "flash" in top_model

    def test_rank_complex_task(self):
        """Test ranking for complex research task."""
        router = default_router()
        ranking = router.rank_models(
            task_type="research",
            quality_threshold=0.90,
        )

        assert ranking.task_type == "research"
        assert len(ranking.ranked_models) > 0

        # Top-ranked should be higher quality (Sonnet or Opus)
        top_model = ranking.ranked_models[0][0]
        assert any(m in top_model.lower() for m in ["sonnet", "opus", "pro"])

    def test_quality_threshold_enforcement(self):
        """Test quality threshold filters models."""
        router = default_router()

        # Very high threshold (only Opus + Sonnet)
        ranking_strict = router.rank_models(
            task_type="code_review",
            quality_threshold=0.94,
        )

        # Low threshold (includes Haiku + Gemini Flash)
        ranking_loose = router.rank_models(
            task_type="code_review",
            quality_threshold=0.70,
        )

        # Loose should have more candidates
        assert len(ranking_loose.ranked_models) > len(ranking_strict.ranked_models)

    def test_cost_limit(self):
        """Test max cost per 1K tokens filter."""
        router = default_router()

        # Very cheap limit (only Haiku and Gemini Flash)
        ranking = router.rank_models(
            task_type="code_review",
            max_cost_per_1k=0.01,  # Under 0.01/1K
        )

        for model_id, _, _ in ranking.ranked_models:
            profile = router.get_model_profile(model_id)
            assert profile.cost_per_1k_tokens <= 0.01

    def test_fallback_chain(self):
        """Test fallback chain logic."""
        router = default_router()
        ranking = router.rank_models(
            task_type="refactor",
            quality_threshold=0.85,
        )

        # Fallback should not include recommended model
        assert ranking.recommended_model not in ranking.fallback_chain

        # Fallback should be top alternatives (worse than recommended)
        rec_idx = next(
            i for i, (m, _, _) in enumerate(ranking.ranked_models)
            if m == ranking.recommended_model
        )

        for fallback_model in ranking.fallback_chain:
            fallback_idx = next(
                i for i, (m, _, _) in enumerate(ranking.ranked_models)
                if m == fallback_model
            )
            assert fallback_idx > rec_idx

    def test_cost_estimation(self):
        """Test cost estimation for a model."""
        router = default_router()

        # Estimate cost for Haiku: 1000 input + 500 output tokens
        cost = router.estimate_task_cost(
            "claude-3-5-haiku",
            estimated_input_tokens=1000,
            estimated_output_tokens=500,
        )

        # Cost should be > 0 (Haiku is not free)
        assert cost > 0
        assert cost < 0.01  # But cheap (< $0.01)

    def test_cost_estimation_free_model(self):
        """Test cost estimation for free model (Llama)."""
        router = default_router()

        cost = router.estimate_task_cost(
            "llama-2-7b",
            estimated_input_tokens=1000,
            estimated_output_tokens=500,
        )

        # Llama is free
        assert cost == 0.0

    def test_cost_estimation_expensive_model(self):
        """Test cost estimation for expensive model (Opus)."""
        router = default_router()

        cost = router.estimate_task_cost(
            "claude-3-opus",
            estimated_input_tokens=1000,
            estimated_output_tokens=500,
        )

        # Opus is expensive
        assert cost > 0.01

    def test_get_model_profile(self):
        """Test retrieving model profile."""
        router = default_router()

        profile = router.get_model_profile("claude-3-5-sonnet")
        assert profile is not None
        assert profile.model_id == "claude-3-5-sonnet-20241022"
        assert profile.provider == "anthropic"

    def test_get_nonexistent_profile(self):
        """Test retrieving nonexistent model."""
        router = default_router()

        profile = router.get_model_profile("nonexistent-model")
        assert profile is None

    def test_task_suitability_aptitude(self):
        """Test task suitability scoring."""
        router = default_router()

        # Code review ranking should prioritize code_review_aptitude
        ranking = router.rank_models(
            task_type="code_review",
            quality_threshold=0.80,
        )

        # Top model should have high code_review_aptitude
        top_model_id = ranking.ranked_models[0][0]
        top_profile = router.get_model_profile(top_model_id)
        assert top_profile.code_review_aptitude >= 0.75

    def test_all_task_types_supported(self):
        """Test that all task types can be ranked."""
        router = default_router()

        task_types = ["code_review", "research", "summary", "refactor"]
        for task_type in task_types:
            ranking = router.rank_models(task_type=task_type)
            assert ranking.recommended_model
            assert len(ranking.ranked_models) > 0


class TestMultiModelCostComparison:
    """Compare costs across models."""

    def test_cost_hierarchy(self):
        """Test cost hierarchy: Haiku < Sonnet < Opus."""
        router = default_router()

        haiku_cost = router.estimate_task_cost("claude-3-5-haiku", 1000, 500)
        sonnet_cost = router.estimate_task_cost("claude-3-5-sonnet", 1000, 500)
        opus_cost = router.estimate_task_cost("claude-3-opus", 1000, 500)

        assert haiku_cost < sonnet_cost < opus_cost

    def test_gemini_costs(self):
        """Test Gemini cost hierarchy."""
        router = default_router()

        flash_cost = router.estimate_task_cost("gemini-1.5-flash", 1000, 500)
        pro_cost = router.estimate_task_cost("gemini-1.5-pro", 1000, 500)

        assert flash_cost < pro_cost

    def test_cost_savings_potential(self):
        """Test potential cost savings with multi-model routing."""
        router = default_router()

        # All-Opus baseline
        opus_cost = router.estimate_task_cost("claude-3-opus", 10000, 5000)

        # Multi-model: Haiku for simple, Sonnet for medium, Opus for complex
        # Assume: 40% simple (Haiku), 40% medium (Sonnet), 20% complex (Opus)
        haiku_cost = router.estimate_task_cost("claude-3-5-haiku", 4000, 2000)
        sonnet_cost = router.estimate_task_cost("claude-3-5-sonnet", 4000, 2000)
        opus_cost_partial = router.estimate_task_cost("claude-3-opus", 2000, 1000)

        multimodel_cost = (haiku_cost + sonnet_cost + opus_cost_partial)

        savings = ((opus_cost - multimodel_cost) / opus_cost) * 100

        # Should achieve > 30% savings
        assert savings > 30

    def test_cost_vs_quality_tradeoff(self):
        """Test cost/quality tradeoff."""
        router = default_router()

        haiku_profile = router.get_model_profile("claude-3-5-haiku")
        opus_profile = router.get_model_profile("claude-3-opus")

        # Haiku should be much cheaper but less accurate
        assert haiku_profile.cost_per_1k_tokens < opus_profile.cost_per_1k_tokens
        assert haiku_profile.accuracy < opus_profile.accuracy


class TestModelSelectionScenarios:
    """Test realistic model selection scenarios."""

    def test_simple_code_review_scenario(self):
        """Simple code review: should select Haiku."""
        router = default_router()
        ranking = router.rank_models(
            task_type="code_review",
            quality_threshold=0.80,
        )

        # Haiku should be recommended (cheapest with good aptitude)
        assert "haiku" in ranking.recommended_model or "flash" in ranking.recommended_model

    def test_complex_research_scenario(self):
        """Complex research: should select Opus."""
        router = default_router()
        ranking = router.rank_models(
            task_type="research",
            quality_threshold=0.95,
        )

        # Opus should be recommended (best quality for research)
        assert ranking.recommended_model == "claude-3-opus"

    def test_constrained_budget_scenario(self):
        """Limited budget: should select Haiku or Gemini Flash."""
        router = default_router()
        ranking = router.rank_models(
            task_type="code_review",
            quality_threshold=0.80,
            max_cost_per_1k=0.005,  # Very cheap
        )

        # Should select from Haiku or Gemini Flash
        for model_id, _, _ in ranking.ranked_models:
            assert "haiku" in model_id or "flash" in model_id

    def test_quality_critical_scenario(self):
        """Quality critical: should select Opus."""
        router = default_router()
        ranking = router.rank_models(
            task_type="code_review",
            quality_threshold=0.98,
        )

        # Only Opus has 1.0 accuracy
        assert ranking.recommended_model == "claude-3-opus"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Multi-model cost model (ADR-0857).

The suite this replaces asserted the defects: it pinned
``claude-3-5-sonnet-20241022`` as an expected profile id, required
``0.0 <= profile.accuracy <= 1.0`` and ``profile.latency_ms > 0`` for fields
that were hand-written constants, and checked
``0.0 <= profile.code_review_aptitude <= 1.0``. Every one of those passed
while the module recommended models this install cannot run, at 2024 prices.
"""
from __future__ import annotations

import pytest

from core.models.multi_model_router import (
    ModelProfile,
    ModelRanking,
    ModelTier,
    MultiModelRouter,
    build_profiles,
    default_router,
    _strip_namespace,
    _tier_for,
)


def _profile(model_id, tier, in_rate, out_rate, provider="anthropic"):
    return ModelProfile(
        model_id=model_id, provider=provider, tier=tier,
        input_usd_per_1k=in_rate, output_usd_per_1k=out_rate,
    )


@pytest.fixture()
def router():
    return MultiModelRouter({
        "claude-haiku-4-5": _profile("claude-haiku-4-5", ModelTier.FAST_CHEAP, 0.001, 0.005),
        "claude-sonnet-5": _profile("claude-sonnet-5", ModelTier.BALANCED, 0.002, 0.010),
        "claude-opus-5": _profile("claude-opus-5", ModelTier.BEST_QUALITY, 0.005, 0.025),
        "claude-fable-5": _profile("claude-fable-5", ModelTier.FRONTIER, 0.010, 0.050),
        "ollama/qwen3:8b": _profile("ollama/qwen3:8b", ModelTier.LOCAL_FREE, 0.0, 0.0, "ollama"),
        "shell": _profile("shell", ModelTier.BALANCED, None, None, "copilot"),
    })


class TestProfilesComeFromRealSources:
    def test_prices_match_the_published_rate_card(self):
        """Not a second table: build_profiles reads model_price_per_1k."""
        p = build_profiles(["claude-opus-5", "claude-haiku-4-5", "claude-sonnet-5"])
        assert p["claude-opus-5"].input_usd_per_1k == pytest.approx(0.005)
        assert p["claude-opus-5"].output_usd_per_1k == pytest.approx(0.025)
        assert p["claude-haiku-4-5"].input_usd_per_1k == pytest.approx(0.001)
        assert p["claude-sonnet-5"].output_usd_per_1k == pytest.approx(0.010)

    def test_input_and_output_are_separate(self):
        """They differ 5x; one averaged cost_per_1k cannot be right for both."""
        p = build_profiles(["claude-opus-5"])["claude-opus-5"]
        assert p.input_usd_per_1k != p.output_usd_per_1k

    def test_no_accuracy_or_aptitude_fields_exist(self):
        """The invented constants must not come back."""
        p = build_profiles(["claude-opus-5"])["claude-opus-5"]
        for gone in ("accuracy", "latency_ms", "code_review_aptitude",
                     "research_aptitude", "summary_aptitude", "refactor_aptitude",
                     "cost_per_1k_tokens"):
            assert not hasattr(p, gone), f"{gone} is back"

    def test_registry_namespace_is_stripped_for_lookup(self):
        """OpenCode declares anthropic/<id>; without stripping it looks unknown."""
        assert _strip_namespace("anthropic/claude-opus-5") == "claude-opus-5"
        p = build_profiles(["anthropic/claude-opus-5"])["anthropic/claude-opus-5"]
        assert p.priced
        assert p.provider == "anthropic"
        assert p.tier is ModelTier.BEST_QUALITY

    def test_unknown_model_is_unpriced_not_free(self):
        p = build_profiles(["totally-made-up"])["totally-made-up"]
        assert p.priced is False
        assert p.input_usd_per_1k is None

    def test_local_model_is_genuinely_zero(self):
        p = build_profiles(["ollama/qwen3:8b"])["ollama/qwen3:8b"]
        assert p.tier is ModelTier.LOCAL_FREE
        assert p.priced and p.input_usd_per_1k == 0.0

    def test_tiers_follow_the_vendor_line_up(self):
        assert _tier_for("claude-haiku-4-5") < _tier_for("claude-sonnet-5")
        assert _tier_for("claude-sonnet-5") < _tier_for("claude-opus-5")
        assert _tier_for("claude-opus-5") < _tier_for("claude-fable-5")


class TestRanking:
    def test_ranks_cheapest_capable_first(self, router):
        r = router.rank_models(min_tier=ModelTier.BALANCED)
        assert r.recommended_model == "claude-sonnet-5"
        assert r.ranked_models[0][0] == "claude-sonnet-5"

    def test_tier_bar_excludes_weaker_models(self, router):
        r = router.rank_models(min_tier=ModelTier.BEST_QUALITY)
        ids = [m for m, _, _ in r.ranked_models]
        assert "claude-haiku-4-5" not in ids
        assert "claude-sonnet-5" not in ids
        assert "claude-opus-5" in ids

    def test_price_bound_is_respected(self, router):
        r = router.rank_models(min_tier=ModelTier.FAST_CHEAP, max_output_usd_per_1k=0.006)
        ids = [m for m, _, _ in r.ranked_models]
        assert "claude-haiku-4-5" in ids
        assert "claude-opus-5" not in ids

    def test_unpriced_models_are_reported_not_silently_dropped(self, router):
        r = router.rank_models(min_tier=ModelTier.BALANCED)
        assert "shell" in r.unpriced
        assert "shell" not in [m for m, _, _ in r.ranked_models]

    def test_local_can_be_excluded(self, router):
        r = router.rank_models(min_tier=ModelTier.LOCAL_FREE, allow_local=False)
        assert "ollama/qwen3:8b" not in [m for m, _, _ in r.ranked_models]

    def test_no_candidate_recommends_nothing(self, router):
        """It used to fall back to a hardcoded 'claude-3-5-sonnet'."""
        r = router.rank_models(min_tier=ModelTier.BEST_QUALITY, max_output_usd_per_1k=0.0001)
        assert r.ranked_models == []
        assert r.recommended_model is None
        assert "Nothing is recommended" in r.recommended_reason

    def test_recommended_is_not_also_a_fallback(self, router):
        r = router.rank_models(min_tier=ModelTier.FAST_CHEAP)
        assert r.recommended_model not in r.fallback_chain

    def test_empty_registry_yields_an_empty_router(self):
        r = MultiModelRouter({}).rank_models()
        assert r.recommended_model is None
        assert r.ranked_models == []


class TestCostEstimate:
    def test_applies_each_rate_to_its_own_side(self, router):
        # 10k in @ $0.005/1k = $0.05; 2k out @ $0.025/1k = $0.05
        assert router.estimate_task_cost("claude-opus-5", 10_000, 2_000) == pytest.approx(0.10)

    def test_output_heavy_costs_more_than_input_heavy(self, router):
        """The single averaged rate made these identical."""
        out_heavy = router.estimate_task_cost("claude-opus-5", 1_000, 10_000)
        in_heavy = router.estimate_task_cost("claude-opus-5", 10_000, 1_000)
        assert out_heavy > in_heavy

    def test_unknown_model_returns_none_not_zero(self, router):
        """0.0 was indistinguishable from a genuinely free local model."""
        assert router.estimate_task_cost("nope", 1000, 1000) is None

    def test_unpriced_model_returns_none(self, router):
        assert router.estimate_task_cost("shell", 1000, 1000) is None

    def test_local_model_is_zero(self, router):
        assert router.estimate_task_cost("ollama/qwen3:8b", 10_000, 10_000) == 0.0


class TestAgainstTheRealRegistry:
    def test_default_router_only_offers_runnable_models(self):
        """The old profiles named claude-3-opus and gemini-1.5-pro, which the
        engine registry does not declare and this install cannot run."""
        r = default_router()
        if not r.profiles:
            pytest.skip("engine registry not available in this environment")
        from core.models.multi_model_router import registry_model_ids

        declared = set(registry_model_ids())
        assert set(r.profiles) <= declared
        assert not [m for m in r.profiles if m.startswith("gemini")]
        assert not [m for m in r.profiles if "claude-3" in m]

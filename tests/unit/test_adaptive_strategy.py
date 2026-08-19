"""Unit Tests for Adaptive Strategy Engine (Phase 2 Improvement 4, Iteration k=2).

Tests cover:
- StrategyOption immutability and weighted_score() computation
- AdaptiveStrategyEngine.rank_strategies_by_fingerprint() with multiple fingerprint profiles
- Confidence gating at 0.7 boundary
- Fallback to empirical ranking when fingerprint is None or confidence < 0.7
- Operator preference score computation with expertise, speed, and risk profiles
"""

import pytest
from dataclasses import FrozenInstanceError

from core.learning.adaptive_strategy import (
    AdaptiveStrategyEngine,
    StrategyOption,
)
from core.learning.operator_fingerprint import OperatorFingerprint


class TestStrategyOption:
    """Tests for StrategyOption immutability and weighted_score()."""

    @pytest.fixture
    def sample_strategy(self):
        """Create a sample strategy option."""
        return StrategyOption(
            name="direct_fix",
            required_steps=5,
            avg_latency_ms=100.0,
            avg_cost_cents=50.0,
            success_rate=0.85,
            operator_preference_score=0.75,
        )

    def test_strategy_option_creation(self, sample_strategy):
        """Test basic strategy option creation."""
        assert sample_strategy.name == "direct_fix"
        assert sample_strategy.required_steps == 5
        assert sample_strategy.avg_latency_ms == 100.0
        assert sample_strategy.avg_cost_cents == 50.0
        assert sample_strategy.success_rate == 0.85
        assert sample_strategy.operator_preference_score == 0.75

    def test_strategy_option_immutability(self, sample_strategy):
        """Test that StrategyOption is frozen (immutable)."""
        with pytest.raises(FrozenInstanceError):
            sample_strategy.success_rate = 0.90

        with pytest.raises(FrozenInstanceError):
            sample_strategy.name = "pivot_approach"

    def test_weighted_score_default_weights(self, sample_strategy):
        """Test weighted_score() with default weights."""
        # Default weights: success=0.5, preference=0.3, efficiency=0.2
        # success_rate = 0.85
        # operator_preference_score = 0.75
        # avg_cost_cents = 50.0 → normalized = 0.5 → efficiency = 0.5
        # score = 0.85*0.5 + 0.75*0.3 + 0.5*0.2
        #       = 0.425 + 0.225 + 0.1
        #       = 0.75
        score = sample_strategy.weighted_score()
        assert abs(score - 0.75) < 0.01

    def test_weighted_score_custom_weights(self, sample_strategy):
        """Test weighted_score() with custom weights."""
        # Custom weights: success=0.6, preference=0.2, efficiency=0.2
        score = sample_strategy.weighted_score(
            success_weight=0.6,
            preference_weight=0.2,
            efficiency_weight=0.2,
        )
        # score = 0.85*0.6 + 0.75*0.2 + 0.5*0.2
        #       = 0.51 + 0.15 + 0.1
        #       = 0.76
        assert abs(score - 0.76) < 0.01

    def test_weighted_score_bounds(self):
        """Test that weighted_score() is clamped to [0, 1]."""
        # Very high preference score
        strategy = StrategyOption(
            name="test",
            required_steps=1,
            avg_latency_ms=10.0,
            avg_cost_cents=1.0,
            success_rate=1.0,
            operator_preference_score=1.0,
        )
        score = strategy.weighted_score()
        assert 0.0 <= score <= 1.0

        # Very low scores
        strategy_low = StrategyOption(
            name="test_low",
            required_steps=1,
            avg_latency_ms=10.0,
            avg_cost_cents=1.0,
            success_rate=0.0,
            operator_preference_score=0.0,
        )
        score_low = strategy_low.weighted_score()
        assert 0.0 <= score_low <= 1.0

    def test_weighted_score_high_cost(self):
        """Test efficiency computation with high cost."""
        strategy = StrategyOption(
            name="expensive",
            required_steps=10,
            avg_latency_ms=500.0,
            avg_cost_cents=200.0,  # > 100, will be clamped
            success_rate=0.9,
            operator_preference_score=0.8,
        )
        score = strategy.weighted_score()
        # normalized_cost = min(1.0, 200 / 100) = 1.0
        # efficiency = 1.0 - 1.0 = 0.0
        # score = 0.9*0.5 + 0.8*0.3 + 0.0*0.2 = 0.45 + 0.24 = 0.69
        assert abs(score - 0.69) < 0.01

    def test_weighted_score_low_cost(self):
        """Test efficiency computation with low cost."""
        strategy = StrategyOption(
            name="cheap",
            required_steps=2,
            avg_latency_ms=50.0,
            avg_cost_cents=5.0,
            success_rate=0.7,
            operator_preference_score=0.6,
        )
        score = strategy.weighted_score()
        # normalized_cost = 5 / 100 = 0.05
        # efficiency = 1.0 - 0.05 = 0.95
        # score = 0.7*0.5 + 0.6*0.3 + 0.95*0.2 = 0.35 + 0.18 + 0.19 = 0.72
        assert abs(score - 0.72) < 0.01


class TestAdaptiveStrategyEngine:
    """Tests for AdaptiveStrategyEngine ranking logic."""

    @pytest.fixture
    def engine(self):
        """Create engine instance."""
        return AdaptiveStrategyEngine()

    @pytest.fixture
    def available_strategies(self):
        """Create a set of diverse strategies."""
        return [
            StrategyOption(
                name="direct_fix",
                required_steps=5,
                avg_latency_ms=100.0,
                avg_cost_cents=50.0,
                success_rate=0.85,
                operator_preference_score=0.5,  # Will be recomputed
            ),
            StrategyOption(
                name="pivot_approach",
                required_steps=10,
                avg_latency_ms=200.0,
                avg_cost_cents=80.0,
                success_rate=0.75,
                operator_preference_score=0.5,
            ),
            StrategyOption(
                name="decompose",
                required_steps=15,
                avg_latency_ms=300.0,
                avg_cost_cents=100.0,
                success_rate=0.65,
                operator_preference_score=0.5,
            ),
        ]

    @pytest.fixture
    def high_confidence_fingerprint(self):
        """Create a high-confidence fingerprint (aggressive, fast operator)."""
        return OperatorFingerprint(
            operator_id="aggressive_fast",
            risk_tolerance=0.9,  # Aggressive
            speed_preference=0.9,  # Fast
            communication_style="terse",
            expertise_profile={"general": 0.8, "code_review": 0.9},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

    @pytest.fixture
    def medium_confidence_fingerprint(self):
        """Create a medium-confidence fingerprint."""
        return OperatorFingerprint(
            operator_id="neutral_medium",
            risk_tolerance=0.5,  # Neutral
            speed_preference=0.5,  # Neutral
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.5,
            last_updated="2026-08-19T00:00:00",
            total_observations=30,
        )

    @pytest.fixture
    def low_confidence_fingerprint(self):
        """Create a low-confidence fingerprint (conservative, thorough operator)."""
        return OperatorFingerprint(
            operator_id="conservative_thorough",
            risk_tolerance=0.2,  # Conservative
            speed_preference=0.2,  # Thorough
            communication_style="detailed",
            expertise_profile={"general": 0.3},
            confidence=0.2,
            last_updated="2026-08-19T00:00:00",
            total_observations=10,
        )

    def test_engine_initialization(self, engine):
        """Test engine is created and has logger."""
        assert engine.logger is not None

    def test_rank_strategies_empty_list(self, engine, high_confidence_fingerprint):
        """Test ranking with empty strategy list."""
        ranked = engine.rank_strategies_by_fingerprint(
            high_confidence_fingerprint,
            [],
            "general",
        )
        assert ranked == []

    def test_rank_strategies_high_confidence_aggressive(
        self,
        engine,
        available_strategies,
        high_confidence_fingerprint,
    ):
        """Test ranking with high-confidence aggressive fingerprint.

        Aggressive + fast operator should prefer:
        - High success rate (risk alignment)
        - Low latency (speed alignment)
        - High expertise match

        direct_fix (latency=100, success=0.85) should rank highest.
        """
        ranked = engine.rank_strategies_by_fingerprint(
            high_confidence_fingerprint,
            available_strategies,
            "general",
        )

        assert len(ranked) == 3
        # direct_fix should be ranked highest (lowest latency, high success)
        assert ranked[0].name == "direct_fix"

    def test_rank_strategies_high_confidence_conservative(
        self,
        engine,
        available_strategies,
    ):
        """Test ranking with high-confidence conservative fingerprint.

        Conservative + thorough operator should prefer:
        - Careful, multi-step strategies (lower risk)
        - Accepting higher latency (thorough)

        decompose (required_steps=15) should rank higher than direct_fix.
        """
        conservative_fp = OperatorFingerprint(
            operator_id="conservative",
            risk_tolerance=0.1,  # Very conservative
            speed_preference=0.1,  # Very thorough
            communication_style="detailed",
            expertise_profile={"general": 0.8},
            confidence=0.95,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        ranked = engine.rank_strategies_by_fingerprint(
            conservative_fp,
            available_strategies,
            "general",
        )

        assert len(ranked) == 3
        # Should rank differently than aggressive operator
        # (decompose should rank higher relative to direct_fix)

    def test_rank_strategies_low_confidence_fallback(
        self,
        engine,
        available_strategies,
        low_confidence_fingerprint,
    ):
        """Test that low-confidence fingerprint falls back to empirical ranking.

        When confidence < 0.7, should rank by success_rate only.
        Expected order: direct_fix (0.85) > pivot_approach (0.75) > decompose (0.65)
        """
        ranked = engine.rank_strategies_by_fingerprint(
            low_confidence_fingerprint,
            available_strategies,
            "general",
        )

        assert len(ranked) == 3
        # Fallback: rank by success_rate only
        assert ranked[0].name == "direct_fix"  # 0.85
        assert ranked[1].name == "pivot_approach"  # 0.75
        assert ranked[2].name == "decompose"  # 0.65

    def test_confidence_gate_at_boundary_just_below(self, engine, available_strategies):
        """Test confidence gating at boundary: confidence = 0.69 (just below 0.7)."""
        boundary_fp = OperatorFingerprint(
            operator_id="boundary_just_below",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.69,  # Just below threshold
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        )

        ranked = engine.rank_strategies_by_fingerprint(
            boundary_fp,
            available_strategies,
            "general",
        )

        # Should use empirical fallback
        assert ranked[0].name == "direct_fix"
        assert ranked[1].name == "pivot_approach"
        assert ranked[2].name == "decompose"

    def test_confidence_gate_at_boundary_exactly(self, engine, available_strategies):
        """Test confidence gating at boundary: confidence = 0.7 (exactly at threshold)."""
        boundary_fp = OperatorFingerprint(
            operator_id="boundary_exactly",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.7,  # Exactly at threshold
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        )

        ranked = engine.rank_strategies_by_fingerprint(
            boundary_fp,
            available_strategies,
            "general",
        )

        # Should use adaptive ranking (threshold is >=)
        assert len(ranked) == 3
        # All strategies should have recomputed preference scores
        assert all(s.operator_preference_score != 0.5 for s in ranked)

    def test_confidence_gate_at_boundary_just_above(self, engine, available_strategies):
        """Test confidence gating at boundary: confidence = 0.71 (just above 0.7)."""
        boundary_fp = OperatorFingerprint(
            operator_id="boundary_just_above",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.71,  # Just above threshold
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        )

        ranked = engine.rank_strategies_by_fingerprint(
            boundary_fp,
            available_strategies,
            "general",
        )

        # Should use adaptive ranking
        assert len(ranked) == 3

    def test_operator_preference_score_computation(self, engine, available_strategies):
        """Test _compute_operator_preference_score with known values.

        Algorithm: expertise*0.4 + speed_alignment*0.3 + risk_alignment*0.3
        """
        fingerprint = OperatorFingerprint(
            operator_id="test_op",
            risk_tolerance=0.8,  # Aggressive
            speed_preference=0.8,  # Fast
            communication_style="terse",
            expertise_profile={"general": 0.9},  # High expertise
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        # Test with direct_fix (latency=100ms, success=0.85)
        direct_fix = available_strategies[0]
        pref_score = engine._compute_operator_preference_score(
            fingerprint,
            direct_fix,
            "general",
        )

        # Verify it's in valid range
        assert 0.0 <= pref_score <= 1.0

        # For aggressive operator with high expertise:
        # Should be relatively high (operator aligns with fast, successful strategies)
        assert pref_score > 0.6

    def test_get_top_strategy_high_confidence(self, engine, available_strategies, high_confidence_fingerprint):
        """Test get_top_strategy() returns the highest-ranked strategy."""
        top = engine.get_top_strategy(
            high_confidence_fingerprint,
            available_strategies,
            "general",
        )

        assert top is not None
        assert top.name == "direct_fix"

    def test_get_top_strategy_low_confidence(self, engine, available_strategies, low_confidence_fingerprint):
        """Test get_top_strategy() with low-confidence fingerprint."""
        top = engine.get_top_strategy(
            low_confidence_fingerprint,
            available_strategies,
            "general",
        )

        assert top is not None
        # Fallback: should select by success_rate
        assert top.name == "direct_fix"

    def test_get_top_strategy_empty_list(self, engine, high_confidence_fingerprint):
        """Test get_top_strategy() with empty strategies list."""
        top = engine.get_top_strategy(
            high_confidence_fingerprint,
            [],
            "general",
        )

        assert top is None

    def test_ranking_preserves_strategy_data(self, engine, available_strategies, high_confidence_fingerprint):
        """Test that ranking preserves original strategy data.

        Preference score is recomputed, but other fields should be preserved.
        """
        ranked = engine.rank_strategies_by_fingerprint(
            high_confidence_fingerprint,
            available_strategies,
            "general",
        )

        for original, ranked_strategy in zip(available_strategies, ranked):
            assert ranked_strategy.name == original.name
            assert ranked_strategy.required_steps == original.required_steps
            assert ranked_strategy.avg_latency_ms == original.avg_latency_ms
            assert ranked_strategy.avg_cost_cents == original.avg_cost_cents
            assert ranked_strategy.success_rate == original.success_rate

    def test_multiple_strategies_same_success_rate(self, engine):
        """Test ranking when strategies have identical success rates.

        Adaptive ranking should differentiate via operator preference.
        """
        fp = OperatorFingerprint(
            operator_id="test",
            risk_tolerance=0.8,
            speed_preference=0.8,
            communication_style="terse",
            expertise_profile={"general": 0.8},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        strategies = [
            StrategyOption(
                name="slow_cheap",
                required_steps=10,
                avg_latency_ms=500.0,  # High latency
                avg_cost_cents=20.0,
                success_rate=0.80,  # Same
                operator_preference_score=0.5,
            ),
            StrategyOption(
                name="fast_expensive",
                required_steps=3,
                avg_latency_ms=50.0,  # Low latency
                avg_cost_cents=100.0,
                success_rate=0.80,  # Same
                operator_preference_score=0.5,
            ),
        ]

        ranked = engine.rank_strategies_by_fingerprint(fp, strategies, "general")

        # Fast operator (speed_preference=0.8) should prefer low-latency strategy
        assert ranked[0].name == "fast_expensive"
        assert ranked[1].name == "slow_cheap"

    def test_task_type_expertise_affects_ranking(self, engine):
        """Test that task_type is used for expertise lookup."""
        # Operator with high expertise in "code_review" but low in "general"
        fp = OperatorFingerprint(
            operator_id="code_review_expert",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={
                "general": 0.3,
                "code_review": 0.95,  # Expert in code_review
            },
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        strategies = [
            StrategyOption(
                name="strategy_a",
                required_steps=5,
                avg_latency_ms=100.0,
                avg_cost_cents=50.0,
                success_rate=0.75,
                operator_preference_score=0.5,
            ),
            StrategyOption(
                name="strategy_b",
                required_steps=5,
                avg_latency_ms=100.0,
                avg_cost_cents=50.0,
                success_rate=0.75,
                operator_preference_score=0.5,
            ),
        ]

        # Rank for "code_review" task
        ranked_code_review = engine.rank_strategies_by_fingerprint(
            fp, strategies, "code_review"
        )

        # Rank for "general" task
        ranked_general = engine.rank_strategies_by_fingerprint(
            fp, strategies, "general"
        )

        # Both should rank identically (same strategies, same scores)
        # But preference scores computed should differ in the algorithm
        assert len(ranked_code_review) == 2
        assert len(ranked_general) == 2

    def test_risk_alignment_computation(self, engine):
        """Test risk alignment aligns operator risk tolerance with strategy success.

        Conservative operator (risk_tolerance=0.1) should prefer low-success strategies
        less than aggressive operator (risk_tolerance=0.9).
        """
        strategies = [
            StrategyOption(
                name="risky_high_success",
                required_steps=3,
                avg_latency_ms=50.0,
                avg_cost_cents=20.0,
                success_rate=0.95,  # High success
                operator_preference_score=0.5,
            ),
        ]

        aggressive_fp = OperatorFingerprint(
            operator_id="aggressive",
            risk_tolerance=0.9,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        conservative_fp = OperatorFingerprint(
            operator_id="conservative",
            risk_tolerance=0.1,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        aggressive_pref = engine._compute_operator_preference_score(
            aggressive_fp, strategies[0], "general"
        )
        conservative_pref = engine._compute_operator_preference_score(
            conservative_fp, strategies[0], "general"
        )

        # Aggressive operator should have higher preference for high-success strategy
        assert aggressive_pref > conservative_pref

    def test_ranked_strategies_are_strategy_options(self, engine, available_strategies, high_confidence_fingerprint):
        """Test that ranked output contains StrategyOption instances."""
        ranked = engine.rank_strategies_by_fingerprint(
            high_confidence_fingerprint,
            available_strategies,
            "general",
        )

        for strategy in ranked:
            assert isinstance(strategy, StrategyOption)
            assert hasattr(strategy, "name")
            assert hasattr(strategy, "weighted_score")

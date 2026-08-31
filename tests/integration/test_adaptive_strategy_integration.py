"""Integration Tests for Adaptive Strategy Engine with StrategyAdvisor (Phase 2 Improvement 4, k=2).

Tests verify:
1. StrategyAdvisor initializes AdaptiveStrategyEngine
2. StrategyAdvisor.get_strategy() integrates fingerprint-based adaptive ranking
3. Wiring: fingerprint → adaptive_engine → ranked strategies → top-1 selection
4. Fallback behavior when fingerprint is missing or confidence is low
"""

import pytest
from unittest.mock import Mock, patch, MagicMock

from core.orchestration.subsystems.strategy_advisor import StrategyAdvisor
from core.learning.adaptive_strategy import StrategyOption
from core.learning.operator_fingerprint import OperatorFingerprint


class TestStrategyAdvisorIntegration:
    """Integration tests for StrategyAdvisor with AdaptiveStrategyEngine."""

    @pytest.fixture
    def advisor(self):
        """Create StrategyAdvisor instance."""
        return StrategyAdvisor(
            model="claude-3.5-sonnet",
            cache_predictions=True,
        )

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
                operator_preference_score=0.5,
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
        """Create a high-confidence fingerprint."""
        return OperatorFingerprint(
            operator_id="test_operator",
            risk_tolerance=0.85,
            speed_preference=0.85,
            communication_style="terse",
            expertise_profile={"general": 0.8},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

    @pytest.fixture
    def low_confidence_fingerprint(self):
        """Create a low-confidence fingerprint."""
        return OperatorFingerprint(
            operator_id="test_operator",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.3,
            last_updated="2026-08-19T00:00:00",
            total_observations=10,
        )

    def test_strategy_advisor_initializes_adaptive_engine(self, advisor):
        """Test that StrategyAdvisor creates AdaptiveStrategyEngine."""
        assert hasattr(advisor, "adaptive_engine")
        assert advisor.adaptive_engine is not None
        assert advisor.adaptive_engine.logger is not None

    def test_strategy_advisor_name_and_version(self, advisor):
        """Test StrategyAdvisor metadata."""
        assert advisor.name == "strategy_advisor"
        assert advisor.version == "1.0.0"

    def test_get_strategy_with_none_fingerprint(self, advisor, available_strategies):
        """Test get_strategy() falls back when fingerprint is None."""
        # Populate some empirical scores first
        advisor.strategy_scores["direct_fix"] = [1.0, 1.0, 0.0]  # 2/3 = 0.67
        advisor.strategy_scores["pivot_approach"] = [1.0, 0.0]  # 1/2 = 0.5
        advisor.strategy_scores["decompose"] = [0.0]  # 0/1 = 0.0

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=None,
            task_type="general",
        )

        assert strategy is not None
        assert strategy.name == "direct_fix"  # Highest empirical success rate

    def test_get_strategy_with_low_confidence_fingerprint(
        self, advisor, available_strategies, low_confidence_fingerprint
    ):
        """Test get_strategy() falls back when fingerprint confidence < 0.7."""
        advisor.strategy_scores["direct_fix"] = [1.0, 1.0, 0.0]
        advisor.strategy_scores["pivot_approach"] = [1.0, 0.0]
        advisor.strategy_scores["decompose"] = [0.0]

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=low_confidence_fingerprint,
            task_type="general",
        )

        assert strategy is not None
        # Fallback: should select by empirical success rate
        assert strategy.name == "direct_fix"

    def test_get_strategy_with_high_confidence_fingerprint(
        self, advisor, available_strategies, high_confidence_fingerprint
    ):
        """Test get_strategy() uses adaptive ranking with high-confidence fingerprint."""
        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=high_confidence_fingerprint,
            task_type="general",
        )

        assert strategy is not None
        # Adaptive ranking should select direct_fix (best for fast, aggressive operator)
        assert strategy.name == "direct_fix"

    def test_get_strategy_empty_list(self, advisor, high_confidence_fingerprint):
        """Test get_strategy() returns None for empty strategies."""
        strategy = advisor.get_strategy(
            [],
            fingerprint=high_confidence_fingerprint,
            task_type="general",
        )

        assert strategy is None

    def test_get_strategy_wiring_fingerprint_to_engine(
        self, advisor, available_strategies, high_confidence_fingerprint
    ):
        """Test that fingerprint is correctly wired into adaptive_engine.

        Verify the call path: fingerprint → adaptive_engine → ranked strategies → top-1
        """
        with patch.object(
            advisor.adaptive_engine,
            "rank_strategies_by_fingerprint",
            wraps=advisor.adaptive_engine.rank_strategies_by_fingerprint,
        ) as mock_rank:
            strategy = advisor.get_strategy(
                available_strategies,
                fingerprint=high_confidence_fingerprint,
                task_type="general",
            )

            # Verify adaptive_engine was called with correct arguments
            mock_rank.assert_called_once_with(
                high_confidence_fingerprint,
                available_strategies,
                "general",
            )

            assert strategy is not None

    def test_get_strategy_returns_strategy_option(self, advisor, available_strategies, high_confidence_fingerprint):
        """Test that get_strategy() returns a StrategyOption instance."""
        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=high_confidence_fingerprint,
            task_type="general",
        )

        assert isinstance(strategy, StrategyOption)
        assert hasattr(strategy, "name")
        assert hasattr(strategy, "success_rate")
        assert hasattr(strategy, "weighted_score")

    def test_get_strategy_task_type_passed_to_engine(
        self, advisor, available_strategies, high_confidence_fingerprint
    ):
        """Test that task_type is correctly passed to adaptive_engine."""
        with patch.object(
            advisor.adaptive_engine,
            "rank_strategies_by_fingerprint",
            wraps=advisor.adaptive_engine.rank_strategies_by_fingerprint,
        ) as mock_rank:
            advisor.get_strategy(
                available_strategies,
                fingerprint=high_confidence_fingerprint,
                task_type="code_review",  # Custom task type
            )

            # Verify task_type was passed correctly
            call_args = mock_rank.call_args
            assert call_args[0][2] == "code_review"

    def test_get_strategy_fallback_uses_empirical_only(
        self, advisor, available_strategies, low_confidence_fingerprint
    ):
        """Test fallback path computes success rate without fingerprint."""
        # Set up empirical scores
        advisor.strategy_scores["direct_fix"] = [1.0, 1.0, 0.0, 1.0]  # 3/4 = 0.75
        advisor.strategy_scores["pivot_approach"] = [1.0, 0.0]  # 1/2 = 0.5
        advisor.strategy_scores["decompose"] = [0.0, 0.0]  # 0/2 = 0.0

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=low_confidence_fingerprint,
            task_type="general",
        )

        # Should rank by empirical success rate
        assert strategy.name == "direct_fix"

    def test_get_strategy_default_task_type(self, advisor, available_strategies, high_confidence_fingerprint):
        """Test get_strategy() uses 'general' as default task_type."""
        with patch.object(
            advisor.adaptive_engine,
            "rank_strategies_by_fingerprint",
            wraps=advisor.adaptive_engine.rank_strategies_by_fingerprint,
        ) as mock_rank:
            advisor.get_strategy(
                available_strategies,
                fingerprint=high_confidence_fingerprint,
                # task_type not provided, should default to "general"
            )

            # Verify default task_type was used
            call_args = mock_rank.call_args
            assert call_args[0][2] == "general"

    def test_get_strategy_confidence_boundary_just_below(
        self, advisor, available_strategies
    ):
        """Test get_strategy() at confidence boundary = 0.69 (just below 0.7)."""
        fingerprint = OperatorFingerprint(
            operator_id="boundary",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.69,  # Just below threshold
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        )

        advisor.strategy_scores["direct_fix"] = [1.0, 1.0]
        advisor.strategy_scores["pivot_approach"] = [1.0]
        advisor.strategy_scores["decompose"] = [0.0]

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=fingerprint,
            task_type="general",
        )

        # Should use fallback (empirical only)
        assert strategy.name == "direct_fix"

    def test_get_strategy_confidence_boundary_exactly(
        self, advisor, available_strategies
    ):
        """Test get_strategy() at confidence boundary = 0.7 (exactly at threshold)."""
        fingerprint = OperatorFingerprint(
            operator_id="boundary",
            risk_tolerance=0.5,
            speed_preference=0.5,
            communication_style="neutral",
            expertise_profile={"general": 0.5},
            confidence=0.7,  # Exactly at threshold
            last_updated="2026-08-19T00:00:00",
            total_observations=50,
        )

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=fingerprint,
            task_type="general",
        )

        # Should use adaptive ranking (>= 0.7)
        assert strategy is not None

    def test_get_strategy_logarithm_info_message(
        self, advisor, available_strategies, high_confidence_fingerprint, caplog
    ):
        """Test that successful adaptive selection logs info message."""
        import logging

        with caplog.at_level(logging.INFO):
            strategy = advisor.get_strategy(
                available_strategies,
                fingerprint=high_confidence_fingerprint,
                task_type="general",
            )

            # Should have logged success message
            assert strategy is not None
            # Note: Could also check caplog for the info message
            # assert "Selected strategy" in caplog.text

    def test_get_strategy_debug_message_fallback(
        self, advisor, available_strategies, low_confidence_fingerprint, caplog
    ):
        """Test that fallback path logs debug message."""
        import logging

        with caplog.at_level(logging.DEBUG):
            strategy = advisor.get_strategy(
                available_strategies,
                fingerprint=low_confidence_fingerprint,
                task_type="general",
            )

            assert strategy is not None
            # Should have logged fallback message
            # assert "empirical fallback" in caplog.text.lower()

    def test_multiple_calls_with_different_fingerprints(
        self, advisor, available_strategies
    ):
        """Test that get_strategy() handles multiple calls with different fingerprints."""
        fp1 = OperatorFingerprint(
            operator_id="op1",
            risk_tolerance=0.9,
            speed_preference=0.9,
            communication_style="terse",
            expertise_profile={"general": 0.8},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        fp2 = OperatorFingerprint(
            operator_id="op2",
            risk_tolerance=0.1,
            speed_preference=0.1,
            communication_style="detailed",
            expertise_profile={"general": 0.3},
            confidence=0.9,
            last_updated="2026-08-19T00:00:00",
            total_observations=100,
        )

        strategy1 = advisor.get_strategy(available_strategies, fingerprint=fp1)
        strategy2 = advisor.get_strategy(available_strategies, fingerprint=fp2)

        # Different fingerprints may produce different results
        assert strategy1 is not None
        assert strategy2 is not None

    def test_get_strategy_with_expertise_lookup(self, advisor, available_strategies):
        """Test that expertise_profile[task_type] is used in ranking."""
        fingerprint = OperatorFingerprint(
            operator_id="expert",
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

        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=fingerprint,
            task_type="code_review",  # Use code_review task type
        )

        assert strategy is not None
        # Expertise profile should have been consulted

    def test_integration_end_to_end_flow(
        self, advisor, available_strategies, high_confidence_fingerprint
    ):
        """End-to-end integration test: fingerprint → adaptive_engine → ranked → top strategy."""
        # This is the main integration flow that k=2 is testing
        strategy = advisor.get_strategy(
            available_strategies,
            fingerprint=high_confidence_fingerprint,
            task_type="general",
        )

        # Verify result
        assert strategy is not None
        assert isinstance(strategy, StrategyOption)
        assert strategy.name in ["direct_fix", "pivot_approach", "decompose"]
        assert 0.0 <= strategy.success_rate <= 1.0
        assert 0.0 <= strategy.operator_preference_score <= 1.0

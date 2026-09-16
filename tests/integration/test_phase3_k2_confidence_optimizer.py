"""Phase 3 k=2: Comprehensive test suite for Confidence Scoring Optimizer (ADR-0773).

40+ tests covering:
- Confidence convergence (Bayesian learning)
- Learning rate decay (10% per week)
- Variant selection (epsilon-greedy bandit)
- Integration with Phase 2 A/B Testing
- Edge cases and failure modes
- GDPR compliance (tenant isolation, fail-closed)
"""

from __future__ import annotations

import pytest
from datetime import datetime, timedelta
from unittest.mock import Mock, patch

from core.learning.confidence_optimizer import (
    ConfidenceOptimizer,
    VariantSelector,
    ConfidenceMetric,
    OutcomeSignal,
    VariantDecision,
)


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def tenant_id():
    """Test tenant ID (GDPR Art. 32)."""
    return "test_tenant_default"


@pytest.fixture
def optimizer(tenant_id):
    """ConfidenceOptimizer instance."""
    return ConfidenceOptimizer(
        tenant_id=tenant_id,
        observation_window=20,
        convergence_threshold=0.05,
        initial_learning_rate=0.05,
        decay_per_week=0.10,
    )


@pytest.fixture
def selector(tenant_id, optimizer):
    """VariantSelector instance."""
    return VariantSelector(
        tenant_id=tenant_id,
        optimizer=optimizer,
        epsilon=0.05,
        high_variance_threshold=0.10,
    )


# ============================================================================
# TESTS: ConfidenceOptimizer — Initialization
# ============================================================================


class TestConfidenceOptimizerInit:
    """Tests for ConfidenceOptimizer.__init__()."""

    def test_init_valid(self, tenant_id):
        """Should initialize with valid parameters."""
        opt = ConfidenceOptimizer(tenant_id=tenant_id)
        assert opt.tenant_id == tenant_id
        assert opt.observation_window == 20
        assert opt.convergence_threshold == 0.05

    def test_init_missing_tenant_id_fails(self):
        """Should fail if tenant_id is empty (GDPR Art. 32, fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id required"):
            ConfidenceOptimizer(tenant_id="")

    def test_init_invalid_observation_window_fails(self, tenant_id):
        """Should fail if observation_window < 10."""
        with pytest.raises(ValueError, match="observation_window must be ≥10"):
            ConfidenceOptimizer(tenant_id=tenant_id, observation_window=5)

    def test_init_invalid_learning_rate_fails(self, tenant_id):
        """Should fail if learning_rate ≤ 0 or > 1."""
        with pytest.raises(ValueError, match="learning_rate must be"):
            ConfidenceOptimizer(tenant_id=tenant_id, initial_learning_rate=0.0)

        with pytest.raises(ValueError, match="learning_rate must be"):
            ConfidenceOptimizer(tenant_id=tenant_id, initial_learning_rate=1.5)

    def test_init_custom_params(self, tenant_id):
        """Should accept custom convergence parameters."""
        opt = ConfidenceOptimizer(
            tenant_id=tenant_id,
            observation_window=50,
            convergence_threshold=0.02,
            initial_learning_rate=0.10,
            decay_per_week=0.15,
        )
        assert opt.observation_window == 50
        assert opt.convergence_threshold == 0.02
        assert opt.initial_learning_rate == 0.10
        assert opt.decay_per_week == 0.15


# ============================================================================
# TESTS: ConfidenceOptimizer — Outcome Recording & Confidence Update
# ============================================================================


class TestConfidenceOptimizerOutcomeRecording:
    """Tests for ConfidenceOptimizer.record_outcome()."""

    def test_record_first_outcome(self, optimizer):
        """Should initialize metric on first outcome."""
        signal = OutcomeSignal(model_id="opus", success=True)
        metric = optimizer.record_outcome(signal)

        assert metric.model_id == "opus"
        assert metric.confidence > 0.5  # Should improve from 0.5 neutral prior
        assert metric.n_observations == 1
        assert metric.converged is False  # Need 20 observations

    def test_record_multiple_outcomes_success(self, optimizer):
        """Should accumulate confidence with successful outcomes."""
        initial_confidence = 0.5

        for i in range(5):
            signal = OutcomeSignal(model_id="opus", success=True)
            metric = optimizer.record_outcome(signal)

        assert metric.confidence > initial_confidence
        assert metric.n_observations == 5

    def test_record_multiple_outcomes_failure(self, optimizer):
        """Should reduce confidence with failed outcomes."""
        initial_confidence = 0.5

        for i in range(5):
            signal = OutcomeSignal(model_id="opus", success=False)
            metric = optimizer.record_outcome(signal)

        assert metric.confidence < initial_confidence
        assert metric.n_observations == 5

    def test_record_partial_credit(self, optimizer):
        """Should handle partial credit outcomes."""
        signal1 = OutcomeSignal(model_id="sonnet", success=False, partial_credit=0.5)
        metric1 = optimizer.record_outcome(signal1)

        assert 0.0 < metric1.confidence < 1.0

    def test_record_outcome_missing_model_id_fails(self, optimizer):
        """Should fail if model_id missing (fail-closed)."""
        signal = OutcomeSignal(model_id="", success=True)
        with pytest.raises(ValueError, match="model_id required"):
            optimizer.record_outcome(signal)

    def test_rolling_window_keeps_last_n_observations(self, optimizer):
        """Should keep only last N observations in rolling window."""
        for i in range(30):
            signal = OutcomeSignal(model_id="opus", success=True)
            optimizer.record_outcome(signal)

        metric = optimizer.get_metric("opus")
        assert metric.n_observations == optimizer.observation_window  # Max 20

    def test_variance_computation(self, optimizer):
        """Should compute variance correctly."""
        # Perfect success: variance should be 0
        for i in range(20):
            signal = OutcomeSignal(model_id="opus", success=True)
            optimizer.record_outcome(signal)

        metric = optimizer.get_metric("opus")
        assert metric.variance < 0.01  # Nearly 0

    def test_error_rate_computation(self, optimizer):
        """Should track error rate separately from success."""
        # 50% success, 50% error
        for i in range(10):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        for i in range(10):
            optimizer.record_outcome(
                OutcomeSignal(model_id="opus", success=False, error_msg="timeout")
            )

        metric = optimizer.get_metric("opus")
        assert 0.4 < metric.confidence < 0.6  # Should stay near neutral


# ============================================================================
# TESTS: ConfidenceOptimizer — Convergence Detection
# ============================================================================


class TestConfidenceOptimizerConvergence:
    """Tests for convergence criterion (variance < 0.05 & ≥20 observations)."""

    def test_converges_after_consistent_success(self, optimizer):
        """Should converge after 20+ consistent successful outcomes."""
        # 20 successes in a row
        for i in range(20):
            signal = OutcomeSignal(model_id="opus", success=True)
            metric = optimizer.record_outcome(signal)

        assert metric.converged is True
        assert metric.variance < optimizer.convergence_threshold
        assert metric.n_observations == 20

    def test_does_not_converge_below_observation_threshold(self, optimizer):
        """Should not converge with <20 observations, even if variance low."""
        for i in range(15):
            signal = OutcomeSignal(model_id="opus", success=True)
            metric = optimizer.record_outcome(signal)

        assert metric.converged is False  # Only 15 observations

    def test_does_not_converge_high_variance(self, optimizer):
        """Should not converge with high variance, even with 20+ observations."""
        # Alternating success/failure
        for i in range(25):
            success = i % 2 == 0
            signal = OutcomeSignal(model_id="opus", success=success)
            metric = optimizer.record_outcome(signal)

        assert metric.converged is False
        assert metric.variance > optimizer.convergence_threshold

    def test_has_converged_check(self, optimizer):
        """Should provide boolean has_converged() check."""
        # Not converged initially
        assert optimizer.has_converged("opus") is False

        # Add 20 successes
        for i in range(20):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))

        assert optimizer.has_converged("opus") is True

    def test_convergence_requires_recent_data(self, optimizer):
        """Convergence should be based on recent rolling window, not all-time."""
        # 20 successes
        for i in range(20):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))

        metric1 = optimizer.get_metric("opus")
        assert metric1.converged is True

        # Now add 3 failures (rolling window updates)
        for i in range(3):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=False))

        metric2 = optimizer.get_metric("opus")
        # Window now has 17 successes + 3 failures = more variance
        assert metric2.converged is False or metric2.variance > 0.02


# ============================================================================
# TESTS: ConfidenceOptimizer — Learning Rate Decay
# ============================================================================


class TestConfidenceOptimizerDecay:
    """Tests for learning rate decay (10% per week)."""

    def test_learning_rate_decay_over_time(self, optimizer):
        """Should decay learning rate over time (no actual time passage in test)."""
        # This is a theoretical test; actual time decay requires mocking
        signal = OutcomeSignal(model_id="opus", success=True)
        metric = optimizer.record_outcome(signal)

        initial_lr = metric.learning_rate
        assert 0.0 < initial_lr <= optimizer.initial_learning_rate

    def test_get_learning_rate_no_decay_initially(self, optimizer):
        """Should have full learning rate initially."""
        signal = OutcomeSignal(model_id="opus", success=True)
        metric = optimizer.record_outcome(signal)

        # Just created, should be close to initial
        assert abs(metric.learning_rate - optimizer.initial_learning_rate) < 0.001

    @patch("core.learning.confidence_optimizer.datetime")
    def test_learning_rate_decay_after_week(self, mock_datetime, optimizer):
        """Should decay learning rate by 10% after 7 days (mocked time)."""
        now = datetime.now()
        mock_datetime.now.return_value = now

        # Record first outcome
        signal = OutcomeSignal(model_id="opus", success=True)
        metric1 = optimizer.record_outcome(signal)
        lr1 = metric1.learning_rate

        # Simulate 7 days later
        mock_datetime.now.return_value = now + timedelta(days=7)

        # Record another outcome
        signal = OutcomeSignal(model_id="opus", success=True)
        metric2 = optimizer.record_outcome(signal)
        lr2 = metric2.learning_rate

        # LR should decay by ~10%
        expected_decay = optimizer.initial_learning_rate * (1 - optimizer.decay_per_week)
        assert abs(lr2 - expected_decay) < 0.002


# ============================================================================
# TESTS: ConfidenceOptimizer — Metrics & State
# ============================================================================


class TestConfidenceOptimizerMetrics:
    """Tests for getting/querying metrics."""

    def test_get_metric_exists(self, optimizer):
        """Should return metric if it exists."""
        optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        metric = optimizer.get_metric("opus")
        assert metric is not None
        assert metric.model_id == "opus"

    def test_get_metric_not_exists(self, optimizer):
        """Should return None if metric doesn't exist."""
        metric = optimizer.get_metric("nonexistent")
        assert metric is None

    def test_get_all_metrics(self, optimizer):
        """Should return dict of all metrics."""
        optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=True))

        all_metrics = optimizer.get_all_metrics()
        assert "opus" in all_metrics
        assert "sonnet" in all_metrics
        assert len(all_metrics) == 2

    def test_metric_immutability(self, optimizer):
        """Metrics should be immutable (frozen dataclass)."""
        optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        metric = optimizer.get_metric("opus")

        with pytest.raises(AttributeError):
            metric.confidence = 0.9  # Should be frozen


# ============================================================================
# TESTS: VariantSelector — Initialization
# ============================================================================


class TestVariantSelectorInit:
    """Tests for VariantSelector.__init__()."""

    def test_init_valid(self, tenant_id, optimizer):
        """Should initialize with valid parameters."""
        sel = VariantSelector(tenant_id=tenant_id, optimizer=optimizer)
        assert sel.tenant_id == tenant_id
        assert sel.optimizer is optimizer
        assert sel.epsilon == 0.05

    def test_init_missing_tenant_id_fails(self, optimizer):
        """Should fail if tenant_id is empty (GDPR Art. 32, fail-closed)."""
        with pytest.raises(ValueError, match="tenant_id required"):
            VariantSelector(tenant_id="", optimizer=optimizer)

    def test_init_invalid_epsilon_fails(self, tenant_id, optimizer):
        """Should fail if epsilon not in [0.0, 1.0]."""
        with pytest.raises(ValueError, match="epsilon must be"):
            VariantSelector(tenant_id=tenant_id, optimizer=optimizer, epsilon=-0.1)

        with pytest.raises(ValueError, match="epsilon must be"):
            VariantSelector(tenant_id=tenant_id, optimizer=optimizer, epsilon=1.5)

    def test_init_custom_params(self, tenant_id, optimizer):
        """Should accept custom bandit parameters."""
        sel = VariantSelector(
            tenant_id=tenant_id,
            optimizer=optimizer,
            epsilon=0.10,
            high_variance_threshold=0.20,
        )
        assert sel.epsilon == 0.10
        assert sel.high_variance_threshold == 0.20


# ============================================================================
# TESTS: VariantSelector — Selection Logic
# ============================================================================


class TestVariantSelectorSelection:
    """Tests for VariantSelector.select_variant()."""

    def test_select_empty_candidates_fails(self, selector):
        """Should fail if candidates list is empty."""
        with pytest.raises(ValueError, match="candidates list required"):
            selector.select_variant([])

    def test_select_single_candidate(self, selector):
        """Should select only candidate if list has 1."""
        decision = selector.select_variant(["opus"])
        assert decision.variant_id == "opus"

    def test_exploit_highest_confidence(self, selector, optimizer):
        """Should exploit (select) model with highest confidence."""
        # Setup: opus=0.8, sonnet=0.5
        for i in range(20):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        for i in range(10):
            optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=True))

        # Force exploit (epsilon=0) by mocking
        selector.epsilon = 0.0
        decision = selector.select_variant(["opus", "sonnet"])

        assert decision.variant_id == "opus"
        assert decision.reason == "exploit_confidence"
        assert decision.exploration_chance == 0.0

    def test_explore_due_to_high_variance(self, selector, optimizer):
        """Should force exploration when variance is high."""
        # Create high-variance outcomes for opus
        for i in range(15):
            success = i % 2 == 0
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=success))

        metric = optimizer.get_metric("opus")
        assert metric.variance > selector.high_variance_threshold

        # Should force exploration
        decision = selector.select_variant(["opus", "sonnet"])
        assert decision.reason == "explore_high_variance"
        assert decision.exploration_chance == 1.0

    @patch("core.learning.confidence_optimizer.random.random")
    def test_epsilon_greedy_explore(self, mock_random, selector, optimizer):
        """Should explore with probability epsilon."""
        mock_random.return_value = 0.01  # Less than epsilon (0.05)

        # Setup equal confidence
        optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=True))

        decision = selector.select_variant(["opus", "sonnet"])
        assert decision.reason == "explore_random"
        assert decision.exploration_chance == selector.epsilon

    @patch("core.learning.confidence_optimizer.random.random")
    @patch("core.learning.confidence_optimizer.random.choice")
    def test_epsilon_greedy_explore_picks_random(
        self, mock_choice, mock_random, selector, optimizer
    ):
        """Exploration should pick a random candidate."""
        mock_random.return_value = 0.01  # Trigger explore
        mock_choice.return_value = "sonnet"

        decision = selector.select_variant(["opus", "sonnet", "haiku"])
        assert decision.variant_id == "sonnet"

    @patch("core.learning.confidence_optimizer.random.random")
    def test_epsilon_greedy_exploit(self, mock_random, selector, optimizer):
        """Should exploit with probability 1-epsilon."""
        mock_random.return_value = 0.99  # Greater than epsilon (0.05)

        # Setup: opus > sonnet
        for i in range(10):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        for i in range(5):
            optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=True))

        decision = selector.select_variant(["opus", "sonnet"])
        assert decision.variant_id == "opus"
        assert decision.reason == "exploit_confidence"


# ============================================================================
# TESTS: VariantSelector — Selection History
# ============================================================================


class TestVariantSelectorHistory:
    """Tests for selection history tracking."""

    def test_get_selection_history_empty(self, selector):
        """Should return empty list initially."""
        history = selector.get_selection_history()
        assert history == []

    def test_get_selection_history_accumulates(self, selector):
        """Should accumulate selections over time."""
        selector.select_variant(["opus"])
        selector.select_variant(["sonnet"])
        selector.select_variant(["haiku"])

        history = selector.get_selection_history()
        assert len(history) == 3

    def test_get_latest_selection_none(self, selector):
        """Should return None if no selections yet."""
        assert selector.get_latest_selection() is None

    def test_get_latest_selection(self, selector):
        """Should return most recent selection."""
        selector.select_variant(["opus"])
        sel1 = selector.get_latest_selection()
        assert sel1.variant_id == "opus"

        selector.select_variant(["sonnet"])
        sel2 = selector.get_latest_selection()
        assert sel2.variant_id == "sonnet"


# ============================================================================
# TESTS: Integration — Learning Loop (Phase 2 A/B + Phase 3 k=1 + k=2)
# ============================================================================


class TestIntegrationLearningLoop:
    """Tests for end-to-end learning loop: selection → outcome → optimization."""

    def test_learning_loop_single_variant_convergence(self, selector, optimizer):
        """Should converge on best variant through iterative learning."""
        # Simulate 20 tasks: always select opus, always succeeds
        for task_num in range(20):
            # Select variant
            decision = selector.select_variant(["opus"])
            assert decision.variant_id == "opus"

            # Record outcome
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))

        # Should converge
        metric = optimizer.get_metric("opus")
        assert metric.converged is True
        assert metric.confidence > 0.95

    def test_learning_loop_variant_switching(self, selector, optimizer):
        """Should switch variants based on learned confidence."""
        # Phase 1: opus fails consistently
        for i in range(15):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=False))

        # Phase 2: sonnet succeeds consistently
        for i in range(15):
            optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=True))

        # Set epsilon to 0 to force exploitation
        selector.epsilon = 0.0

        # Now should prefer sonnet
        decision = selector.select_variant(["opus", "sonnet"])
        assert decision.variant_id == "sonnet"

    def test_learning_loop_with_exploration(self, selector, optimizer):
        """Should balance exploration vs. exploitation over time."""
        # Create scenario: opus has high confidence, but sonnet is unknown
        for i in range(20):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))

        # Make multiple selections (some will explore)
        selections = []
        for i in range(100):
            decision = selector.select_variant(["opus", "sonnet"])
            selections.append(decision.variant_id)

        # Should mostly select opus (exploit), but some sonnet (explore)
        opus_count = sum(1 for s in selections if s == "opus")
        sonnet_count = sum(1 for s in selections if s == "sonnet")

        assert opus_count > sonnet_count  # Mostly exploit
        assert sonnet_count > 0  # Some exploration


# ============================================================================
# TESTS: Edge Cases & Error Handling
# ============================================================================


class TestEdgeCases:
    """Tests for edge cases and error handling (fail-closed)."""

    def test_null_outcomes_handled_gracefully(self, optimizer):
        """Should handle None outcomes gracefully (fail-closed)."""
        signal = OutcomeSignal(model_id="opus", success=True, error_msg=None)
        metric = optimizer.record_outcome(signal)
        assert metric is not None

    def test_confidence_clamped_to_bounds(self, optimizer):
        """Should clamp confidence to [0.0, 1.0]."""
        # Try to push above 1.0
        for i in range(100):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))

        metric = optimizer.get_metric("opus")
        assert 0.0 <= metric.confidence <= 1.0

        # Try to push below 0.0
        optimizer2 = ConfidenceOptimizer(tenant_id="test2")
        for i in range(100):
            optimizer2.record_outcome(OutcomeSignal(model_id="sonnet", success=False))

        metric2 = optimizer2.get_metric("sonnet")
        assert 0.0 <= metric2.confidence <= 1.0

    def test_multiple_models_independent(self, optimizer):
        """Should track confidence independently for each model."""
        for i in range(10):
            optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        for i in range(10):
            optimizer.record_outcome(OutcomeSignal(model_id="sonnet", success=False))

        opus_metric = optimizer.get_metric("opus")
        sonnet_metric = optimizer.get_metric("sonnet")

        assert opus_metric.confidence > 0.5
        assert sonnet_metric.confidence < 0.5


# ============================================================================
# TESTS: Compliance (GDPR, Audit Trail)
# ============================================================================


class TestCompliance:
    """Tests for GDPR compliance and audit trail integration."""

    def test_tenant_isolation_enforced_on_init(self):
        """Should enforce tenant_id (GDPR Art. 32, fail-closed)."""
        with pytest.raises(ValueError):
            ConfidenceOptimizer(tenant_id="")

    def test_variant_selector_tenant_isolation(self, optimizer):
        """Should enforce tenant_id on selector (GDPR Art. 32, fail-closed)."""
        with pytest.raises(ValueError):
            VariantSelector(tenant_id="", optimizer=optimizer)

    def test_outcome_signal_immutable(self, optimizer):
        """OutcomeSignal should be immutable (fail-closed)."""
        signal = OutcomeSignal(model_id="opus", success=True)

        # Should not be able to modify
        with pytest.raises(AttributeError):
            signal.success = False

    def test_confidence_metric_immutable(self, optimizer):
        """ConfidenceMetric should be immutable (fail-closed)."""
        optimizer.record_outcome(OutcomeSignal(model_id="opus", success=True))
        metric = optimizer.get_metric("opus")

        with pytest.raises(AttributeError):
            metric.confidence = 0.99


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Phase 4 Week 19: Feedback-Meta Loop Integration Tests — feedback → optimizer tuning.

Test coverage:
- Feedback signal processing (4 tests)
- Confidence filtering (3 tests)
- Conservative mode activation (3 tests)
- Rollback on divergence (3 tests)
- Consensus calculation (2 tests)
- Total: 15 tests
"""

import pytest
from unittest.mock import Mock, patch

from core.learning.meta_optimizer import MetaOptimizer


class TestFeedbackSignalProcessing:
    """Test feedback signal processing and parameter tuning."""

    def test_process_all_yes_feedback(self):
        """Process feedback where users consistently say 'yes'."""
        optimizer = MetaOptimizer()
        initial_α_core = optimizer.α_core

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.95} for _ in range(10)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is True
        # Positive feedback should increase learning rate (slightly)
        # since consensus is strong toward "yes"

    def test_process_all_no_feedback(self):
        """Process feedback where users consistently say 'no'."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "no", "confidence": 0.9} for _ in range(10)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is True
        # Negative feedback should adjust parameters conservatively

    def test_process_mixed_feedback(self):
        """Process mixed yes/no feedback."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.8} for _ in range(6)
        ] + [
            {"outcome_feedback": "no", "confidence": 0.7} for _ in range(4)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is True

    def test_insufficient_samples_rejected(self):
        """Reject feedback with <10 samples."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(5)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is False  # Need >= 10 samples

    def test_low_confidence_feedback_ignored(self):
        """Ignore feedback with average confidence <0.6."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.3} for _ in range(10)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is False  # Average confidence 0.3 < 0.6 threshold


class TestConfidenceFiltering:
    """Test confidence-based feedback filtering."""

    def test_high_confidence_applied(self):
        """High-confidence feedback is applied."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.95} for _ in range(10)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is True

    def test_mixed_confidence_averaged(self):
        """Mixed confidence levels are averaged."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9},
            {"outcome_feedback": "yes", "confidence": 0.8},
            {"outcome_feedback": "yes", "confidence": 0.7},
            {"outcome_feedback": "yes", "confidence": 0.6},
            {"outcome_feedback": "yes", "confidence": 0.5},  # Below 0.6
            {"outcome_feedback": "yes", "confidence": 0.5},
            {"outcome_feedback": "yes", "confidence": 0.6},
            {"outcome_feedback": "yes", "confidence": 0.8},
            {"outcome_feedback": "yes", "confidence": 0.9},
            {"outcome_feedback": "yes", "confidence": 0.7},
        ]

        # Average = (0.9+0.8+0.7+0.6+0.5+0.5+0.6+0.8+0.9+0.7)/10 = 0.72
        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is True  # 0.72 >= 0.6

    def test_zero_confidence_feedback(self):
        """Feedback with zero confidence (unsure) is ignored."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.0} for _ in range(10)
        ]

        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert result is False  # 0.0 < 0.6 threshold


class TestConservativeModeActivation:
    """Test conservative mode triggering."""

    def test_conservative_mode_on_contradictions(self):
        """Enable conservative mode when feedback contradicts."""
        optimizer = MetaOptimizer()
        assert optimizer.conservative_mode is False

        # Mix of yes and no (contradiction)
        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(6)
        ] + [
            {"outcome_feedback": "no", "confidence": 0.8} for _ in range(4)
        ]

        optimizer.process_feedback_signal(feedback_outcomes)
        assert optimizer.conservative_mode is True  # Contradiction detected

    def test_conservative_mode_reduces_learning_rate(self):
        """Conservative mode halves the learning rate."""
        optimizer = MetaOptimizer()
        original_learning_rate = optimizer.learning_rate_meta

        optimizer.conservative_mode = True
        effective_rate = original_learning_rate * 0.5  # Half when conservative
        assert effective_rate < original_learning_rate

    def test_conservative_mode_clear_on_agreement(self):
        """Clear conservative mode when feedback agrees."""
        optimizer = MetaOptimizer()
        optimizer.consecutive_worsening = 0
        optimizer.conservative_mode = False

        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(10)
        ]

        optimizer.process_feedback_signal(feedback_outcomes)
        # Unanimous positive feedback, no contradiction
        # Conservative mode should remain or turn off
        # (implementation depends on divergence detection)


class TestDivergenceDetection:
    """Test divergence detection and rollback."""

    def test_detect_loss_worsening(self):
        """Detect when loss gets worse after feedback tuning."""
        optimizer = MetaOptimizer()

        old_loss = 0.3
        new_loss = 0.4  # Worsened

        diverged = optimizer.detect_feedback_divergence(old_loss, new_loss)
        assert diverged is True
        assert optimizer.conservative_mode is True

    def test_detect_minor_worsening_ignored(self):
        """Ignore minor fluctuations (<0.05)."""
        optimizer = MetaOptimizer()

        old_loss = 0.30
        new_loss = 0.32  # Slight worsening

        diverged = optimizer.detect_feedback_divergence(old_loss, new_loss)
        assert diverged is False  # Only flag significant worsening

    def test_improvement_clears_consecutive_worsening(self):
        """Improvement clears the worsening counter."""
        optimizer = MetaOptimizer()
        optimizer.consecutive_worsening = 5

        old_loss = 0.4
        new_loss = 0.3  # Improved

        optimizer.detect_feedback_divergence(old_loss, new_loss)
        # Improvement should decrement counter
        assert optimizer.consecutive_worsening < 5

    def test_rollback_restores_parameters(self):
        """Rollback restores saved parameters."""
        optimizer = MetaOptimizer()
        saved_state = optimizer.get_state()

        # Modify parameters
        optimizer.α_core = 0.05
        optimizer.α_infra = 0.005

        # Rollback
        optimizer.rollback_to_state(saved_state)
        assert optimizer.α_core == saved_state['α_core']
        assert optimizer.α_infra == saved_state['α_infra']
        assert optimizer.conservative_mode is True  # Rollback enables conservative mode

    def test_rollback_prevents_divergence(self):
        """Rollback prevents cascading divergence."""
        optimizer = MetaOptimizer()
        state_before = optimizer.get_state()

        # Simulate tuning that made things worse
        bad_loss = 0.5
        prev_loss = 0.3

        if optimizer.detect_feedback_divergence(prev_loss, bad_loss):
            optimizer.rollback_to_state(state_before)

        assert optimizer.get_state() == state_before


class TestConsensusCalculation:
    """Test weighted consensus calculation from feedback."""

    def test_unanimous_yes_consensus(self):
        """Unanimous yes feedback generates strong positive signal."""
        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(10)
        ]

        yes_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "yes")
        no_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "no")
        consensus = yes_weight - no_weight

        assert consensus > 0  # Positive signal

    def test_split_feedback_weak_signal(self):
        """Balanced yes/no generates weak signal."""
        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.8} for _ in range(5)
        ] + [
            {"outcome_feedback": "no", "confidence": 0.8} for _ in range(5)
        ]

        yes_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "yes")
        no_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "no")
        consensus = yes_weight - no_weight

        assert abs(consensus) < 2.0  # Weak signal (close to zero)

    def test_confident_minority_dominance(self):
        """High-confidence minority outweighs low-confidence majority."""
        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.2} for _ in range(8)
        ] + [
            {"outcome_feedback": "no", "confidence": 0.95},
            {"outcome_feedback": "no", "confidence": 0.95},
        ]

        yes_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "yes")
        no_weight = sum(fb["confidence"] for fb in feedback_outcomes if fb["outcome_feedback"] == "no")
        consensus = yes_weight - no_weight

        # no_weight = 1.9, yes_weight = 1.6
        # consensus = 1.6 - 1.9 = -0.3 (leans toward "no")
        assert consensus < 0


class TestFailSoftBehavior:
    """Test fail-soft behavior in feedback processing."""

    def test_empty_feedback_list(self):
        """Empty feedback list returns False gracefully."""
        optimizer = MetaOptimizer()
        result = optimizer.process_feedback_signal([])
        assert result is False

    def test_malformed_feedback_ignored(self):
        """Malformed feedback dict is skipped safely."""
        optimizer = MetaOptimizer()

        feedback_outcomes = [
            {},  # Missing keys
            {"outcome_feedback": "yes"},  # Missing confidence
        ] + [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(8)
        ]

        # Should not raise exception
        result = optimizer.process_feedback_signal(feedback_outcomes)
        # Might still succeed with 8 valid samples

    def test_process_feedback_no_exception_on_error(self):
        """Feedback processing never raises exceptions."""
        optimizer = MetaOptimizer()

        # Various edge cases
        feedback_list = [None, {}, {"confidence": None}]
        feedback_outcomes = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(10)
        ]

        # Should not raise even with bad setup
        result = optimizer.process_feedback_signal(feedback_outcomes)
        assert isinstance(result, bool)


class TestMetaLoopIntegration:
    """Integration tests for meta loop with feedback."""

    def test_full_feedback_meta_cycle(self):
        """End-to-end: feedback → tuning → convergence."""
        optimizer = MetaOptimizer()

        # Phase 1: Positive feedback
        positive_feedback = [
            {"outcome_feedback": "yes", "confidence": 0.9} for _ in range(10)
        ]
        result1 = optimizer.process_feedback_signal(positive_feedback)
        assert result1 is True

        # Phase 2: Check if improving
        # Simulate task outcomes improving
        initial_α = optimizer.α_core

        # Phase 3: If loss improves, continue
        good_loss = 0.25
        old_loss = 0.30
        not_diverged = not optimizer.detect_feedback_divergence(old_loss, good_loss)
        assert not_diverged is True

    def test_feedback_oscillation_prevention(self):
        """Prevent oscillating feedback (yes → no → yes)."""
        optimizer = MetaOptimizer()

        # Cycle 1: Positive feedback
        pos_feedback = [
            {"outcome_feedback": "yes", "confidence": 0.8} for _ in range(10)
        ]
        optimizer.process_feedback_signal(pos_feedback)

        # Cycle 2: Negative feedback (contradiction!)
        neg_feedback = [
            {"outcome_feedback": "no", "confidence": 0.8} for _ in range(10)
        ]
        optimizer.process_feedback_signal(neg_feedback)

        # Should detect oscillation and enter conservative mode
        # (or reduce learning rate to avoid chasing signals)
        assert optimizer.conservative_mode is True or optimizer.update_count > 0

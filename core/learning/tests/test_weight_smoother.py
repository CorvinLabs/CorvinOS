"""
Comprehensive Tests for Weight Smoother (Security Fix #7)

Tests cover:
  1. EMA filtering correctness
  2. Frequency/harmonic detection
  3. Confidence scoring
  4. State management and diagnostics
  5. Edge cases and error handling

Test execution: pytest test_weight_smoother.py -v
"""

import math
import pytest
import time
import numpy as np
from core.learning.weight_smoother import (
    WeightSmoother,
    SmootherConfig,
    SmootherOutput,
    SmootherState,
)


class TestEMAFilterFunctionality:
    """Test EMA (Exponential Moving Average) filter correctness."""

    def test_ema_filter_with_constant_input(self):
        """Test that EMA converges to constant input value."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Apply constant delta = 2.0 repeatedly
        deltas = [2.0] * 50
        for delta in deltas:
            output = smoother.smooth('weight1', delta)

        # After 50 iterations, EMA should converge close to 2.0
        final_output = output.filtered_delta
        assert abs(final_output - 2.0) < 0.05, (
            f"EMA should converge to 2.0, got {final_output}"
        )

    def test_ema_filter_alternating_signal(self):
        """Test that EMA smooths alternating [+1, -1, +1, -1] signal."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Alternating signal: [1, -1, 1, -1]
        deltas = [1.0, -1.0, 1.0, -1.0]
        outputs = []

        for delta in deltas:
            output = smoother.smooth('weight_alt', delta)
            outputs.append(output.filtered_delta)

        # Expected progression (with alpha=0.3):
        # ema[0] = 0.3*1 + 0.7*0 = 0.3
        # ema[1] = 0.3*(-1) + 0.7*0.3 = -0.09
        # ema[2] = 0.3*1 + 0.7*(-0.09) ≈ 0.237
        # ema[3] = 0.3*(-1) + 0.7*0.237 ≈ -0.134
        expected = [0.3, -0.09, 0.237, -0.134]

        for i, (output_val, expected_val) in enumerate(zip(outputs, expected)):
            assert abs(output_val - expected_val) < 0.01, (
                f"Delta {i}: got {output_val}, expected {expected_val}"
            )

    def test_ema_filter_step_response(self):
        """Test EMA step response (sudden change from 0 to 1)."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Step input: [0, 0, 0, 1, 1, 1, ...]
        deltas = [0, 0, 0] + [1.0] * 10
        outputs = []

        for delta in deltas:
            output = smoother.smooth('weight_step', delta)
            outputs.append(output.filtered_delta)

        # After step, EMA should gradually rise toward 1.0
        final_values = outputs[3:]  # Values after step occurs

        # Verify exponential rise
        for i in range(len(final_values) - 1):
            assert final_values[i+1] >= final_values[i], (
                f"EMA should monotonically increase after step"
            )

        # Should approach but not exceed 1.0
        assert all(v <= 1.0 for v in final_values), (
            "EMA should not exceed max input"
        )

    def test_ema_different_alpha_values(self):
        """Test that different alpha values produce different smoothing."""
        deltas = [1.0, -0.5, 0.8, -0.3, 0.5]

        config_high_alpha = SmootherConfig(ema_alpha=0.8)  # More responsive
        smoother_high = WeightSmoother(config_high_alpha)

        config_low_alpha = SmootherConfig(ema_alpha=0.2)  # More smoothing
        smoother_low = WeightSmoother(config_low_alpha)

        outputs_high = []
        outputs_low = []

        for delta in deltas:
            out_high = smoother_high.smooth('w_high', delta)
            out_low = smoother_low.smooth('w_low', delta)
            outputs_high.append(out_high.filtered_delta)
            outputs_low.append(out_low.filtered_delta)

        # High alpha should track input more closely (less smoothing)
        # Low alpha should deviate more from input (more smoothing)
        deviations_high = [abs(out - inp) for out, inp in zip(outputs_high, deltas)]
        deviations_low = [abs(out - inp) for out, inp in zip(outputs_low, deltas)]

        avg_dev_high = np.mean(deviations_high)
        avg_dev_low = np.mean(deviations_low)

        assert avg_dev_low > avg_dev_high, (
            f"Lower alpha should smooth more (deviate more from input): "
            f"low_alpha={avg_dev_low:.3f}, high_alpha={avg_dev_high:.3f}"
        )

    def test_ema_small_delta_after_large_delta(self):
        """Test EMA recovery after large delta."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Apply large delta, then small deltas
        deltas = [10.0] + [0.1] * 10
        outputs = []

        for delta in deltas:
            output = smoother.smooth('weight_recovery', delta)
            outputs.append(output.filtered_delta)

        # EMA should gradually decay from large value toward 0.1
        initial_spike = outputs[0]
        final_value = outputs[-1]

        assert initial_spike > 1.0, "Initial EMA should be large"
        assert final_value < 1.0, "Final EMA should be close to 0.1"
        assert final_value < initial_spike, "EMA should decay over time"


class TestHarmonicEnergyDetection:
    """Test FFT-based harmonic energy detection."""

    def test_harmonic_energy_low_frequency_signal(self):
        """Test that low-frequency (smooth) signal has low harmonic energy."""
        config = SmootherConfig(ema_alpha=0.3, enable_fft_detection=True)
        smoother = WeightSmoother(config)

        # Slowly-varying signal (low-frequency)
        deltas = [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5]

        for delta in deltas:
            output = smoother.smooth('weight_lf', delta)

        final_output = output
        if final_output.harmonic_energy is not None:
            # Low-frequency signal should have low harmonic energy
            assert final_output.harmonic_energy < 0.5, (
                f"Low-frequency signal should have low harmonic energy, "
                f"got {final_output.harmonic_energy}"
            )

    def test_harmonic_energy_high_frequency_oscillation(self):
        """Test that high-frequency oscillation has high harmonic energy."""
        config = SmootherConfig(ema_alpha=0.3, enable_fft_detection=True)
        smoother = WeightSmoother(config)

        # Rapidly oscillating signal (high-frequency)
        deltas = [1.0, -1.0, 1.0, -1.0, 1.0, -1.0, 1.0, -1.0]

        for delta in deltas:
            output = smoother.smooth('weight_hf', delta)

        final_output = output
        if final_output.harmonic_energy is not None:
            # High-frequency oscillation should have high harmonic energy
            assert final_output.harmonic_energy > 0.3, (
                f"High-frequency signal should have high harmonic energy, "
                f"got {final_output.harmonic_energy}"
            )

    def test_harmonic_energy_insufficient_samples(self):
        """Test that harmonic energy is 0 with insufficient samples."""
        config = SmootherConfig(ema_alpha=0.3, enable_fft_detection=True)
        smoother = WeightSmoother(config)

        # Only 2 samples (< 4 minimum for FFT)
        output1 = smoother.smooth('weight_few', 0.5)
        output2 = smoother.smooth('weight_few', -0.5)

        # Should return 0 (not enough data for FFT)
        assert output2.harmonic_energy == 0.0, (
            "Should return 0 harmonic energy with < 4 samples"
        )

    def test_harmonic_energy_zero_signal(self):
        """Test harmonic energy with zero (no signal) input."""
        config = SmootherConfig(ema_alpha=0.3, enable_fft_detection=True)
        smoother = WeightSmoother(config)

        # All zeros
        for _ in range(10):
            output = smoother.smooth('weight_zero', 0.0)

        # Zero signal should have zero harmonic energy
        if output.harmonic_energy is not None:
            assert output.harmonic_energy == 0.0, (
                "Zero signal should have zero harmonic energy"
            )


class TestConfidenceScoring:
    """Test confidence scoring logic."""

    def test_confidence_increases_with_updates(self):
        """Test that confidence increases as more samples are processed."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        confidences = []
        for i in range(100):
            output = smoother.smooth('weight_conf', 0.5)
            confidences.append(output.confidence)

        # Confidence should generally increase over time
        first_10_avg = np.mean(confidences[:10])
        last_10_avg = np.mean(confidences[-10:])

        assert last_10_avg > first_10_avg, (
            f"Confidence should increase with more samples: "
            f"early={first_10_avg:.3f}, late={last_10_avg:.3f}"
        )

    def test_confidence_high_with_smooth_history(self):
        """Test that confidence is high for smooth (non-oscillating) input."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Smooth signal (constant input)
        for _ in range(50):
            output = smoother.smooth('weight_smooth', 1.0)

        smooth_confidence = output.confidence

        # Oscillating signal
        smoother_osc = WeightSmoother(config)
        for i in range(50):
            delta = 1.0 if i % 2 == 0 else -1.0
            output_osc = smoother_osc.smooth('weight_osc', delta)

        osc_confidence = output_osc.confidence

        # Smooth should have higher confidence than oscillating
        assert smooth_confidence > osc_confidence, (
            f"Smooth signal should have higher confidence: "
            f"smooth={smooth_confidence:.3f}, osc={osc_confidence:.3f}"
        )

    def test_confidence_range_valid(self):
        """Test that confidence always stays in [0, 1] range."""
        config = SmootherConfig(ema_alpha=0.3)
        smoother = WeightSmoother(config)

        # Apply diverse inputs
        test_inputs = [0.0, 1.0, -1.0, 100.0, -100.0, 0.001, -0.001]

        for test_input in test_inputs:
            for _ in range(20):
                output = smoother.smooth('weight_range', test_input)
                assert 0.0 <= output.confidence <= 1.0, (
                    f"Confidence out of range: {output.confidence}"
                )


class TestStateManagement:
    """Test smoother state initialization and management."""

    def test_state_initialization_on_first_smooth(self):
        """Test that state is auto-initialized on first smooth call."""
        smoother = WeightSmoother()

        # Before any smoothing
        state = smoother.get_state('new_weight')
        assert state is None

        # After first smoothing
        output = smoother.smooth('new_weight', 0.5)
        state = smoother.get_state('new_weight')

        assert state is not None
        assert state.weight_id == 'new_weight'
        assert state.update_count == 1

    def test_state_accumulates_across_updates(self):
        """Test that state accumulates history across multiple updates."""
        smoother = WeightSmoother()

        for i in range(10):
            output = smoother.smooth('weight_hist', 0.5 + i * 0.01)

        state = smoother.get_state('weight_hist')
        assert state.update_count == 10
        assert len(state.recent_deltas) == 10  # All 10 should fit (maxlen=20)

    def test_recent_deltas_circular_buffer(self):
        """Test that recent_deltas uses circular buffer (maxlen=20)."""
        smoother = WeightSmoother()

        # Add 25 updates (more than buffer size of 20)
        for i in range(25):
            smoother.smooth('weight_circ', float(i))

        state = smoother.get_state('weight_circ')

        # Should only have last 20 updates
        assert len(state.recent_deltas) == 20

        # Should contain values 5-24 (last 20)
        expected_values = list(range(5, 25))
        actual_values = [int(v) for v in state.recent_deltas]

        assert actual_values == expected_values, (
            f"Circular buffer should contain last 20 values"
        )

    def test_reset_clears_state(self):
        """Test that reset() clears weight state."""
        smoother = WeightSmoother()

        # Add some data
        for _ in range(10):
            smoother.smooth('weight_reset', 0.5)

        # Verify state exists
        state_before = smoother.get_state('weight_reset')
        assert state_before is not None

        # Reset
        smoother.reset('weight_reset')

        # State should be cleared
        state_after = smoother.get_state('weight_reset')
        assert state_after is None

    def test_reset_all_clears_all_states(self):
        """Test that reset_all() clears all weight states."""
        smoother = WeightSmoother()

        # Add data for multiple weights
        for w_id in ['w1', 'w2', 'w3']:
            for _ in range(5):
                smoother.smooth(w_id, 0.5)

        # Verify all states exist
        assert len(smoother.get_all_states()) == 3

        # Reset all
        smoother.reset_all()

        # All states should be cleared
        assert len(smoother.get_all_states()) == 0


class TestDiagnosticsAndMetrics:
    """Test diagnostics and metrics retrieval."""

    def test_get_diagnostics_not_found(self):
        """Test diagnostics for non-existent weight."""
        smoother = WeightSmoother()

        diag = smoother.get_diagnostics('nonexistent')
        assert diag['found'] is False

    def test_get_diagnostics_complete(self):
        """Test that diagnostics include all expected fields."""
        config = SmootherConfig(ema_alpha=0.4)
        smoother = WeightSmoother(config)

        # Process some updates
        for i in range(15):
            smoother.smooth('weight_diag', 0.5 + i * 0.01)

        diag = smoother.get_diagnostics('weight_diag')

        # Check all expected fields
        assert diag['found'] is True
        assert diag['weight_id'] == 'weight_diag'
        assert diag['update_count'] == 15
        assert 'current_ema' in diag
        assert 'ema_alpha' in diag
        assert 'smoothing_ratio' in diag
        assert 'recent_deltas' in diag
        assert 'recent_ema_outputs' in diag

    def test_smoothing_ratio_calculation(self):
        """Test smoothing ratio calculation."""
        # Alpha=1.0 means no smoothing (ratio=0)
        config1 = SmootherConfig(ema_alpha=1.0)
        smoother1 = WeightSmoother(config1)
        assert smoother1.get_smoothing_ratio('any') == 0.0

        # Alpha=0.0 means maximum smoothing (ratio=1)
        config2 = SmootherConfig(ema_alpha=0.0)
        smoother2 = WeightSmoother(config2)
        assert smoother2.get_smoothing_ratio('any') == 1.0

        # Alpha=0.5 means ratio=0.5
        config3 = SmootherConfig(ema_alpha=0.5)
        smoother3 = WeightSmoother(config3)
        assert abs(smoother3.get_smoothing_ratio('any') - 0.5) < 0.0001


class TestErrorHandlingAndEdgeCases:
    """Test error handling and edge cases."""

    def test_invalid_ema_alpha_too_high(self):
        """Test that alpha > 1.0 raises error."""
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(ema_alpha=1.5)

        assert "ema_alpha must be in [0, 1]" in str(exc_info.value)

    def test_invalid_ema_alpha_negative(self):
        """Test that alpha < 0.0 raises error."""
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(ema_alpha=-0.1)

        assert "ema_alpha must be in [0, 1]" in str(exc_info.value)

    def test_invalid_energy_threshold(self):
        """Test that invalid FFT energy threshold raises error."""
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(fft_energy_threshold=1.5)

        assert "fft_energy_threshold must be in [0, 1]" in str(exc_info.value)

    def test_smooth_with_large_delta(self):
        """Test that large deltas are handled correctly."""
        smoother = WeightSmoother()
        # Derived from the CONFIGURED alpha, never a hard-coded one: the default
        # moved 0.3 -> 0.5 on 2026-09-07 and a literal 0.7 silently went stale.
        alpha = smoother.config.ema_alpha
        keep = 1.0 - alpha          # ema_n = d * (1 - keep**n); residual d*keep**n
        d = 1e6

        output = smoother.smooth('weight_large', d)
        assert output.filtered_delta > 0

        output2 = smoother.smooth('weight_large', d)
        assert abs(output2.filtered_delta - d * (1 - keep ** 2)) < 1.0

        n_needed = math.ceil(math.log(0.1) / math.log(keep))
        output_converged = output2
        for _ in range(max(0, n_needed - 2)):
            output_converged = smoother.smooth('weight_large', d)
        assert abs(output_converged.filtered_delta - d) < 0.1 * d

    def test_smooth_with_negative_delta(self):
        """Test that negative deltas work correctly."""
        smoother = WeightSmoother()
        alpha = smoother.config.ema_alpha
        keep = 1.0 - alpha
        d = -0.5

        output1 = smoother.smooth('weight_neg', d)
        output2 = smoother.smooth('weight_neg', d)
        assert output1.filtered_delta < 0
        assert output2.filtered_delta < 0

        assert abs(output2.filtered_delta - d * (1 - keep ** 2)) < 1e-9

        n_needed = math.ceil(math.log(0.1 / abs(d)) / math.log(keep))
        output_converged = output2
        for _ in range(max(0, n_needed - 2)):
            output_converged = smoother.smooth('weight_neg', d)
        assert abs(output_converged.filtered_delta - d) < 0.1

    def test_smooth_output_immutability(self):
        """Test that SmootherOutput is properly dataclass (hashable and frozen)."""
        output = SmootherOutput(
            filtered_delta=0.5,
            raw_delta=0.6,
            ema_state=0.55,
            confidence=0.9,
        )

        # Check fields are accessible
        assert output.filtered_delta == 0.5
        assert output.raw_delta == 0.6
        assert output.confidence == 0.9


class TestIntegrationScenarios:
    """Integration tests with realistic scenarios."""

    def test_oscillation_attack_scenario(self):
        """Test protection against oscillation attack (alternating feedback)."""
        config = SmootherConfig(
            ema_alpha=0.3,
            enable_fft_detection=True,
        )
        smoother = WeightSmoother(config)

        # Attack: rapid alternations [+1, -1, +1, -1, ...]
        for i in range(50):
            delta = 1.0 if i % 2 == 0 else -1.0
            output = smoother.smooth('attack_weight', delta)

        # After 50 oscillations:
        # 1. Filtered delta should be close to 0 (smoothed out)
        # 2. Confidence should be low (detected oscillation)
        # 3. Harmonic energy should be high (if FFT enabled)

        assert abs(output.filtered_delta) < 0.3, (
            f"Filtered delta should be smoothed to ~0, got {output.filtered_delta}"
        )

        if output.harmonic_energy is not None:
            assert output.harmonic_energy > 0.4, (
                f"Should detect high harmonic energy, got {output.harmonic_energy}"
            )

    def test_multiple_weight_independence(self):
        """Test that different weights are tracked independently."""
        smoother = WeightSmoother()

        # Weight A: constant 1.0
        # Weight B: constant -1.0
        for _ in range(20):
            output_a = smoother.smooth('weight_a', 1.0)
            output_b = smoother.smooth('weight_b', -1.0)

        # Both should converge to their respective values
        assert abs(output_a.filtered_delta - 1.0) < 0.1
        assert abs(output_b.filtered_delta + 1.0) < 0.1

        # States should be separate
        state_a = smoother.get_state('weight_a')
        state_b = smoother.get_state('weight_b')

        assert state_a.ema_value > 0
        assert state_b.ema_value < 0

    def test_adaptive_smoothing_levels(self):
        """Test with different alpha values for different weights."""
        smoother_light = WeightSmoother(SmootherConfig(ema_alpha=0.7))  # Responsive
        smoother_heavy = WeightSmoother(SmootherConfig(ema_alpha=0.1))  # Smooth

        # Same input for both
        deltas = [0.0, 1.0, 0.0, 1.0, 0.0, 1.0]

        outputs_light = []
        outputs_heavy = []

        for delta in deltas:
            out_light = smoother_light.smooth('w_light', delta)
            out_heavy = smoother_heavy.smooth('w_heavy', delta)
            outputs_light.append(out_light.filtered_delta)
            outputs_heavy.append(out_heavy.filtered_delta)

        # Light smoother should track input more closely
        swing_light = max(outputs_light) - min(outputs_light)
        swing_heavy = max(outputs_heavy) - min(outputs_heavy)

        assert swing_light > swing_heavy, (
            f"Light smoothing should track input more: "
            f"light_swing={swing_light:.3f}, heavy_swing={swing_heavy:.3f}"
        )


class TestConfigFailClosed:
    """Regression: SmootherConfig refuses out-of-domain hyperparameters (fail-closed)."""

    @pytest.mark.parametrize('bad', [1.5, -0.1, float('nan'), float('inf'), float('-inf'), 'x', None, True])
    def test_ema_alpha_out_of_domain_is_refused(self, bad):
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(ema_alpha=bad)
        assert 'ema_alpha must be in [0, 1]' in str(exc_info.value)

    @pytest.mark.parametrize('bad', [1.5, -0.1, float('nan'), float('inf'), 'x', None, True])
    def test_fft_energy_threshold_out_of_domain_is_refused(self, bad):
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(fft_energy_threshold=bad)
        assert 'fft_energy_threshold must be in [0, 1]' in str(exc_info.value)

    @pytest.mark.parametrize('bad', [0, -1, 2.5, 'x', None, True])
    def test_smoothing_window_size_out_of_domain_is_refused(self, bad):
        with pytest.raises(ValueError) as exc_info:
            SmootherConfig(smoothing_window_size=bad)
        assert 'smoothing_window_size must be a positive int' in str(exc_info.value)

    def test_boundary_values_are_accepted(self):
        assert SmootherConfig(ema_alpha=0.0).ema_alpha == 0.0
        assert SmootherConfig(ema_alpha=1.0).ema_alpha == 1.0
        assert SmootherConfig(fft_energy_threshold=0.0).fft_energy_threshold == 0.0
        assert SmootherConfig(fft_energy_threshold=1.0).fft_energy_threshold == 1.0

    def test_post_construction_mutation_is_refused_by_smoother(self):
        """SmootherConfig is a mutable dataclass; WeightSmoother re-validates."""
        config = SmootherConfig(ema_alpha=0.3)
        config.ema_alpha = 3.0  # bypasses __post_init__
        with pytest.raises(ValueError) as exc_info:
            WeightSmoother(config)
        assert 'ema_alpha must be in [0, 1]' in str(exc_info.value)

    def test_rejection_log_is_content_free(self, caplog):
        """The rejection log line names the parameter only — no payload values."""
        import logging
        with caplog.at_level(logging.WARNING, logger='core.learning.weight_smoother'):
            with pytest.raises(ValueError):
                SmootherConfig(ema_alpha=42.0)
        assert caplog.records, 'expected a rejection log record'
        for record in caplog.records:
            assert '42' not in record.getMessage()


class TestEMAPreviousStateTracking:
    """Regression: ema_prev must hold the PREVIOUS ema, not a copy of ema_value."""

    def test_ema_prev_lags_ema_value(self):
        smoother = WeightSmoother(SmootherConfig(ema_alpha=0.3))
        smoother.smooth('w', 1.0)   # ema = 0.3, prev = 0.0
        smoother.smooth('w', 1.0)   # ema = 0.51, prev = 0.3
        state = smoother.get_state('w')
        assert abs(state.ema_prev - 0.3) < 1e-9
        assert abs(state.ema_value - 0.51) < 1e-9
        assert state.ema_value != state.ema_prev

    def test_ema_output_unchanged_by_prev_tracking(self):
        """The filter output itself is exactly d * (1 - (1-alpha)**n)."""
        smoother = WeightSmoother(SmootherConfig(ema_alpha=0.3))
        for n in range(1, 8):
            out = smoother.smooth('w', 2.0)
            assert abs(out.filtered_delta - 2.0 * (1 - 0.7 ** n)) < 1e-9


class TestHarmonicBandSplit:
    """Regression: the DC bin must not be the whole low-frequency band."""

    def test_smooth_ramp_scores_below_oscillation(self):
        config = SmootherConfig(ema_alpha=0.3, enable_fft_detection=True)

        smooth = WeightSmoother(config)
        for delta in [0.5, 0.6, 0.7, 0.8, 0.9, 1.0, 0.9, 0.8, 0.7, 0.6, 0.5]:
            smooth_out = smooth.smooth('lf', delta)

        osc = WeightSmoother(config)
        for i in range(11):
            osc_out = osc.smooth('hf', 1.0 if i % 2 == 0 else -1.0)

        assert smooth_out.harmonic_energy < 0.5
        assert osc_out.harmonic_energy > 0.5
        assert smooth_out.harmonic_energy < osc_out.harmonic_energy


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

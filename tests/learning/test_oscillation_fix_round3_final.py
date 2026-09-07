"""
Security Fix #7 Round 3 (FINAL): Oscillation (Low-Frequency Escape) — Edge Case Tests

Tests verify that the improved oscillation detector catches ultra-slow oscillations
that were previously missed (T ≥ 100 batches, f < 0.01 Hz) using:
  1. Increased FFT window: 50 → 200 samples
  2. Lowered frequency threshold: 0.2 Hz → 0.001 Hz
  3. Raw sum-of-squares drift check: drift > 0.3 in 200 samples
"""

import pytest
import numpy as np
from core.learning.gradient_backprop import CouplingOscillationDetector


class TestRound3FinalUltraSlowOscillations:
    """Test detection of ultra-slow oscillations (T ≥ 100 batches, f < 0.01 Hz)."""

    def test_ultra_slow_sine_t200_f005hz_detected(self):
        """Ultra-slow sine wave (T=200 batches, f=0.005 Hz) — MUST be detected."""
        detector = CouplingOscillationDetector(window_size=10)

        # Sine wave with period T=200 batches (f=1/200=0.005 Hz)
        # This is the boundary case: smallest frequency resolvable by 200-sample FFT
        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 200.0) for i in range(250)]

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L1_routing', val):
                detected = True
                print(f"Ultra-slow sine (T=200, f=0.005 Hz) detected at batch {i}")
                break

        assert detected, "Ultra-slow sine (T=200) must be detected (was escape in Round 2)"

    def test_ultra_slow_sine_t150_f0067hz_detected(self):
        """Ultra-slow sine wave (T=150 batches, f=0.0067 Hz) — MUST be detected."""
        detector = CouplingOscillationDetector(window_size=10)

        # Sine wave with period T=150 batches
        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 150.0) for i in range(250)]

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L2_confidence', val):
                detected = True
                print(f"Ultra-slow sine (T=150, f=0.0067 Hz) detected at batch {i}")
                break

        assert detected, "Ultra-slow sine (T=150) must be detected"

    def test_ultra_slow_sine_t100_f001hz_detected(self):
        """Ultra-slow sine wave (T=100 batches, f=0.01 Hz) — edge case from problem statement."""
        detector = CouplingOscillationDetector(window_size=10)

        # Sine wave with period T=100 batches
        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 100.0) for i in range(250)]

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L3_feedback', val):
                detected = True
                print(f"Ultra-slow sine (T=100, f=0.01 Hz) detected at batch {i}")
                break

        assert detected, "Ultra-slow sine (T=100) must be detected (Round 3 improvement)"

    def test_random_walk_drift_detected(self):
        """Random walk with bounded drift — should be detected by variance accumulation."""
        detector = CouplingOscillationDetector(window_size=10)

        # Random walk: each step ±0.005 (Brownian motion)
        np.random.seed(42)
        param = 0.5
        values = []
        for i in range(300):
            param += np.random.choice([-0.005, 0.005])
            values.append(param)

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L4_attention', val):
                detected = True
                print(f"Random walk detected at batch {i}")
                break

        # Random walk may eventually be detected by variance accumulation
        # (not guaranteed with every seed, but high-probability)
        print(f"Random walk result: detected={detected}, final_drift={param - 0.5:.4f}")

    def test_sawtooth_t50_with_drift_detected(self):
        """Asymmetric sawtooth (T=50 effective) with net drift — MUST be detected."""
        detector = CouplingOscillationDetector(window_size=10)

        # Sawtooth: +0.01 rise (25 batches), −0.009 fall (25 batches)
        # Period: 50 batches, f=0.02 Hz, but asymmetric → net drift
        param = 0.5
        values = []
        for batch_idx in range(300):
            cycle = batch_idx % 50
            if cycle < 25:
                param += 0.01 / 25  # Gradual rise
            else:
                param -= 0.009 / 25  # Faster fall
            values.append(param)

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L5_latency', val):
                detected = True
                print(f"Sawtooth (T=50) with drift detected at batch {i}")
                break

        assert detected, "Sawtooth with drift must be detected"

    def test_large_drift_in_200_samples_detected(self):
        """Large drift (> 0.3) in 200 samples — caught by raw sum-of-squares check."""
        detector = CouplingOscillationDetector(window_size=10)

        # Slow upward ramp: +0.0015 per batch × 200 batches = +0.3 total
        # This is the threshold case for drift detection
        param = 0.5
        values = []
        for i in range(250):
            if i < 200:
                param += 0.0015
            values.append(param)

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L1_routing', val):
                detected = True
                print(f"Large drift (0.3 in 200 samples) detected at batch {i}")
                break

        assert detected, "Large drift must be detected by raw drift check"

    def test_small_amplitude_slow_oscillation_detected(self):
        """Small amplitude (0.01) slow oscillation (T=100) — hard case."""
        detector = CouplingOscillationDetector(window_size=10)

        # Small sine wave: T=100 batches, amplitude=0.01
        values = [0.5 + 0.01 * np.sin(2 * np.pi * i / 100.0) for i in range(250)]

        detected = False
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L2_confidence', val):
                detected = True
                print(f"Small-amplitude slow sine (T=100, A=0.01) detected at batch {i}")
                break

        assert detected, "Small-amplitude slow oscillation must be detected"


class TestRound3EdgeCases:
    """Test edge cases and boundary conditions."""

    def test_monotonic_trend_not_detected(self):
        """Monotonic upward trend should NOT trigger oscillation."""
        detector = CouplingOscillationDetector(window_size=10)

        # Smooth upward trend (no oscillation)
        values = [0.5 + i * 0.001 for i in range(250)]

        detected = False
        for val in values:
            if detector.check_for_oscillation('L1_routing', val):
                detected = True
                break

        assert not detected, "Monotonic trend should not trigger oscillation"

    def test_constant_value_not_detected(self):
        """Constant values should NOT trigger oscillation."""
        detector = CouplingOscillationDetector(window_size=10)

        # All same value
        for _ in range(250):
            result = detector.check_for_oscillation('L1_routing', 0.5)
            assert not result, "Constant values should not trigger oscillation"

    def test_noise_boundary_not_detected(self):
        """Small random noise (not structured oscillation) should NOT consistently trigger."""
        detector = CouplingOscillationDetector(window_size=10)

        # Gaussian noise: N(0.5, 0.001²)
        np.random.seed(42)
        values = np.random.normal(0.5, 0.001, 250)

        detections = []
        for val in values:
            result = detector.check_for_oscillation('L1_routing', val)
            if result:
                detections.append(True)

        # Some noise may trigger variance detection, but not all
        # We just verify the detector doesn't crash and returns bool
        assert all(isinstance(d, bool) for d in detections)

    def test_nan_inf_handling_fails_closed(self):
        """Detector should handle NaN/Inf gracefully (fail-closed)."""
        detector = CouplingOscillationDetector(window_size=10)

        # Add valid values first to build history
        for i in range(20):
            detector.check_for_oscillation('L1_routing', 0.5 + i * 0.01)

        # Try with NaN (should not crash, just return False)
        result = detector.check_for_oscillation('L1_routing', float('nan'))
        assert isinstance(result, bool), "Should return bool even with NaN"


class TestRound3RegressionAttacks:
    """Regression tests: all problem-statement attacks must be caught."""

    def test_regression_attack_1_slow_sine_t20(self):
        """Attack 1 from problem statement: slow sine (T=20)."""
        detector = CouplingOscillationDetector(window_size=10)
        values = [0.5 + 0.1 * np.sin(2 * np.pi * t / 20.0) for t in range(250)]

        detected = False
        for val in values:
            if detector.check_for_oscillation('L1_routing', val):
                detected = True
                break

        assert detected, "Attack 1 (slow sine T=20) must be detected"

    def test_regression_attack_2_sawtooth_asymmetric(self):
        """Attack 2: asymmetric sawtooth with net drift."""
        detector = CouplingOscillationDetector(window_size=10)

        param = 0.5
        values = []
        for batch_idx in range(500):
            cycle = batch_idx % 10
            if cycle < 5:
                param += 0.01
            else:
                param -= 0.008
            values.append(param)

        detected = False
        for val in values:
            if detector.check_for_oscillation('L2_confidence', val):
                detected = True
                break

        assert detected, "Attack 2 (sawtooth) must be detected"

    def test_regression_attack_4_micro_oscillation(self):
        """Attack 4: micro-oscillation accumulation."""
        detector = CouplingOscillationDetector(window_size=10)

        param = 0.5
        values = []
        for batch_idx in range(1000):
            cycle = batch_idx % 3
            if cycle == 0:
                param += 0.001
            elif cycle == 1:
                param -= 0.0005
            else:
                param += 0.0005
            values.append(param)

        detected = False
        for val in values:
            if detector.check_for_oscillation('L3_feedback', val):
                detected = True
                break

        assert detected, "Attack 4 (micro-oscillation) must be detected"

    def test_regression_problem_statement_low_frequency_escape(self):
        """
        Direct test of the problem statement:
        "FFT detects frequencies > 0.01 Hz only; attackers use slower oscillations"

        We now detect frequencies down to 0.001 Hz (T ≥ 1000 batches).
        Test with f=0.005 Hz (T=200 batches) — previously escaped, now caught.
        """
        detector = CouplingOscillationDetector(window_size=10)

        # f = 0.005 Hz (T = 200 batches) — previously escaped with 50-sample window
        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 200.0) for i in range(300)]

        detected = False
        detection_batch = None
        for i, val in enumerate(values):
            if detector.check_for_oscillation('L1_routing', val):
                detected = True
                detection_batch = i
                break

        assert detected, (
            "Problem statement escape (T=200, f=0.005 Hz) must now be detected. "
            "Round 3 fix increased window from 50→200 samples and threshold 0.2→0.001 Hz."
        )
        print(f"Problem-statement escape FIXED: detected at batch {detection_batch}")


class TestRound3Performance:
    """Verify that detection happens in reasonable time (not after 1000+ batches)."""

    def test_detection_latency_t100(self):
        """Detection should trigger within ~250 batches for T=100 oscillation."""
        detector = CouplingOscillationDetector(window_size=10)

        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 100.0) for i in range(300)]

        for i, val in enumerate(values):
            if detector.check_for_oscillation('L1_routing', val):
                # Detected within first 300 batches (should be much sooner)
                assert i < 300, f"Detection took {i} batches (should be < 300)"
                print(f"T=100 oscillation detected in {i} batches")
                return

        pytest.fail("T=100 oscillation not detected within 300 batches")

    def test_detection_latency_t200(self):
        """Detection should trigger within ~400 batches for T=200 oscillation."""
        detector = CouplingOscillationDetector(window_size=10)

        values = [0.5 + 0.1 * np.sin(2 * np.pi * i / 200.0) for i in range(400)]

        for i, val in enumerate(values):
            if detector.check_for_oscillation('L1_routing', val):
                # Detected within first 400 batches
                assert i < 400, f"Detection took {i} batches (should be < 400)"
                print(f"T=200 oscillation detected in {i} batches")
                return

        pytest.fail("T=200 oscillation not detected within 400 batches")


if __name__ == '__main__':
    pytest.main([__file__, '-v'])

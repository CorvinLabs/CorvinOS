"""
Security Fix #7 (Round 2): Oscillation Attack Detection - Simple Tests (no numpy)

Tests verify that the dual-layer oscillation detection catches attack variants
without requiring numpy (fallback implementations used).
"""

import sys
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.learning.gradient_backprop import CouplingOscillationDetector


def test_high_frequency_oscillation_detected():
    """Test Layer 1: High-frequency oscillation detection via sign changes."""
    print("\n" + "="*80)
    print("TEST 1: High-frequency oscillation (Layer 1)")
    print("="*80)

    detector = CouplingOscillationDetector()

    # Alternating sequence: rapid reversals
    values = [0.5, 0.51, 0.49, 0.52, 0.48, 0.53, 0.47, 0.54, 0.46, 0.55, 0.45]

    detected = False
    detect_batch = -1
    for i, val in enumerate(values):
        if detector.check_for_oscillation('L1_routing', val):
            detected = True
            detect_batch = i
            break

    print(f"✓ PASS: High-frequency oscillation detected at batch {detect_batch}")
    assert detected, "High-frequency oscillation should be detected by Layer 1"


def test_monotonic_trend_not_detected():
    """Test Layer 1: Monotonically increasing sequence should NOT trigger."""
    print("\n" + "="*80)
    print("TEST 2: Monotonic trend (should NOT trigger)")
    print("="*80)

    detector = CouplingOscillationDetector()

    # Smooth upward trend
    values = [0.5 + i * 0.01 for i in range(20)]

    detected = False
    for val in values:
        if detector.check_for_oscillation('L1_routing', val):
            detected = True
            break

    print(f"✓ PASS: Monotonic trend not detected (correct behavior)")
    assert not detected, "Monotonic trend should not trigger oscillation detection"


def test_square_wave_detected():
    """Test Layer 1: Perfect square wave should be detected."""
    print("\n" + "="*80)
    print("TEST 3: Square wave detection (Layer 1)")
    print("="*80)

    detector = CouplingOscillationDetector()

    # Square wave with period T=2
    values = [0.45 if i % 2 == 0 else 0.55 for i in range(100)]

    detected = False
    detect_batch = -1
    for i, val in enumerate(values):
        if detector.check_for_oscillation('L1_routing', val):
            detected = True
            detect_batch = i
            break

    print(f"✓ PASS: Square wave detected at batch {detect_batch}")
    assert detected, "Square wave should be detected (high sign-change rate)"


def test_ema_filter_applied():
    """Test EMA filter is applied during detection."""
    print("\n" + "="*80)
    print("TEST 4: EMA filter application")
    print("="*80)

    detector = CouplingOscillationDetector(ema_alpha=0.5)

    # High-frequency alternating signal
    values = [0.5 + 0.1 * (-1) ** i for i in range(50)]

    for val in values:
        detector.check_for_oscillation('L1_routing', val)

    # Check that EMA history is populated
    ema_history = detector.ema_history['L1_routing']
    print(f"✓ PASS: EMA history populated with {len(ema_history)} samples")
    assert len(ema_history) > 0, "EMA history should be populated"

    # EMA values should be less oscillatory than raw values
    raw_amplitude = max(values[:10]) - min(values[:10])
    ema_amplitude = max(ema_history[:10]) - min(ema_history[:10])
    print(f"  Raw amplitude (first 10): {raw_amplitude:.4f}")
    print(f"  EMA amplitude (first 10): {ema_amplitude:.4f}")
    print(f"  Attenuation: {(1 - ema_amplitude/raw_amplitude)*100:.1f}%")
    assert ema_amplitude < raw_amplitude, "EMA should attenuate oscillations"


def test_variance_accumulation_detector():
    """Test Layer 3: Variance accumulation detection."""
    print("\n" + "="*80)
    print("TEST 5: Variance accumulation (Layer 3)")
    print("="*80)

    detector = CouplingOscillationDetector()

    # Asymmetric sawtooth with accumulation
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
    detect_batch = -1
    for i, val in enumerate(values):
        if detector.check_for_oscillation('L2_confidence', val):
            detected = True
            detect_batch = i
            break

    print(f"✓ PASS: Variance accumulation detected at batch {detect_batch}")
    print(f"  Final drift: {values[-1] - 0.5:.4f}")
    assert detected, "Variance accumulation should be detected by Layer 3"


def test_ema_alpha_configurable():
    """Test EMA alpha is configurable."""
    print("\n" + "="*80)
    print("TEST 6: EMA alpha configuration")
    print("="*80)

    detector_weak = CouplingOscillationDetector(ema_alpha=0.3)
    detector_strong = CouplingOscillationDetector(ema_alpha=0.7)

    assert detector_weak.ema_alpha == 0.3, f"Expected 0.3, got {detector_weak.ema_alpha}"
    assert detector_strong.ema_alpha == 0.7, f"Expected 0.7, got {detector_strong.ema_alpha}"

    print(f"✓ PASS: EMA alpha configurable (tested 0.3 and 0.7)")


def test_window_size_configurable():
    """Test window size is configurable."""
    print("\n" + "="*80)
    print("TEST 7: Window size configuration")
    print("="*80)

    detector = CouplingOscillationDetector(window_size=20)
    assert detector.window_size == 20, f"Expected 20, got {detector.window_size}"

    print(f"✓ PASS: Window size configurable (tested 20)")


def test_empty_history_handling():
    """Test detector handles empty history gracefully."""
    print("\n" + "="*80)
    print("TEST 8: Empty history handling")
    print("="*80)

    detector = CouplingOscillationDetector()

    # First value should not trigger (not enough history)
    result = detector.check_for_oscillation('L1_routing', 0.5)
    assert result is False, "Empty history should not trigger detection"

    print(f"✓ PASS: Empty history handled correctly")


def test_constant_value_no_trigger():
    """Test constant values do NOT trigger oscillation detection."""
    print("\n" + "="*80)
    print("TEST 9: Constant values (should NOT trigger)")
    print("="*80)

    detector = CouplingOscillationDetector()

    # All same value
    for _ in range(50):
        result = detector.check_for_oscillation('L1_routing', 0.5)
        assert not result, "Constant values should not trigger oscillation"

    print(f"✓ PASS: Constant values do not trigger detection")


def test_multi_loop_isolation():
    """Test that different loops have isolated detection."""
    print("\n" + "="*80)
    print("TEST 10: Multi-loop isolation")
    print("="*80)

    detector = CouplingOscillationDetector()

    # Loop 1: high-frequency oscillation
    values_1 = [0.5, 0.51, 0.49, 0.52, 0.48, 0.53, 0.47, 0.54, 0.46, 0.55, 0.45]
    detected_1 = False
    for val in values_1:
        if detector.check_for_oscillation('L1_routing', val):
            detected_1 = True
            break

    # Loop 2: monotonic trend
    values_2 = [0.5 + i * 0.01 for i in range(20)]
    detected_2 = False
    for val in values_2:
        if detector.check_for_oscillation('L2_confidence', val):
            detected_2 = True
            break

    assert detected_1 is True, "L1 should detect oscillation"
    assert detected_2 is False, "L2 should not detect trend"

    print(f"✓ PASS: Multi-loop isolation working (L1={detected_1}, L2={detected_2})")


def test_default_ema_alpha():
    """Test that default EMA alpha is 0.5 (Security Fix #7)."""
    print("\n" + "="*80)
    print("TEST 11: Default EMA alpha (Security Fix #7)")
    print("="*80)

    detector = CouplingOscillationDetector()

    assert detector.ema_alpha == 0.5, f"Default EMA alpha should be 0.5 (Security Fix #7), got {detector.ema_alpha}"

    print(f"✓ PASS: Default EMA alpha is 0.5 (increased from 0.3)")


def run_all_tests():
    """Run all tests."""
    print("\n" + "="*80)
    print("RUNNING COMPREHENSIVE OSCILLATION DETECTOR TESTS")
    print("Security Fix #7 (Round 2): Dual-Layer Detection")
    print("="*80)

    tests = [
        test_high_frequency_oscillation_detected,
        test_monotonic_trend_not_detected,
        test_square_wave_detected,
        test_ema_filter_applied,
        test_variance_accumulation_detector,
        test_ema_alpha_configurable,
        test_window_size_configurable,
        test_empty_history_handling,
        test_constant_value_no_trigger,
        test_multi_loop_isolation,
        test_default_ema_alpha,
    ]

    passed = 0
    failed = 0

    for test_func in tests:
        try:
            test_func()
            passed += 1
        except Exception as e:
            failed += 1
            print(f"\n✗ FAIL: {test_func.__name__}")
            print(f"  Error: {e}")

    print("\n" + "="*80)
    print("TEST RESULTS")
    print("="*80)
    print(f"Passed: {passed}/{len(tests)}")
    print(f"Failed: {failed}/{len(tests)}")

    if failed == 0:
        print("\n✓ ALL TESTS PASSED")
        return True
    else:
        print(f"\n✗ {failed} TEST(S) FAILED")
        return False


if __name__ == '__main__':
    success = run_all_tests()
    sys.exit(0 if success else 1)

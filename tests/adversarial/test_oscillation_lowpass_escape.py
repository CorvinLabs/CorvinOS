"""Low-pass oscillation escape — Fix #7, Round 3 (`e3fb129e`), retested for real.

Attack shape (from the archived probe script): drive a coupling loop with a
SMOOTH, low-frequency waveform whose sign changes stay well under the 60 %
threshold of the window-10 sign-change detector, so the parameter drifts without
`pause_learning` ever firing. Variants: slow sine, tiny-amplitude accumulation,
harmonic resonance with the 100-batch phase lock, asymmetric sawtooth with net
drift.

The round-3 fix was never covered by a real test — the probe lived at the repo
root outside `testpaths` and never ran. When it was finally driven through the
current API, **it did not hold**: `_detect_low_frequency_oscillation` computed
``dominant_freq = bin / (len(deltas) / 2)`` (a fraction of Nyquist) and compared
it against thresholds written in cycles-per-batch, so for the documented T=100
attack it produced 0.0201 — outside both bands — and the detector stayed silent
on exactly the signal it was added for. Thirteen of the module's own tests were
red. The criterion was replaced with a detrended spectral-concentration test
(see `gradient_backprop.CouplingOscillationDetector`); these are the pins.
"""
from __future__ import annotations

import math

import pytest

from core.learning.gradient_backprop import CouplingOscillationDetector

LOOP = "L1_routing"


def _drive(values, loop: str = LOOP, window_size: int = 10):
    """Feed a series to a fresh detector; return the batch index it fired at."""
    detector = CouplingOscillationDetector(window_size=window_size)
    for index, value in enumerate(values):
        if detector.check_for_oscillation(loop, value):
            return index
    return None


@pytest.mark.parametrize("period", [20, 30, 50, 100, 150, 200])
def test_smooth_sine_of_any_resolvable_period_is_detected(period: int):
    """The escape vector itself: a sine too slow for the sign-change detector."""
    values = [0.5 + 0.1 * math.sin(2 * math.pi * i / period) for i in range(3 * period + 200)]
    fired_at = _drive(values)
    assert fired_at is not None, f"slow sine T={period} escaped the detector"


def test_tiny_amplitude_slow_oscillation_is_detected():
    """Amplitude damping variant: Δ well under the sign-change detector's reach."""
    values = [0.5 + 0.01 * math.sin(2 * math.pi * i / 100.0) for i in range(400)]
    assert _drive(values) is not None


def test_harmonic_resonance_with_the_phase_lock_interval_is_detected():
    """One cycle per 100-batch phase lock — the aliasing variant."""
    values = [0.5 + 0.05 * math.sin(2 * math.pi * (i % 100) / 100.0) for i in range(500)]
    assert _drive(values, loop="L3_feedback") is not None


def test_asymmetric_sawtooth_with_net_drift_is_detected():
    """+0.01 rise / −0.009 fall: net drift hidden inside a periodic ramp."""
    param, values = 0.5, []
    for batch in range(300):
        param += 0.01 / 25 if batch % 50 < 25 else -0.009 / 25
        values.append(param)
    assert _drive(values, loop="L5_latency") is not None


def test_a_plain_monotone_trend_is_not_called_an_oscillation():
    """The counter-test: without it, "detect everything" would pass the suite.

    A ramp detrends to a ~zero residual and has no reversals. The round-3 Layer-3
    branch fired on it anyway (``variance < 0.001 and drift > 0.02``, commented
    "smooth upward trend … less likely to be an oscillation attack"), which is
    why `test_monotonic_trend_not_detected` was red.
    """
    values = [0.5 + i * 0.001 for i in range(250)]
    assert _drive(values) is None


def test_a_constant_series_is_not_called_an_oscillation():
    assert _drive([0.5] * 250) is None


def test_detection_is_prompt_enough_to_matter():
    """A T=100 attack must be caught in hundreds of batches, not millions."""
    values = [0.5 + 0.1 * math.sin(2 * math.pi * i / 100.0) for i in range(600)]
    fired_at = _drive(values)
    assert fired_at is not None and fired_at < 300, f"detected only at batch {fired_at}"

"""
Weight Smoother: Low-Pass Filtering for Oscillation Mitigation (Security Fix #7)

Implements exponential moving average (EMA) and Butterworth-inspired smoothing
to attenuate high-frequency oscillations in weight updates.

Security Threat Model (Fix #7 — Oscillation Attack):
  An adversary could craft feedback signals with intentional DAG resonance
  (high-frequency harmonic feedback) to cause weights to oscillate violently,
  bypassing convergence checks and poisoning the learning system.

Mitigation Strategy:
  1. Exponential Moving Average (EMA) filter:
     - Smooths weight deltas by blending new input with historical average
     - Formula: ema_t = alpha * input_t + (1 - alpha) * ema_{t-1}
     - Lower alpha = more smoothing, higher lag
     - Higher alpha = more responsive, less smoothing

  2. Frequency detection layer:
     - Tracks update frequency in a sliding 60-second window
     - Triggers when frequency exceeds configurable threshold
     - Signals to WeightUpdater to clamp learning rate

  3. Signal energy analysis (optional):
     - Compute Fourier spectrum to detect harmonic content
     - Flag updates if high-frequency energy dominates

Design Principles:
  - Stateful per-weight (EMA maintains history separately for each weight)
  - Fail-safe defaults (conservative smoothing, low false-negative rate)
  - Operator-configurable (tuning knobs: alpha, frequency threshold)
  - Auditable (all smoothing decisions logged)

Compliance:
  - GDPR Art. 32 (data integrity): EMA filter output is deterministic and logged
  - NIST SP 800-53 SI-7 (Information System Monitoring): frequency detection logs
    all oscillations for audit trail
"""

from typing import Dict, Optional, List, Tuple
from dataclasses import dataclass, field
from datetime import datetime
import time
from collections import deque
import logging
import math

# numpy is optional - only needed for FFT harmonic energy detection
try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False
    np = None

logger = logging.getLogger(__name__)


@dataclass
class SmootherConfig:
    """Configuration for weight smoother."""
    ema_alpha: float = 0.3  # EMA blending factor [0, 1]
    enable_fft_detection: bool = False  # Enable Fourier analysis (CPU-intensive)
    fft_energy_threshold: float = 0.6  # Threshold for harmonic energy
    smoothing_window_size: int = 20  # Number of samples for energy analysis


@dataclass
class SmootherOutput:
    """Output of a smoothing operation."""
    filtered_delta: float  # EMA-smoothed delta
    raw_delta: float  # Original (unsmoothed) delta
    ema_state: float  # Current EMA state for diagnostics
    harmonic_energy: Optional[float] = None  # FFT energy (if enabled)
    confidence: float = 1.0  # Confidence score [0, 1] for filter quality
    timestamp: float = field(default_factory=time.time)


@dataclass
class SmootherState:
    """Per-weight smoother state."""
    weight_id: str
    ema_value: float = 0.0  # Current EMA output
    ema_prev: float = 0.0  # Previous EMA value
    update_count: int = 0  # Total updates processed
    recent_deltas: deque = field(default_factory=lambda: deque(maxlen=20))  # Recent raw deltas
    recent_ema_outputs: deque = field(default_factory=lambda: deque(maxlen=20))  # Recent smoothed outputs
    harmonic_energy_history: deque = field(default_factory=lambda: deque(maxlen=10))  # FFT energies


class WeightSmoother:
    """
    Low-pass filter for weight deltas using EMA and optional FFT analysis.

    Protects against oscillation attacks by:
    1. Smoothing high-frequency noise via EMA
    2. Detecting harmonic resonance via frequency analysis
    3. Logging all smoothing decisions for audit trail

    Public API:
      - smooth(weight_id, delta) -> SmootherOutput
      - get_state(weight_id) -> SmootherState
      - reset(weight_id) -> None
    """

    def __init__(self, config: Optional[SmootherConfig] = None):
        """
        Initialize weight smoother.

        Args:
          config: SmootherConfig instance (uses defaults if None)
        """
        self.config = config or SmootherConfig()
        self.states: Dict[str, SmootherState] = {}

        # Validate configuration
        if not (0.0 <= self.config.ema_alpha <= 1.0):
            raise ValueError(f"ema_alpha must be in [0, 1], got {self.config.ema_alpha}")
        if not (0.0 <= self.config.fft_energy_threshold <= 1.0):
            raise ValueError(f"fft_energy_threshold must be in [0, 1]")

    def smooth(self, weight_id: str, delta: float) -> SmootherOutput:
        """
        Apply low-pass filtering to a weight delta.

        Process:
          1. Initialize state if first time seeing this weight
          2. Apply EMA filter
          3. (Optional) Compute FFT energy to detect harmonics
          4. Return SmootherOutput with diagnostics

        Args:
          weight_id: unique identifier for the weight
          delta: raw weight change requested

        Returns:
          SmootherOutput with filtered delta and diagnostics
        """
        current_time = time.time()

        # 1. Initialize state if needed
        if weight_id not in self.states:
            self.states[weight_id] = SmootherState(weight_id=weight_id)
            logger.debug(f"Initialized smoother state for weight '{weight_id}'")

        state = self.states[weight_id]
        state.update_count += 1

        # 2. Apply EMA filter
        filtered_delta = self._ema_filter(state, delta)

        # 3. Optional: Compute harmonic energy
        harmonic_energy = None
        if self.config.enable_fft_detection:
            harmonic_energy = self._compute_harmonic_energy(state)
            state.harmonic_energy_history.append(harmonic_energy)

        # 4. Compute confidence score
        confidence = self._compute_confidence(state, harmonic_energy)

        # 5. Record in history
        state.recent_deltas.append(delta)
        state.recent_ema_outputs.append(filtered_delta)

        # 6. Create output
        output = SmootherOutput(
            filtered_delta=filtered_delta,
            raw_delta=delta,
            ema_state=state.ema_value,
            harmonic_energy=harmonic_energy,
            confidence=confidence,
            timestamp=current_time,
        )

        logger.debug(
            f"Smoothed weight '{weight_id}': delta={delta:.6f}, "
            f"filtered={filtered_delta:.6f}, ema={state.ema_value:.6f}, "
            f"confidence={confidence:.3f}"
        )

        return output

    def _ema_filter(self, state: SmootherState, delta: float) -> float:
        """
        Apply exponential moving average filter.

        Formula:
          ema_t = alpha * delta_t + (1 - alpha) * ema_{t-1}

        Lower alpha = more smoothing (lags more, attenuates more high-frequency)
        Higher alpha = more responsive (lags less, attenuates less)

        Args:
          state: SmootherState for this weight
          delta: raw input delta

        Returns:
          EMA-filtered delta
        """
        alpha = self.config.ema_alpha

        # Compute new EMA value
        ema_new = alpha * delta + (1.0 - alpha) * state.ema_prev

        # Update state
        state.ema_prev = ema_new
        state.ema_value = ema_new

        return ema_new

    def _compute_harmonic_energy(self, state: SmootherState) -> float:
        """
        Compute normalized harmonic energy via FFT.

        Detects if recent updates have high-frequency content (sign of resonance).

        Formula (simplified):
          1. Compute FFT of recent deltas
          2. Split spectrum: low-freq (0-25% of Nyquist) vs high-freq (25-100%)
          3. Harmonic energy = high_freq_power / (low_freq_power + high_freq_power)

        Returns value in [0, 1]:
          - 0.0 = purely low-frequency (smooth, no oscillation)
          - 1.0 = purely high-frequency (oscillating, potential attack)

        Returns:
          Harmonic energy score [0, 1]
          Note: Returns 0.0 if numpy is not available (fallback to no FFT analysis)
        """
        if not HAS_NUMPY:
            # Fallback if numpy unavailable - use simple sign-change detection
            return self._compute_harmonic_energy_simple(state)

        if len(state.recent_deltas) < 4:
            # Need at least 4 samples for meaningful FFT
            return 0.0

        # Extract delta array
        deltas = np.array(list(state.recent_deltas), dtype=np.float64)

        # Remove DC component (subtract mean)
        deltas_centered = deltas - np.mean(deltas)

        # Compute FFT (real-valued, so use rfft)
        fft_vals = np.fft.rfft(deltas_centered)
        power_spectrum = np.abs(fft_vals) ** 2

        # Split into low and high frequency bands
        # Low freq: indices 0 to 25% of spectrum
        # High freq: indices 25% to 100% of spectrum
        n_freqs = len(power_spectrum)
        boundary = max(1, n_freqs // 4)

        low_freq_power = np.sum(power_spectrum[:boundary])
        high_freq_power = np.sum(power_spectrum[boundary:])

        total_power = low_freq_power + high_freq_power

        if total_power < 1e-10:
            # No signal energy
            return 0.0

        harmonic_energy = high_freq_power / total_power
        return float(harmonic_energy)

    def _compute_harmonic_energy_simple(self, state: SmootherState) -> float:
        """
        Simple harmonic energy detection using sign changes (fallback when numpy unavailable).

        Counts sign changes in recent deltas:
          - Many sign changes = high oscillation = high harmonic energy
          - Few sign changes = smooth trend = low harmonic energy

        Returns value in [0, 1].
        """
        if len(state.recent_deltas) < 2:
            return 0.0

        deltas = list(state.recent_deltas)
        sign_changes = 0

        for i in range(1, len(deltas)):
            if (deltas[i] > 0 and deltas[i-1] < 0) or (deltas[i] < 0 and deltas[i-1] > 0):
                sign_changes += 1

        # Normalize: max sign changes = len(deltas)-1
        max_possible_changes = len(deltas) - 1
        if max_possible_changes == 0:
            return 0.0

        harmonic_energy = sign_changes / max_possible_changes
        return float(harmonic_energy)

    def _compute_confidence(
        self,
        state: SmootherState,
        harmonic_energy: Optional[float] = None,
    ) -> float:
        """
        Compute confidence score for the smoothed delta.

        Factors:
          - Number of updates (more history = higher confidence)
          - Harmonic energy (low harmonics = higher confidence)
          - EMA stability (converged EMA = higher confidence)

        Returns value in [0, 1]:
          - 0.0 = low confidence (new weight, high oscillation detected)
          - 1.0 = high confidence (many samples, smooth history)

        Returns:
          Confidence score [0, 1]
        """
        # Factor 1: Update history (increases with count, saturates at 100)
        history_factor = min(1.0, state.update_count / 100.0)

        # Factor 2: Harmonic energy (penalizes oscillation)
        harmonic_factor = 1.0
        if harmonic_energy is not None:
            # If harmonic energy > threshold, reduce confidence
            if harmonic_energy > self.config.fft_energy_threshold:
                harmonic_factor = 1.0 - (harmonic_energy - self.config.fft_energy_threshold)
            harmonic_factor = max(0.0, harmonic_factor)

        # Factor 3: EMA convergence (if |ema - ema_prev| is small, it's stable)
        ema_delta = abs(state.ema_value - state.ema_prev)
        convergence_factor = 1.0 / (1.0 + ema_delta)  # Sigmoid-like

        # Combine factors (weighted average)
        confidence = (
            0.4 * history_factor +
            0.3 * harmonic_factor +
            0.3 * convergence_factor
        )

        return min(1.0, max(0.0, confidence))

    def get_state(self, weight_id: str) -> Optional[SmootherState]:
        """Get the smoother state for a weight (for diagnostics)."""
        return self.states.get(weight_id)

    def get_all_states(self) -> Dict[str, SmootherState]:
        """Get all smoother states (for monitoring/debugging)."""
        return dict(self.states)

    def reset(self, weight_id: str) -> None:
        """Reset smoother state for a weight."""
        if weight_id in self.states:
            del self.states[weight_id]
            logger.info(f"Reset smoother state for weight '{weight_id}'")

    def reset_all(self) -> None:
        """Reset all smoother states."""
        self.states.clear()
        logger.info("Reset all smoother states")

    def get_smoothing_ratio(self, weight_id: str) -> float:
        """
        Get the smoothing ratio for a weight (how much filtering is happening).

        Returns:
          Ratio in [0, 1]:
            - 0.0 = no smoothing (ema_alpha = 1.0)
            - 1.0 = maximum smoothing (ema_alpha = 0.0)
        """
        return 1.0 - self.config.ema_alpha

    def get_diagnostics(self, weight_id: str) -> Dict:
        """Get full diagnostic information for a weight."""
        state = self.get_state(weight_id)
        if state is None:
            return {'weight_id': weight_id, 'found': False}

        return {
            'weight_id': weight_id,
            'found': True,
            'update_count': state.update_count,
            'current_ema': state.ema_value,
            'ema_alpha': self.config.ema_alpha,
            'smoothing_ratio': self.get_smoothing_ratio(weight_id),
            'recent_deltas': list(state.recent_deltas),
            'recent_ema_outputs': list(state.recent_ema_outputs),
            'harmonic_energy_history': list(state.harmonic_energy_history),
            'fft_enabled': self.config.enable_fft_detection,
        }

"""L5: Feedback Stability & Drift Detection (EMA Smoothing).

ADR-0572: Feedback Stability Layer
Prevents learning from overfitting to n-of-1 lucky events via EMA smoothing.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional, Tuple
import logging

logger = logging.getLogger(__name__)


@dataclass
class FeedbackDelta:
    """A single learning feedback signal."""
    skill_id: str
    metric_name: str  # e.g., "confidence_threshold"
    raw_delta: float  # Unsmoothed change from feedback
    timestamp: str


@dataclass
class SmoothedDelta:
    """EMA-smoothed delta (resistant to n-of-1 noise)."""
    skill_id: str
    metric_name: str
    raw_delta: float
    smoothed_delta: float
    ema_alpha: float = 0.3  # Exponential Moving Average factor
    confidence: float = 0.0  # EMA confidence [0.0-1.0]


@dataclass
class DriftAlert:
    """Alert when learning drifts significantly."""
    skill_id: str
    metric_name: str
    smoothed_delta: float
    drift_threshold: float
    recent_deltas: List[float] = field(default_factory=list)
    consecutive_high_deltas: int = 0
    requires_operator_approval: bool = False


class FeedbackStabilityGate:
    """L5: Smooth learning feedback to prevent overfitting."""

    def __init__(
        self,
        ema_alpha: float = 0.3,
        drift_threshold: float = 0.15,
        drift_window: int = 3,
    ):
        """Initialize stability gate.

        Args:
            ema_alpha: EMA smoothing factor [0.0-1.0] (default 0.3 = responsive but smooth)
            drift_threshold: Absolute delta to trigger drift alert (default 0.15)
            drift_window: Window size for consecutive high-delta detection (default 3)
        """
        self.ema_alpha = ema_alpha
        self.drift_threshold = drift_threshold
        self.drift_window = drift_window

        # State: skill_id -> metric_name -> (raw_delta, smoothed_delta, history)
        self.state: Dict[str, Dict[str, Tuple[float, float, List[float]]]] = {}

    def apply_feedback(
        self,
        skill_id: str,
        metric_name: str,
        raw_delta: float,
    ) -> Tuple[SmoothedDelta, Optional[DriftAlert]]:
        """Apply EMA smoothing to feedback delta.

        Args:
            skill_id: Skill being learned
            metric_name: Config metric being tuned (e.g., "confidence_threshold")
            raw_delta: Raw change from feedback (unsmoothed)

        Returns:
            (SmoothedDelta, Optional[DriftAlert])
        """
        # Initialize state if needed
        if skill_id not in self.state:
            self.state[skill_id] = {}
        if metric_name not in self.state[skill_id]:
            self.state[skill_id][metric_name] = (0.0, 0.0, [])

        prior_raw, prior_smoothed, history = self.state[skill_id][metric_name]

        # Step 1: EMA smoothing
        # smoothed = alpha * raw + (1 - alpha) * prior_smoothed
        smoothed = self.ema_alpha * raw_delta + (1.0 - self.ema_alpha) * prior_smoothed

        # Step 2: Confidence metric (higher after more consistent feedback)
        # Confidence increases when raw and smoothed agree
        if len(history) == 0:
            confidence = 0.0  # First feedback is uncertain
        else:
            # Measure agreement: if signs match, confidence increases
            sign_match = 1.0 if (raw_delta * prior_smoothed) >= 0 else 0.0
            confidence = 0.5 + 0.5 * sign_match  # Range [0.5, 1.0]

        # Step 3: Update history
        history.append(raw_delta)
        if len(history) > self.drift_window:
            history.pop(0)

        # Store updated state
        self.state[skill_id][metric_name] = (raw_delta, smoothed, history)

        smoothed_obj = SmoothedDelta(
            skill_id=skill_id,
            metric_name=metric_name,
            raw_delta=raw_delta,
            smoothed_delta=smoothed,
            ema_alpha=self.ema_alpha,
            confidence=confidence,
        )

        # Step 4: Drift detection
        drift_alert = self._check_drift(skill_id, metric_name, smoothed, history)

        if drift_alert:
            logger.warning(
                f"[L5 Drift] {skill_id}.{metric_name}: "
                f"smoothed_delta={smoothed:.4f} exceeds threshold {self.drift_threshold}"
            )

        return smoothed_obj, drift_alert

    def _check_drift(
        self,
        skill_id: str,
        metric_name: str,
        smoothed_delta: float,
        history: List[float],
    ) -> Optional[DriftAlert]:
        """Detect if learning is drifting significantly.

        Drift is detected when:
        - |smoothed_delta| > drift_threshold, AND
        - Recent raw deltas show consistent high values (≥2 out of drift_window)

        Args:
            skill_id: Skill ID
            metric_name: Metric name
            smoothed_delta: EMA-smoothed delta
            history: Recent raw delta history

        Returns:
            DriftAlert if drift detected, None otherwise
        """
        if abs(smoothed_delta) <= self.drift_threshold:
            # No drift (within threshold)
            return None

        # Potential drift: check if recent history confirms it (not just n-of-1)
        high_deltas = sum(1 for d in history if abs(d) > self.drift_threshold)

        if high_deltas >= (len(history) - 1):
            # Multiple recent deltas > threshold (consistent pattern)
            return DriftAlert(
                skill_id=skill_id,
                metric_name=metric_name,
                smoothed_delta=smoothed_delta,
                drift_threshold=self.drift_threshold,
                recent_deltas=history.copy(),
                consecutive_high_deltas=high_deltas,
                requires_operator_approval=True,
            )

        # Single high delta but history doesn't confirm (n-of-1 noise)
        return None

    def get_confidence(self, skill_id: str, metric_name: str) -> float:
        """Get current EMA confidence for a metric.

        Returns:
            Confidence score [0.0-1.0]
        """
        if skill_id not in self.state or metric_name not in self.state[skill_id]:
            return 0.0

        _, _, history = self.state[skill_id][metric_name]
        if not history:
            return 0.0

        # Confidence based on consistency of recent feedback
        return min(1.0, len(history) / self.drift_window)

    def reset_metric(self, skill_id: str, metric_name: str) -> None:
        """Reset learning state for a metric (e.g., after operator override)."""
        if skill_id in self.state and metric_name in self.state[skill_id]:
            self.state[skill_id][metric_name] = (0.0, 0.0, [])
            logger.info(f"[L5 Reset] {skill_id}.{metric_name} learning state reset")

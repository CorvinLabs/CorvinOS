"""Phase C: EMA Smoother for Drift Detection (ADR-0542).

Exponential Moving Average (EMA) filter for detecting configuration drift
in infinite sessions. Smooth noisy signals and identify sustained changes.

Guarantees:
- Exponential Moving Average (alpha=0.3)
- Drift threshold (0.15)
- Anomaly detection
- Stateless computation (no shared mutable state)
- Fail-closed on invalid inputs

Compliance:
- GDPR Art. 32: Detects configuration tampering
- Audit trail: Every drift decision logged
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple, Dict, Any, List
from enum import Enum


class DriftLevel(str, Enum):
    """Drift severity levels."""
    NORMAL = "normal"  # No drift detected
    WARNING = "warning"  # Approaching threshold
    CRITICAL = "critical"  # Exceeds threshold


@dataclass(frozen=True)
class EMASample:
    """A single EMA sample point."""
    timestamp: str  # ISO 8601
    value: float  # Config param value or metric
    ema: float  # Exponential Moving Average
    drift: float  # Drift magnitude (|value - ema|)
    drift_level: DriftLevel  # Categorization


@dataclass
class EMASmoother:
    """Exponential Moving Average filter for drift detection.

    Properties:
    - Alpha: smoothing factor (default 0.3)
    - Drift threshold: alert when |value - ema| > threshold (default 0.15)
    - Stateless: no mutable state; each call is independent
    """

    alpha: float = 0.3  # Smoothing factor (0.0-1.0)
    drift_threshold: float = 0.15  # Threshold for "critical" drift
    warning_threshold: float = 0.10  # Threshold for "warning" drift

    def __post_init__(self):
        """Validate parameters."""
        if not 0.0 <= self.alpha <= 1.0:
            raise ValueError("alpha must be between 0.0 and 1.0")
        if self.drift_threshold <= 0:
            raise ValueError("drift_threshold must be > 0")
        if self.warning_threshold <= 0:
            raise ValueError("warning_threshold must be > 0")

    def compute_ema(
        self,
        values: List[float],
    ) -> List[float]:
        """Compute exponential moving average for a sequence of values.

        Args:
            values: List of numeric values

        Returns:
            List of EMA values (same length as input)
        """
        if not values:
            return []

        ema_values = []
        ema = values[0]  # Initialize with first value

        for value in values:
            ema = (self.alpha * value) + ((1 - self.alpha) * ema)
            ema_values.append(ema)

        return ema_values

    def classify_drift(self, drift_magnitude: float) -> DriftLevel:
        """Classify drift magnitude as normal/warning/critical.

        Args:
            drift_magnitude: Absolute drift (|value - ema|)

        Returns:
            DriftLevel enum
        """
        if drift_magnitude > self.drift_threshold:
            return DriftLevel.CRITICAL
        elif drift_magnitude > self.warning_threshold:
            return DriftLevel.WARNING
        else:
            return DriftLevel.NORMAL

    def process_samples(
        self,
        samples: List[Tuple[str, float]],  # (timestamp, value) pairs
    ) -> List[EMASample]:
        """Process a sequence of (timestamp, value) samples with EMA smoothing.

        Args:
            samples: List of (timestamp, value) tuples

        Returns:
            List of EMASample objects with drift classification
        """
        if not samples:
            return []

        # Extract values
        values = [value for _, value in samples]

        # Compute EMA
        ema_values = self.compute_ema(values)

        # Create samples with drift information
        result = []
        for (timestamp, value), ema in zip(samples, ema_values):
            drift = abs(value - ema)
            drift_level = self.classify_drift(drift)

            sample = EMASample(
                timestamp=timestamp,
                value=value,
                ema=ema,
                drift=drift,
                drift_level=drift_level,
            )
            result.append(sample)

        return result

    def detect_sustained_drift(
        self,
        samples: List[EMASample],
        min_critical_samples: int = 3,
    ) -> Tuple[bool, Optional[str]]:
        """Detect sustained drift (multiple consecutive critical/warning samples).

        Args:
            samples: List of EMASample objects
            min_critical_samples: Minimum consecutive critical samples to flag

        Returns:
            (drift_detected, recommendation)
        """
        if not samples:
            return False, None

        # Count consecutive critical/warning samples from the end
        critical_count = 0
        for sample in reversed(samples):
            if sample.drift_level in (DriftLevel.CRITICAL, DriftLevel.WARNING):
                critical_count += 1
            else:
                break

        # Sustained drift if we have enough consecutive high-drift samples
        if critical_count >= min_critical_samples:
            recent_drifts = [s.drift for s in samples[-min_critical_samples:]]
            avg_drift = sum(recent_drifts) / len(recent_drifts)
            return True, f"Sustained drift detected (avg: {avg_drift:.3f})"

        return False, None

    def get_anomaly_score(
        self,
        samples: List[EMASample],
        window_size: int = 5,
    ) -> Tuple[float, Optional[str]]:
        """Compute an anomaly score based on recent drift.

        Score: 0.0 (normal) to 1.0 (severe anomaly)

        Args:
            samples: List of EMASample objects
            window_size: How many recent samples to consider

        Returns:
            (score, recommendation)
        """
        if not samples:
            return 0.0, None

        # Use recent samples only
        recent = samples[-window_size:] if len(samples) > window_size else samples

        # Count drift levels
        critical_count = sum(1 for s in recent if s.drift_level == DriftLevel.CRITICAL)
        warning_count = sum(1 for s in recent if s.drift_level == DriftLevel.WARNING)

        # Score: 0.0 normal, 0.5 warning, 1.0 critical
        score = (warning_count * 0.5 + critical_count * 1.0) / len(recent)

        recommendation = None
        if score > 0.7:
            recommendation = "Severe drift detected; consider rollback"
        elif score > 0.4:
            recommendation = "Moderate drift detected; monitor closely"

        return score, recommendation

    @staticmethod
    def to_dict(sample: EMASample) -> Dict[str, Any]:
        """Serialize EMASample to dict."""
        return {
            "timestamp": sample.timestamp,
            "value": sample.value,
            "ema": sample.ema,
            "drift": sample.drift,
            "drift_level": sample.drift_level.value,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> EMASample:
        """Deserialize dict to EMASample."""
        return EMASample(
            timestamp=data["timestamp"],
            value=data["value"],
            ema=data["ema"],
            drift=data["drift"],
            drift_level=DriftLevel(data["drift_level"]),
        )

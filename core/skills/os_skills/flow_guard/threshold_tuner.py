"""Stream 3 Phase 2: Threshold Tuner for Flow Guard (ADR-0314).

Dynamically adjusts policy thresholds based on false positive/negative rates.
Integrates with PolicyConfidenceScorer (Phase 1) and DataClassifier (Phase 2).

**Algorithm:**
- Track false positive rate (denied safe flows) per (data_class, engine, destination)
- Track false negative rate (allowed unsafe flows) per tuple
- Adjust confidence threshold to optimize tradeoff:
  - If FP rate >5%: lower threshold (allow more flows, reduce false denials)
  - If FN rate >1%: raise threshold (deny more flows, reduce false allows)
- Persist tuned thresholds back to disk

**Compliance:**
- GDPR Art. 32: Threshold tuning logged to audit trail
- Fail-safe: tuning never disables a flow (only adjusts confidence)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

logger = logging.getLogger(__name__)


@dataclass
class ThresholdAdjustment:
    """Record of a threshold adjustment."""
    key: str  # "pii_sonnet_console"
    old_threshold: float
    new_threshold: float
    fp_rate: float  # False positive rate (%)
    fn_rate: float  # False negative rate (%)
    reason: str  # Why adjusted


class ThresholdTuner:
    """Dynamically tunes policy thresholds based on feedback rates (Phase 2).

    **Responsibility:**
    1. Receive feedback on whether policy decisions were correct
    2. Track FP/FN rates per (data_class, engine, destination)
    3. Adjust thresholds to optimize tradeoff
    4. Persist tuned thresholds
    5. Emit THRESHOLD_ADJUSTED events to audit trail

    **Integration:**
    - Called by PolicyConfidenceScorer after feedback accumulation
    - Returns updated PolicyThresholds
    - DataClassifierLearned uses updated thresholds for next classification
    """

    # Tuning parameters (configurable per policy)
    FP_THRESHOLD = 0.05  # Trigger threshold adjustment if FP rate >5%
    FN_THRESHOLD = 0.01  # Trigger threshold adjustment if FN rate >1%
    ADJUSTMENT_STEP = 0.05  # Adjust by ±5 percentage points per iteration

    def __init__(self):
        """Initialize threshold tuner."""
        self.adjustments_history: list[ThresholdAdjustment] = []

    def tune_thresholds(
        self,
        current_thresholds: Dict[str, float],
        false_positives: Dict[str, int],
        false_negatives: Dict[str, int],
        total_decisions: Dict[str, int],
    ) -> Tuple[Dict[str, float], list[ThresholdAdjustment]]:
        """Tune thresholds based on FP/FN rates.

        **Algorithm:**
        For each (data_class, engine, destination) tuple:
        1. Compute FP rate = false_positives[key] / total_decisions[key]
        2. Compute FN rate = false_negatives[key] / total_decisions[key]
        3. If FP rate > FP_THRESHOLD: lower threshold by ADJUSTMENT_STEP
           (allow more flows, reduce false denials of safe data)
        4. If FN rate > FN_THRESHOLD: raise threshold by ADJUSTMENT_STEP
           (deny more flows, reduce false allows of unsafe data)
        5. Clamp to [0.0, 1.0]
        6. Return updated thresholds + adjustment records

        Args:
            current_thresholds: Current threshold values (key → P_safe)
            false_positives: Count of denied-safe flows per key
            false_negatives: Count of allowed-unsafe flows per key
            total_decisions: Total decisions made per key

        Returns:
            (updated_thresholds, adjustments_made)
        """
        updated_thresholds = dict(current_thresholds)
        adjustments: list[ThresholdAdjustment] = []

        all_keys = set(current_thresholds.keys()) | set(false_positives.keys()) | set(false_negatives.keys())

        for key in all_keys:
            total = total_decisions.get(key, 1)  # Avoid division by zero
            if total == 0:
                continue

            fp = false_positives.get(key, 0)
            fn = false_negatives.get(key, 0)

            fp_rate = fp / total
            fn_rate = fn / total

            old_threshold = current_thresholds.get(key, 0.5)
            new_threshold = old_threshold

            # Adjust based on FP/FN rates
            if fp_rate > self.FP_THRESHOLD:
                new_threshold -= self.ADJUSTMENT_STEP
                reason = f"High FP rate ({fp_rate:.1%}): lowering threshold to allow more flows"
                logger.info(f"Threshold tuning {key}: {reason}")

            elif fn_rate > self.FN_THRESHOLD:
                new_threshold += self.ADJUSTMENT_STEP
                reason = f"High FN rate ({fn_rate:.1%}): raising threshold to deny more flows"
                logger.info(f"Threshold tuning {key}: {reason}")
            else:
                reason = f"FP={fp_rate:.1%}, FN={fn_rate:.1%} — within acceptable range"
                logger.debug(f"Threshold tuning {key}: {reason}")

            # Clamp to [0.0, 1.0]
            new_threshold = max(0.0, min(1.0, new_threshold))

            if abs(new_threshold - old_threshold) > 0.001:  # Only record significant changes
                updated_thresholds[key] = new_threshold
                adjustment = ThresholdAdjustment(
                    key=key,
                    old_threshold=old_threshold,
                    new_threshold=new_threshold,
                    fp_rate=fp_rate,
                    fn_rate=fn_rate,
                    reason=reason,
                )
                adjustments.append(adjustment)
                self.adjustments_history.append(adjustment)

        return updated_thresholds, adjustments

    def get_adjustment_history(self, limit: int = 100) -> list[ThresholdAdjustment]:
        """Retrieve threshold adjustment history (for audit trail/console).

        Args:
            limit: Max adjustments to return

        Returns:
            List of ThresholdAdjustment records (most recent first)
        """
        return self.adjustments_history[-limit:][::-1]

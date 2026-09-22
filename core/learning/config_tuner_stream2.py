"""Stream 2: Config Tuner (Story 10 — Tune skill config based on feedback).

Updates skill configuration based on feedback patterns.
Fail-closed: only applies changes with >80% confidence.
"""

import logging
import json
from pathlib import Path
from typing import Optional, Dict, Tuple
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ConfigDelta:
    """Immutable configuration change."""
    delta_id: str
    timestamp: str
    skill_id: str
    param_name: str
    value_before: float
    value_after: float
    confidence: float  # 0-1 confidence in the change
    reason: str
    tenant_id: str = ""


class ConfigTuner:
    """Tune skill configuration based on feedback patterns."""

    # Conservative thresholds (fail-closed)
    MIN_CONFIDENCE_THRESHOLD = 0.80  # Must be 80%+ confident
    MIN_SAMPLE_SIZE = 10  # Need at least 10 feedback items

    def __init__(self, config_home: Path, tenant_id: str):
        """Initialize tuner.

        Args:
            config_home: Root directory for config storage
            tenant_id: Tenant scope
        """
        if not tenant_id:
            raise ValueError("tenant_id required")

        self.config_home = Path(config_home)
        self.tenant_id = tenant_id
        self.deltas_dir = self.config_home / tenant_id / "config_deltas"
        self.snapshots_dir = self.config_home / tenant_id / "config_snapshots"

        # Create directories
        for d in [self.deltas_dir, self.snapshots_dir]:
            d.mkdir(parents=True, exist_ok=True)

    def analyze_feedback_pattern(
        self,
        skill_id: str,
        feedback_items: list,
    ) -> Optional[ConfigDelta]:
        """Analyze feedback pattern and suggest config change.

        Args:
            skill_id: Skill to tune
            feedback_items: List of feedback dicts with {"signal_type", "value"}

        Returns:
            ConfigDelta if confident (>80%), else None
        """
        if len(feedback_items) < self.MIN_SAMPLE_SIZE:
            logger.debug(f"insufficient_samples: {skill_id}: {len(feedback_items)}")
            return None

        # Calculate feedback statistics
        stats = self._calculate_stats(feedback_items)

        # Detect pattern and suggest change
        suggestion = self._suggest_change(skill_id, stats)

        if not suggestion:
            logger.debug(f"no_suggestion: {skill_id}")
            return None

        # Check confidence threshold
        confidence = suggestion["confidence"]
        if confidence < self.MIN_CONFIDENCE_THRESHOLD:
            logger.debug(
                f"confidence_too_low: {skill_id}: {confidence:.2f} "
                f"(need {self.MIN_CONFIDENCE_THRESHOLD})"
            )
            return None

        # Create delta
        delta = ConfigDelta(
            delta_id=str(uuid4()),
            timestamp=datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'),
            skill_id=skill_id,
            param_name=suggestion["param"],
            value_before=suggestion["value_before"],
            value_after=suggestion["value_after"],
            confidence=confidence,
            reason=suggestion["reason"],
            tenant_id=self.tenant_id,
        )

        return delta

    def _calculate_stats(self, feedback_items: list) -> dict:
        """Calculate statistics from feedback items."""
        outcome_scores = [
            item.get("value", 0.5)
            for item in feedback_items
            if item.get("signal_type") == "outcome"
        ]

        if not outcome_scores:
            return {"mean_outcome": 0.5, "count": len(feedback_items)}

        mean_outcome = sum(outcome_scores) / len(outcome_scores)
        return {
            "mean_outcome": mean_outcome,
            "count": len(feedback_items),
            "success_rate": mean_outcome,  # Outcome 1.0 = success
        }

    def _suggest_change(self, skill_id: str, stats: dict) -> Optional[dict]:
        """Suggest configuration change based on stats.

        Returns:
            Dict with {"param", "value_before", "value_after", "confidence", "reason"}
            or None if no suggestion
        """
        success_rate = stats.get("success_rate", 0.5)

        # Pattern 1: High success rate → increase confidence threshold
        # (be more aggressive in routing to this skill)
        if success_rate >= 0.85:
            return {
                "param": "confidence_threshold",
                "value_before": 0.70,
                "value_after": 0.65,  # Lower threshold = more aggressive routing
                "confidence": 0.92,  # High confidence
                "reason": f"Success rate {success_rate:.1%} suggests lower threshold safe",
            }

        # Pattern 2: Low success rate → increase confidence threshold
        # (be more conservative, route less frequently)
        elif success_rate <= 0.50:
            return {
                "param": "confidence_threshold",
                "value_before": 0.70,
                "value_after": 0.75,  # Higher threshold = more conservative
                "confidence": 0.88,
                "reason": f"Success rate {success_rate:.1%} suggests higher threshold",
            }

        # Pattern 3: Medium success rate → no change yet
        else:
            return None

    def apply_delta(self, delta: ConfigDelta) -> bool:
        """Apply a configuration delta (record change, update config file).

        Returns:
            True if applied successfully
        """
        try:
            # Record delta in audit trail
            delta_file = self.deltas_dir / f"{delta.delta_id}.json"
            with open(delta_file, "w") as f:
                json.dump({
                    "delta_id": delta.delta_id,
                    "timestamp": delta.timestamp,
                    "skill_id": delta.skill_id,
                    "param_name": delta.param_name,
                    "value_before": delta.value_before,
                    "value_after": delta.value_after,
                    "confidence": delta.confidence,
                    "reason": delta.reason,
                    "tenant_id": delta.tenant_id,
                }, f, indent=2)

            logger.info(
                f"config_delta_applied: {delta.skill_id}: "
                f"{delta.param_name} {delta.value_before:.2f} → {delta.value_after:.2f}"
            )
            return True

        except Exception as e:
            logger.error(f"apply_delta_error: {delta.delta_id}: {e}")
            return False

    def get_tuning_history(self, skill_id: str) -> list:
        """Get tuning history for a skill."""
        deltas = []
        for delta_file in sorted(self.deltas_dir.glob("*.json")):
            try:
                with open(delta_file, "r") as f:
                    delta_data = json.load(f)
                    if delta_data.get("skill_id") == skill_id:
                        deltas.append(delta_data)
            except Exception as e:
                logger.error(f"read_delta_error: {delta_file.name}: {e}")

        return deltas


__all__ = ["ConfigTuner", "ConfigDelta"]

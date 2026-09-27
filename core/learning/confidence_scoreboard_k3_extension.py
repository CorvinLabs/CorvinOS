"""ConfidenceScoreboard K=3 Extension — Track escalation + feedback loop (ADR-0314, ADR-2084)."""

from datetime import datetime
from typing import Dict, Optional, List
from collections import deque
import logging

logger = logging.getLogger(__name__)


class ConfidenceScoreboardK3:
    """
    Track model confidence with escalation data (K=3 enhancement).
    Feeds into next task's classification via confidence_delta.
    """

    def __init__(self):
        self.scores: Dict[str, Dict] = {
            "haiku": self._init_entry(),
            "sonnet": self._init_entry(),
            "opus": self._init_entry(),
        }

    def _init_entry(self) -> Dict:
        """Initialize score entry."""
        return {
            "success_count": 0,
            "total_count": 0,
            "escalation_count": 0,
            "latency_samples": deque(maxlen=10),  # Rolling window
            "confidence_delta": 0.0,
            "last_updated": None,
            "trend": "stable",
        }

    def update(
        self,
        model: str,
        outcome: str,  # "success" | "timeout" | "error"
        escalated: bool = False,
        latency_observed_ms: Optional[float] = None,
    ) -> float:
        """
        Update model score and compute confidence delta.

        Returns:
            confidence_delta (applied to next task's classification)
        """
        if model not in self.scores:
            return 0.0

        entry = self.scores[model]

        # Update counters
        entry["total_count"] += 1
        if outcome == "success":
            entry["success_count"] += 1

        if escalated:
            entry["escalation_count"] += 1

        # Record latency
        if latency_observed_ms:
            entry["latency_samples"].append(latency_observed_ms)

        # Compute confidence delta
        if entry["total_count"] > 0:
            success_rate = entry["success_count"] / entry["total_count"]
            escalation_rate = entry["escalation_count"] / entry["total_count"]

            # Penalty: 0.1 per escalation% (escalation lowers confidence)
            confidence_delta = success_rate - (escalation_rate * 0.1)
        else:
            confidence_delta = 0.0

        entry["confidence_delta"] = confidence_delta
        entry["trend"] = self._compute_trend(entry)
        entry["last_updated"] = datetime.utcnow()

        logger.debug(f"Scoreboard update: {model} δ={confidence_delta:.2f}, escalated={escalated}")

        return confidence_delta

    def get_confidence_delta(self, model: str) -> float:
        """Get confidence delta for next task classification."""
        return self.scores.get(model, {}).get("confidence_delta", 0.0)

    def get_all_scores(self) -> Dict[str, Dict]:
        """Return all model scores (for monitoring/diagnostics)."""
        return self.scores.copy()

    def _compute_trend(self, entry: Dict) -> str:
        """Detect trend: improving/degrading/stable."""
        if len(entry["latency_samples"]) < 3:
            return "stable"

        recent = list(entry["latency_samples"])[-3:]
        trend_delta = recent[-1] - recent[0]

        if trend_delta > 50:
            return "degrading"
        elif trend_delta < -50:
            return "improving"
        else:
            return "stable"


# Global singleton
_SCOREBOARD: Optional[ConfidenceScoreboardK3] = None


def initialize_scoreboard():
    """Initialize global scoreboard."""
    global _SCOREBOARD
    _SCOREBOARD = ConfidenceScoreboardK3()


def update_scoreboard(model: str, outcome: str, escalated: bool = False, latency_ms: Optional[float] = None) -> float:
    """Update scoreboard and return confidence delta."""
    if _SCOREBOARD:
        return _SCOREBOARD.update(model, outcome, escalated, latency_ms)
    return 0.0


def get_confidence_delta(model: str) -> float:
    """Get confidence delta for next task."""
    if _SCOREBOARD:
        return _SCOREBOARD.get_confidence_delta(model)
    return 0.0


def get_scoreboard_state() -> Dict:
    """Get current scoreboard state (for monitoring)."""
    if _SCOREBOARD:
        return _SCOREBOARD.get_all_scores()
    return {}

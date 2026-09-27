"""ConfidenceScoreboard K=3 Extension — Track escalation + feedback loop (ADR-0314, ADR-2084).

In-process only (no persistence, no audit): a restart resets every score.
The module-level helpers keep ONE scoreboard PER TENANT — until 2026-09-27 a
single process-global scoreboard mixed every tenant's outcomes into one
confidence delta.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""

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


# One scoreboard per tenant (never a process-global one).
_SCOREBOARDS: Dict[str, ConfidenceScoreboardK3] = {}


def _board(tenant_id: str, *, create: bool) -> Optional[ConfidenceScoreboardK3]:
    from core.tenants import validate_tenant_id  # noqa: PLC0415

    validate_tenant_id(tenant_id)
    if create and tenant_id not in _SCOREBOARDS:
        _SCOREBOARDS[tenant_id] = ConfidenceScoreboardK3()
    return _SCOREBOARDS.get(tenant_id)


def initialize_scoreboard(*, tenant_id: str) -> None:
    """(Re)initialize the tenant's scoreboard."""
    _board(tenant_id, create=False)
    _SCOREBOARDS[tenant_id] = ConfidenceScoreboardK3()


def update_scoreboard(model: str, outcome: str, escalated: bool = False,
                      latency_ms: Optional[float] = None, *, tenant_id: str) -> float:
    """Update the tenant's scoreboard and return its confidence delta."""
    return _board(tenant_id, create=True).update(model, outcome, escalated, latency_ms)


def get_confidence_delta(model: str, *, tenant_id: str) -> float:
    """Get the tenant's confidence delta for next task."""
    b = _board(tenant_id, create=False)
    return b.get_confidence_delta(model) if b else 0.0


def get_scoreboard_state(*, tenant_id: str) -> Dict:
    """Get the tenant's scoreboard state (for monitoring)."""
    b = _board(tenant_id, create=False)
    return b.get_all_scores() if b else {}

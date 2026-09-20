"""Feedback loop plugin for learning infrastructure."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from corvin_plugins import BasePlugin


@dataclass
class FeedbackSignal:
    """Feedback from operator on plugin/skill decision."""
    decision_id: str
    decision_type: str  # "routing", "reasoning", etc.
    feedback_type: Literal["outcome", "preference", "confidence"]
    signal: bool | str | float  # yes/no, "prefer_lm", 0.95
    timestamp: int


class FeedbackLoopHandler(BasePlugin):
    """Collects + processes user feedback for learning loop."""

    async def initialize(self) -> None:
        """Load learning infrastructure."""
        self.auto_grade = self.config.get("auto_grade_enabled", True)
        self.logger.info(f"Feedback Loop initialized (auto_grade={self.auto_grade})")

    async def record_feedback(self, signal: FeedbackSignal) -> None:
        """Record feedback + emit to learning backend."""
        # Placeholder: real implementation would emit to event_store
        self.logger.info(f"Feedback recorded: {signal.decision_id} → {signal.feedback_type}={signal.signal}")

    async def auto_grade_decision(self, decision_id: str) -> float:
        """Auto-grade a decision outcome (0.0-1.0)."""
        if not self.auto_grade:
            return 0.5  # Neutral if disabled

        # Placeholder: real implementation would check decision outcome
        return 0.8

    def is_enabled(self) -> bool:
        """Check if plugin is enabled."""
        return getattr(self, "_enabled", True)

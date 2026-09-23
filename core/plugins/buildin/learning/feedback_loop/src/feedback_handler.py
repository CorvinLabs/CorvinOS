"""Feedback loop plugin for learning infrastructure."""
from __future__ import annotations

import re
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

    # HIGH #7: Log injection protection
    LOG_SAFE_PATTERN = re.compile(r'^[a-zA-Z0-9_\-\.]+$')

    @staticmethod
    def _sanitize_for_log(value: str, max_len: int = 256) -> str:
        """Sanitize user input for logging (HIGH #7: log injection protection).

        Only allows alphanumeric, hyphen, underscore, and dot.
        Truncates to max_len to prevent log flooding.

        Args:
            value: String to sanitize
            max_len: Maximum length (default 256)

        Returns:
            Sanitized string safe for logging
        """
        if not isinstance(value, str):
            value = str(value)

        # Truncate first
        value = value[:max_len]

        # Replace unsafe characters with underscores
        if not FeedbackLoopHandler.LOG_SAFE_PATTERN.match(value):
            value = re.sub(r'[^a-zA-Z0-9_\-\.]', '_', value)

        return value

    async def initialize(self) -> None:
        """Load learning infrastructure."""
        self.auto_grade = self.config.get("auto_grade_enabled", True)
        self.logger.info(f"Feedback Loop initialized (auto_grade={self.auto_grade})")

    async def record_feedback(self, signal: FeedbackSignal) -> None:
        """Record feedback + emit to learning backend.

        HIGH #7: Sanitizes decision_id and feedback_type before logging
        to prevent log injection attacks.
        """
        # Placeholder: real implementation would emit to event_store
        safe_decision_id = self._sanitize_for_log(signal.decision_id)
        safe_feedback_type = self._sanitize_for_log(signal.feedback_type)
        self.logger.info(f"Feedback recorded: {safe_decision_id} → {safe_feedback_type}={signal.signal}")

    async def auto_grade_decision(self, decision_id: str) -> float:
        """Auto-grade a decision outcome (0.0-1.0)."""
        if not self.auto_grade:
            return 0.5  # Neutral if disabled

        # Placeholder: real implementation would check decision outcome
        return 0.8

    def is_enabled(self) -> bool:
        """Check if plugin is enabled."""
        return getattr(self, "_enabled", True)

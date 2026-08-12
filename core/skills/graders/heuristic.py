"""Heuristic Skill Grader — rule-based grading (ADR-0307)."""

from __future__ import annotations

from typing import Any

from core.skills.skill import Grade


class HeuristicGrader:
    """Rule-based skill grader (no external calls).

    Scoring rules:
    - Exception raised: 0.2 (skill failed)
    - Execution time > 10s: 0.5 (skill is slow)
    - Execution time 1–10s: 0.7 (skill is moderate)
    - Execution time < 1s + no exception: 0.9 (skill is fast)
    - Default: 0.8 (normal execution)
    """

    def __init__(
        self,
        slow_threshold_s: float = 10.0,
        moderate_threshold_s: float = 1.0,
    ):
        """Initialize with configurable thresholds.

        Args:
            slow_threshold_s: Execution time threshold for "slow" (default 10s)
            moderate_threshold_s: Execution time threshold for "moderate" (default 1s)
        """
        self.slow_threshold = slow_threshold_s
        self.moderate_threshold = moderate_threshold_s

    async def grade(self, request: dict[str, Any]) -> Grade | None:
        """Grade based on heuristic rules.

        Returns:
            Grade with value and feedback, never None (heuristic always grades).
        """
        exception = request.get("exception")
        elapsed = request.get("elapsed", 0.0)

        # Rule 1: Exception → low score
        if exception:
            return Grade(
                value=0.2,
                feedback=f"Skill raised {exception}",
            )

        # Rule 2: Latency-based scoring
        if elapsed > self.slow_threshold:
            return Grade(
                value=0.5,
                feedback=f"Slow execution ({elapsed:.2f}s > {self.slow_threshold}s)",
            )

        if elapsed > self.moderate_threshold:
            return Grade(
                value=0.7,
                feedback=f"Moderate latency ({elapsed:.2f}s)",
            )

        # Rule 3: Fast + no exception
        if elapsed < self.moderate_threshold:
            return Grade(
                value=0.9,
                feedback=f"Fast execution ({elapsed:.3f}s)",
            )

        # Fallback (shouldn't reach here)
        return Grade(value=0.8, feedback="Normal execution")

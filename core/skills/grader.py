"""Skill Grader Protocol and Manager (ADR-0307)."""

from __future__ import annotations

import asyncio
import time
from typing import Any, Protocol

from .skill import Grade, Skill
from .store import SkillStore


class Grader(Protocol):
    """Protocol for pluggable skill grading strategies.

    A grader receives invocation metadata and returns a grade (0.0–1.0)
    or None if unable to grade.
    """

    async def grade(self, request: dict[str, Any]) -> Grade | None:
        """Grade a skill invocation.

        Args:
            request: Invocation metadata dict with keys:
                - skill_name: str
                - skill_version: str
                - output: str (skill output, truncated to ~100 chars)
                - elapsed: float (execution time in seconds)
                - exception: str | None (exception type name if raised)
                - context: dict (user/tenant/session context)
                - timestamp: float (invocation timestamp)

        Returns:
            Grade(value=0.0–1.0, feedback=str) or None if unable to grade.
        """
        ...


class GradingManager:
    """Manages async skill grading loop (ADR-0307).

    Orchestrates:
    1. Pull grading requests from SkillLearningManager.grading_queue
    2. Apply grader strategy
    3. Persist grades to store
    4. Track metrics
    """

    def __init__(self, store: SkillStore, grader: Grader):
        """Initialize grading manager.

        Args:
            store: SkillStore for persistence
            grader: Grader strategy (pluggable)
        """
        self.store = store
        self.grader = grader
        self.graded_count = 0
        self.failed_count = 0
        self.latencies: list[float] = []
        self._lock = asyncio.Lock()

    async def grade_request(self, request: dict[str, Any]) -> bool:
        """Grade a single request and persist the result.

        Args:
            request: Invocation metadata dict

        Returns:
            True if graded successfully, False if failed or skill not found.
        """
        name = request.get("skill_name")
        version = request.get("skill_version")

        if not name or not version:
            return False

        skill = self.store.load(name, version)
        if not skill:
            return False

        try:
            start_t = time.time()
            grade = await self.grader.grade(request)
            latency = time.time() - start_t

            async with self._lock:
                self.latencies.append(latency)
                if grade:
                    skill.add_grade(grade)
                    self.store.save(skill)
                    self.graded_count += 1
                    return True
                else:
                    self.failed_count += 1
                    return False
        except Exception:
            async with self._lock:
                self.failed_count += 1
            return False

    async def run_grading_loop(self, learning_manager: Any, check_interval: float = 0.1) -> None:
        """Run the async grading loop (infinite).

        Pulls from SkillLearningManager.grading_queue and grades requests.

        Args:
            learning_manager: SkillLearningManager instance (has grading_queue)
            check_interval: Sleep duration between queue checks (seconds)
        """
        while True:
            try:
                request = learning_manager.grading_queue.get(blocking=False)
                if request:
                    await self.grade_request(request)
                else:
                    await asyncio.sleep(check_interval)
            except Exception:
                await asyncio.sleep(check_interval)

    def get_stats(self) -> dict[str, Any]:
        """Get grading statistics.

        Returns:
            Dict with:
            - graded_count: int
            - failed_count: int
            - avg_latency: float (seconds)
            - total_latency: float
        """
        avg_latency = (
            sum(self.latencies) / len(self.latencies)
            if self.latencies
            else 0.0
        )
        return {
            "graded_count": self.graded_count,
            "failed_count": self.failed_count,
            "avg_latency": avg_latency,
            "total_latency": sum(self.latencies),
            "total_requests": self.graded_count + self.failed_count,
        }

    def reset_stats(self) -> None:
        """Reset all metrics."""
        self.graded_count = 0
        self.failed_count = 0
        self.latencies.clear()

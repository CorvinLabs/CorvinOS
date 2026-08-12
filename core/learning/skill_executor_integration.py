"""SkillExecutor Integration — measure latency and capture metrics (Phase 4)."""

import time
from typing import Optional, Callable, Any
from .skill_integration import SkillLearningHooks


class SkillExecutorWithLearning:
    """Wrapper: SkillExecutor with latency measurement & learning capture."""

    def __init__(self, hooks: SkillLearningHooks):
        """Initialize executor with learning hooks.

        Args:
            hooks: SkillLearningHooks for event emission
        """
        self.hooks = hooks

    async def execute_skill(
        self,
        skill_name: str,
        skill_fn: Callable,
        decision_id: str,
        session_id: str,
    ) -> Any:
        """Execute skill and measure latency.

        Args:
            skill_name: Name of skill being executed
            skill_fn: Async callable to execute
            decision_id: Decision ID from selection
            session_id: Session ID for event

        Returns:
            Result from skill_fn
        """
        start_time = time.perf_counter()

        try:
            # Execute skill
            result = await skill_fn()
        finally:
            # Always measure latency, even on error
            elapsed_ms = (time.perf_counter() - start_time) * 1000

            # Emit learning signal
            await self.hooks.on_skill_executed(
                decision_id=decision_id,
                session_id=session_id,
                skill_name=skill_name,
                latency_ms=elapsed_ms,
            )

        return result

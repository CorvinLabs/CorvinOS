"""SkillExecutorWithLearning — measure latency and capture performance signals (Phase 4)."""

import time
from typing import Any, Optional
from .skill_integration import SkillLearningHooks


class SkillExecutorWithLearning:
    """Wraps skill execution to capture performance signals."""

    def __init__(self, tenant_id: str, hooks: SkillLearningHooks):
        """Initialize executor with learning integration.

        Args:
            tenant_id: Tenant ID
            hooks: SkillLearningHooks instance
        """
        self.tenant_id = tenant_id
        self.hooks = hooks

    async def execute_skill(
        self,
        skill_name: str,
        decision_id: str,
        *args,
        session_id: str = "none",
        **kwargs,
    ) -> Any:
        """Execute skill and capture latency signal.

        Args:
            skill_name: Name of skill to execute
            decision_id: Decision ID from selection phase (for linkage)
            *args: Skill args
            **kwargs: Skill kwargs

        Returns:
            Skill result
        """
        start_time = time.perf_counter()

        # Mock execution (real impl would call skill.run(*args, **kwargs))
        result = {"status": "ok", "output": f"Result of {skill_name}"}

        elapsed_ms = (time.perf_counter() - start_time) * 1000

        # Capture latency via learning hook
        await self.hooks.on_skill_executed(
            skill_name=skill_name,
            decision_id=decision_id,
            session_id=session_id,
            latency_ms=elapsed_ms,
        )

        return result

"""SkillSelectorWithLearning — capture decision signals during skill selection (Phase 4)."""

from typing import Optional, Tuple
from .skill_integration import SkillLearningHooks
from .decision_history import DecisionRecorder


class SkillSelectorWithLearning:
    """Wraps skill selection to capture learning signals."""

    def __init__(self, tenant_id: str, hooks: SkillLearningHooks):
        """Initialize selector with learning integration.

        Args:
            tenant_id: Tenant ID
            hooks: SkillLearningHooks instance
        """
        self.tenant_id = tenant_id
        self.hooks = hooks
        self.decision_recorder = DecisionRecorder(tenant_id)

    async def select_skill(
        self,
        candidates: list[str],
        session_id: str,
        confidence_score: Optional[float] = None,
        reasoning: Optional[str] = None,
    ) -> Tuple[str, str]:
        """Select a skill and capture decision signal.

        Args:
            candidates: Available skills
            session_id: Session ID
            confidence_score: Confidence (0.0-1.0)
            reasoning: Reasoning string

        Returns:
            (chosen_skill, decision_id) tuple for outcome linking
        """
        # Mock selection logic (real impl would use skill ranking)
        chosen = candidates[0] if candidates else "default"

        # Capture decision via learning hook
        decision_id = await self.hooks.on_skill_selection(
            candidates=candidates,
            chosen=chosen,
            session_id=session_id,
            confidence_score=confidence_score,
            reasoning=reasoning,
        )

        return chosen, decision_id

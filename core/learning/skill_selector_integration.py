"""SkillSelector Integration — capture decision_id when skill selected (Phase 4)."""

from typing import Optional
from .skill_integration import SkillLearningHooks
from .event_emitter import EventEmitter


class SkillSelectorWithLearning:
    """Wrapper: SkillSelector with learning signal capture."""

    def __init__(self, hooks: SkillLearningHooks):
        """Initialize selector with learning hooks.

        Args:
            hooks: SkillLearningHooks for event emission
        """
        self.hooks = hooks

    async def select_skill(
        self,
        candidates: list[str],
        confidence_scores: Optional[dict[str, float]] = None,
        reasoning: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> tuple[str, str]:
        """Select best skill and capture decision.

        Args:
            candidates: Available skill names
            confidence_scores: Optional {skill_name: score} dict
            reasoning: Why this skill was chosen
            session_id: Session ID for event

        Returns:
            (chosen_skill, decision_id)
        """
        # TODO: Call actual skill selector logic
        # For now: select first (highest confidence)
        chosen = candidates[0]
        confidence = None
        if confidence_scores:
            confidence = confidence_scores.get(chosen, 0.5)

        # Emit learning signal
        decision_id = await self.hooks.on_skill_selection(
            candidates=candidates,
            chosen=chosen,
            session_id=session_id or "default",
            confidence_score=confidence,
            reasoning=reasoning,
        )

        return chosen, decision_id

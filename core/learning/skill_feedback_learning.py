"""SkillFeedbackWithLearning — capture user feedback and link to outcomes (Phase 4)."""

from typing import Optional
from .skill_integration import SkillLearningHooks
from .outcome_feedback import OutcomeType


class SkillFeedbackWithLearning:
    """Wraps feedback collection to capture outcome signals."""

    def __init__(self, tenant_id: str, hooks: SkillLearningHooks):
        """Initialize feedback with learning integration.

        Args:
            tenant_id: Tenant ID
            hooks: SkillLearningHooks instance
        """
        self.tenant_id = tenant_id
        self.hooks = hooks

    async def record_feedback(
        self,
        decision_id: str,
        feedback_text: str,
        rating: int = 3,  # 1-5
        session_id: str = "none",
    ) -> None:
        """Record user feedback and capture outcome signal.

        Args:
            decision_id: Decision ID from selection (for linkage)
            feedback_text: User feedback ("good", "bad", "partial", etc.)
            rating: User rating (1-5)
        """
        # Map feedback text to outcome type
        outcome_map = {
            "good": OutcomeType.SUCCESS,
            "success": OutcomeType.SUCCESS,
            "bad": OutcomeType.FAILURE,
            "fail": OutcomeType.FAILURE,
            "partial": OutcomeType.PARTIAL,
            "ok": OutcomeType.PARTIAL,
        }

        outcome = outcome_map.get(
            feedback_text.lower().strip(),
            OutcomeType.PARTIAL,
        )

        # Capture outcome via learning hook
        await self.hooks.on_skill_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            user_feedback=feedback_text,
            rating=rating,
        )

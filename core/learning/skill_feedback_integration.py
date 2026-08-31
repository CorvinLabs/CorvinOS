"""SkillFeedback Integration — capture outcome when user responds (Phase 4)."""

from typing import Optional
from .skill_integration import SkillLearningHooks
from .outcome_feedback import OutcomeType


class SkillFeedbackWithLearning:
    """Wrapper: SkillFeedback with learning signal capture."""

    def __init__(self, hooks: SkillLearningHooks):
        """Initialize feedback handler with learning hooks.

        Args:
            hooks: SkillLearningHooks for event emission
        """
        self.hooks = hooks

    async def record_feedback(
        self,
        decision_id: str,
        session_id: str,
        user_response: str,
        rating: Optional[int] = None,
    ) -> None:
        """Record user feedback on skill execution.

        Args:
            decision_id: Decision ID from selection
            session_id: Session ID
            user_response: User's feedback ("good", "bad", "partial", etc.)
            rating: Optional numeric rating (1-5)
        """
        # Map user response to outcome type
        outcome_map = {
            "good": OutcomeType.SUCCESS,
            "bad": OutcomeType.FAILURE,
            "partial": OutcomeType.PARTIAL,
            "success": OutcomeType.SUCCESS,
            "failure": OutcomeType.FAILURE,
        }

        outcome = outcome_map.get(user_response.lower(), OutcomeType.PARTIAL)

        # Emit learning signal
        await self.hooks.on_skill_outcome(
            decision_id=decision_id,
            session_id=session_id,
            outcome=outcome,
            user_feedback=user_response,
            rating=rating,
        )

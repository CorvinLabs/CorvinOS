"""Subsystem integration for VoiceChannelCoordinator.

Provides a simple interface for Brain subsystems to ask user questions.

ADR-0352: Bidirectional Voice Channel
"""

import asyncio
import logging
from typing import Optional

from .question_types import UserQuestion, UserAnswer, QuestionPriority
from .bidirectional_coordinator import VoiceChannelCoordinator

logger = logging.getLogger(__name__)


class SubsystemVoiceAPI:
    """API for subsystems to interact with the voice channel coordinator."""

    def __init__(self, coordinator: VoiceChannelCoordinator):
        """Initialize subsystem voice API.

        Args:
            coordinator: VoiceChannelCoordinator instance
        """
        self.coordinator = coordinator
        self.subsystem_id: Optional[str] = None
        self.pending_answers: dict[str, UserAnswer] = {}

    async def ask_user(
        self,
        question_text: str,
        timeout_seconds: int = 10,
        default_answer: Optional[str] = None,
        priority: QuestionPriority = QuestionPriority.NORMAL,
    ) -> Optional[UserAnswer]:
        """Ask the user a question via voice.

        Args:
            question_text: Question to ask
            timeout_seconds: Max time to wait for answer
            default_answer: Default if no answer provided
            priority: Question priority in queue

        Returns:
            UserAnswer if provided, None on timeout/error
        """
        if not self.subsystem_id:
            logger.error("Subsystem ID not set; cannot ask user")
            return None

        # Create question
        question = UserQuestion(
            question_text=question_text,
            subsystem_id=self.subsystem_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            default_answer=default_answer,
        )

        # Ask via coordinator
        answer = await self.coordinator.ask_user(
            question_text=question_text,
            subsystem_id=self.subsystem_id,
            priority=priority,
            timeout_seconds=timeout_seconds,
            default_answer=default_answer,
        )

        if answer:
            self.pending_answers[answer.id] = answer
            logger.debug(f"Received answer to question: {answer.answer_text}")

        return answer

    def set_subsystem_id(self, subsystem_id: str) -> None:
        """Set the ID of this subsystem.

        Args:
            subsystem_id: Unique identifier (e.g., "CostController")
        """
        self.subsystem_id = subsystem_id
        logger.debug(f"Subsystem ID set to: {subsystem_id}")

    def get_subsystem_id(self) -> Optional[str]:
        """Get this subsystem's ID."""
        return self.subsystem_id


class CostControllerVoiceExtension:
    """Voice extension for CostController subsystem."""

    def __init__(self, voice_api: SubsystemVoiceAPI):
        """Initialize cost controller voice extension.

        Args:
            voice_api: SubsystemVoiceAPI instance
        """
        self.voice_api = voice_api
        self.voice_api.set_subsystem_id("CostController")

    async def ask_model_preference(self) -> Optional[str]:
        """Ask user to choose between model options.

        Returns:
            Selected model name, or None
        """
        answer = await self.voice_api.ask_user(
            question_text="Would you prefer Opus for better quality, or Sonnet for faster responses?",
            timeout_seconds=10,
            default_answer="Sonnet",
            priority=QuestionPriority.HIGH,
        )

        if answer:
            return answer.answer_text
        return None

    async def confirm_budget_increase(self, new_budget: float) -> Optional[bool]:
        """Ask user to confirm a budget increase.

        Args:
            new_budget: Proposed new budget

        Returns:
            True if confirmed, False if rejected, None on timeout
        """
        answer = await self.voice_api.ask_user(
            question_text=f"Increase budget to ${new_budget:.2f}? Say yes or no.",
            timeout_seconds=5,
            default_answer="no",
            priority=QuestionPriority.NORMAL,
        )

        if answer:
            return answer.answer_text.lower() in ["yes", "yeah", "ok", "okay"]
        return None


class LoopEngineerVoiceExtension:
    """Voice extension for LoopEngineer subsystem."""

    def __init__(self, voice_api: SubsystemVoiceAPI):
        """Initialize loop engineer voice extension.

        Args:
            voice_api: SubsystemVoiceAPI instance
        """
        self.voice_api = voice_api
        self.voice_api.set_subsystem_id("LoopEngineer")

    async def ask_strategy_preference(self) -> Optional[str]:
        """Ask user for strategy preference.

        Returns:
            Strategy name, or None
        """
        answer = await self.voice_api.ask_user(
            question_text="Try decomposing the problem, or take a step back?",
            timeout_seconds=10,
            default_answer="decompose",
            priority=QuestionPriority.HIGH,
        )

        if answer:
            return answer.answer_text
        return None

    async def ask_continue_or_retry(self) -> Optional[bool]:
        """Ask user whether to continue or retry current step.

        Returns:
            True to continue, False to retry, None on timeout
        """
        answer = await self.voice_api.ask_user(
            question_text="Continue with this approach, or retry the last step?",
            timeout_seconds=5,
            default_answer="continue",
            priority=QuestionPriority.NORMAL,
        )

        if answer:
            return answer.answer_text.lower() in ["continue", "yes", "yeah"]
        return None


class HealthMonitorVoiceExtension:
    """Voice extension for HealthMonitor subsystem."""

    def __init__(self, voice_api: SubsystemVoiceAPI):
        """Initialize health monitor voice extension.

        Args:
            voice_api: SubsystemVoiceAPI instance
        """
        self.voice_api = voice_api
        self.voice_api.set_subsystem_id("HealthMonitor")

    async def ask_health_check_action(self, issue: str) -> Optional[str]:
        """Ask user what to do about a health issue.

        Args:
            issue: Description of health issue

        Returns:
            Action to take, or None
        """
        answer = await self.voice_api.ask_user(
            question_text=f"Health issue detected: {issue}. Continue anyway, or pause?",
            timeout_seconds=10,
            default_answer="pause",
            priority=QuestionPriority.CRITICAL,
        )

        if answer:
            return answer.answer_text.lower()
        return None

"""Bidirectional voice channel subsystem for Voice-Native Midstream Guidance.

Enables Brain subsystems to ask questions and receive user answers via voice.
Features: question queue with priority, TTS/STT fallback, timeout handling.

ADR-0352: Bidirectional Voice Channel
"""

from .bidirectional_coordinator import VoiceChannelCoordinator
from .question_queue import QuestionQueue
from .question_types import (
    UserQuestion,
    UserAnswer,
    QuestionState,
    QuestionPriority,
)

__all__ = [
    "VoiceChannelCoordinator",
    "QuestionQueue",
    "UserQuestion",
    "UserAnswer",
    "QuestionState",
    "QuestionPriority",
]

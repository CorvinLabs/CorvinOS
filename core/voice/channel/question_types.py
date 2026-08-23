"""Data types for bidirectional voice channel.

Question and answer structures with state tracking for queue management.

ADR-0352: Bidirectional Voice Channel
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional
from uuid import uuid4


class QuestionPriority(Enum):
    """Priority levels for question queue."""
    CRITICAL = 4
    HIGH = 3
    NORMAL = 2
    LOW = 1


class QuestionState(Enum):
    """States of a question in the queue."""
    PENDING = "pending"  # Enqueued, waiting to be active
    ACTIVE = "active"  # Currently being asked to user
    ANSWERED = "answered"  # User provided answer
    EXPIRED = "expired"  # Timeout, using default
    CANCELLED = "cancelled"  # Cancelled by subsystem


@dataclass(frozen=True)
class UserQuestion:
    """A question asked by a Brain subsystem to the user."""
    id: str = field(default_factory=lambda: str(uuid4()))
    question_text: str = ""
    subsystem_id: str = ""  # e.g., "CostController", "LoopEngineer"
    priority: QuestionPriority = QuestionPriority.NORMAL
    timeout_seconds: int = 10
    allow_text_fallback: bool = True
    default_answer: Optional[str] = None
    created_at: datetime = field(default_factory=datetime.utcnow)
    context: dict = field(default_factory=dict)  # Additional metadata

    def is_expired(self, ttl_seconds: int = 30) -> bool:
        """Check if question has exceeded TTL."""
        elapsed = (datetime.utcnow() - self.created_at).total_seconds()
        return elapsed > ttl_seconds


@dataclass(frozen=True)
class UserAnswer:
    """Answer provided by user to a question."""
    id: str = field(default_factory=lambda: str(uuid4()))
    question_id: str = ""
    answer_text: str = ""
    answer_confidence: float = 0.0  # STT confidence 0.0–1.0
    channel: str = "voice"  # "voice" or "text"
    corrected: bool = False  # Was answer confirmed by user?
    created_at: datetime = field(default_factory=datetime.utcnow)

    def is_confident(self, threshold: float = 0.70) -> bool:
        """Check if answer confidence exceeds threshold."""
        return self.answer_confidence >= threshold


@dataclass
class QuestionMetrics:
    """Metrics for question lifecycle."""
    total_questions: int = 0
    questions_answered: int = 0
    questions_expired: int = 0
    questions_cancelled: int = 0
    avg_time_to_answer_ms: float = 0.0
    max_queue_depth: int = 0
    text_fallback_count: int = 0
    confidence_scores: list = field(default_factory=list)

    def record_question_answered(self, latency_ms: float):
        """Record a successfully answered question."""
        self.questions_answered += 1
        self.confidence_scores.append(latency_ms)
        if len(self.confidence_scores) > 1:
            self.avg_time_to_answer_ms = sum(self.confidence_scores) / len(
                self.confidence_scores
            )

    def record_text_fallback(self):
        """Record TTS fallback to text."""
        self.text_fallback_count += 1

    def record_queue_depth(self, depth: int):
        """Record current queue depth."""
        if depth > self.max_queue_depth:
            self.max_queue_depth = depth

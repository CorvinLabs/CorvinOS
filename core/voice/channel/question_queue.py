"""Priority queue for managing user questions.

Handles enqueuing, prioritization, TTL expiration, and overflow.

ADR-0352: Bidirectional Voice Channel
"""

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Optional

from .question_types import UserQuestion, QuestionState, QuestionPriority, QuestionMetrics

logger = logging.getLogger(__name__)


class QuestionQueue:
    """Thread-safe priority queue for voice questions."""

    MAX_QUEUE_SIZE = 10
    DEFAULT_TTL_SECONDS = 30

    def __init__(self, max_size: int = MAX_QUEUE_SIZE, ttl_seconds: int = DEFAULT_TTL_SECONDS):
        """Initialize question queue.

        Args:
            max_size: Maximum queue size (older low-priority dropped on overflow)
            ttl_seconds: Question TTL before auto-expiration
        """
        self.queue: list[tuple[UserQuestion, QuestionState]] = []
        self.active_question_id: Optional[str] = None
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self.metrics = QuestionMetrics()
        self.lock = asyncio.Lock()

    async def enqueue(self, question: UserQuestion) -> bool:
        """Enqueue a question. Returns False if dropped due to overflow.

        Args:
            question: Question to enqueue

        Returns:
            True if enqueued, False if dropped instead
        """
        async with self.lock:
            if len(self.queue) >= self.max_size:
                # Drop oldest low-priority question to make room
                dropped = self._drop_lowest_priority()
                if dropped:
                    logger.warning(
                        f"Question queue full; dropped {dropped.id} "
                        f"(priority={dropped.priority.name})"
                    )
                    # Continue to add the new question

            self.queue.append((question, QuestionState.PENDING))
            self.queue.sort(key=lambda x: x[0].priority.value, reverse=True)
            self.metrics.total_questions += 1
            self.metrics.record_queue_depth(len(self.queue))
            logger.debug(f"Enqueued question {question.id} (priority={question.priority.name})")
            return True

    async def get_active_question(self) -> Optional[UserQuestion]:
        """Get the highest-priority pending question and mark as active.

        Returns:
            Question to ask, or None if queue empty
        """
        async with self.lock:
            # Expire old questions
            self._expire_old_questions()

            # Find first pending question
            for i, (q, state) in enumerate(self.queue):
                if state == QuestionState.PENDING:
                    self.queue[i] = (q, QuestionState.ACTIVE)
                    self.active_question_id = q.id
                    logger.debug(f"Activated question {q.id}")
                    return q

            return None

    async def answer_question(
        self, question_id: str, answer_text: str, confidence: float = 0.0
    ) -> bool:
        """Mark question as answered and remove from queue.

        Args:
            question_id: Question ID
            answer_text: User's answer
            confidence: STT confidence [0.0–1.0]

        Returns:
            True if found and answered, False otherwise
        """
        async with self.lock:
            for i, (q, state) in enumerate(self.queue):
                if q.id == question_id:
                    if state != QuestionState.ACTIVE:
                        logger.warning(
                            f"Answering non-active question {question_id} "
                            f"(state={state.value})"
                        )

                    self.queue.pop(i)
                    self.active_question_id = None
                    self.metrics.record_question_answered(
                        (datetime.utcnow() - q.created_at).total_seconds() * 1000
                    )
                    logger.debug(f"Answered question {question_id}")
                    return True

            logger.warning(f"Question {question_id} not found in queue")
            return False

    async def expire_question(self, question_id: str) -> Optional[str]:
        """Mark question as expired (timeout); use default answer.

        Args:
            question_id: Question ID

        Returns:
            Default answer if available, None otherwise
        """
        async with self.lock:
            for i, (q, state) in enumerate(self.queue):
                if q.id == question_id:
                    self.queue.pop(i)
                    self.active_question_id = None
                    self.metrics.questions_expired += 1
                    logger.warning(f"Question {question_id} expired; using default")
                    return q.default_answer

            logger.warning(f"Question {question_id} not found for expiration")
            return None

    async def cancel_question(self, question_id: str) -> bool:
        """Cancel a pending question.

        Args:
            question_id: Question ID

        Returns:
            True if cancelled, False if not found
        """
        async with self.lock:
            for i, (q, state) in enumerate(self.queue):
                if q.id == question_id:
                    self.queue.pop(i)
                    self.active_question_id = None
                    self.metrics.questions_cancelled += 1
                    logger.debug(f"Cancelled question {question_id}")
                    return True

            return False

    def _drop_lowest_priority(self) -> Optional[UserQuestion]:
        """Drop the lowest-priority question from queue.

        Drops oldest PENDING question with lowest priority.
        """
        if not self.queue:
            return None

        # Find lowest-priority pending question
        # Sort by (state, priority, age): pending first, lowest priority, oldest first
        pending_questions = [
            (i, q, state) for i, (q, state) in enumerate(self.queue)
            if state == QuestionState.PENDING
        ]

        if pending_questions:
            # Sort by priority (ascending = lowest first), then by creation time (oldest first)
            pending_questions.sort(
                key=lambda x: (x[1].priority.value, x[1].created_at)
            )
            idx, q, _ = pending_questions[0]
            self.queue.pop(idx)
            return q

        # If no pending, drop the last active question (shouldn't happen often)
        if self.queue:
            q, _ = self.queue.pop()
            return q

        return None

    def _expire_old_questions(self) -> None:
        """Remove questions that exceed TTL."""
        expired = []
        now = datetime.utcnow()

        for q, state in self.queue:
            if state != QuestionState.ANSWERED:
                age = (now - q.created_at).total_seconds()
                if age > self.ttl_seconds:
                    expired.append(q.id)

        for q_id in expired:
            for i, (q, state) in enumerate(self.queue):
                if q.id == q_id:
                    self.queue.pop(i)
                    self.metrics.questions_expired += 1
                    logger.debug(f"Auto-expired question {q_id} (age={age:.1f}s)")
                    break

    async def get_metrics(self) -> dict:
        """Return queue metrics."""
        async with self.lock:
            return {
                "total_questions": self.metrics.total_questions,
                "questions_answered": self.metrics.questions_answered,
                "questions_expired": self.metrics.questions_expired,
                "questions_cancelled": self.metrics.questions_cancelled,
                "current_queue_size": len(self.queue),
                "max_queue_depth": self.metrics.max_queue_depth,
                "text_fallback_count": self.metrics.text_fallback_count,
                "avg_time_to_answer_ms": self.metrics.avg_time_to_answer_ms,
            }

    async def get_queue_size(self) -> int:
        """Return current queue size."""
        async with self.lock:
            return len(self.queue)

    async def is_empty(self) -> bool:
        """Check if queue is empty."""
        async with self.lock:
            return len(self.queue) == 0

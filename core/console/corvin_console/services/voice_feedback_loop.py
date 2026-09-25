"""
Voice Summary Feedback Loop (Phase 2c)
Collects user feedback on summary quality + learns + improves strategies

@date 2026-09-25
@phase Phase 2c: Learning Loop
"""

from typing import Optional, Dict, Any
from datetime import datetime
from enum import Enum


class FeedbackType(str, Enum):
    """Feedback signal types (Phase 2c)"""
    QUALITY_RATING = "quality_rating"  # User rates summary 0-5
    STRATEGY_PREFERENCE = "strategy_preference"  # User prefers different strategy
    CORRECTION = "correction"  # User corrected the summary
    PARTIAL_HELPFUL = "partial_helpful"  # Some parts helpful, some not


class SummaryFeedback:
    """Feedback on a generated summary"""

    def __init__(self, session_id: str, feedback_type: FeedbackType, score: Optional[float] = None, comment: Optional[str] = None):
        self.session_id = session_id
        self.feedback_type = feedback_type
        self.score = score  # 0-5 for quality rating
        self.comment = comment
        self.timestamp = datetime.utcnow()

    def to_dict(self) -> Dict[str, Any]:
        return {
            "session_id": self.session_id,
            "feedback_type": self.feedback_type.value,
            "score": self.score,
            "comment": self.comment,
            "timestamp": self.timestamp.isoformat(),
        }


class VoiceFeedbackStore:
    """
    Simple in-memory feedback store (Phase 2c MVP).
    Phase 3: Persist to database + train model on feedback.
    """

    def __init__(self):
        self._feedback: Dict[str, list[SummaryFeedback]] = {}
        self._quality_history: Dict[str, list[float]] = {}

    def record_feedback(self, feedback: SummaryFeedback):
        """Record feedback for a session"""
        if feedback.session_id not in self._feedback:
            self._feedback[feedback.session_id] = []
            self._quality_history[feedback.session_id] = []

        self._feedback[feedback.session_id].append(feedback)

        if feedback.score is not None:
            self._quality_history[feedback.session_id].append(feedback.score)

    def get_average_quality(self, session_id: str) -> Optional[float]:
        """Get average quality score for a session"""
        if session_id not in self._quality_history:
            return None

        scores = self._quality_history[session_id]
        return sum(scores) / len(scores) if scores else None

    def get_feedback_for_session(self, session_id: str) -> list[SummaryFeedback]:
        """Retrieve all feedback for a session"""
        return self._feedback.get(session_id, [])

    def get_learning_signal(self, strategy: str) -> Dict[str, Any]:
        """
        Aggregate learning signal for a strategy (Phase 2c MVP).
        Phase 3: Use this to retrain summary model.
        """
        strategy_sessions = {}

        for session_id, feedback_list in self._feedback.items():
            # TODO: Map session to strategy used
            # For now: simple aggregation
            for fb in feedback_list:
                if fb.score is not None:
                    if strategy not in strategy_sessions:
                        strategy_sessions[strategy] = []
                    strategy_sessions[strategy].append(fb.score)

        scores = strategy_sessions.get(strategy, [])
        avg_score = sum(scores) / len(scores) if scores else 0.0

        return {
            "strategy": strategy,
            "sample_count": len(scores),
            "average_quality": avg_score,
            "recommendation": "improve" if avg_score < 3.0 else "maintain",
        }


# Singleton feedback store
_feedback_store: Optional[VoiceFeedbackStore] = None


def get_feedback_store() -> VoiceFeedbackStore:
    """Get or create feedback store"""
    global _feedback_store
    if _feedback_store is None:
        _feedback_store = VoiceFeedbackStore()
    return _feedback_store


class TaskCollisionDetector:
    """
    Detects task collisions between user and invited agents (Phase 2c).

    Prevents:
    - Both user and agent working on same task
    - Race conditions in task assignment
    - Duplicate work
    """

    def __init__(self):
        self._user_tasks: Dict[str, set] = {}  # session_id → {task_ids}
        self._agent_tasks: Dict[str, set] = {}  # session_id → {task_ids}

    def register_user_task(self, session_id: str, task_id: str):
        """Register a task initiated by user"""
        if session_id not in self._user_tasks:
            self._user_tasks[session_id] = set()
        self._user_tasks[session_id].add(task_id)

    def register_agent_task(self, session_id: str, task_id: str) -> bool:
        """
        Register a task initiated by agent.
        Returns False if collision detected.
        """
        if session_id not in self._agent_tasks:
            self._agent_tasks[session_id] = set()

        # Check for collision
        user_tasks = self._user_tasks.get(session_id, set())
        if task_id in user_tasks:
            return False  # Collision!

        self._agent_tasks[session_id].add(task_id)
        return True

    def get_user_tasks(self, session_id: str) -> set:
        """Get all tasks owned by user in session"""
        return self._user_tasks.get(session_id, set())

    def get_agent_tasks(self, session_id: str) -> set:
        """Get all tasks owned by agent in session"""
        return self._agent_tasks.get(session_id, set())

    def clear_session(self, session_id: str):
        """Clear task tracking for completed session"""
        self._user_tasks.pop(session_id, None)
        self._agent_tasks.pop(session_id, None)


# Singleton collision detector
_collision_detector: Optional[TaskCollisionDetector] = None


def get_collision_detector() -> TaskCollisionDetector:
    """Get or create collision detector"""
    global _collision_detector
    if _collision_detector is None:
        _collision_detector = TaskCollisionDetector()
    return _collision_detector

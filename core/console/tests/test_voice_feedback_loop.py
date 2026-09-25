"""
Voice Feedback Loop + Task Collision Tests (Phase 2c)
"""

import pytest
from core.console.corvin_console.services.voice_feedback_loop import (
    get_feedback_store,
    get_collision_detector,
    SummaryFeedback,
    FeedbackType,
)


class TestVoiceFeedbackLoopPhase2c:
    """Phase 2c: Learning from user feedback"""

    def test_record_feedback(self):
        """Test: Record user feedback on summary"""
        store = get_feedback_store()

        feedback = SummaryFeedback(
            session_id="session-001",
            feedback_type=FeedbackType.QUALITY_RATING,
            score=4.5,
            comment="Summary was accurate and concise",
        )

        store.record_feedback(feedback)

        feedback_list = store.get_feedback_for_session("session-001")
        assert len(feedback_list) == 1
        assert feedback_list[0].score == 4.5

    def test_average_quality(self):
        """Test: Calculate average feedback score"""
        store = get_feedback_store()

        for score in [4.0, 5.0, 3.0]:
            fb = SummaryFeedback(
                session_id="session-002",
                feedback_type=FeedbackType.QUALITY_RATING,
                score=score,
            )
            store.record_feedback(fb)

        avg = store.get_average_quality("session-002")
        assert avg == 4.0  # (4+5+3)/3 = 4

    def test_learning_signal(self):
        """Test: Generate learning signal for strategy"""
        store = get_feedback_store()

        fb = SummaryFeedback(
            session_id="session-003",
            feedback_type=FeedbackType.QUALITY_RATING,
            score=3.5,
        )
        store.record_feedback(fb)

        signal = store.get_learning_signal("syntax_aware")
        assert signal["strategy"] == "syntax_aware"
        assert signal["sample_count"] >= 0


class TestTaskCollisionDetectionPhase2c:
    """Phase 2c: Prevent task collisions between user and agent"""

    def test_register_user_task(self):
        """Test: Register task initiated by user"""
        detector = get_collision_detector()

        detector.register_user_task("session-001", "task-001")

        user_tasks = detector.get_user_tasks("session-001")
        assert "task-001" in user_tasks

    def test_register_agent_task_no_collision(self):
        """Test: Register agent task when no collision"""
        detector = get_collision_detector()
        detector.clear_session("session-002")  # Start fresh

        success = detector.register_agent_task("session-002", "task-002")
        assert success is True

        agent_tasks = detector.get_agent_tasks("session-002")
        assert "task-002" in agent_tasks

    def test_detect_collision(self):
        """Test: Detect collision when user and agent work on same task"""
        detector = get_collision_detector()
        detector.clear_session("session-003")  # Start fresh

        # User starts task
        detector.register_user_task("session-003", "task-conflict")

        # Agent tries same task
        success = detector.register_agent_task("session-003", "task-conflict")
        assert success is False  # Collision detected!

    def test_session_isolation(self):
        """Test: Tasks in different sessions don't collide"""
        detector = get_collision_detector()
        detector.clear_session("session-a")
        detector.clear_session("session-b")

        detector.register_user_task("session-a", "task-x")
        success = detector.register_agent_task("session-b", "task-x")

        assert success is True  # No collision across sessions

    def test_clear_session(self):
        """Test: Clear tracking for completed session"""
        detector = get_collision_detector()

        detector.register_user_task("session-completed", "task-old")
        detector.register_agent_task("session-completed", "task-new")

        detector.clear_session("session-completed")

        assert len(detector.get_user_tasks("session-completed")) == 0
        assert len(detector.get_agent_tasks("session-completed")) == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

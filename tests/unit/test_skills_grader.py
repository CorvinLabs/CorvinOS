"""Unit tests for Skill Grader system (ADR-0307)."""

import asyncio
from unittest.mock import AsyncMock, MagicMock

import pytest

from core.skills import Grade, InMemorySkillStore, Skill
from core.skills.grader import GradingManager


class DummyGrader:
    """Simple test grader that returns fixed scores."""

    def __init__(self, score: float = 0.8, fail: bool = False):
        self.score = score
        self.fail = fail

    async def grade(self, request: dict) -> Grade | None:
        if self.fail:
            return None
        return Grade(value=self.score, feedback="test grade")


class TestGradingManager:
    """GradingManager orchestration tests."""

    def test_init(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        assert manager.store is store
        assert manager.grader is grader
        assert manager.graded_count == 0
        assert manager.failed_count == 0

    @pytest.mark.asyncio
    async def test_grade_request_success(self):
        store = InMemorySkillStore()
        grader = DummyGrader(score=0.9)
        manager = GradingManager(store, grader)

        # Register skill
        skill = Skill(name="test", version="1.0", body="code")
        store.save(skill)

        # Grade it
        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.1,
        }
        result = await manager.grade_request(request)

        assert result is True
        assert manager.graded_count == 1

        # Verify grade persisted
        updated = store.load("test", "1.0")
        assert updated.mean_score == 0.9

    @pytest.mark.asyncio
    async def test_grade_request_grader_fails(self):
        store = InMemorySkillStore()
        grader = DummyGrader(fail=True)
        manager = GradingManager(store, grader)

        skill = Skill(name="test", version="1.0", body="code")
        store.save(skill)

        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.1,
        }
        result = await manager.grade_request(request)

        assert result is False
        assert manager.failed_count == 1
        assert manager.graded_count == 0

    @pytest.mark.asyncio
    async def test_grade_request_skill_not_found(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        request = {
            "skill_name": "nonexistent",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.1,
        }
        result = await manager.grade_request(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_grade_request_missing_name(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        request = {"skill_version": "1.0"}
        result = await manager.grade_request(request)

        assert result is False

    @pytest.mark.asyncio
    async def test_get_stats_empty(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        stats = manager.get_stats()
        assert stats["graded_count"] == 0
        assert stats["failed_count"] == 0
        assert stats["avg_latency"] == 0.0

    @pytest.mark.asyncio
    async def test_get_stats_with_grades(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        skill = Skill(name="test", version="1.0", body="code")
        store.save(skill)

        # Grade twice
        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.1,
        }
        await manager.grade_request(request)
        await manager.grade_request(request)

        stats = manager.get_stats()
        assert stats["graded_count"] == 2
        assert stats["failed_count"] == 0
        assert stats["avg_latency"] > 0

    @pytest.mark.asyncio
    async def test_reset_stats(self):
        store = InMemorySkillStore()
        grader = DummyGrader()
        manager = GradingManager(store, grader)

        skill = Skill(name="test", version="1.0", body="code")
        store.save(skill)

        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.1,
        }
        await manager.grade_request(request)

        stats_before = manager.get_stats()
        assert stats_before["graded_count"] == 1

        manager.reset_stats()
        stats_after = manager.get_stats()
        assert stats_after["graded_count"] == 0
        assert stats_after["failed_count"] == 0

    @pytest.mark.asyncio
    async def test_run_grading_loop_one_iteration(self):
        store = InMemorySkillStore()
        grader = DummyGrader(score=0.7)
        manager = GradingManager(store, grader)

        # Register skill
        skill = Skill(name="test", version="1.0", body="code")
        store.save(skill)

        # Create mock learning manager
        mock_learning_manager = MagicMock()
        mock_queue = MagicMock()
        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.05,
        }
        mock_queue.get.side_effect = [request, None]  # Return request, then None
        mock_learning_manager.grading_queue = mock_queue

        # Run loop with timeout
        try:
            await asyncio.wait_for(
                manager.run_grading_loop(mock_learning_manager, check_interval=0.01),
                timeout=0.5,
            )
        except asyncio.TimeoutError:
            pass  # Expected (infinite loop)

        # Verify grading happened
        updated = store.load("test", "1.0")
        assert updated.mean_score == 0.7

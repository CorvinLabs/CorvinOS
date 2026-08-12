"""Unit tests for HeuristicGrader (ADR-0307)."""

import pytest

from core.skills.graders.heuristic import HeuristicGrader


class TestHeuristicGrader:
    """HeuristicGrader rule-based scoring tests."""

    def test_init_default(self):
        grader = HeuristicGrader()
        assert grader.slow_threshold == 10.0
        assert grader.moderate_threshold == 1.0

    def test_init_custom_thresholds(self):
        grader = HeuristicGrader(slow_threshold_s=5.0, moderate_threshold_s=0.5)
        assert grader.slow_threshold == 5.0
        assert grader.moderate_threshold == 0.5

    @pytest.mark.asyncio
    async def test_grade_exception_low_score(self):
        grader = HeuristicGrader()
        request = {
            "skill_name": "test",
            "exception": "ValueError",
            "elapsed": 0.1,
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.2
        assert "ValueError" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_slow_execution(self):
        grader = HeuristicGrader(slow_threshold_s=10.0)
        request = {
            "skill_name": "test",
            "exception": None,
            "elapsed": 15.0,
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.5
        assert "Slow" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_moderate_execution(self):
        grader = HeuristicGrader(
            slow_threshold_s=10.0,
            moderate_threshold_s=1.0,
        )
        request = {
            "skill_name": "test",
            "exception": None,
            "elapsed": 2.5,
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.7
        assert "Moderate" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_fast_execution(self):
        grader = HeuristicGrader(moderate_threshold_s=1.0)
        request = {
            "skill_name": "test",
            "exception": None,
            "elapsed": 0.1,
        }
        grade = await grader.grade(request)

        assert grade is not None
        assert grade.value == 0.9
        assert "Fast" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_threshold_boundary_slow(self):
        grader = HeuristicGrader(slow_threshold_s=10.0)
        request = {
            "skill_name": "test",
            "exception": None,
            "elapsed": 10.0,  # Exactly at threshold
        }
        grade = await grader.grade(request)

        # At boundary, should still be "moderate" (< slow_threshold)
        assert grade.value == 0.7

    @pytest.mark.asyncio
    async def test_grade_threshold_boundary_moderate(self):
        grader = HeuristicGrader(moderate_threshold_s=1.0)
        request = {
            "skill_name": "test",
            "exception": None,
            "elapsed": 1.5,  # Just above threshold
        }
        grade = await grader.grade(request)

        # Just above threshold, should be "moderate"
        assert grade.value == 0.7

    @pytest.mark.asyncio
    async def test_grade_missing_elapsed(self):
        grader = HeuristicGrader()
        request = {
            "skill_name": "test",
            "exception": None,
            # elapsed is missing
        }
        grade = await grader.grade(request)

        # Missing elapsed defaults to 0.0 (fast)
        assert grade is not None
        assert grade.value == 0.9

    @pytest.mark.asyncio
    async def test_grade_never_returns_none(self):
        grader = HeuristicGrader()

        # Test various scenarios
        scenarios = [
            {"exception": "ValueError", "elapsed": 0.1},
            {"exception": None, "elapsed": 20.0},
            {"exception": None, "elapsed": 0.5},
            {"elapsed": 0.01},
        ]

        for request in scenarios:
            grade = await grader.grade(request)
            assert grade is not None, f"Heuristic grader returned None for {request}"
            assert 0.0 <= grade.value <= 1.0

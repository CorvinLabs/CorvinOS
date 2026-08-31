"""Unit tests for CompositeGrader (ADR-0307)."""

import pytest

from core.skills import Grade
from core.skills.graders.composite import CompositeGrader


class MockGrader:
    """Test grader that returns fixed score."""

    def __init__(self, score: float, fail: bool = False):
        self.score = score
        self.fail = fail

    async def grade(self, request: dict) -> Grade | None:
        if self.fail:
            return None
        return Grade(value=self.score, feedback=f"mock:{self.score}")


class TestCompositeGrader:
    """CompositeGrader chaining tests."""

    def test_init_requires_graders(self):
        with pytest.raises(ValueError, match="at least one"):
            CompositeGrader([])

    def test_init_weighted_requires_positive_weights(self):
        grader1 = MockGrader(0.5)
        grader2 = MockGrader(0.7)

        with pytest.raises(ValueError, match="positive"):
            CompositeGrader(
                [(grader1, 1.0), (grader2, -0.5)],
                strategy="weighted",
            )

    @pytest.mark.asyncio
    async def test_grade_average_strategy(self):
        grader1 = MockGrader(0.6)
        grader2 = MockGrader(0.8)
        grader3 = MockGrader(1.0)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0), (grader3, 1.0)],
            strategy="average",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        assert grade.value == pytest.approx(0.8)  # (0.6 + 0.8 + 1.0) / 3
        assert "Average of 3" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_weighted_strategy(self):
        grader1 = MockGrader(0.5)
        grader2 = MockGrader(0.9)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 2.0)],  # grader2 weighted 2x
            strategy="weighted",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        # (0.5*1 + 0.9*2) / (1+2) = 2.3 / 3 = 0.7667
        assert grade.value == pytest.approx(2.3 / 3.0)
        assert "Weighted average" in grade.feedback

    @pytest.mark.asyncio
    async def test_grade_max_strategy(self):
        grader1 = MockGrader(0.3)
        grader2 = MockGrader(0.7)
        grader3 = MockGrader(0.5)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0), (grader3, 1.0)],
            strategy="max",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        assert grade.value == 0.7

    @pytest.mark.asyncio
    async def test_grade_min_strategy(self):
        grader1 = MockGrader(0.8)
        grader2 = MockGrader(0.3)
        grader3 = MockGrader(0.6)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0), (grader3, 1.0)],
            strategy="min",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        assert grade.value == 0.3

    @pytest.mark.asyncio
    async def test_grade_first_strategy(self):
        grader1 = MockGrader(0.6)
        grader2 = MockGrader(0.9)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0)],
            strategy="first",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        assert grade.value == 0.6

    @pytest.mark.asyncio
    async def test_grade_some_graders_fail(self):
        grader1 = MockGrader(0.5, fail=True)
        grader2 = MockGrader(0.8)
        grader3 = MockGrader(0.9)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0), (grader3, 1.0)],
            strategy="average",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is not None
        # Only grader2 and grader3 succeeded
        assert grade.value == pytest.approx((0.8 + 0.9) / 2)

    @pytest.mark.asyncio
    async def test_grade_all_graders_fail(self):
        grader1 = MockGrader(0.5, fail=True)
        grader2 = MockGrader(0.8, fail=True)

        composite = CompositeGrader(
            [(grader1, 1.0), (grader2, 1.0)],
            strategy="average",
        )

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        assert grade is None

    @pytest.mark.asyncio
    async def test_grade_invalid_strategy(self):
        grader = MockGrader(0.5)

        composite = CompositeGrader([(grader, 1.0)], strategy="invalid")

        request = {"skill_name": "test", "output": "result"}

        with pytest.raises(ValueError, match="Unknown strategy"):
            await composite.grade(request)

    @pytest.mark.asyncio
    async def test_grade_exception_in_grader(self):
        class FailingGrader:
            async def grade(self, request):
                raise Exception("Grader crashed")

        grader1 = FailingGrader()
        grader2 = MockGrader(0.7)

        composite = CompositeGrader([(grader1, 1.0), (grader2, 1.0)], strategy="average")

        request = {"skill_name": "test", "output": "result"}
        grade = await composite.grade(request)

        # Should fall back to grader2
        assert grade is not None
        assert grade.value == 0.7

"""Unit tests for Skill Composition (ADR-0311)."""

import asyncio
from functools import wraps
from typing import Callable

import pytest

from core.skills.composition import CompositionStep, SkillComposition


def mock_skill_learnable(fn: Callable) -> Callable:
    """Test fixture: add _skill_metadata to satisfy K3-001 contract validation.

    Preserves async/sync nature of the function.
    """
    if asyncio.iscoroutinefunction(fn):
        @wraps(fn)
        async def async_wrapper(*args, **kwargs):
            return await fn(*args, **kwargs)
        async_wrapper._skill_metadata = {"name": fn.__name__, "version": "1.0.0"}
        return async_wrapper
    else:
        @wraps(fn)
        def sync_wrapper(*args, **kwargs):
            return fn(*args, **kwargs)
        sync_wrapper._skill_metadata = {"name": fn.__name__, "version": "1.0.0"}
        return sync_wrapper


class TestCompositionStep:
    """CompositionStep tests."""

    def test_create_step(self):
        def dummy_skill(x):
            return x

        step = CompositionStep(name="step1", skill_fn=dummy_skill, tags=["test"])
        assert step.name == "step1"
        assert step.tags == ["test"]


class TestSkillComposition:
    """SkillComposition tests."""

    def test_create_empty(self):
        comp = SkillComposition(name="test-pipeline")
        assert comp.name == "test-pipeline"
        assert len(comp.steps) == 0

    def test_add_step(self):
        comp = SkillComposition(name="pipeline")

        @mock_skill_learnable
        def skill1(x):
            return x + 1

        comp.add_step("step1", skill1, tags=["math"])
        assert len(comp.steps) == 1
        assert comp.steps[0].name == "step1"

    @pytest.mark.asyncio
    async def test_execute_sync_pipeline(self):
        comp = SkillComposition(name="pipeline")

        @mock_skill_learnable
        def add_one(x):
            return x + 1

        @mock_skill_learnable
        def mul_two(x):
            return x * 2

        comp.add_step("add", add_one)
        comp.add_step("mul", mul_two)

        result = await comp.execute(5)
        assert result == (5 + 1) * 2  # 12

    @pytest.mark.asyncio
    async def test_execute_async_pipeline(self):
        comp = SkillComposition(name="async-pipeline")

        @mock_skill_learnable
        async def async_add(x):
            await asyncio.sleep(0.01)
            return x + 1

        @mock_skill_learnable
        async def async_mul(x):
            await asyncio.sleep(0.01)
            return x * 2

        comp.add_step("add", async_add)
        comp.add_step("mul", async_mul)

        result = await comp.execute(5)
        assert result == (5 + 1) * 2

    @pytest.mark.asyncio
    async def test_execute_mixed_pipeline(self):
        comp = SkillComposition(name="mixed")

        @mock_skill_learnable
        def sync_add(x):
            return x + 10

        @mock_skill_learnable
        async def async_mul(x):
            await asyncio.sleep(0.01)
            return x * 2

        comp.add_step("sync", sync_add)
        comp.add_step("async", async_mul)

        result = await comp.execute(5)
        assert result == (5 + 10) * 2

    @pytest.mark.asyncio
    async def test_execute_step_failure(self):
        comp = SkillComposition(name="fail-pipeline")

        @mock_skill_learnable
        def fail_step(x):
            raise ValueError("Intentional error")

        comp.add_step("fail", fail_step)

        with pytest.raises(RuntimeError, match="failed"):
            await comp.execute(5)

    def test_get_pipeline_info(self):
        comp = SkillComposition(name="info-test")

        s1 = mock_skill_learnable(lambda x: x)
        s2 = mock_skill_learnable(lambda x: x)

        comp.add_step("s1", s1, tags=["math", "core"])
        comp.add_step("s2", s2, tags=["core"])

        info = comp.get_pipeline_info()
        assert info["name"] == "info-test"
        assert info["steps"] == 2
        assert "math" in info["all_tags"]
        assert "core" in info["all_tags"]

    def test_add_step_contract_validation_k3_001(self):
        """K3-001: Verify skill contract validation (must be @skill_learnable)."""
        comp = SkillComposition(name="validate-test")

        # Undecorated skill should raise ValueError
        def undecorated_skill(x):
            return x + 1

        with pytest.raises(ValueError, match="must be decorated with @skill_learnable"):
            comp.add_step("bad", undecorated_skill)

        # Decorated skill should succeed
        @mock_skill_learnable
        def decorated_skill(x):
            return x + 1

        comp.add_step("good", decorated_skill)
        assert len(comp.steps) == 1

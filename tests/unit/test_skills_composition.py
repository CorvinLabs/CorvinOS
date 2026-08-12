"""Unit tests for Skill Composition (ADR-0311)."""

import asyncio

import pytest

from core.skills.composition import CompositionStep, SkillComposition


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

        def skill1(x):
            return x + 1

        comp.add_step("step1", skill1, tags=["math"])
        assert len(comp.steps) == 1
        assert comp.steps[0].name == "step1"

    @pytest.mark.asyncio
    async def test_execute_sync_pipeline(self):
        comp = SkillComposition(name="pipeline")

        def add_one(x):
            return x + 1

        def mul_two(x):
            return x * 2

        comp.add_step("add", add_one)
        comp.add_step("mul", mul_two)

        result = await comp.execute(5)
        assert result == (5 + 1) * 2  # 12

    @pytest.mark.asyncio
    async def test_execute_async_pipeline(self):
        comp = SkillComposition(name="async-pipeline")

        async def async_add(x):
            await asyncio.sleep(0.01)
            return x + 1

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

        def sync_add(x):
            return x + 10

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

        def fail_step(x):
            raise ValueError("Intentional error")

        comp.add_step("fail", fail_step)

        with pytest.raises(RuntimeError, match="failed"):
            await comp.execute(5)

    def test_get_pipeline_info(self):
        comp = SkillComposition(name="info-test")

        comp.add_step("s1", lambda x: x, tags=["math", "core"])
        comp.add_step("s2", lambda x: x, tags=["core"])

        info = comp.get_pipeline_info()
        assert info["name"] == "info-test"
        assert info["steps"] == 2
        assert "math" in info["all_tags"]
        assert "core" in info["all_tags"]

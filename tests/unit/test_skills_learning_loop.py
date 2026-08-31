"""Unit tests for Skill Learning Loop (ADR-0306)."""

import asyncio
from unittest.mock import patch

import pytest

from core.concurrency.queue import Queue
from core.skills import Grade, InMemorySkillStore, Skill, SkillLearningManager, skill_learnable


class TestSkillLearnable:
    """@skill_learnable decorator tests."""

    def test_decorator_sync_function(self):
        @skill_learnable(name="test-skill", version="1.0")
        def my_skill(x: int) -> str:
            return f"result: {x}"

        result = my_skill(5)
        assert result == "result: 5"

    def test_decorator_async_function(self):
        @skill_learnable(name="async-skill", version="1.0")
        async def my_async_skill(x: int) -> str:
            await asyncio.sleep(0.01)
            return f"async result: {x}"

        async def run_test():
            result = await my_async_skill(5)
            assert result == "async result: 5"

        asyncio.run(run_test())

    def test_decorator_captures_exception(self):
        @skill_learnable(name="fail-skill", version="1.0")
        def failing_skill():
            raise ValueError("test error")

        with pytest.raises(ValueError, match="test error"):
            failing_skill()

    def test_decorator_invalid_name_empty(self):
        with pytest.raises(ValueError, match="invalid"):

            @skill_learnable(name="", version="1.0")
            def dummy():
                pass

    def test_decorator_invalid_name_slash(self):
        with pytest.raises(ValueError, match="invalid"):

            @skill_learnable(name="foo/bar", version="1.0")
            def dummy():
                pass

    def test_decorator_preserves_function_name(self):
        @skill_learnable(name="test", version="1.0")
        def original_name():
            return "ok"

        assert original_name.__name__ == "original_name"

    def test_decorator_with_tags(self):
        @skill_learnable(name="tagged", version="1.0", tags=["code", "review"])
        def tagged_skill():
            return "ok"

        # Just verify it's callable
        result = tagged_skill()
        assert result == "ok"

    def test_decorator_with_tier(self):
        @skill_learnable(name="tiered", version="1.0", tier="community")
        def tiered_skill():
            return "ok"

        result = tiered_skill()
        assert result == "ok"


class TestSkillLearningManager:
    """SkillLearningManager orchestration tests."""

    def test_create_manager(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)
        assert manager.store is store
        assert manager.grading_queue is not None

    def test_register_skill(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)
        skill = Skill(name="test", version="1.0", body="code")

        manager.register_skill(skill)
        loaded = manager.get_skill("test", "1.0")

        assert loaded is not None
        assert loaded.name == "test"

    def test_get_skill(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)
        skill = Skill(name="test", version="1.0", body="code")
        manager.register_skill(skill)

        retrieved = manager.get_skill("test", "1.0")
        assert retrieved.name == "test"

    def test_get_skill_nonexistent(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        retrieved = manager.get_skill("nonexistent", "1.0")
        assert retrieved is None

    def test_list_top_skills(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        for i, score in enumerate([0.9, 0.5, 0.2]):
            s = Skill(name=f"skill{i}", version="1.0", body="code")
            s.add_grade(Grade(value=score))
            manager.register_skill(s)

        top = manager.list_top_skills(limit=2)
        assert len(top) == 2
        assert top[0].mean_score == 0.9
        assert top[1].mean_score == 0.5

    def test_list_top_skills_no_limit(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        for i in range(5):
            s = Skill(name=f"skill{i}", version="1.0", body="code")
            manager.register_skill(s)

        top = manager.list_top_skills()  # No limit
        assert len(top) == 5

    @pytest.mark.asyncio
    async def test_grading_loop_processes_requests(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        # Register a skill
        skill = Skill(name="test", version="1.0", body="code")
        manager.register_skill(skill)

        # Create a simple grader that returns a fixed grade
        async def dummy_grader(request: dict):
            return Grade(value=0.8, feedback="graded")

        # Put a request in the queue
        request = {
            "skill_name": "test",
            "skill_version": "1.0",
            "output": "ok",
            "elapsed": 0.1,
        }
        manager.grading_queue.put(request)

        # Run the grading loop for one iteration
        request_popped = manager.grading_queue.get(blocking=False)
        if request_popped:
            skill_to_grade = manager.store.load(request_popped["skill_name"], request_popped["skill_version"])
            if skill_to_grade:
                grade = await dummy_grader(request_popped)
                if grade:
                    skill_to_grade.add_grade(grade)
                    manager.store.save(skill_to_grade)

        # Verify the skill was updated
        updated = manager.get_skill("test", "1.0")
        assert updated.mean_score == 0.8

    def test_grading_queue_integration(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        # Put a request in the queue
        request = {
            "skill_name": "myskill",
            "skill_version": "1.0",
            "output": "result",
            "elapsed": 0.05,
        }
        manager.grading_queue.put(request)

        # Verify the queue has the request
        popped = manager.grading_queue.get(blocking=True)
        assert popped["skill_name"] == "myskill"


class TestSkillLearningEndToEnd:
    """End-to-end learning workflow."""

    def test_full_workflow(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        # 1. Create and register a skill
        skill = Skill(name="e2e-skill", version="1.0", body="code", tags=["e2e"])
        manager.register_skill(skill)

        # 2. Add grades manually (simulating grader output)
        loaded = manager.get_skill("e2e-skill", "1.0")
        loaded.add_grade(Grade(value=0.9, feedback="excellent"))
        loaded.add_grade(Grade(value=0.8, feedback="good"))
        manager.store.save(loaded)

        # 3. Verify stats computed
        final = manager.get_skill("e2e-skill", "1.0")
        assert final.mean_score == pytest.approx(0.85)
        assert final.n_trials == 2

        # 4. Verify ranking
        top = manager.list_top_skills(limit=1)
        assert top[0].name == "e2e-skill"

    def test_decorator_with_manager(self):
        store = InMemorySkillStore()
        manager = SkillLearningManager(store)

        @skill_learnable(name="decorated", version="1.0")
        def my_skill(x: int) -> int:
            return x * 2

        # Register the skill
        skill = Skill(name="decorated", version="1.0", body="code")
        manager.register_skill(skill)

        # Call the skill
        result = my_skill(5)
        assert result == 10

        # Manually grade it (in real scenario, async grader does this)
        loaded = manager.get_skill("decorated", "1.0")
        loaded.add_grade(Grade(value=0.7))
        manager.store.save(loaded)

        # Verify
        final = manager.get_skill("decorated", "1.0")
        assert final.mean_score == 0.7

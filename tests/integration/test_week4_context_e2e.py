"""Week 4: TaskContextTracker E2E tests."""

import pytest
from core.brain.task_context_tracker import TaskContextTracker, TaskContext, TaskStatus, SafetyValidator


class TestWeek4ContextTracking:
    """Test task context tracking end-to-end."""

    @pytest.mark.asyncio
    async def test_context_stack_push_pop(self):
        """Test: context stack push/pop works."""
        tracker = TaskContextTracker()

        context = TaskContext(task_id="task_1", task_title="Test Task")
        await tracker.push_context(context)

        current = await tracker.get_current_context()
        assert current.task_id == "task_1"

        popped = await tracker.pop_context()
        assert popped.task_id == "task_1"
        assert await tracker.get_current_context() is None

    @pytest.mark.asyncio
    async def test_nested_contexts(self):
        """Test: nested task contexts work."""
        tracker = TaskContextTracker()

        parent = TaskContext(task_id="parent", task_title="Parent Task")
        child = TaskContext(task_id="child", task_title="Child Task", parent_task_id="parent")

        await tracker.push_context(parent)
        await tracker.push_context(child)

        assert len(tracker.context_stack) == 2
        current = await tracker.get_current_context()
        assert current.task_id == "child"

    @pytest.mark.asyncio
    async def test_guidance_scoping(self):
        """Test: guidance is scoped to correct task."""
        tracker = TaskContextTracker()

        task_a = TaskContext(task_id="task_a", task_title="Task A")
        task_b = TaskContext(task_id="task_b", task_title="Task B", parent_task_id="task_a")

        await tracker.push_context(task_a)
        await tracker.push_context(task_b)

        # Guidance for task_b should find task_b
        scoped = await tracker.get_context_for_guidance("task_b")
        assert scoped.task_id == "task_b"

        # Guidance for current (no ID) should find task_b
        current_scoped = await tracker.get_context_for_guidance()
        assert current_scoped.task_id == "task_b"

    @pytest.mark.asyncio
    async def test_safety_validator_high_risk(self):
        """Test: high-risk guidance caught."""
        tracker = TaskContextTracker()
        validator = SafetyValidator(tracker)

        # High-risk keyword
        safe, reason = await validator.validate_guidance("delete everything", "medium")
        assert not safe
        assert "High-risk keyword detected" in reason

        # High-risk level
        safe, reason = await validator.validate_guidance("continue", "high")
        assert not safe
        assert "requires confirmation" in reason

        # Safe guidance
        safe, reason = await validator.validate_guidance("use Opus", "safe")
        assert safe

    @pytest.mark.asyncio
    async def test_e2e_full_flow(self):
        """Test: E2E flow from context push to guidance scoping to safety."""
        tracker = TaskContextTracker()
        validator = SafetyValidator(tracker)

        # Push context
        context = TaskContext(task_id="e2e_1", task_title="E2E Task", total_steps=10)
        await tracker.push_context(context)

        # Update step
        await tracker.update_step(1)

        # Scope guidance
        scoped = await tracker.get_context_for_guidance()
        assert scoped.task_id == "e2e_1"
        assert scoped.current_step == 1
        assert scoped.progress_pct() == 10.0

        # Validate guidance
        safe, _ = await validator.validate_guidance("use Opus", "safe")
        assert safe

        # Pop context
        popped = await tracker.pop_context()
        assert popped.task_id == "e2e_1"

    @pytest.mark.asyncio
    async def test_metrics(self):
        """Test: metrics collected."""
        tracker = TaskContextTracker()

        context = TaskContext(task_id="m1", task_title="Metric Task")
        await tracker.push_context(context)
        await tracker.pop_context()

        metrics = await tracker.get_metrics()
        assert metrics["total_tasks"] == 1
        assert metrics["max_depth"] == 1

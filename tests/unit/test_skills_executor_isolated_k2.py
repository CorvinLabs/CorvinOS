"""Integration Tests: L4 k=2 — SkillExecutor.execute_isolated().

Verify:
1. execute_isolated() wraps context in IsolatedTaskContext
2. Skill receives isolated context copy
3. Mutations are tracked in result
4. Original context never modified
5. State hashes in result for audit chain
6. Isolation verification passes/fails correctly
"""

import asyncio
import pytest
from core.skills.executor import SkillExecutor, ExecutionResult


class TestExecuteIsolated:
    """Test isolated skill execution."""

    def setup_method(self):
        """Set up executor for each test."""
        self.executor = SkillExecutor()

    @pytest.mark.asyncio
    async def test_execute_isolated_success(self):
        """execute_isolated should succeed with isolated context."""

        # Define a skill that modifies context
        async def test_skill(context):
            context["user"]["role"] = "viewer"  # Modify isolated copy
            return {"status": "ok", "new_role": context["user"]["role"]}

        original_context = {
            "user": {"name": "Alice", "role": "admin"},
            "task": "test",
        }

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.test_skill",
            skill=test_skill,
            context=original_context,
            task_id="task_123",
        )

        # Verify success
        assert result.status == "success"
        assert result.output == {"status": "ok", "new_role": "viewer"}

        # Verify original context was never modified
        assert original_context["user"]["role"] == "admin"

        # Verify state hashes in result
        assert result.context_state_before_hash is not None
        assert result.context_state_after_hash is not None
        assert result.context_state_before_hash != result.context_state_after_hash

        # Verify mutations tracked
        assert result.mutations is not None
        assert len(result.mutations) > 0

    @pytest.mark.asyncio
    async def test_execute_isolated_preserves_original(self):
        """Original context should never be modified."""

        async def mutating_skill(context):
            context["user"]["name"] = "Bob"
            context["config"] = {"theme": "dark"}
            return {"result": "done"}

        original = {"user": {"name": "Alice"}, "config": {"theme": "light"}}
        original_copy = original.copy()

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.mutating",
            skill=mutating_skill,
            context=original,
            task_id="task_123",
        )

        # Original should be untouched
        assert original == original_copy

    @pytest.mark.asyncio
    async def test_execute_isolated_tracks_multiple_mutations(self):
        """Multiple mutations should all be tracked."""

        async def multi_mutating_skill(context):
            context["user"]["name"] = "Bob"
            context["user"]["role"] = "viewer"
            context["config"]["theme"] = "dark"
            return {"mutations_done": 3}

        context = {
            "user": {"name": "Alice", "role": "admin"},
            "config": {"theme": "light"},
        }

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.multi_mutate",
            skill=multi_mutating_skill,
            context=context.copy(),
            task_id="task_123",
        )

        assert result.status == "success"
        assert result.mutations is not None
        assert len(result.mutations) >= 3

    @pytest.mark.asyncio
    async def test_execute_isolated_timeout(self):
        """execute_isolated should timeout gracefully."""

        async def slow_skill(context):
            await asyncio.sleep(10)  # Sleep for 10 seconds
            return {"status": "done"}

        self.executor.set_timeout("os.slow_skill", 100)  # 100ms timeout

        context = {"user": {"name": "Alice"}}

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.slow_skill",
            skill=slow_skill,
            context=context,
            task_id="task_123",
        )

        assert result.status == "failure"
        assert "timeout" in result.error_message.lower() or "exceeded" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_isolated_exception_handling(self):
        """execute_isolated should handle skill exceptions."""

        async def failing_skill(context):
            raise ValueError("Intentional error in skill")

        context = {"user": {"name": "Alice"}}

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.failing",
            skill=failing_skill,
            context=context,
            task_id="task_123",
        )

        assert result.status == "failure"
        assert result.error_message is not None
        assert result.error_class is not None

    @pytest.mark.asyncio
    async def test_execute_isolated_empty_tenant_id_rejected(self):
        """Empty tenant_id should cause context isolation setup to fail."""

        async def test_skill(context):
            return {"status": "ok"}

        context = {"user": {"name": "Alice"}}

        # Empty tenant_id should trigger validation error in isolation setup
        result = await self.executor.execute_isolated(
            tenant_id="",  # Empty!
            skill_id="os.test",
            skill=test_skill,
            context=context,
            task_id="task_123",
        )

        assert result.status == "failure"
        assert "isolation" in result.error_message.lower() or "tenant" in result.error_message.lower()

    @pytest.mark.asyncio
    async def test_execute_isolated_no_mutations(self):
        """Skill that doesn't mutate should still work."""

        async def readonly_skill(context):
            # Read-only access, no mutations
            return {"name": context["user"]["name"], "status": "ok"}

        context = {"user": {"name": "Alice", "role": "admin"}}

        result = await self.executor.execute_isolated(
            tenant_id="_default",
            skill_id="os.readonly",
            skill=readonly_skill,
            context=context.copy(),
            task_id="task_123",
        )

        assert result.status == "success"
        assert result.output == {"name": "Alice", "status": "ok"}

        # No mutations expected
        assert result.mutations is not None
        assert len(result.mutations) == 0

    @pytest.mark.asyncio
    async def test_execute_isolated_multi_tenant_isolation(self):
        """Different tenants should have isolated contexts."""

        async def mutating_skill(context):
            context["user"]["name"] = "Bob"
            return {"result": "done"}

        context_a = {"user": {"name": "Alice"}}
        context_b = {"user": {"name": "Charlie"}}

        result_a = await self.executor.execute_isolated(
            tenant_id="tenant_a",
            skill_id="os.test",
            skill=mutating_skill,
            context=context_a.copy(),
            task_id="task_a",
        )

        result_b = await self.executor.execute_isolated(
            tenant_id="tenant_b",
            skill_id="os.test",
            skill=mutating_skill,
            context=context_b.copy(),
            task_id="task_b",
        )

        # Both should succeed
        assert result_a.status == "success"
        assert result_b.status == "success"

        # Original contexts untouched (different tenants)
        assert context_a["user"]["name"] == "Alice"
        assert context_b["user"]["name"] == "Charlie"

        # State hashes should differ (different tenants)
        assert result_a.context_state_before_hash != result_b.context_state_before_hash


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

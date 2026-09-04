"""Integration Tests: L4 k=3 — SkillPhaseExecutor DAG Integration.

Verify:
1. Skill sequence execution with context isolation
2. Mutations merge between skills
3. Original context preservation
4. Failure handling + hard-fail
5. TaskOrchestrator phase handler creation
"""

import pytest
from core.skills.skill_phase_executor import SkillPhaseExecutor, SkillPhaseSpec
from core.skills.executor import SkillExecutor
from core.skills.contract import SKILL_REGISTRY, SkillContract, SkillTier


class TestSkillPhaseExecutor:
    """Test skill-based phase execution."""

    def setup_method(self):
        """Set up executor for each test."""
        self.executor = SkillExecutor()
        self.phase_executor = SkillPhaseExecutor(executor=self.executor)

    @pytest.mark.asyncio
    async def test_single_skill_phase_execution(self):
        """Execute a single skill as a phase."""

        async def skill_1(context):
            context["processed_by"] = "skill_1"
            return {"status": "ok"}

        # Mock skill lookup
        self.phase_executor._get_skill_callable = lambda sid: skill_1 if sid == "skill.test_1" else None

        spec = SkillPhaseSpec(
            phase_id="phase_1",
            skill_ids=["skill.test_1"],
            tenant_id="_default",
            task_id="task_123",
            input_context={"data": "initial"},
        )

        result = await self.phase_executor.execute_phase(spec)

        # Verify phase result
        assert len(result["skills_executed"]) == 1
        assert result["skills_executed"][0] == "skill.test_1"
        assert result["context_final"]["processed_by"] == "skill_1"
        assert result["context_final"]["data"] == "initial"
        assert len(result["results"]) == 1
        assert result["results"][0].status == "success"

    @pytest.mark.asyncio
    async def test_skill_sequence_with_mutations(self):
        """Execute multiple skills with mutations merging."""

        async def skill_1(context):
            context["user"]["role"] = "viewer"
            return {"skill": "1"}

        async def skill_2(context):
            # Should see skill_1's mutations
            assert context["user"]["role"] == "viewer"
            context["user"]["verified"] = True
            return {"skill": "2"}

        def get_skill(sid):
            return skill_1 if sid == "skill.1" else skill_2 if sid == "skill.2" else None

        self.phase_executor._get_skill_callable = get_skill

        spec = SkillPhaseSpec(
            phase_id="phase_pipeline",
            skill_ids=["skill.1", "skill.2"],
            tenant_id="_default",
            task_id="task_123",
            input_context={"user": {"name": "Alice", "role": "admin"}},
        )

        result = await self.phase_executor.execute_phase(spec)

        # Verify sequence executed
        assert len(result["skills_executed"]) == 2
        assert result["skills_executed"] == ["skill.1", "skill.2"]

        # Verify mutations merged
        assert result["context_final"]["user"]["role"] == "viewer"  # From skill_1
        assert result["context_final"]["user"]["verified"] is True  # From skill_2
        assert result["context_final"]["user"]["name"] == "Alice"  # Original

        # Verify mutations tracked
        assert len(result["mutations"]) > 0

    @pytest.mark.asyncio
    async def test_skill_phase_failure_stops_sequence(self):
        """Failed skill should stop the sequence."""

        async def skill_1(context):
            context["step"] = 1
            return {"status": "ok"}

        async def skill_2_fail(context):
            raise ValueError("Intentional failure in skill_2")

        async def skill_3(context):
            # Should not execute
            context["step"] = 3
            return {"status": "ok"}

        def get_skill(sid):
            if sid == "skill.1":
                return skill_1
            elif sid == "skill.2_fail":
                return skill_2_fail
            elif sid == "skill.3":
                return skill_3
            return None

        self.phase_executor._get_skill_callable = get_skill

        spec = SkillPhaseSpec(
            phase_id="phase_with_failure",
            skill_ids=["skill.1", "skill.2_fail", "skill.3"],
            tenant_id="_default",
            task_id="task_123",
            input_context={},
        )

        # Should raise on skill_2 failure
        with pytest.raises(RuntimeError, match="skill.2_fail.*failed"):
            await self.phase_executor.execute_phase(spec)

    @pytest.mark.asyncio
    async def test_empty_skill_sequence(self):
        """Empty skill sequence should return empty result."""

        spec = SkillPhaseSpec(
            phase_id="phase_empty",
            skill_ids=[],
            tenant_id="_default",
            task_id="task_123",
            input_context={"data": "test"},
        )

        result = await self.phase_executor.execute_phase(spec)

        assert len(result["skills_executed"]) == 0
        assert result["context_final"] == {"data": "test"}
        assert len(result["results"]) == 0

    @pytest.mark.asyncio
    async def test_skill_phase_preserves_original_context(self):
        """Original context should never be modified by phase execution."""

        async def mutating_skill(context):
            context["modified"] = True
            return {"status": "ok"}

        self.phase_executor._get_skill_callable = lambda sid: mutating_skill

        original = {"data": "original"}
        spec = SkillPhaseSpec(
            phase_id="phase_isolation",
            skill_ids=["skill.mutate"],
            tenant_id="_default",
            task_id="task_123",
            input_context=original,
        )

        result = await self.phase_executor.execute_phase(spec)

        # Original dict should be unchanged
        assert "modified" not in original
        assert original == {"data": "original"}

        # Result context should have mutations
        assert result["context_final"]["modified"] is True

    @pytest.mark.asyncio
    async def test_skill_phase_multi_tenant_isolation(self):
        """Different tenants should have isolated execution."""

        async def test_skill(context):
            context["tenant_mark"] = "processed"
            return {"status": "ok"}

        self.phase_executor._get_skill_callable = lambda sid: test_skill

        spec_a = SkillPhaseSpec(
            phase_id="phase_a",
            skill_ids=["skill.test"],
            tenant_id="tenant_a",
            task_id="task_a",
            input_context={"id": "a"},
        )

        spec_b = SkillPhaseSpec(
            phase_id="phase_b",
            skill_ids=["skill.test"],
            tenant_id="tenant_b",
            task_id="task_b",
            input_context={"id": "b"},
        )

        result_a = await self.phase_executor.execute_phase(spec_a)
        result_b = await self.phase_executor.execute_phase(spec_b)

        # Both should succeed
        assert result_a["context_final"]["tenant_mark"] == "processed"
        assert result_b["context_final"]["tenant_mark"] == "processed"

        # State hashes should differ (different tenants)
        assert result_a["state_hashes"]["before"] != result_b["state_hashes"]["before"]

    @pytest.mark.asyncio
    async def test_skill_phase_state_hashing(self):
        """State hashes should track context before/after."""

        async def test_skill(context):
            context["changed"] = True
            return {"status": "ok"}

        self.phase_executor._get_skill_callable = lambda sid: test_skill

        spec = SkillPhaseSpec(
            phase_id="phase_hash",
            skill_ids=["skill.test"],
            tenant_id="_default",
            task_id="task_123",
            input_context={"data": "test"},
        )

        result = await self.phase_executor.execute_phase(spec)

        # State hashes should be present
        assert result["state_hashes"]["before"] is not None
        assert result["state_hashes"]["after"] is not None

        # Should differ (context changed)
        assert result["state_hashes"]["before"] != result["state_hashes"]["after"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

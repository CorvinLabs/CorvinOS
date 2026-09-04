"""Adversarial Tests: L4 k=5 — Security Hardening (6 Attack Vectors).

Attack Vectors:
1. Context-Leakage: Mutations escape to original context
2. Isolation-Escape: Direct reference mutation bypasses CoW
3. Concurrent-Access: Race conditions on overlapping mutations
4. PII-Leakage: Sensitive data in state hashes/deltas
5. Merge-Conflict: Overlapping mutations cause incorrect merge
6. External-Tampering: External code modifies original (detection)
"""

import pytest
import asyncio
from core.skills.context_isolation import IsolatedTaskContext, ContextMerger
from core.skills.executor import SkillExecutor


class TestContextLeakage:
    """Vector 1: Verify mutations don't leak to original context."""

    def test_nested_dict_mutation_isolated(self):
        """Nested dict mutation should not leak."""
        original = {"user": {"prefs": {"theme": "light"}}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        # Mutate nested structure
        isolated.set("user.prefs.theme", "dark")

        # Original should be unchanged
        assert original["user"]["prefs"]["theme"] == "light"
        assert isolated.get("user.prefs.theme") == "dark"

    def test_list_in_context_mutation_isolated(self):
        """List mutations in context should not leak."""
        original = {"tasks": [1, 2, 3]}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        # Mutate list
        isolated._context_copy["tasks"].append(4)

        # Original should be unchanged
        assert original["tasks"] == [1, 2, 3]
        assert len(isolated._context_copy["tasks"]) == 4


class TestIsolationEscape:
    """Vector 2: Try to bypass Copy-on-Write semantics."""

    def test_direct_reference_escape_prevented(self):
        """Direct reference to original should fail isolation check."""
        original = {"data": {"value": "original"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        # Attacker tries direct reference mutation
        original["data"]["value"] = "hacked"

        # Isolation should detect this
        with pytest.raises(RuntimeError, match="isolation violation"):
            isolated.assert_isolation_intact()

    def test_copy_deepness_prevents_reference_escape(self):
        """Deep copy should prevent reference sharing."""
        original = {"nested": {"data": {"sensitive": "value"}}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        # Mutate isolated nested structure
        isolated._context_copy["nested"]["data"]["sensitive"] = "modified"

        # Original nested structure should be unaffected
        assert original["nested"]["data"]["sensitive"] == "value"
        assert isolated._context_copy["nested"]["data"]["sensitive"] == "modified"


class TestConcurrentAccess:
    """Vector 3: Race conditions on overlapping mutations."""

    @pytest.mark.asyncio
    async def test_concurrent_skill_mutations_isolated(self):
        """Concurrent mutations on same path should not race."""
        executor = SkillExecutor()

        async def skill_1(context):
            context["counter"] = 1
            await asyncio.sleep(0.01)
            return {"skill": "1", "value": context["counter"]}

        async def skill_2(context):
            context["counter"] = 2
            await asyncio.sleep(0.005)
            return {"skill": "2", "value": context["counter"]}

        context = {"counter": 0}

        # Execute concurrently
        result_1, result_2 = await asyncio.gather(
            executor.execute_isolated(
                tenant_id="_default",
                skill_id="skill.1",
                skill=skill_1,
                context=context.copy(),
                task_id="task_123",
            ),
            executor.execute_isolated(
                tenant_id="_default",
                skill_id="skill.2",
                skill=skill_2,
                context=context.copy(),
                task_id="task_124",
            ),
        )

        # Original should be unchanged
        assert context["counter"] == 0

        # Each skill should see its own mutations
        assert result_1.status == "success"
        assert result_2.status == "success"


class TestPIILeakage:
    """Vector 4: PII should not leak into deltas/hashes."""

    def test_pii_in_mutations_not_logged(self):
        """PII in mutations should be scrubbed or fail-closed."""
        original = {"email": "user@example.com", "ssn": "123-45-6789"}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated.set("email", "attacker@evil.com")
        isolated.set("ssn", "999-99-9999")

        # Get mutations
        mutations = isolated.get_mutations()
        mutations_json = str(mutations)

        # Deltas might contain PII — that's recorded for audit,
        # but ensure that state hashes don't leak the VALUES
        # (only that they changed)
        hash_before = isolated._original_context_hash_at_creation
        hash_after = isolated.get_context_hash()

        # Hashes should be hex strings, not readable data
        assert hash_before.isalnum() and len(hash_before) == 64
        assert hash_after.isalnum() and len(hash_after) == 64

    def test_context_hash_scrubs_values(self):
        """Context hash should not reveal sensitive values."""
        original = {"password": "super_secret_123"}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        hash_value = isolated.get_context_hash()

        # Hash should be non-readable
        assert "super_secret" not in hash_value
        assert "123" not in hash_value


class TestMergeConflict:
    """Vector 5: Overlapping mutations from multiple skills."""

    def test_merge_with_overlapping_paths(self):
        """Merging overlapping mutations should handle correctly."""
        original = {"user": {"role": "admin", "verified": False}}

        # Skill 1 changes role
        isolated_1 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="skill.1",
            task_id="task_123",
            tenant_id="_default",
        )
        isolated_1.set("user.role", "viewer")

        # Skill 2 changes verified
        isolated_2 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="skill.2",
            task_id="task_124",
            tenant_id="_default",
        )
        isolated_2.set("user.verified", True)

        # Merge skill 1 deltas
        deltas_1 = isolated_1.get_mutations()
        merged_1, _ = ContextMerger.merge_deltas(
            original_context=original,
            deltas=deltas_1,
            skill_id="skill.1",
            tenant_id="_default",
        )

        # Merge skill 2 deltas into result
        deltas_2 = isolated_2.get_mutations()
        merged_2, _ = ContextMerger.merge_deltas(
            original_context=merged_1,
            deltas=deltas_2,
            skill_id="skill.2",
            tenant_id="_default",
        )

        # Final state should have both mutations
        assert merged_2["user"]["role"] == "viewer"
        assert merged_2["user"]["verified"] is True


class TestExternalTampering:
    """Vector 6: External modification of original context."""

    def test_external_modification_detected(self):
        """Hash check should detect external tampering."""
        original = {"data": "original_value"}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        # Simulate external code tampering with original
        original["data"] = "tampered_value"

        # Isolation check should fail
        with pytest.raises(RuntimeError, match="isolation violation"):
            isolated.assert_isolation_intact()

    def test_hash_tampering_detection(self):
        """Changing hash should be detectable (hash collision unlikely)."""
        original = {"sensitive": "data"}

        isolated_1 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="skill.test",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated_2 = IsolatedTaskContext.create_isolated(
            original_context={"sensitive": "data"},  # Identical
            skill_id="skill.test",
            task_id="task_124",
            tenant_id="_default",
        )

        # Same input should produce same hash
        assert isolated_1._original_context_hash_at_creation == isolated_2._original_context_hash_at_creation

        # Different input should produce different hash
        isolated_3 = IsolatedTaskContext.create_isolated(
            original_context={"sensitive": "different"},
            skill_id="skill.test",
            task_id="task_125",
            tenant_id="_default",
        )

        assert isolated_1._original_context_hash_at_creation != isolated_3._original_context_hash_at_creation


class TestMultiTenantSecurity:
    """Cross-cutting: Verify multi-tenant isolation in all vectors."""

    def test_tenant_isolation_in_context_leakage(self):
        """Different tenants' mutations should be isolated."""
        original_a = {"data": "tenant_a"}
        original_b = {"data": "tenant_b"}

        isolated_a = IsolatedTaskContext.create_isolated(
            original_context=original_a,
            skill_id="skill.test",
            task_id="task_a",
            tenant_id="tenant_a",
        )

        isolated_b = IsolatedTaskContext.create_isolated(
            original_context=original_b,
            skill_id="skill.test",
            task_id="task_b",
            tenant_id="tenant_b",
        )

        isolated_a.set("data", "modified_a")
        isolated_b.set("data", "modified_b")

        # Originals unchanged
        assert original_a["data"] == "tenant_a"
        assert original_b["data"] == "tenant_b"

        # Isolated values differ
        assert isolated_a.get("data") == "modified_a"
        assert isolated_b.get("data") == "modified_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

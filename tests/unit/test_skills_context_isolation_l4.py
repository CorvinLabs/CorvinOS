"""Unit Tests: L4 Context Isolation (Copy-on-Write).

Verify:
1. Isolated copy is independent from original
2. Mutations are tracked
3. Original context is never modified
4. Isolation can be verified (assert_isolation_intact)
5. Deltas can be validated + merged
6. Multi-tenant isolation (tenant_id separation)
"""

import pytest
from core.skills.context_isolation import (
    IsolatedTaskContext,
    StateDelta,
    ContextMutationValidator,
    ContextMerger,
)


class TestIsolatedTaskContextCreation:
    """Test isolated context creation."""

    def test_create_isolated_copies_context(self):
        """Isolated copy should be independent from original."""
        original = {"user": {"name": "Alice", "role": "admin"}, "task": "test"}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Verify copy was made
        assert isolated._context_copy == original
        assert isolated._context_copy is not original  # Different object

    def test_create_isolated_requires_tenant_id(self):
        """Empty tenant_id should raise ValueError (multi-tenant safety)."""
        original = {"user": {"name": "Alice"}}

        with pytest.raises(ValueError, match="tenant_id cannot be empty"):
            IsolatedTaskContext.create_isolated(
                original_context=original,
                skill_id="os.test_skill",
                task_id="task_123",
                tenant_id="",  # Empty!
            )

    def test_create_isolated_marks_as_isolated(self):
        """Isolated context should be marked."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="tenant_a",
        )

        assert isolated._is_isolated is True
        assert isolated._skill_id == "os.test_skill"
        assert isolated._task_id == "task_123"
        assert isolated._tenant_id == "tenant_a"


class TestMutationTracking:
    """Test that mutations are tracked correctly."""

    def test_set_tracks_mutation(self):
        """Calling set() should track the mutation as a delta."""
        original = {"user": {"name": "Alice", "role": "admin"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Skill changes user role
        isolated.set("user.role", "viewer")

        # Verify mutation is tracked
        mutations = isolated.get_mutations()
        assert len(mutations) == 1

        delta = list(mutations.values())[0]
        assert delta.skill_id == "os.test_skill"
        assert delta.path == "user.role"
        assert delta.old_value == "admin"
        assert delta.new_value == "viewer"

    def test_set_multiple_mutations(self):
        """Multiple mutations should all be tracked."""
        original = {"user": {"name": "Alice", "role": "admin"}, "config": {"theme": "light"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated.set("user.name", "Bob")
        isolated.set("user.role", "viewer")
        isolated.set("config.theme", "dark")

        mutations = isolated.get_mutations()
        assert len(mutations) == 3

    def test_set_readonly_fields_raises_error(self):
        """Trying to set read-only fields should raise ValueError."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Try to modify tenant_id (read-only)
        with pytest.raises(ValueError, match="read-only"):
            isolated.set("_tenant_id", "tenant_b")

    def test_get_returns_from_copy(self):
        """get() should return values from the isolated copy, not original."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Modify copy
        isolated.set("user.name", "Bob")

        # get() should return the modified value (from copy)
        assert isolated.get("user.name") == "Bob"

    def test_get_with_default(self):
        """get() should return default if path not found."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Path doesn't exist
        assert isolated.get("config.theme", "light") == "light"


class TestIsolationIntegrity:
    """Test that isolation is maintained."""

    def test_original_context_unchanged_after_mutations(self):
        """Original context should never be modified."""
        original = {"user": {"name": "Alice", "role": "admin"}}
        original_copy = {"user": {"name": "Alice", "role": "admin"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Make many mutations
        isolated.set("user.name", "Bob")
        isolated.set("user.role", "viewer")

        # Original should be unchanged
        assert original == original_copy

    def test_assert_isolation_intact_passes_when_original_unchanged(self):
        """assert_isolation_intact() should pass if original wasn't modified."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated.set("user.name", "Bob")

        # Should pass (original was never touched)
        assert isolated.assert_isolation_intact() is True

    def test_assert_isolation_intact_detects_external_modification(self):
        """assert_isolation_intact() should fail if original was modified externally."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        # Simulate external modification of original (bad!)
        original["user"]["name"] = "Eve"

        # Should fail
        with pytest.raises(RuntimeError, match="isolation violation"):
            isolated.assert_isolation_intact()


class TestContextHashing:
    """Test hashing for audit chain."""

    def test_get_context_hash_is_deterministic(self):
        """Same context should produce same hash."""
        original = {"user": {"name": "Alice", "role": "admin"}}

        isolated1 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated2 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        hash1 = isolated1.get_context_hash()
        hash2 = isolated2.get_context_hash()

        assert hash1 == hash2

    def test_get_context_hash_changes_after_mutation(self):
        """Hash should change after mutation."""
        original = {"user": {"name": "Alice"}}

        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        hash_before = isolated.get_context_hash()
        isolated.set("user.name", "Bob")
        hash_after = isolated.get_context_hash()

        assert hash_before != hash_after


class TestContextMerger:
    """Test merging deltas back into original context."""

    def test_merge_deltas_applies_mutations(self):
        """merge_deltas should apply mutations to original context."""
        original = {"user": {"name": "Alice", "role": "admin"}}

        # Simulate skill execution
        isolated = IsolatedTaskContext.create_isolated(
            original_context=original,
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="_default",
        )

        isolated.set("user.name", "Bob")
        isolated.set("user.role", "viewer")

        deltas = isolated.get_mutations()

        # Merge
        merged, applied = ContextMerger.merge_deltas(
            original_context=original,
            deltas=deltas,
            skill_id="os.test_skill",
            tenant_id="_default",
        )

        # Verify merge
        assert merged["user"]["name"] == "Bob"
        assert merged["user"]["role"] == "viewer"
        assert len(applied) == 2

    def test_merge_deltas_filters_by_skill_id(self):
        """merge_deltas should only apply deltas from the specified skill."""
        original = {"user": {"name": "Alice"}, "config": {"theme": "light"}}

        # Two skills made different mutations
        deltas = {
            "os.skill_a:user.name": StateDelta(
                skill_id="os.skill_a",
                path="user.name",
                old_value="Alice",
                new_value="Bob",
            ),
            "os.skill_b:config.theme": StateDelta(
                skill_id="os.skill_b",
                path="config.theme",
                old_value="light",
                new_value="dark",
            ),
        }

        # Merge only skill_a's deltas
        merged, applied = ContextMerger.merge_deltas(
            original_context=original,
            deltas=deltas,
            skill_id="os.skill_a",
            tenant_id="_default",
        )

        # Only skill_a's delta should be applied
        assert merged["user"]["name"] == "Bob"
        assert merged["config"]["theme"] == "light"  # Unchanged (from skill_b)
        assert len(applied) == 1

    def test_merge_compute_hash_includes_tenant(self):
        """merge_compute_hash should include tenant_id in hash."""
        context_a = {"user": {"name": "Alice"}}
        context_b = {"user": {"name": "Alice"}}

        # Same context, different tenant
        hash_tenant_1 = ContextMerger.compute_merge_hash(
            merged_context=context_a,
            tenant_id="tenant_1",
        )
        hash_tenant_2 = ContextMerger.compute_merge_hash(
            merged_context=context_b,
            tenant_id="tenant_2",
        )

        # Hashes should differ (different tenant)
        assert hash_tenant_1 != hash_tenant_2


class TestStateDelta:
    """Test StateDelta serialization."""

    def test_state_delta_to_dict(self):
        """to_dict() should serialize delta for audit logging."""
        delta = StateDelta(
            skill_id="os.test_skill",
            path="user.role",
            old_value="admin",
            new_value="viewer",
        )

        delta_dict = delta.to_dict()

        assert delta_dict["skill_id"] == "os.test_skill"
        assert delta_dict["path"] == "user.role"
        assert delta_dict["old_value"] == "admin"
        assert delta_dict["new_value"] == "viewer"
        assert "timestamp" in delta_dict


class TestMultiTenantIsolation:
    """Test that multi-tenant isolation is enforced."""

    def test_different_tenants_have_separate_contexts(self):
        """Contexts from different tenants should be isolated."""
        original = {"user": {"name": "Alice"}}

        isolated_1 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="tenant_a",
        )

        isolated_2 = IsolatedTaskContext.create_isolated(
            original_context=original.copy(),
            skill_id="os.test_skill",
            task_id="task_123",
            tenant_id="tenant_b",
        )

        # Modify each
        isolated_1.set("user.name", "Bob")
        isolated_2.set("user.name", "Charlie")

        # Verify separation
        assert isolated_1.get("user.name") == "Bob"
        assert isolated_2.get("user.name") == "Charlie"
        assert isolated_1._tenant_id == "tenant_a"
        assert isolated_2._tenant_id == "tenant_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

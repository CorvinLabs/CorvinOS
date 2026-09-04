"""Unit Tests: L4 k=4 — Postcondition Validation.

Verify:
1. Postconditions evaluate correctly
2. Failed postconditions reject mutations
3. Predicate evaluation errors (fail-closed)
4. No postconditions = accept all mutations
"""

import pytest
from core.skills.context_isolation import ContextMutationValidator
from core.skills.contract import Predicate


class TestPostconditionValidation:
    """Test postcondition validation."""

    def test_validate_deltas_no_postconditions(self):
        """No postconditions should accept all mutations."""
        deltas = {
            "user.role": {"old_value": "admin", "new_value": "viewer"}
        }
        context_after = {"user": {"role": "viewer"}}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas=deltas,
            postconditions=[],
            context_after=context_after,
        )

        assert valid is True
        assert "postconditions" in reason.lower()

    def test_validate_deltas_passing_postcondition(self):
        """Postcondition that passes should accept mutations."""
        postcond = Predicate(
            name="role_changed",
            condition=lambda ctx: ctx.get("user", {}).get("role") == "viewer",
        )

        context_after = {"user": {"role": "viewer"}}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas={"user.role": {"old_value": "admin", "new_value": "viewer"}},
            postconditions=[postcond],
            context_after=context_after,
        )

        assert valid is True
        assert "all postconditions" in reason.lower()

    def test_validate_deltas_failing_postcondition(self):
        """Postcondition that fails should reject mutations."""
        postcond = Predicate(
            name="role_not_admin",
            condition=lambda ctx: ctx.get("user", {}).get("role") != "admin",
        )

        # Context still has admin role (postcondition expects it not to)
        context_after = {"user": {"role": "admin"}}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas={"user.role": {"old_value": "admin", "new_value": "admin"}},
            postconditions=[postcond],
            context_after=context_after,
        )

        assert valid is False
        assert "role_not_admin" in reason

    def test_validate_deltas_multiple_postconditions(self):
        """Multiple postconditions, all must pass."""
        postcond1 = Predicate(
            name="role_is_viewer",
            condition=lambda ctx: ctx.get("user", {}).get("role") == "viewer",
        )
        postcond2 = Predicate(
            name="user_verified",
            condition=lambda ctx: ctx.get("user", {}).get("verified", False) is True,
        )

        context_after = {"user": {"role": "viewer", "verified": True}}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas={},
            postconditions=[postcond1, postcond2],
            context_after=context_after,
        )

        assert valid is True

    def test_validate_deltas_multiple_postconditions_one_fails(self):
        """If any postcondition fails, reject."""
        postcond1 = Predicate(
            name="role_is_viewer",
            condition=lambda ctx: ctx.get("user", {}).get("role") == "viewer",
        )
        postcond2 = Predicate(
            name="user_verified",
            condition=lambda ctx: ctx.get("user", {}).get("verified", False) is True,
        )

        # postcond2 will fail (verified is False)
        context_after = {"user": {"role": "viewer", "verified": False}}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas={},
            postconditions=[postcond1, postcond2],
            context_after=context_after,
        )

        assert valid is False
        assert "user_verified" in reason

    def test_validate_deltas_postcondition_evaluation_error(self):
        """Postcondition evaluation error should fail (fail-closed)."""
        def bad_condition(ctx):
            raise ValueError("Intentional error in postcondition")

        postcond = Predicate(
            name="bad_postcond",
            condition=bad_condition,
        )

        context_after = {"data": "test"}

        valid, reason = ContextMutationValidator.validate_deltas(
            deltas={},
            postconditions=[postcond],
            context_after=context_after,
        )

        assert valid is False
        assert "evaluation error" in reason.lower() or "bad_postcond" in reason

    def test_validate_deltas_postcondition_complex_predicate(self):
        """Postcondition with complex logic."""
        def complex_check(ctx):
            # Check that if role is viewer, then verified must be True
            role = ctx.get("user", {}).get("role")
            verified = ctx.get("user", {}).get("verified", False)
            if role == "viewer":
                return verified is True
            return True

        postcond = Predicate(
            name="viewer_must_be_verified",
            condition=complex_check,
        )

        # Valid: viewer and verified
        context_valid = {"user": {"role": "viewer", "verified": True}}
        valid, _ = ContextMutationValidator.validate_deltas(
            deltas={},
            postconditions=[postcond],
            context_after=context_valid,
        )
        assert valid is True

        # Invalid: viewer but not verified
        context_invalid = {"user": {"role": "viewer", "verified": False}}
        valid, _ = ContextMutationValidator.validate_deltas(
            deltas={},
            postconditions=[postcond],
            context_after=context_invalid,
        )
        assert valid is False


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

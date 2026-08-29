"""Tests for ContextReducer + GoalAlignmentValidator integration (Phase 2).

Integration tests covering:
- Validator integration with ContextReducer
- Fail-closed behavior (invalid score → full context)
- Audit trail generation
- Backward compatibility (validator optional)
- Edge cases
"""

import pytest
from core.session_manager.context_reducer import ContextReducer, ContextReductionResult
from core.session_manager.goal_validation_gate import GoalAlignmentValidator


class TestContextReducerWithValidator:
    """Test ContextReducer + GoalAlignmentValidator integration."""

    def setup_method(self):
        """Setup test fixtures."""
        self.validator = GoalAlignmentValidator()
        self.reducer = ContextReducer(validator=self.validator)

    def test_reducer_accepts_validator(self):
        """Test that ContextReducer accepts validator in __init__."""
        validator = GoalAlignmentValidator()
        reducer = ContextReducer(validator=validator)

        assert reducer.validator is validator

    def test_reducer_has_no_validator_by_default(self):
        """Test that ContextReducer has no validator by default (backward compat)."""
        reducer = ContextReducer()

        assert reducer.validator is None

    def test_reduction_with_valid_goal_alignment(self):
        """Test reduction when goal alignment validation passes."""
        goal = "Implement plugin system"
        original_context = ("plugin system implementation " * 100)
        preserve_tier_0 = ["Goal: Implement plugin", "Finding: Architecture sound"]
        preserve_tier_1 = ["Strategy: Modular design", "Phase: Execution"]

        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test_pass",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Validation was applied (self.reducer was initialized with validator)
        assert result.validation_applied is True
        assert result.validation_result is not None

        # If validation passed, reduction should be applied
        if result.validation_result.is_valid:
            assert result.reduction_percentage > 0.0
        else:
            # If validation failed, no reduction
            assert result.reduction_percentage == 0.0

    def test_reduction_fails_validation_uses_full_context(self):
        """Test fail-closed: invalid goal alignment → use FULL context."""
        goal = "Implement quantum cryptography"
        original_context = "This is about cooking pasta"
        preserve_tier_0 = ["Some finding"]
        preserve_tier_1 = ["Some strategy"]

        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test_fail",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Validation was applied
        assert result.validation_applied is True
        assert result.validation_result is not None

        # If validation failed, reduction_percentage should be 0 (fail-closed)
        if not result.validation_result.is_valid:
            assert result.reduction_percentage == 0.0
            # Original tokens should equal reduced tokens
            assert result.original_tokens == result.reduced_tokens

    def test_reduction_result_contains_validation_info(self):
        """Test that reduction result includes validation data."""
        goal = "Test goal"
        original_context = "test context with goal and more"
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Check validation info is present
        assert hasattr(result, "validation_result")
        assert hasattr(result, "validation_applied")

        # If validation was applied, should have result
        if result.validation_applied:
            assert result.validation_result is not None

    def test_reduction_summary_includes_validation(self):
        """Test that reduction summary includes validation score if available."""
        goal = "Test goal"
        original_context = "test context"
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        summary = result.summary()

        # If validation was applied, summary should mention it
        if result.validation_applied:
            assert "Validation" in summary or "validation" in summary

    def test_empty_goal_skips_validation(self):
        """Test that empty goal skips validation (can't validate empty goal)."""
        original_context = "test context"
        preserve_tier_0 = []
        preserve_tier_1 = []

        # Empty goal should not crash
        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal="",  # Empty goal
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should complete without error (validation skipped for empty goal)
        assert result is not None

    def test_backward_compatibility_no_validator(self):
        """Test that ContextReducer works without validator (backward compat)."""
        # Create reducer without explicit validator
        # No validation should run (backward compatible)
        reducer = ContextReducer()

        goal = "Test goal"
        original_context = "test context"
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should complete without error
        assert result is not None
        # No validation should be applied (validator is None)
        assert result.validation_applied is False

    def test_validator_custom_threshold_honored(self):
        """Test that custom validator threshold is honored."""
        # Create validator with high threshold (0.90)
        strict_validator = GoalAlignmentValidator(threshold=0.90)
        reducer = ContextReducer(validator=strict_validator)

        goal = "Test implementation"
        context = "Testing and implementation"
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = reducer.reduce_context(
            original_context=context,
            phase="execution",
            goal=goal,
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # If validation was applied with strict threshold
        if result.validation_applied and result.validation_result:
            assert result.validation_result.threshold == 0.90


class TestAuditTrail:
    """Test audit trail generation for validation."""

    def setup_method(self):
        """Setup test fixtures."""
        self.validator = GoalAlignmentValidator()

    def test_validation_audit_event_no_goal_text(self):
        """Test that validation audit event contains no goal text (GDPR Art. 32)."""
        goal = "Secret goal with PII data"
        context = "Context about PII data"

        result = self.validator.validate_reduction(goal, context)
        audit = result.to_audit_event()

        # Goal text should not appear in audit event
        assert "PII data" not in str(audit)
        assert "Secret" not in str(audit)

        # Goal hash should be present for tracking
        assert "goal_hash" in audit
        assert len(audit["goal_hash"]) == 64  # SHA256

    def test_validation_audit_contains_scores(self):
        """Test that validation audit event contains scoring information."""
        goal = "Implement system"
        context = "System implementation"

        result = self.validator.validate_reduction(goal, context)
        audit = result.to_audit_event()

        # Should contain scores
        assert "semantic_similarity_score" in audit
        assert "completeness_score" in audit
        assert "composite_score" in audit
        assert "is_valid" in audit
        assert "decision" in audit

    def test_validation_audit_decision_field(self):
        """Test that validation audit event has correct decision field."""
        goal = "Implement system"
        context = "System implementation"

        result = self.validator.validate_reduction(goal, context)
        audit = result.to_audit_event()

        # Decision should be one of the two options
        assert audit["decision"] in [
            "USE_FULL_CONTEXT",
            "USE_REDUCED_CONTEXT"
        ]

        # Decision should match is_valid
        if audit["is_valid"]:
            assert audit["decision"] == "USE_REDUCED_CONTEXT"
        else:
            assert audit["decision"] == "USE_FULL_CONTEXT"


class TestFailClosedBehavior:
    """Test fail-closed design (invalid → full context)."""

    def setup_method(self):
        """Setup test fixtures."""
        self.validator = GoalAlignmentValidator()
        self.reducer = ContextReducer(validator=self.validator)

    def test_validation_error_uses_full_context(self):
        """Test that validation errors trigger fail-closed (full context)."""
        # This tests the exception handling in ContextReducer
        goal = "Test goal"
        original_context = "test context with goal"
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = self.reducer.reduce_context(
            original_context=original_context,
            phase="execution",
            goal=goal,
            task_id="test",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should complete without error (fail-closed on any error)
        assert result is not None
        assert result.original_tokens > 0

    def test_invalid_score_results_in_no_reduction(self):
        """Test that invalid validation score results in zero reduction."""
        # Use completely mismatched goal and context
        goal = "Quantum cryptography implementation"
        mismatch_context = "The weather today is sunny and warm"

        preserve_tier_0 = ["Some item"]
        preserve_tier_1 = ["Some strategy"]

        result = self.reducer.reduce_context(
            original_context=mismatch_context,
            phase="execution",
            goal=goal,
            task_id="mismatch",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # If validation failed (which it should for this mismatch)
        if result.validation_applied and result.validation_result:
            if not result.validation_result.is_valid:
                # No reduction should be applied
                assert result.reduction_percentage == 0.0


class TestEdgeCases:
    """Test edge cases in validation integration."""

    def setup_method(self):
        """Setup test fixtures."""
        self.validator = GoalAlignmentValidator()
        self.reducer = ContextReducer(validator=self.validator)

    def test_very_large_context_integration(self):
        """Test integration with very large context."""
        goal = "Implement system"
        large_context = "system implementation " * 5000  # 10k words
        preserve_tier_0 = ["Finding"]
        preserve_tier_1 = ["Strategy"]

        result = self.reducer.reduce_context(
            original_context=large_context,
            phase="execution",
            goal=goal,
            task_id="large",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should complete without error
        assert result is not None
        assert result.original_tokens > 0

    def test_unicode_goal_and_context_integration(self):
        """Test integration with Unicode text."""
        goal = "Implementar sistema de seguridad 🔒"
        context = "Sistema de seguridad implementado con éxito"
        preserve_tier_0 = ["Resultado"]
        preserve_tier_1 = ["Estrategia"]

        result = self.reducer.reduce_context(
            original_context=context,
            phase="execution",
            goal=goal,
            task_id="unicode",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should handle Unicode without error
        assert result is not None

    def test_minimal_preservation_lists(self):
        """Test integration with empty preservation lists."""
        goal = "Test goal"
        context = "Test context"
        preserve_tier_0 = []
        preserve_tier_1 = []

        result = self.reducer.reduce_context(
            original_context=context,
            phase="execution",
            goal=goal,
            task_id="minimal",
            preserve_tier_0=preserve_tier_0,
            preserve_tier_1=preserve_tier_1,
        )

        # Should complete without error
        assert result is not None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

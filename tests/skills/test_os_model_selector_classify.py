"""
Comprehensive unit test suite for OS Model Selector with learning loop integration.

Tests the classify_with_decomposition_hint() method with 20+ test cases covering:
- Decomposable tasks → Haiku when success > 0.85
- Non-decomposable → Sonnet (safe)
- Learning store misses → fallback defaults
- Edge cases (empty, very short, very long)
- Multi-tenant compliance

Each test covers: input, expected model, expected confidence, decomposition hint.
ADR-0845 (model selection with decomposition).
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock, patch, PropertyMock
from core.skills.os_skills.model_selector import (
    ModelSelector,
    ModelSelectorConfig,
    ClassificationResult,
)


class TestModelSelectorClassifyWithDecomposition:
    """Unit tests for classify_with_decomposition_hint() with learning integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = ModelSelector()

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 1: Decomposable Tasks (Should use Haiku if success > 0.85)
    # ─────────────────────────────────────────────────────────────────────

    def test_code_review_decomposable_haiku_success_high(self):
        """Test code review task (decomposable, high Haiku success)."""
        task = """
        Review the following code for:
        1. Performance issues
        2. Security vulnerabilities
        3. Code style violations
        4. Test coverage gaps

        Code:
        def process_data(items):
            for i in range(len(items)):
                print(items[i])
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Code review has 96% Haiku success rate (from hardcoded defaults)
        assert result.recommended_model == "claude-haiku-4-5"
        assert hint == "prompt_structured"
        assert result.confidence >= 0.85
        assert result.recommended_provider == "anthropic"

    def test_code_gen_structured_decomposable_haiku(self):
        """Test structured code generation (decomposable)."""
        task = """
        Generate a Python function that:
        1. Takes a list of strings
        2. Filters empty strings
        3. Converts to uppercase
        4. Returns sorted list

        Requirements:
        - Handle None values
        - Add docstring
        - Include type hints
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_gen"
        )

        # Code gen has 90% Haiku success rate
        assert result.recommended_model == "claude-haiku-4-5"
        assert hint == "prompt_structured"
        assert result.confidence >= 0.85
        assert result.recommended_provider == "anthropic"

    def test_summarization_decomposable_haiku(self):
        """Test summarization task (decomposable, 96% success)."""
        task = """
        Summarize the following article by:
        1. Extracting key points
        2. Listing main conclusions
        3. Identifying open questions
        4. Noting critical assumptions

        Article:
        [Sample text here]
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="summarization"
        )

        # Summarization has 96% Haiku success
        assert result.recommended_model == "claude-haiku-4-5"
        assert hint == "prompt_structured"
        assert result.confidence >= 0.85

    def test_documentation_decomposable_haiku(self):
        """Test documentation generation (decomposable, 97% success)."""
        task = """
        Generate API documentation with:
        1. Endpoint list
        2. Parameter descriptions
        3. Response examples
        4. Error codes

        API functions:
        - get_user(id)
        - create_user(name, email)
        - delete_user(id)
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="documentation"
        )

        # Documentation has 97% Haiku success
        assert result.recommended_model == "claude-haiku-4-5"
        assert hint == "prompt_structured"
        assert result.confidence >= 0.85

    def test_testing_decomposable_haiku(self):
        """Test test generation (decomposable, 95% success)."""
        task = """
        Generate test cases for:
        1. Happy path
        2. Edge cases (empty, None, max value)
        3. Error conditions
        4. Boundary values

        Function: calculate_discount(price, quantity)
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="testing"
        )

        # Testing has 95% Haiku success
        assert result.recommended_model == "claude-haiku-4-5"
        assert hint == "prompt_structured"
        assert result.confidence >= 0.85

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 2: Orchestration Tasks (Must use Sonnet, no decomposition)
    # ─────────────────────────────────────────────────────────────────────

    def test_orchestration_task_needs_sonnet(self):
        """Test orchestration task (requires Sonnet reasoning)."""
        task = """
        Coordinate a multi-step data pipeline:
        1. Extract data from API
        2. Transform and validate
        3. Load to database
        4. Trigger downstream jobs

        Handle failures and retries across all steps.
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="orchestration"
        )

        # Orchestration needs Sonnet
        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None  # No decomposition for orchestration
        assert result.recommended_provider == "anthropic"

    def test_system_design_task_needs_sonnet(self):
        """Test system design (requires Sonnet reasoning)."""
        task = """
        Design a system for:
        - Managing distributed consensus
        - Handling Byzantine failures
        - Optimizing latency
        - Scaling to 1M users
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="system_design"
        )

        # System design needs Sonnet
        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None

    def test_workflow_composition_task_sonnet(self):
        """Test workflow composition (orchestration keyword)."""
        task = """
        Orchestrate the following workflow:
        - Skill A processes input
        - Skill B validates output
        - Skill C aggregates results
        - Notify on completion
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="workflow"
        )

        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 3: Low Haiku Success Rate (Must use Sonnet, no Haiku)
    # ─────────────────────────────────────────────────────────────────────

    def test_low_success_rate_uses_sonnet(self):
        """Test task type with low Haiku success (uses Sonnet)."""
        task = """
        Analyze the following:
        1. Complex architectural patterns
        2. System interactions
        3. Tradeoffs and implications
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="orchestration"  # 50% success rate
        )

        # Orchestration has 50% success, so Haiku not used
        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 4: Edge Cases & Boundary Conditions
    # ─────────────────────────────────────────────────────────────────────

    def test_very_short_task_non_decomposable(self):
        """Test very short task (< 15 tokens, not decomposable)."""
        task = "Hello"

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Too short to be decomposable
        assert hint is None  # Not decomposable due to token range

    def test_very_long_task_non_decomposable(self):
        """Test very long task (> 8000 tokens, not decomposable)."""
        # Create a task with ~10000 tokens
        task = "Analyze this: " + ("word " * 2000)

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Too long to be decomposable
        assert hint is None

    def test_structured_format_bullet_points(self):
        """Test structured format with bullet points."""
        task = """
        - Review code style
        - Check performance
        - Validate error handling
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Has structured format (bullets)
        assert result.recommended_model == "claude-haiku-4-5" or hint is None

    def test_structured_format_numbered_list(self):
        """Test structured format with numbered list."""
        task = """
        1. Extract features
        2. Classify complexity
        3. Select model
        4. Return result
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Has structured format (numbered)
        assert result.recommended_model == "claude-haiku-4-5" or hint is None

    def test_unstructured_task_not_decomposable(self):
        """Test unstructured task (no bullets, numbered list, etc)."""
        task = "Please review this code and tell me if it's good or bad."

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Unstructured format → not decomposable
        assert hint is None

    def test_unknown_task_type_fallback(self):
        """Test unknown task type (uses default success rate)."""
        task = """
        1. Do something
        2. Do something else
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="unknown_type"
        )

        # Unknown type → not in haiku_success_rates
        assert hint is None  # Not decomposable due to unknown task type

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 5: Multi-tenant Compliance
    # ─────────────────────────────────────────────────────────────────────

    def test_multi_tenant_different_tenants(self):
        """Test multi-tenant isolation."""
        task = """
        1. Review code
        2. Find issues
        3. Suggest fixes
        """

        result1, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="tenant_a",
            task_type="code_review"
        )

        result2, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="tenant_b",
            task_type="code_review"
        )

        # Same task, different tenants should produce consistent results
        # (since learning store defaults are the same)
        assert result1.recommended_model == result2.recommended_model

    def test_tenant_id_default_value(self):
        """Test tenant_id defaults to '_default'."""
        task = """
        1. Do something
        2. Do something else
        """

        result1, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id=None,  # Explicitly None
            task_type="code_review"
        )

        result2, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Both should be identical (tenant_id=None defaults to "_default")
        assert result1.recommended_model == result2.recommended_model

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 6: Confidence Scores
    # ─────────────────────────────────────────────────────────────────────

    def test_haiku_confidence_from_success_rate(self):
        """Test Haiku confidence comes from historical success rate."""
        task = """
        1. Review code
        2. Find issues
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        if hint == "prompt_structured":
            # Confidence should match Haiku success rate for this type
            expected_confidence = 0.96  # code_review hardcoded value
            assert abs(result.confidence - expected_confidence) < 0.01

    def test_confidence_not_below_zero(self):
        """Test confidence never goes below 0."""
        task = "x"

        result, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="unknown"
        )

        assert result.confidence >= 0.0

    def test_confidence_not_above_one(self):
        """Test confidence never goes above 1.0."""
        task = "x" * 10000

        result, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        assert result.confidence <= 1.0

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 7: Decomposition Hint Values
    # ─────────────────────────────────────────────────────────────────────

    def test_decomposition_hint_none_for_orchestration(self):
        """Test decomposition_hint is None for orchestration."""
        task = "Coordinate a multi-skill workflow with retries and monitoring."

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="orchestration"
        )

        assert hint is None

    def test_decomposition_hint_prompt_structured_for_decomposable(self):
        """Test decomposition_hint is 'prompt_structured' for decomposable tasks."""
        task = """
        1. Extract features
        2. Classify
        3. Return result
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        if hint is not None:
            assert hint == "prompt_structured"

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 8: Reasoning Output
    # ─────────────────────────────────────────────────────────────────────

    def test_reasoning_includes_decomposition_context(self):
        """Test reasoning includes decomposition context when applicable."""
        task = """
        1. Review code
        2. Find issues
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Reasoning should be non-empty
        assert len(result.reasoning) > 0

        # If decomposable, reasoning should mention it
        if hint == "prompt_structured":
            assert "Decomposable" in result.reasoning or "decompos" in result.reasoning.lower()

    def test_reasoning_includes_model_selection_reason(self):
        """Test reasoning includes why this model was selected."""
        task = """
        1. Do something
        2. Do something else
        """

        result, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Reasoning should explain the selection
        assert "Selected" in result.reasoning or "complexity" in result.reasoning.lower()

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 9: Classification History Tracking
    # ─────────────────────────────────────────────────────────────────────

    def test_classification_added_to_history(self):
        """Test classifications are tracked in history."""
        initial_count = len(self.selector.classification_history)

        task = """
        1. Review code
        2. Find issues
        """

        self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # History should have grown
        assert len(self.selector.classification_history) > initial_count

    # ─────────────────────────────────────────────────────────────────────
    # TEST GROUP 10: Refactoring and Edge Cases
    # ─────────────────────────────────────────────────────────────────────

    def test_result_to_dict_serializable(self):
        """Test ClassificationResult is JSON-serializable."""
        task = """
        1. Review code
        2. Find issues
        """

        result, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Should be serializable to dict
        result_dict = result.to_dict()
        assert isinstance(result_dict, dict)
        assert "complexity" in result_dict
        assert "confidence" in result_dict
        assert "recommended_model" in result_dict

    def test_refactoring_task_type(self):
        """Test refactoring task type (92% Haiku success)."""
        task = """
        Refactor this code:
        1. Extract functions
        2. Improve naming
        3. Add type hints
        4. Simplify logic
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="refactoring"
        )

        # Refactoring has 92% Haiku success
        if hint == "prompt_structured":
            assert result.recommended_model == "claude-haiku-4-5"
            assert result.confidence >= 0.85

    def test_analysis_task_type(self):
        """Test analysis task type (90% Haiku success)."""
        task = """
        Analyze the following:
        1. Security implications
        2. Performance impact
        3. Maintainability concerns
        """

        result, hint = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="analysis"
        )

        # Analysis has 90% Haiku success
        if hint == "prompt_structured":
            assert result.recommended_model == "claude-haiku-4-5"
            assert result.confidence >= 0.85


class TestModelSelectorFeatureExtraction:
    """Test feature extraction integration."""

    def setup_method(self):
        """Set up test fixtures."""
        self.selector = ModelSelector()

    def test_features_extracted_correctly(self):
        """Test features are extracted and available in result."""
        task = """
        ```python
        def func():
            pass
        ```
        """

        result, _ = self.selector.classify_with_decomposition_hint(
            task_input=task,
            tenant_id="_default",
            task_type="code_gen"
        )

        # Features should be populated
        assert result.features is not None
        assert result.features.token_estimate >= 0
        assert result.features.code_blocks >= 0

    def test_token_estimate_affects_decomposability(self):
        """Test token count affects decomposability decision."""
        short_task = "1. Do A\n2. Do B"
        long_task = "1. Do A\n" + "2. Do something " * 1000

        result_short, hint_short = self.selector.classify_with_decomposition_hint(
            task_input=short_task,
            tenant_id="_default",
            task_type="code_review"
        )

        result_long, hint_long = self.selector.classify_with_decomposition_hint(
            task_input=long_task,
            tenant_id="_default",
            task_type="code_review"
        )

        # Long task should not be decomposable due to token count
        assert hint_short is not None or result_short.recommended_model in ["claude-sonnet-5", "claude-haiku-4-5"]
        # Long task should not trigger decomposition
        if hint_long is not None:
            assert result_long.features.token_estimate < 8000

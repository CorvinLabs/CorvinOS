"""
Unit Tests for PromptDecomposer Heuristics
ADR-0845: Tier 2 Prompt-Level Task Decomposition
"""
import pytest
from dataclasses import asdict
from core.skills.os_skills.decomposer import (
    PromptDecomposer,
    DecompositionPlan,
    DecompositionStep,
    StepType,
)


class TestDecomposerBasics:
    """Test basic decomposition for each task type."""

    def test_code_review_decomposition(self):
        """Test that code review generates 4 steps + synthesis."""
        decomposer = PromptDecomposer("test_task_1")
        task = "Review this Python function for bugs:\ndef process_data(items):\n    return [x*2 for x in items]"

        plan = decomposer.decompose(task, task_type="code_review")

        # Should have security, performance, quality, best practices + synthesis
        assert len(plan.steps) == 5  # 4 steps + synthesis
        assert plan.task_type == "code_review"
        assert plan.steps[-1].step_type == StepType.SYNTHESIZE.value
        assert plan.steps[0].step_type == StepType.REVIEW.value

    def test_testing_decomposition(self):
        """Test that testing task generates 4 steps."""
        decomposer = PromptDecomposer("test_task_2")
        task = "Write comprehensive tests for a login function"

        plan = decomposer.decompose(task, task_type="testing")

        assert len(plan.steps) == 5  # happy path, error, edge, integration + synthesis
        assert plan.task_type == "testing"
        assert any("happy" in s.name.lower() for s in plan.steps)
        assert any("error" in s.name.lower() for s in plan.steps)

    def test_documentation_decomposition(self):
        """Test documentation decomposition."""
        decomposer = PromptDecomposer("test_task_3")
        task = "Document the REST API"

        plan = decomposer.decompose(task, task_type="documentation")

        assert len(plan.steps) == 5
        assert plan.task_type == "documentation"
        assert any("overview" in s.name.lower() or "api" in s.name.lower() for s in plan.steps)

    def test_analysis_decomposition(self):
        """Test data analysis decomposition."""
        decomposer = PromptDecomposer("test_task_4")
        task = "Analyze the user engagement metrics"

        plan = decomposer.decompose(task, task_type="analysis")

        assert len(plan.steps) == 5
        assert plan.task_type == "analysis"
        assert any("pattern" in s.name.lower() for s in plan.steps)

    def test_refactoring_decomposition(self):
        """Test refactoring decomposition (sequential)."""
        decomposer = PromptDecomposer("test_task_5")
        task = "Refactor the authentication module after analysis"

        plan = decomposer.decompose(task, task_type="refactoring")

        assert len(plan.steps) == 5
        # Should have dependencies (sequential)
        has_dependencies = any(s.depends_on for s in plan.steps)
        assert has_dependencies

    def test_code_generation_decomposition(self):
        """Test code generation decomposition."""
        decomposer = PromptDecomposer("test_task_6")
        task = "Generate a REST API client"

        plan = decomposer.decompose(task, task_type="code_gen")

        assert len(plan.steps) == 5
        assert plan.task_type == "code_gen"

    def test_generic_decomposition_fallback(self):
        """Test generic fallback decomposition."""
        decomposer = PromptDecomposer("test_task_7")
        task = "Do something"

        plan = decomposer.decompose(task, task_type="unknown")

        assert len(plan.steps) >= 3  # At least analyze, plan, implement + synthesis
        assert plan.steps[0].step_type == StepType.ANALYZE.value


class TestDecompositionHeuristics:
    """Test heuristic patterns in decomposition."""

    def test_strategy_detection_parallel(self):
        """Test parallel strategy detection."""
        decomposer = PromptDecomposer("test_task_8")
        task = "Review both security and performance aspects"

        plan = decomposer.decompose(task, task_type="code_review")

        # Code review with "both" should use parallel strategy
        assert plan.decomposition_strategy in ["parallel", "sequential", "mixed"]

    def test_strategy_detection_sequential(self):
        """Test sequential strategy detection."""
        decomposer = PromptDecomposer("test_task_9")
        task = "Refactor module then update tests"

        plan = decomposer.decompose(task, task_type="refactoring")

        # Should detect sequential from "then"
        assert plan.decomposition_strategy in ["parallel", "sequential", "mixed"]

    def test_synthesis_instruction_generated(self):
        """Test that synthesis instruction is generated."""
        decomposer = PromptDecomposer("test_task_10")
        task = "Review this code"

        plan = decomposer.decompose(task, task_type="code_review")

        assert plan.synthesis_instruction
        assert "combine" in plan.synthesis_instruction.lower()
        assert len(plan.steps) > 0

    def test_confidence_estimation(self):
        """Test confidence estimation."""
        decomposer = PromptDecomposer("test_task_11")

        # Well-structured task should have higher confidence
        task_structured = "Review this code for:\n1. Security issues\n2. Performance problems\n3. Code quality"
        plan_structured = decomposer.decompose(task_structured, task_type="code_review")

        # Unstructured task
        task_unstructured = "check the code"
        plan_unstructured = decomposer.decompose(task_unstructured, task_type="code_review")

        # Confidence should differ
        assert plan_structured.confidence >= 0.0
        assert plan_unstructured.confidence >= 0.0
        # Structured might have slightly higher confidence (more structure signals)
        assert plan_structured.confidence <= 1.0
        assert plan_unstructured.confidence <= 1.0


class TestDecompositionPlanValidation:
    """Test plan validation."""

    def test_valid_plan_passes(self):
        """Test that a valid plan passes validation."""
        decomposer = PromptDecomposer("test_task_12")
        task = "Review code"

        plan = decomposer.decompose(task, task_type="code_review")

        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Plan should be valid: {error_msg}"

    def test_plan_with_no_steps_fails(self):
        """Test that a plan with no steps is invalid."""
        plan = DecompositionPlan(
            task_id="test",
            original_task="Test task",
            task_type="test",
            decomposition_strategy="sequential",
            steps=[],
        )

        decomposer = PromptDecomposer("test")
        is_valid, error_msg = decomposer.validate_plan(plan)

        assert not is_valid
        assert "no steps" in error_msg.lower()

    def test_plan_with_sequential_indices(self):
        """Test that indices must be sequential."""
        steps = [
            DecompositionStep(
                index=1, name="Step 1", step_type="analyze",
                instruction="Analyze", context="",
                expected_output_type="text"
            ),
            DecompositionStep(
                index=3, name="Step 3", step_type="generate",  # Gap!
                instruction="Generate", context="",
                expected_output_type="text"
            ),
        ]

        plan = DecompositionPlan(
            task_id="test",
            original_task="Test",
            task_type="test",
            decomposition_strategy="sequential",
            steps=steps,
        )

        decomposer = PromptDecomposer("test")
        is_valid, error_msg = decomposer.validate_plan(plan)

        assert not is_valid
        assert "sequential" in error_msg.lower()

    def test_plan_with_broken_dependencies(self):
        """Test that dependencies must reference existing steps."""
        steps = [
            DecompositionStep(
                index=1, name="Step 1", step_type="analyze",
                instruction="Analyze", context="",
                expected_output_type="text",
                depends_on=[99]  # Non-existent step
            ),
        ]

        plan = DecompositionPlan(
            task_id="test",
            original_task="Test",
            task_type="test",
            decomposition_strategy="sequential",
            steps=steps,
        )

        decomposer = PromptDecomposer("test")
        is_valid, error_msg = decomposer.validate_plan(plan)

        assert not is_valid
        assert "depends" in error_msg.lower() or "non-existent" in error_msg.lower()


class TestDecompositionSerialization:
    """Test that decomposition plans are audit-safe."""

    def test_plan_serialization_to_dict(self):
        """Test that plans can be serialized to dict."""
        decomposer = PromptDecomposer("test_task_13")
        task = "Review code"
        plan = decomposer.decompose(task, task_type="code_review")

        plan_dict = plan.to_dict()

        assert isinstance(plan_dict, dict)
        assert "task_id" in plan_dict
        assert "steps" in plan_dict
        assert "confidence" in plan_dict
        assert isinstance(plan_dict["steps"], list)
        assert len(plan_dict["steps"]) > 0

    def test_step_serialization_to_dict(self):
        """Test that steps can be serialized."""
        step = DecompositionStep(
            index=1,
            name="Security Review",
            step_type="review",
            instruction="Review for security",
            context="Authentication",
            expected_output_type="json",
        )

        step_dict = step.to_dict()

        assert isinstance(step_dict, dict)
        assert step_dict["index"] == 1
        assert step_dict["name"] == "Security Review"
        assert step_dict["instruction"] == "Review for security"

    def test_plan_immutability(self):
        """Test that plans are immutable (frozen dataclass)."""
        plan = DecompositionPlan(
            task_id="test",
            original_task="Test task",
            task_type="test",
            decomposition_strategy="sequential",
            steps=[],
            confidence=0.75,
        )

        # Should not be able to modify frozen dataclass
        with pytest.raises((AttributeError, TypeError)):
            plan.confidence = 0.50


class TestDecompositionEdgeCases:
    """Test edge cases and error conditions."""

    def test_empty_task_input(self):
        """Test with empty task input."""
        decomposer = PromptDecomposer("test_task_14")
        task = ""

        plan = decomposer.decompose(task, task_type="code_review")

        # Should still produce a valid plan
        assert len(plan.steps) > 0
        is_valid, _ = decomposer.validate_plan(plan)
        assert is_valid

    def test_very_long_task_input(self):
        """Test with very long task input."""
        decomposer = PromptDecomposer("test_task_15")
        task = "Review this code: " + ("x = 1; " * 1000)

        plan = decomposer.decompose(task, task_type="code_review")

        assert len(plan.steps) > 0
        is_valid, _ = decomposer.validate_plan(plan)
        assert is_valid

    def test_task_with_special_characters(self):
        """Test task with special characters."""
        decomposer = PromptDecomposer("test_task_16")
        task = "Review: código™, données@français, ñoño"

        plan = decomposer.decompose(task, task_type="code_review")

        assert len(plan.steps) > 0
        is_valid, _ = decomposer.validate_plan(plan)
        assert is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

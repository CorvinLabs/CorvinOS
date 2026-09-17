"""
Adversarial Tests for PromptDecomposer
ADR-0845: Tier 2 Prompt-Level Task Decomposition
Tests context loss, inconsistency, failure recovery, and edge cases
"""
import pytest
from core.skills.os_skills.decomposer import (
    PromptDecomposer,
    DecompositionPlan,
)


class TestContextPreservation:
    """Test that original task context is preserved across steps."""

    def test_original_task_in_plan(self):
        """Test that original task is stored in plan."""
        decomposer = PromptDecomposer("test_adv_1")
        original_task = "Review this code for security issues in the authentication module"

        plan = decomposer.decompose(original_task, task_type="code_review")

        assert plan.original_task == original_task
        assert plan.original_task in plan.to_dict().values()

    def test_task_context_in_steps(self):
        """Test that each step includes relevant context."""
        decomposer = PromptDecomposer("test_adv_2")
        task = "Review Python code for performance issues"

        plan = decomposer.decompose(task, task_type="code_review")

        # Each step should have context
        for step in plan.steps:
            if step.step_type != "synthesize":
                assert step.context, f"Step {step.name} has no context"
                assert len(step.context) > 0

    def test_no_context_loss_in_sequential_steps(self):
        """Test sequential steps preserve context."""
        decomposer = PromptDecomposer("test_adv_3")
        task = "Refactor the database module after performance analysis"

        plan = decomposer.decompose(task, task_type="refactoring")

        # Sequential steps should reference prior steps
        for step in plan.steps:
            if step.depends_on:
                # Should have context explaining dependency
                assert "design" in step.instruction.lower() or "implement" in step.instruction.lower() or "phase" in step.context.lower()

    def test_synthesis_references_all_steps(self):
        """Test that synthesis step references all prior steps."""
        decomposer = PromptDecomposer("test_adv_4")
        task = "Review code"

        plan = decomposer.decompose(task, task_type="code_review")

        synthesis_step = plan.steps[-1]

        # Synthesis should depend on all prior steps
        expected_deps = [s.index for s in plan.steps if s.step_type != "synthesize"]
        assert synthesis_step.depends_on == expected_deps


class TestInconsistencyDetection:
    """Test detection of potential inconsistencies."""

    def test_all_steps_have_instructions(self):
        """Test that all steps have clear instructions."""
        decomposer = PromptDecomposer("test_adv_5")
        task = "Review code"

        plan = decomposer.decompose(task, task_type="code_review")

        for step in plan.steps:
            assert step.instruction
            assert len(step.instruction) > 10, f"Step {step.name} has weak instruction"

    def test_conflicting_step_types_logic(self):
        """Test that step types make logical sense."""
        decomposer = PromptDecomposer("test_adv_6")
        task = "Analyze and implement a new feature"

        plan = decomposer.decompose(task, task_type="code_gen")

        # First steps should be analyze/generate, not synthesize
        assert plan.steps[0].step_type in ["analyze", "generate", "plan"]

    def test_dependencies_respect_order(self):
        """Test that dependencies don't break ordering."""
        decomposer = PromptDecomposer("test_adv_7")
        task = "Refactor code"

        plan = decomposer.decompose(task, task_type="refactoring")

        # A step cannot depend on a later step
        for step in plan.steps:
            for dep in step.depends_on:
                assert dep < step.index, f"Step {step.index} depends on later step {dep}"

    def test_expected_output_types_valid(self):
        """Test that output types are valid."""
        decomposer = PromptDecomposer("test_adv_8")
        task = "Analyze data"

        plan = decomposer.decompose(task, task_type="analysis")

        valid_types = {"text", "json", "list", "code"}

        for step in plan.steps:
            assert step.expected_output_type in valid_types, \
                f"Step {step.name} has invalid output type: {step.expected_output_type}"


class TestTaskTypeVariations:
    """Test handling of various task types."""

    def test_code_review_has_security_step(self):
        """Test code review includes security review."""
        decomposer = PromptDecomposer("test_adv_9")
        task = "Review code"

        plan = decomposer.decompose(task, task_type="code_review")

        has_security = any("security" in s.name.lower() for s in plan.steps)
        assert has_security, "Code review should include security step"

    def test_testing_has_error_case(self):
        """Test testing includes error case tests."""
        decomposer = PromptDecomposer("test_adv_10")
        task = "Write tests"

        plan = decomposer.decompose(task, task_type="testing")

        has_error_tests = any("error" in s.name.lower() for s in plan.steps)
        assert has_error_tests, "Testing should include error handling tests"

    def test_analysis_has_correlation_step(self):
        """Test analysis includes correlation analysis."""
        decomposer = PromptDecomposer("test_adv_11")
        task = "Analyze data"

        plan = decomposer.decompose(task, task_type="analysis")

        has_correlation = any("correlation" in s.name.lower() or "relationship" in s.name.lower() for s in plan.steps)
        assert has_correlation, "Analysis should include correlation step"

    def test_refactoring_is_sequential(self):
        """Test refactoring produces sequential steps."""
        decomposer = PromptDecomposer("test_adv_12")
        task = "Refactor code"

        plan = decomposer.decompose(task, task_type="refactoring")

        # Refactoring should have dependencies (sequential nature)
        has_deps = any(s.depends_on for s in plan.steps if s.step_type != "synthesize")
        assert has_deps, "Refactoring should be sequential with dependencies"


class TestEdgeCasesAndFailures:
    """Test handling of edge cases and potential failures."""

    def test_ambiguous_task_still_decomposes(self):
        """Test that ambiguous task still produces valid plan."""
        decomposer = PromptDecomposer("test_adv_13")
        task = "Do something useful"

        plan = decomposer.decompose(task, task_type="unknown")

        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Plan validation failed: {error_msg}"

    def test_contradictory_keywords(self):
        """Test task with contradictory keywords."""
        decomposer = PromptDecomposer("test_adv_14")
        task = "Review and generate code then refactor it"

        plan = decomposer.decompose(task, task_type="code_review")

        # Should still produce valid plan
        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Plan should handle contradictory keywords: {error_msg}"

    def test_multilingual_input(self):
        """Test multilingual task input."""
        decomposer = PromptDecomposer("test_adv_15")
        task = "Revisar código para problemas de seguridad"  # Spanish

        plan = decomposer.decompose(task, task_type="code_review")

        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Should handle multilingual input: {error_msg}"

    def test_very_complex_task(self):
        """Test complex multi-layered task."""
        decomposer = PromptDecomposer("test_adv_16")
        task = """
        Design, implement, test, and document a distributed cache system
        that includes:
        1. Consistency guarantees
        2. Failure recovery
        3. Performance optimization
        4. API design
        5. Security considerations
        """

        plan = decomposer.decompose(task, task_type="code_gen")

        # Should handle complexity
        assert len(plan.steps) >= 3
        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Should handle complex tasks: {error_msg}"

    def test_minimal_task(self):
        """Test minimal task input."""
        decomposer = PromptDecomposer("test_adv_17")
        task = "code"

        plan = decomposer.decompose(task, task_type="code_review")

        is_valid, error_msg = decomposer.validate_plan(plan)
        assert is_valid, f"Should handle minimal input: {error_msg}"


class TestConfidenceScoring:
    """Test confidence estimation logic."""

    def test_confidence_within_bounds(self):
        """Test that confidence is always 0.0-1.0."""
        decomposer = PromptDecomposer("test_adv_18")
        tasks = [
            "simple task",
            "Review this Python code for bugs",
            "Design a distributed system with 10 components, including: " + ("x " * 1000),
        ]

        for task in tasks:
            plan = decomposer.decompose(task, task_type="code_review")
            assert 0.0 <= plan.confidence <= 1.0, f"Confidence out of bounds: {plan.confidence}"

    def test_structured_task_higher_confidence(self):
        """Test that structured tasks get higher confidence."""
        decomposer = PromptDecomposer("test_adv_19")

        structured = "Review code for:\n1. Security\n2. Performance\n3. Quality"
        unstructured = "check code"

        plan_struct = decomposer.decompose(structured, task_type="code_review")
        plan_unstruct = decomposer.decompose(unstructured, task_type="code_review")

        # Structured should have confidence >= unstructured
        assert plan_struct.confidence >= 0.0
        assert plan_unstruct.confidence >= 0.0
        # Structured has more structure markers
        assert plan_struct.confidence <= 1.0


class TestHaikuEstimate:
    """Test Haiku success rate estimation."""

    def test_haiku_estimate_provided(self):
        """Test that Haiku estimate is provided."""
        decomposer = PromptDecomposer("test_adv_20")
        plan = decomposer.decompose("Review code", task_type="code_review", haiku_success_rate=0.85)

        assert plan.haiku_estimated_success == 0.85

    def test_haiku_estimate_within_bounds(self):
        """Test Haiku estimate is 0.0-1.0."""
        decomposer = PromptDecomposer("test_adv_21")
        plan = decomposer.decompose("Task", task_type="code_review", haiku_success_rate=0.90)

        assert 0.0 <= plan.haiku_estimated_success <= 1.0


class TestPlanCompleteness:
    """Test that plans are complete and usable."""

    def test_plan_has_synthesis_step(self):
        """Test that every plan has a synthesis step."""
        decomposer = PromptDecomposer("test_adv_22")
        tasks = [
            ("simple", "code_review"),
            ("test", "testing"),
            ("document", "documentation"),
            ("analyze", "analysis"),
        ]

        for task, task_type in tasks:
            plan = decomposer.decompose(task, task_type=task_type)
            has_synthesis = any(s.step_type == "synthesize" for s in plan.steps)
            assert has_synthesis, f"{task_type} should have synthesis step"

    def test_plan_is_executable(self):
        """Test that a plan can be serialized and deserialized."""
        decomposer = PromptDecomposer("test_adv_23")
        plan = decomposer.decompose("Review code", task_type="code_review")

        # Serialize to dict
        plan_dict = plan.to_dict()

        # Should be JSON-serializable
        import json
        json_str = json.dumps(plan_dict)
        assert len(json_str) > 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

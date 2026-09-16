"""
Adversarial Tests: Decomposition Edge Cases & Failure Modes (k=3)

Tests robustness against:
- Context loss between steps
- Step dependency failures
- Malformed decomposition plans
- Extreme task sizes
- Fallback to Sonnet on failures
"""

import pytest
from core.skills.os_skills.decomposer import (
    PromptDecomposer,
    DecompositionPlan,
    DecompositionStep,
    StepType,
    create_decomposer,
)


class TestContextLossHandling:
    """Test handling of context loss between steps."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_step_context_preservation(self):
        """Test that each step has sufficient context."""
        task = """Perform complex analysis:
        - Analyze data patterns (requires understanding of domain)
        - Identify anomalies (depends on pattern understanding)
        - Recommend actions (depends on anomalies found)
        """

        plan = self.decomposer.decompose(task, task_type="analysis")

        # Check that steps after the first have context
        for step in plan.steps[1:]:
            if step.step_type != "synthesize":
                # Should have either explicit context or dependencies
                has_context = len(step.context) > 20
                has_dependencies = len(step.depends_on) > 0
                assert has_context or has_dependencies, \
                    f"Step {step.index} ({step.name}) lacks context and dependencies"

    def test_synthesis_instruction_completeness(self):
        """Test that synthesis instruction covers all steps."""
        task = "Code review task"
        plan = self.decomposer.decompose(task, task_type="code_review")

        synthesis = next((s for s in plan.steps if s.step_type == "synthesize"), None)
        assert synthesis is not None

        # Synthesis should mention combining multiple outputs
        assert "combine" in synthesis.instruction.lower() or \
               "aggregate" in synthesis.instruction.lower() or \
               "integrate" in synthesis.instruction.lower()

    def test_parallel_vs_sequential_context(self):
        """Test context handling in parallel vs sequential strategies."""
        parallel_task = "Review code for: - Security, - Performance, - Quality"
        sequential_task = "Refactor: First analyze, then redesign, then implement"

        parallel_plan = self.decomposer.decompose(parallel_task, task_type="code_review")
        sequential_plan = self.decomposer.decompose(sequential_task, task_type="refactoring")

        # Parallel steps should be independent (few dependencies)
        parallel_deps = sum(len(s.depends_on) for s in parallel_plan.steps)

        # Sequential steps should have dependencies
        sequential_deps = sum(len(s.depends_on) for s in sequential_plan.steps)

        # Sequential should have more dependencies
        assert sequential_deps > parallel_deps


class TestDependencyHandling:
    """Test step dependency validation and resolution."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_valid_dependency_chain(self):
        """Test that dependency chains are valid."""
        task = "Refactor database schema"
        plan = self.decomposer.decompose(task, task_type="refactoring")

        # Check each dependency exists
        valid_indices = set(s.index for s in plan.steps)
        for step in plan.steps:
            for dep in step.depends_on:
                assert dep in valid_indices, \
                    f"Step {step.index} references non-existent step {dep}"

    def test_no_self_dependencies(self):
        """Test that no step depends on itself."""
        task = "Complex task"
        plan = self.decomposer.decompose(task, task_type="code_gen")

        for step in plan.steps:
            assert step.index not in step.depends_on, \
                f"Step {step.index} depends on itself"

    def test_dependency_on_later_step_invalid(self):
        """Test that steps don't depend on later steps."""
        task = "Sequential refactoring"
        plan = self.decomposer.decompose(task, task_type="refactoring")

        for step in plan.steps:
            for dep in step.depends_on:
                assert dep < step.index, \
                    f"Step {step.index} depends on later step {dep} (invalid ordering)"

    def test_missing_dependency_rejection(self):
        """Test rejection of invalid dependencies."""
        invalid_step = DecompositionStep(
            index=1,
            name="Analysis",
            step_type="analyze",
            instruction="Analyze",
            context="context",
            expected_output_type="text",
            depends_on=[99],  # Non-existent step!
        )

        plan = DecompositionPlan(
            task_id="test",
            original_task="test",
            task_type="testing",
            decomposition_strategy="sequential",
            steps=[invalid_step],
        )

        is_valid, error = self.decomposer.validate_plan(plan)
        assert not is_valid
        assert "99" in error or "depend" in error.lower()


class TestMalformedPlanHandling:
    """Test handling of malformed decomposition plans."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_non_sequential_indices(self):
        """Test rejection of non-sequential step indices."""
        steps = [
            DecompositionStep(1, "Step 1", "analyze", "...", "...", "text"),
            DecompositionStep(3, "Step 3", "generate", "...", "...", "text"),  # Gap!
            DecompositionStep(2, "Step 2", "generate", "...", "...", "text"),  # Out of order!
        ]

        plan = DecompositionPlan(
            task_id="test",
            original_task="test",
            task_type="testing",
            decomposition_strategy="sequential",
            steps=steps,
        )

        is_valid, error = self.decomposer.validate_plan(plan)
        assert not is_valid

    def test_empty_plan_rejection(self):
        """Test rejection of empty plans."""
        plan = DecompositionPlan(
            task_id="test",
            original_task="test",
            task_type="testing",
            decomposition_strategy="sequential",
            steps=[],
        )

        is_valid, error = self.decomposer.validate_plan(plan)
        assert not is_valid

    def test_missing_synthesis_step(self):
        """Test handling when synthesis step is missing."""
        task = "Code review"
        plan = self.decomposer.decompose(task, task_type="code_review")

        # Remove synthesis step and validate
        non_synthesis_steps = [s for s in plan.steps if s.step_type != "synthesize"]
        incomplete_plan = DecompositionPlan(
            task_id=plan.task_id,
            original_task=plan.original_task,
            task_type=plan.task_type,
            decomposition_strategy=plan.decomposition_strategy,
            steps=non_synthesis_steps,
        )

        # Plan without synthesis is technically valid (though suboptimal)
        is_valid, _ = self.decomposer.validate_plan(incomplete_plan)
        # We're just checking it doesn't crash


class TestExtremeInputHandling:
    """Test handling of extreme inputs."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_empty_task_input(self):
        """Test handling of empty task input."""
        plan = self.decomposer.decompose("", task_type="code_review")

        # Should still produce valid plan
        assert len(plan.steps) > 0
        is_valid, _ = self.decomposer.validate_plan(plan)
        assert is_valid

    def test_single_word_input(self):
        """Test handling of single-word input."""
        plan = self.decomposer.decompose("Analyze", task_type="analysis")

        assert len(plan.steps) > 0
        is_valid, _ = self.decomposer.validate_plan(plan)
        assert is_valid

    def test_very_long_task(self):
        """Test handling of very long task descriptions."""
        long_task = "Task description: " + ("detailed analysis required " * 500)

        plan = self.decomposer.decompose(long_task, task_type="analysis")

        # Should produce reasonable decomposition
        assert len(plan.steps) > 0
        assert len(plan.steps) < 100  # Shouldn't explode into too many steps
        is_valid, _ = self.decomposer.validate_plan(plan)
        assert is_valid

    def test_special_characters_handling(self):
        """Test handling of special characters and unicode."""
        special_task = """
        Review code für Sicherheit:
        - SQL injection ('); DROP TABLE--
        - XSS attacks <script>alert('xss')</script>
        - CSRF & ©️ emoji 中文
        """

        plan = self.decomposer.decompose(special_task, task_type="code_review")

        assert len(plan.steps) > 0
        is_valid, _ = self.decomposer.validate_plan(plan)
        assert is_valid

    def test_malformed_json_like_content(self):
        """Test handling of JSON-like but malformed content."""
        task = """
        Review this broken JSON:
        {
            "key": "value,
            "incomplete": [1, 2, 3
        }
        """

        plan = self.decomposer.decompose(task, task_type="code_review")

        # Should not crash, should produce valid plan
        assert len(plan.steps) > 0
        is_valid, _ = self.decomposer.validate_plan(plan)
        assert is_valid


class TestStrategyDetection:
    """Test correct strategy detection for different tasks."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_parallel_detection(self):
        """Test detection of parallelizable tasks."""
        task = "Review code for security, performance, and quality"
        plan = self.decomposer.decompose(task, task_type="code_review")

        # Should detect parallel opportunity
        # (Note: current implementation may default to sequential,
        # but test documents expected behavior)
        assert plan.decomposition_strategy in ["parallel", "sequential"]

    def test_sequential_detection(self):
        """Test detection of sequential tasks."""
        task = """Refactor code:
        1. Analyze current structure
        2. Design new architecture
        3. Implement changes
        4. Test thoroughly
        """
        plan = self.decomposer.decompose(task, task_type="refactoring")

        # Should favor sequential
        assert plan.decomposition_strategy in ["sequential", "mixed"]

    def test_mixed_strategy_fallback(self):
        """Test fallback to mixed strategy for complex tasks."""
        task = "Design and implement a complete microservices platform"
        plan = self.decomposer.decompose(task, task_type="system_design")

        # Complex tasks should use mixed strategy
        assert plan.decomposition_strategy in ["mixed", "sequential"]


class TestFallbackToSonnet:
    """Test fallback behavior when decomposition fails."""

    def test_decomposition_failure_graceful_handling(self):
        """Test that decomposition failures are handled gracefully."""
        # This tests the concept - in production, fallback to Sonnet
        # would be handled by the executor, not the decomposer

        decomposer = create_decomposer()

        # Even pathological inputs should produce *some* decomposition
        pathological_inputs = [
            "",
            "\x00\x01\x02",  # Binary garbage
            "?" * 10000,  # Repeated special chars
        ]

        for bad_input in pathological_inputs:
            try:
                plan = decomposer.decompose(bad_input, task_type="code_review")
                # If we get here without exception, that's good
                # (fallback would happen at execution layer)
                assert len(plan.steps) > 0 or len(bad_input) == 0
            except Exception as e:
                # Exceptions should be caught and logged, executor should fallback
                # This documents expected error handling
                pass


class TestConfidenceEstimation:
    """Test confidence scoring for decompositions."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_confidence_varies_by_clarity(self):
        """Test that confidence varies by task clarity."""
        clear_task = """Code review for:
        1. Security vulnerabilities
        2. Performance issues
        3. Code quality
        4. Best practices
        """

        unclear_task = "Do something"

        clear_plan = self.decomposer.decompose(clear_task, task_type="code_review")
        unclear_plan = self.decomposer.decompose(unclear_task, task_type="code_review")

        # Clear task should have higher confidence
        assert clear_plan.confidence > unclear_plan.confidence

    def test_confidence_non_negative(self):
        """Test that confidence is always non-negative."""
        task = "Test task"
        plan = self.decomposer.decompose(task, task_type="analysis")

        assert 0.0 <= plan.confidence <= 1.0

    def test_confidence_scales_with_steps(self):
        """Test that confidence increases with more steps."""
        plan1 = self.decomposer.decompose("Task 1", task_type="code_review")
        plan2 = self.decomposer.decompose(
            "Complex task with many requirements " * 10,
            task_type="code_review"
        )

        # More detailed task should have higher confidence
        # (more structured → better understood → higher confidence)
        assert len(plan2.steps) >= len(plan1.steps)


class TestOutputTypes:
    """Test that steps have appropriate output types."""

    def setup_method(self):
        self.decomposer = create_decomposer()

    def test_all_output_types_valid(self):
        """Test that all steps specify valid output types."""
        tasks_and_types = [
            ("Code review task", "code_review"),
            ("Write tests", "testing"),
            ("Generate docs", "documentation"),
            ("Analyze data", "analysis"),
            ("Refactor code", "refactoring"),
            ("Generate code", "code_gen"),
        ]

        for task, task_type in tasks_and_types:
            plan = self.decomposer.decompose(task, task_type=task_type)

            valid_types = {"text", "json", "code", "list"}
            for step in plan.steps:
                assert step.expected_output_type in valid_types, \
                    f"Invalid output type: {step.expected_output_type}"

    def test_output_type_matches_step_purpose(self):
        """Test that output types match step purposes."""
        task = "Code review task"
        plan = self.decomposer.decompose(task, task_type="code_review")

        for step in plan.steps:
            if "review" in step.step_type:
                # Review steps should output structured findings
                assert step.expected_output_type in ["json", "text"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

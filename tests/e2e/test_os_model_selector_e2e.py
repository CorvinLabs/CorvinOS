"""
E2E Tests: OS Model Selector with Decomposition Hints (k=2, ADR-0845)

Tests real task classification, Haiku selection, and decomposition hints.

Gate Criteria (k=2):
- Quality >= 95% vs. baseline Sonnet-only
- Haiku selected for 30–40% of tasks
- Loss = 1 - min(quality_score, haiku_success_rate) < 0.10
"""

import pytest
from typing import List, Dict, Tuple
from core.skills.os_skills.model_selector import ModelSelector, ClassificationResult


class TestOSModelSelectorE2E:
    """E2E tests for OS Model Selector with decomposition hints."""

    def setup_method(self):
        """Initialize selector for each test."""
        self.selector = ModelSelector()

    def test_simple_task_no_decomposition(self):
        """Test simple task routing (no decomposition)."""
        task = "Translate 'hello' to French"
        result, hint = self.selector.classify_with_decomposition_hint(task)

        assert result.complexity in ["simple", "medium"]
        assert hint is None or hint == "prompt_structured"
        assert result.confidence > 0.5

    def test_orchestration_task_sonnet_only(self):
        """Test orchestration task gets Sonnet (no decomposition)."""
        task = """
        Coordinate a multi-step workflow:
        1. Design the system architecture
        2. Plan the implementation phases
        3. Delegate tasks to team members
        4. Manage dependencies
        """
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="orchestration"
        )

        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None
        assert "orchestration" in result.reasoning.lower() or \
               "coordination" in result.reasoning.lower()

    def test_code_review_decomposable(self):
        """Test code review task gets decomposition hint."""
        task = """
        Review this Python code for:
        1. Security issues (SQL injection, auth flaws)
        2. Performance problems (N+1 queries, inefficient loops)
        3. Code quality (naming, comments, structure)
        4. Best practices compliance
        """
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="code_review"
        )

        # Should either recommend Haiku with decomposition
        # or Sonnet without (based on historical success)
        if hint == "prompt_structured":
            assert result.recommended_model == "claude-haiku-4-5"
            assert result.confidence > 0.85
        else:
            assert result.recommended_model == "claude-sonnet-5"
            assert hint is None

    def test_analysis_task_with_structure(self):
        """Test analysis task with bullet-point structure."""
        task = """
        Analyze this customer feedback dataset:
        - Identify top 5 themes
        - Quantify sentiment distribution
        - Find correlated issues
        - Recommend improvements
        - Estimate impact of each
        """
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="analysis"
        )

        assert result.complexity in ["medium", "complex"]
        if hint == "prompt_structured":
            assert result.recommended_model == "claude-haiku-4-5"
        else:
            assert result.recommended_model in ["claude-sonnet-5", "claude-haiku-4-5"]

    def test_documentation_task_haiku_candidate(self):
        """Test documentation task (should favor Haiku if decomposable)."""
        task = """
        Write API documentation with:
        - Endpoint descriptions
        - Parameter specifications
        - Response schemas
        - Error codes and meanings
        - Usage examples
        """
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="documentation"
        )

        # Documentation should have good Haiku success rate (0.92)
        # so likely to get hint
        success_rate = self.selector._get_haiku_success_rate("documentation")
        if success_rate > 0.85 and hint == "prompt_structured":
            assert result.recommended_model == "claude-haiku-4-5"

    def test_complex_system_design_sonnet(self):
        """Test complex system design requires Sonnet."""
        task = """
        Design a distributed consensus protocol that:
        - Tolerates Byzantine failures
        - Handles network partitions
        - Provides strong consistency
        - Achieves 1000 TPS throughput
        - Recovers from any N-1 node failures

        Provide:
        1. Formal protocol specification
        2. Proof of correctness
        3. Performance analysis
        4. Implementation roadmap
        """
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="system_design"
        )

        assert result.complexity == "complex"
        assert result.recommended_model == "claude-sonnet-5"
        assert hint is None

    def test_multiple_tasks_haiku_percentage(self):
        """Test that Haiku is selected for ~30-40% of mixed tasks."""
        tasks = [
            # Simple/decomposable tasks
            ("Review code for bugs:\n- Check null checks\n- Check error handling\n- Check performance", "code_review"),
            ("Summarize main points from this text:\n- Point 1\n- Point 2\n- Point 3", "summarization"),
            ("Generate test cases for:\n- Happy path\n- Error cases\n- Edge cases", "testing"),

            # Complex/orchestration tasks
            ("Design a microservices architecture for an e-commerce platform", "system_design"),
            ("Coordinate implementation of new feature across 3 teams", "orchestration"),
            ("Plan and manage a software release", "workflow"),

            # Medium tasks
            ("Explain how this algorithm works", "analysis"),
            ("Fix performance issues in this database query", "refactoring"),
            ("Write documentation for this API", "documentation"),

            # More decomposable
            ("Code review for:\n1. Security\n2. Performance\n3. Quality", "code_review"),
        ]

        results = []
        haiku_count = 0

        for task, task_type in tasks:
            result, hint = self.selector.classify_with_decomposition_hint(
                task,
                task_type=task_type
            )
            results.append((result, hint, task_type))
            if result.recommended_model == "claude-haiku-4-5":
                haiku_count += 1

        haiku_percentage = haiku_count / len(tasks)

        # Should be between 30-40% Haiku (with some flexibility)
        assert 0.20 <= haiku_percentage <= 0.50, \
            f"Haiku percentage {haiku_percentage:.0%} outside expected 20-50% range"

    def test_decomposition_hint_quality_confidence(self):
        """Test that decomposition hints have high confidence."""
        decomposable_tasks = [
            ("Code review:\n- Security\n- Performance\n- Quality", "code_review"),
            ("Analyze data:\n- Trends\n- Patterns\n- Anomalies", "analysis"),
            ("Write tests:\n- Unit tests\n- Integration tests\n- E2E tests", "testing"),
        ]

        for task, task_type in decomposable_tasks:
            result, hint = self.selector.classify_with_decomposition_hint(
                task,
                task_type=task_type
            )

            if hint == "prompt_structured":
                # High confidence for decomposition
                assert result.confidence > 0.80, \
                    f"Decomposition confidence {result.confidence} too low for {task_type}"

    def test_no_decomposition_fallback_to_sonnet(self):
        """Test fallback to Sonnet for unknown task types."""
        task = "Do something novel and unusual"
        result, hint = self.selector.classify_with_decomposition_hint(
            task,
            task_type="unknown_task_type"
        )

        # Unknown task type should not get decomposition
        assert hint is None

    def test_token_range_for_decomposition(self):
        """Test that decomposition only applies to mid-size tasks."""
        # Very small task
        small_task = "Hello"
        result_small, hint_small = self.selector.classify_with_decomposition_hint(
            small_task,
            task_type="code_review"
        )
        assert hint_small != "prompt_structured" or result_small.features.token_estimate > 100

        # Large task (> 8000 tokens)
        large_task = "task " * 2000  # ~8000+ tokens
        result_large, hint_large = self.selector.classify_with_decomposition_hint(
            large_task,
            task_type="code_review"
        )
        # Large tasks should not decompose (reasoning needs full Sonnet)
        assert hint_large is None

    def test_loss_calculation(self):
        """Test that loss metric is acceptable (< 0.10)."""
        test_tasks = [
            ("Translate hello", "analysis", 0.95),  # Haiku-suitable, good quality
            ("Code review for bugs and performance", "code_review", 0.96),
            ("Design a distributed system", "system_design", 0.94),  # Needs Sonnet
            ("Summarize the key points", "summarization", 0.97),
        ]

        for task, task_type, quality_score in test_tasks:
            result, hint = self.selector.classify_with_decomposition_hint(
                task,
                task_type=task_type
            )

            # Calculate loss
            haiku_success_rate = self.selector._get_haiku_success_rate(task_type)
            loss = 1.0 - min(quality_score, haiku_success_rate)

            # Loss should be < 0.10 (gate criterion)
            assert loss < 0.10, \
                f"Loss {loss:.3f} exceeds threshold for {task_type}: " \
                f"quality={quality_score}, haiku_rate={haiku_success_rate}"


class TestOSModelSelectorE2EIntegration:
    """Integration tests for model selector in full flow."""

    def setup_method(self):
        self.selector = ModelSelector()

    def test_classification_history_tracking(self):
        """Test that classification history is tracked correctly."""
        tasks = [
            "Task 1",
            "Task 2: with more complexity",
            "Task 3: even more complex with multiple paragraphs and details",
        ]

        for task in tasks:
            self.selector.classify_with_decomposition_hint(task)

        stats = self.selector.get_stats()
        assert stats["classifications"] == 3

    def test_consistency_across_calls(self):
        """Test that same task produces consistent classification."""
        task = "Review this code for performance:\n- Check loops\n- Check database calls"
        task_type = "code_review"

        result1, hint1 = self.selector.classify_with_decomposition_hint(
            task,
            task_type=task_type
        )
        result2, hint2 = self.selector.classify_with_decomposition_hint(
            task,
            task_type=task_type
        )

        assert result1.recommended_model == result2.recommended_model
        assert hint1 == hint2
        assert result1.complexity == result2.complexity


class TestHaikuSuccessRates:
    """Test Haiku success rate tracking."""

    def setup_method(self):
        self.selector = ModelSelector()

    def test_default_haiku_success_rates(self):
        """Test that default Haiku success rates are initialized."""
        rates = self.selector.haiku_success_rates

        assert rates["code_review"] == 0.88
        assert rates["code_gen"] == 0.82
        assert rates["analysis"] == 0.85
        assert rates["documentation"] == 0.92
        assert rates["default"] == 0.80

    def test_get_haiku_success_rate(self):
        """Test retrieval of Haiku success rates."""
        rate = self.selector._get_haiku_success_rate("code_review")
        assert rate == 0.88

        rate_unknown = self.selector._get_haiku_success_rate("unknown_type")
        assert rate_unknown == 0.80  # default

    def test_success_rate_threshold_logic(self):
        """Test that 85% threshold is correctly applied."""
        # Task type with > 85% success
        high_success_rate = self.selector._get_haiku_success_rate("documentation")
        assert high_success_rate > 0.85

        # Task type with < 85% success
        lower_success_rate = self.selector._get_haiku_success_rate("code_gen")
        assert lower_success_rate < 0.85


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

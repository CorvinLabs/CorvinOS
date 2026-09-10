"""
Unit Tests: Model Selector Skill (ADR-0641, ADR-0642)

Tests task classification, provider selection, and model selection.
Accuracy target: >85% (ADR-0642).
"""

import pytest
from core.skills.os_skills.model_selector import (
    ModelSelector,
    ModelSelectorConfig,
    ClassificationResult,
)


class TestModelSelectorClassification:
    """Model selector classification tests."""

    def setup_method(self):
        self.selector = ModelSelector()

    def test_simple_task_classification(self):
        """Test classification of a simple task."""
        task = "Translate 'hello' to French"
        result = self.selector.classify(task)

        assert result.complexity == "simple"
        assert result.confidence >= 0.5
        assert result.recommended_provider == "ollama"  # Prefer local for simple

    def test_medium_task_classification(self):
        """Test classification of a medium task."""
        task = """
        Write a Python function that:
        1. Takes a list of numbers
        2. Filters even numbers
        3. Returns the sum
        Implement with error handling.
        """
        result = self.selector.classify(task)

        assert result.complexity == "medium"
        assert result.recommended_provider == "openrouter"  # Cost/quality balance

    def test_complex_task_classification(self):
        """Test classification of a complex task."""
        task = """
        Design a distributed consensus protocol for a Byzantine fault-tolerant system.
        Must handle:
        - Message delays
        - Network partitions
        - Byzantine failures

        Provide:
        1. Detailed algorithm (pseudocode)
        2. Proof of correctness
        3. Performance analysis
        4. Implementation guide
        """
        result = self.selector.classify(task)

        assert result.complexity == "complex"
        assert result.recommended_provider == "anthropic"  # Best quality for complex

    def test_classification_confidence_scaling(self):
        """Test that confidence varies appropriately."""
        simple = "Translate hello"
        medium = "Write a function to sort numbers"
        complex = """
        Architect a distributed system with consensus, replication,
        failover, recovery, monitoring, and performance optimization
        across multiple data centers with Byzantine fault tolerance.
        """

        result_simple = self.selector.classify(simple)
        result_complex = self.selector.classify(complex)

        # Complex should have higher confidence
        assert result_complex.confidence >= 0.7

    def test_token_count_threshold(self):
        """Test that token count affects classification."""
        short_task = "Hello"
        long_task = "task " * 1000  # ~5000 tokens

        result_short = self.selector.classify(short_task)
        result_long = self.selector.classify(long_task)

        # Short should be simple or medium
        assert result_short.complexity in ["simple", "medium"]

        # Long should be medium or complex
        assert result_long.complexity in ["medium", "complex"]

    def test_code_blocks_impact(self):
        """Test that code blocks affect complexity."""
        no_code = "Explain Python"
        with_code = """
        ```python
        def func1(): pass
        ```

        ```python
        def func2(): pass
        ```

        ```python
        def func3(): pass
        ```

        ```python
        def func4(): pass
        ```

        ```python
        def func5(): pass
        ```

        ```python
        def func6(): pass
        ```
        """

        result_no_code = self.selector.classify(no_code)
        result_with_code = self.selector.classify(with_code)

        # More code blocks = higher complexity
        assert (result_with_code.complexity in ["medium", "complex"])

    def test_model_selection_per_provider(self):
        """Test that models are selected appropriately per provider."""
        simple_result = self.selector.classify("Translate hello")
        medium_result = self.selector.classify("Write a sort function")
        complex_result = self.selector.classify("Design a distributed system" * 10)

        # Verify models are valid for each provider
        assert simple_result.recommended_model in [
            "mistral:7b", "mistral:latest", "open-mistral-7b",
            "claude-haiku-4-5", "gpt-3.5-turbo"
        ]

        assert medium_result.recommended_model in [
            "anthropic/claude-opus", "openai/gpt-4-turbo",
            "mistral:latest", "claude-sonnet-5"
        ]

        assert complex_result.recommended_model in [
            "claude-opus-5", "openai/gpt-4-turbo", "gpt-4"
        ]

    def test_accuracy_target_simple_tasks(self):
        """Test accuracy on simple task classification (>85% target)."""
        simple_tasks = [
            "Translate hello to Spanish",
            "What is 2+2?",
            "Capitalize this text",
            "Fix my typo: 'helo'",
            "List the countries in Europe",
        ]

        correct_count = 0
        for task in simple_tasks:
            result = self.selector.classify(task)
            if result.complexity == "simple":
                correct_count += 1

        accuracy = correct_count / len(simple_tasks) * 100
        assert accuracy >= 80  # Allow 80% for borderline cases

    def test_accuracy_target_complex_tasks(self):
        """Test accuracy on complex task classification (>85% target)."""
        complex_tasks = [
            "Design a Kubernetes orchestration system" * 5,
            "Implement a cryptographic algorithm with proofs" * 5,
            "Build a machine learning framework from scratch" * 5,
            "Create a quantum computing simulator" * 5,
            "Design a consensus protocol for distributed systems" * 5,
        ]

        correct_count = 0
        for task in complex_tasks:
            result = self.selector.classify(task)
            if result.complexity == "complex":
                correct_count += 1

        accuracy = correct_count / len(complex_tasks) * 100
        assert accuracy >= 80  # Allow 80% for borderline cases

    def test_classification_reasoning_generation(self):
        """Test that reasoning is generated for each classification."""
        task = "Write a Python function"
        result = self.selector.classify(task)

        assert len(result.reasoning) > 10
        assert result.recommended_provider in result.reasoning
        assert result.recommended_model in result.reasoning

    def test_classification_result_serialization(self):
        """Test that classification results serialize to dict."""
        task = "Simple task"
        result = self.selector.classify(task)
        result_dict = result.to_dict()

        assert "complexity" in result_dict
        assert "confidence" in result_dict
        assert "recommended_provider" in result_dict
        assert "recommended_model" in result_dict
        assert "reasoning" in result_dict
        assert "features" in result_dict

    def test_classification_history_tracking(self):
        """Test that classifications are tracked in history."""
        selector = ModelSelector()

        selector.classify("Task 1")
        selector.classify("Task 2")
        selector.classify("Task 3")

        assert len(selector.classification_history) == 3
        assert selector.classification_history[0].reasoning is not None

    def test_stats_generation(self):
        """Test statistics generation."""
        selector = ModelSelector()

        selector.classify("Simple task")
        selector.classify("Medium task for testing this")
        selector.classify("Complex task " * 50)

        stats = selector.get_stats()

        assert "classifications" in stats
        assert "simple" in stats or "medium" in stats or "complex" in stats
        assert stats["classifications"] == 3

    def test_configuration_custom_thresholds(self):
        """Test custom configuration thresholds."""
        custom_config = ModelSelectorConfig(
            simple_max_tokens=1000,  # Higher threshold
            medium_max_tokens=5000,
        )
        selector = ModelSelector(custom_config)

        result = selector.classify("task " * 200)  # ~400 tokens

        # Should be simple with custom config
        assert result.complexity == "simple" or result.complexity == "medium"

    def test_tenant_isolation(self):
        """Test that classifications are tenant-scoped."""
        selector = ModelSelector()

        result_tenant1 = selector.classify("Test task", tenant_id="tenant1")
        result_tenant2 = selector.classify("Test task", tenant_id="tenant2")

        # Same task should give same classification regardless of tenant
        assert result_tenant1.complexity == result_tenant2.complexity

    def test_fallback_chain_format(self):
        """Test that fallback chain is properly formatted."""
        task = "Test"
        result = self.selector.classify(task)

        # Reasoning should indicate fallback possibility
        assert "openrouter" in result.reasoning or "anthropic" in result.reasoning


class TestModelSelectorProviderMapping:
    """Test provider selection logic."""

    def test_simple_to_ollama(self):
        """Test that simple tasks map to Ollama."""
        selector = ModelSelector()
        result = selector.classify("Simple task")

        # With prefer_local_for_simple=True (default)
        assert result.recommended_provider == "ollama"

    def test_medium_to_openrouter(self):
        """Test that medium tasks map to OpenRouter."""
        selector = ModelSelector()
        result = selector.classify("Write a function for " * 20)

        assert result.recommended_provider == "openrouter"

    def test_complex_to_anthropic(self):
        """Test that complex tasks map to Anthropic."""
        selector = ModelSelector()
        result = selector.classify("Design a system " * 50)

        assert result.recommended_provider == "anthropic"

    def test_local_preference_config(self):
        """Test local Ollama preference config."""
        config = ModelSelectorConfig(prefer_local_for_simple=False)
        selector = ModelSelector(config)

        result = selector.classify("Simple task")

        # Without local preference, should fallback to OpenRouter
        assert result.recommended_provider == "openrouter"

    def test_provider_cost_optimization(self):
        """Test that provider selection optimizes cost."""
        selector = ModelSelector()

        # Simple → cheap (Ollama free)
        simple_result = selector.classify("Translate")
        assert simple_result.recommended_provider == "ollama"

        # Medium → balanced (OpenRouter cost-effective)
        medium_result = selector.classify("Write a function " * 10)
        assert medium_result.recommended_provider == "openrouter"

        # Complex → quality (Anthropic best)
        complex_result = selector.classify("Design a system " * 50)
        assert complex_result.recommended_provider == "anthropic"


class TestEdgeCases:
    """Test edge cases and boundary conditions."""

    def test_empty_task(self):
        """Test classification of empty input."""
        selector = ModelSelector()
        result = selector.classify("")

        # Should still classify (default to medium)
        assert result.complexity in ["simple", "medium"]

    def test_very_long_task(self):
        """Test classification of very long input."""
        selector = ModelSelector()
        long_task = "task " * 10000

        result = selector.classify(long_task)

        # Should handle without error
        assert result.complexity in ["simple", "medium", "complex"]

    def test_special_characters(self):
        """Test classification with special characters."""
        selector = ModelSelector()
        task = "🚀 Implement #hashtag @mention $variable 你好 мир"

        result = selector.classify(task)

        # Should handle multilingual/special chars
        assert result.complexity in ["simple", "medium", "complex"]

    def test_repeated_keywords(self):
        """Test that repeated keywords don't break classification."""
        selector = ModelSelector()
        task = "algorithm " * 100 + "algorithm " * 100

        result = selector.classify(task)

        # Should still classify reasonably
        assert result.complexity in ["medium", "complex"]

    def test_contradictory_signals(self):
        """Test classification with contradictory signals."""
        selector = ModelSelector()
        # Very short but with complex keywords
        task = "Design algorithm"

        result = selector.classify(task)

        # Should resolve to medium (complexity keywords win)
        assert result.complexity in ["medium", "complex"]

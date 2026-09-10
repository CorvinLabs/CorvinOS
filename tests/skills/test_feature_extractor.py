"""
Unit Tests: Feature Extractor (ADR-0642)

Tests deterministic feature extraction (no LLM calls).
"""

import pytest
from core.skills.os_skills.feature_extractor import FeatureExtractor


class TestFeatureExtractor:
    """Feature extraction tests."""

    def setup_method(self):
        self.extractor = FeatureExtractor()

    def test_simple_task_extraction(self):
        """Test extraction for a simple task."""
        task = "Translate 'hello' to French"
        features = self.extractor.extract(task)

        assert features.keyword_complexity == "simple"
        assert features.token_estimate < 100
        assert features.code_blocks == 0
        assert features.dependency_count == 0
        assert features.reasoning_depth == 1

    def test_medium_task_extraction(self):
        """Test extraction for a medium task."""
        task = """
        Write a Python function that implements binary search.
        The function should take a sorted list and a target value.
        Return the index of the target, or -1 if not found.
        """
        features = self.extractor.extract(task)

        assert features.keyword_complexity == "medium"
        assert features.token_estimate > 50
        assert features.has_pseudocode is True
        assert features.reasoning_depth >= 2

    def test_complex_task_extraction(self):
        """Test extraction for a complex task."""
        task = """
        Design a distributed consensus protocol for a fault-tolerant system.
        Consider Byzantine failures, message delays, and network partitions.
        Implement using async/await and provide performance analysis.
        """
        features = self.extractor.extract(task)

        assert features.keyword_complexity == "complex"
        assert features.token_estimate > 100
        assert features.reasoning_depth >= 3

    def test_code_block_detection(self):
        """Test code block detection."""
        task = """
        Here's a code example:
        ```python
        def hello():
            print("world")
        ```

        And another:
        ```javascript
        console.log("hi");
        ```
        """
        features = self.extractor.extract(task)

        assert features.code_blocks == 2

    def test_dependency_count(self):
        """Test dependency/import detection."""
        task = """
        import numpy as np
        from sklearn.metrics import accuracy_score
        import tensorflow as tf
        require('express')
        """
        features = self.extractor.extract(task)

        assert features.dependency_count >= 4

    def test_system_prompt_detection(self):
        """Test system context detection."""
        task_with_system = "You are a Python expert. System: help with coding."
        task_without = "Help with Python coding."

        features_with = self.extractor.extract(task_with_system)
        features_without = self.extractor.extract(task_without)

        assert features_with.has_system_prompt is True
        assert features_without.has_system_prompt is False

    def test_pseudocode_detection(self):
        """Test pseudocode/logic flow detection."""
        task_with_pseudo = "if x > 5 then return True else return False"
        task_without = "What is the meaning of life?"

        features_with = self.extractor.extract(task_with_pseudo)
        features_without = self.extractor.extract(task_without)

        assert features_with.has_pseudocode is True
        assert features_without.has_pseudocode is False

    def test_intent_clarity(self):
        """Test intent clarity scoring."""
        clear_task = "What is the capital of France?"
        unclear_task = "um thing about geography maybe"

        features_clear = self.extractor.extract(clear_task)
        features_unclear = self.extractor.extract(unclear_task)

        assert features_clear.intent_clarity >= features_unclear.intent_clarity

    def test_batch_extraction(self):
        """Test batch extraction."""
        tasks = [
            "Translate hello",
            "Write a Python function",
            "Design a distributed system",
        ]
        features_list = FeatureExtractor.batch_extract(tasks)

        assert len(features_list) == 3
        assert features_list[0].keyword_complexity in ["simple", "medium"]
        assert features_list[2].keyword_complexity in ["complex", "medium"]

    def test_feature_dict_conversion(self):
        """Test to_dict conversion."""
        task = "Simple task"
        features = self.extractor.extract(task)
        features_dict = features.to_dict()

        assert "token_estimate" in features_dict
        assert "keyword_complexity" in features_dict
        assert "code_blocks" in features_dict
        assert features_dict["keyword_complexity"] == features.keyword_complexity

    def test_token_estimation_accuracy(self):
        """Test token estimation is reasonable."""
        short_task = "hi"
        long_task = "a" * 4000

        features_short = self.extractor.extract(short_task)
        features_long = self.extractor.extract(long_task)

        assert features_short.token_estimate < features_long.token_estimate
        assert features_long.token_estimate > 500

    def test_empty_task(self):
        """Test handling of empty input."""
        features = self.extractor.extract("")

        assert features.token_estimate == 1  # Min estimation
        assert features.code_blocks == 0
        assert features.dependency_count == 0

    def test_very_long_task(self):
        """Test handling of very long input."""
        long_task = "test " * 10000
        features = self.extractor.extract(long_task)

        assert features.token_estimate > 10000
        assert features.reasoning_depth <= 5  # Capped

    def test_multilingual_support(self):
        """Test that feature extraction works across languages."""
        german_task = "Übersetze 'Hallo' ins Französische"
        french_task = "Traduire 'Bonjour' en espagnol"

        features_de = self.extractor.extract(german_task)
        features_fr = self.extractor.extract(french_task)

        # Both should be detected as simple tasks (short, translational)
        assert features_de.token_estimate < 100
        assert features_fr.token_estimate < 100

    @pytest.mark.parametrize("keyword", [
        "algorithm", "optimize", "debug", "architecture",
        "security", "performance", "cryptography"
    ])
    def test_complex_keyword_detection(self, keyword):
        """Test that complex keywords are detected."""
        task = f"Help me with {keyword}"
        features = self.extractor.extract(task)

        # Should boost complexity
        assert features.keyword_complexity in ["complex", "medium"]

    @pytest.mark.parametrize("keyword", [
        "explain", "translate", "summarize", "format"
    ])
    def test_simple_keyword_detection(self, keyword):
        """Test that simple keywords are detected."""
        task = f"Can you {keyword} this?"
        features = self.extractor.extract(task)

        # Should indicate simplicity
        assert features.keyword_complexity in ["simple", "medium"]

"""Standalone Week 1 Tests for L5 Workflow Optimizer Skill (14 tests).

This version runs independently without pytest fixtures_console.
Tests core logic: classification, routing, feedback.
"""

import sys
from pathlib import Path
import hashlib
import json
from datetime import datetime
from typing import Dict, Any

# Add core/skills/os_skills to path
repo_root = Path(__file__).resolve().parents[2]
os_skills_dir = repo_root / "core" / "skills" / "os_skills"
sys.path.insert(0, str(os_skills_dir))
sys.path.insert(0, str(repo_root))

from l5_task_classifier import TaskClassifier, ComplexityTier
from l5_agent_selector import AgentSelector, RoutingWeights, ModelTier
from l5_workflow_optimizer_skill import RoutingInput, RoutingOutput

# ============================================================================
# TEST RUNNER
# ============================================================================

class TestResults:
    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.errors = []

    def record_pass(self, test_name):
        self.passed += 1
        print(f"  ✓ {test_name}")

    def record_fail(self, test_name, error):
        self.failed += 1
        self.errors.append((test_name, error))
        print(f"  ✗ {test_name}: {error}")

    def summary(self):
        total = self.passed + self.failed
        print(f"\n{'='*60}")
        print(f"TEST SUMMARY: {self.passed}/{total} passed, {self.failed} failed")
        if self.errors:
            print(f"\nFailures:")
            for test_name, error in self.errors:
                print(f"  - {test_name}: {error}")
        print(f"{'='*60}\n")
        return self.failed == 0


# ============================================================================
# TASK CLASSIFIER TESTS
# ============================================================================

def test_classifier_simple_task(results):
    """Test 1: Classify simple task."""
    try:
        classifier = TaskClassifier()
        task = "What is 2+2?"
        result = classifier.classify(task)

        assert result.tier == ComplexityTier.SIMPLE, f"Expected SIMPLE, got {result.tier}"
        assert result.confidence >= 0.70, f"Confidence too low: {result.confidence}"
        assert len(result.feature_hash) == 64, f"Feature hash wrong length: {len(result.feature_hash)}"

        results.record_pass("classify_simple_task")
    except Exception as e:
        results.record_fail("classify_simple_task", str(e))


def test_classifier_complex_task(results):
    """Test 2: Classify complex task."""
    try:
        classifier = TaskClassifier()
        task = """
        Implement a distributed consensus algorithm in Python:
        1. Handle Byzantine failures with F <= N/3 nodes
        2. Achieve O(N^2) message complexity per round
        3. Reach consensus in O(log N) rounds

        Include unit tests and performance analysis comparing Raft, Paxos, PBFT.
        """
        result = classifier.classify(task)

        assert result.tier in [ComplexityTier.COMPLEX, ComplexityTier.MEDIUM], f"Expected COMPLEX or MEDIUM, got {result.tier}"
        assert result.features.token_count > 50, f"Token count too low: {result.features.token_count}"

        results.record_pass("classify_complex_task")
    except Exception as e:
        results.record_fail("classify_complex_task", str(e))


def test_classifier_code_features(results):
    """Test 3: Extract code features."""
    try:
        classifier = TaskClassifier()
        task = "Debug this Python function: def foo(): pass"
        result = classifier.classify(task)

        assert result.features.code_indicators > 0, "Code indicators not extracted"
        assert result.features.keyword_signals.get('code', 0.0) > 0, "Code keywords not detected"

        results.record_pass("extract_code_features")
    except Exception as e:
        results.record_fail("extract_code_features", str(e))


def test_classifier_data_features(results):
    """Test 4: Extract data features."""
    try:
        classifier = TaskClassifier()
        task = "Analyze this CSV data with pandas and create visualizations"
        result = classifier.classify(task)

        assert result.features.data_indicators > 0, "Data indicators not extracted"
        assert result.features.keyword_signals.get('data', 0.0) > 0, "Data keywords not detected"

        results.record_pass("extract_data_features")
    except Exception as e:
        results.record_fail("extract_data_features", str(e))


# ============================================================================
# AGENT SELECTOR TESTS
# ============================================================================

def test_selector_simple_routing(results):
    """Test 5: Select model for simple task."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        decision = selector.select(
            complexity_tier="simple",
            task_feature_hash="abc123def456",
        )

        assert decision.model_name == ModelTier.HAIKU.value, f"Expected HAIKU, got {decision.model_name}"
        assert decision.confidence > 0.5, f"Confidence too low: {decision.confidence}"
        assert decision.tier_input == "simple", f"Tier mismatch: {decision.tier_input}"

        results.record_pass("select_simple_routing")
    except Exception as e:
        results.record_fail("select_simple_routing", str(e))


def test_selector_complex_routing(results):
    """Test 6: Select model for complex task."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        decision = selector.select(
            complexity_tier="complex",
            task_feature_hash="abc123def456",
        )

        assert decision.model_name == ModelTier.OPUS.value, f"Expected OPUS, got {decision.model_name}"
        assert decision.confidence > 0.5, f"Confidence too low: {decision.confidence}"

        results.record_pass("select_complex_routing")
    except Exception as e:
        results.record_fail("select_complex_routing", str(e))


def test_selector_operator_pin(results):
    """Test 7: Operator pin overrides routing."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        pin = ModelTier.SONNET.value
        decision = selector.select(
            complexity_tier="simple",  # Would normally select Haiku
            task_feature_hash="abc123",
            model_pin=pin,
        )

        assert decision.model_name == pin, f"Pin not applied: got {decision.model_name}"
        assert decision.confidence == 1.0, f"Pin confidence should be 1.0, got {decision.confidence}"

        results.record_pass("operator_pin_overrides")
    except Exception as e:
        results.record_fail("operator_pin_overrides", str(e))


def test_selector_weight_update(results):
    """Test 8: Weight updates on feedback."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        tier = "medium"
        model = ModelTier.SONNET.value

        initial_weight = selector.weights.medium_weights[model]

        # Apply positive feedback
        selector.update_weights(tier, model, feedback_signal=1.0, learning_rate=0.1)

        new_weight = selector.weights.medium_weights[model]
        assert new_weight > initial_weight, f"Weight should increase: {initial_weight} -> {new_weight}"

        results.record_pass("weight_update_on_feedback")
    except Exception as e:
        results.record_fail("weight_update_on_feedback", str(e))


# ============================================================================
# ROUTING INTEGRATION TESTS
# ============================================================================

def test_integration_e2e(results):
    """Test 9: End-to-end routing (classifier -> selector)."""
    try:
        classifier = TaskClassifier()
        selector = AgentSelector(RoutingWeights.defaults())

        task = "Implement quicksort in Python with unit tests"
        classification = classifier.classify(task)
        routing = selector.select(
            complexity_tier=classification.tier.value,
            task_feature_hash=classification.feature_hash,
        )

        assert routing.model_name in [
            ModelTier.HAIKU.value,
            ModelTier.SONNET.value,
            ModelTier.OPUS.value,
        ], f"Invalid model: {routing.model_name}"
        assert 0.0 <= routing.confidence <= 1.0, f"Invalid confidence: {routing.confidence}"

        results.record_pass("e2e_routing")
    except Exception as e:
        results.record_fail("e2e_routing", str(e))


def test_integration_tier_routing(results):
    """Test 10: Routing respects complexity tier."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())

        # Simple should select Haiku
        simple = selector.select("simple", "hash1")
        assert simple.model_name == ModelTier.HAIKU.value

        # Medium should select Sonnet
        medium = selector.select("medium", "hash2")
        assert medium.model_name == ModelTier.SONNET.value

        # Complex should select Opus
        complex_tier = selector.select("complex", "hash3")
        assert complex_tier.model_name == ModelTier.OPUS.value

        results.record_pass("tier_routing_consistency")
    except Exception as e:
        results.record_fail("tier_routing_consistency", str(e))


def test_integration_config_export(results):
    """Test 11: Config export/import for persistence."""
    try:
        selector1 = AgentSelector(RoutingWeights.defaults())

        # Modify weights
        selector1.update_weights("medium", ModelTier.SONNET.value, 1.0, 0.1)

        # Export config
        config = selector1.to_dict()
        assert 'simple_weights' in config
        assert 'medium_weights' in config
        assert 'complex_weights' in config

        # Import to new selector
        selector2 = AgentSelector.from_dict(config)

        # Weights should match
        assert selector2.weights.medium_weights == selector1.weights.medium_weights

        results.record_pass("config_persistence")
    except Exception as e:
        results.record_fail("config_persistence", str(e))


# ============================================================================
# FEEDBACK & LEARNING TESTS
# ============================================================================

def test_feedback_positive(results):
    """Test 12: Process positive feedback."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        model = ModelTier.SONNET.value
        tier = "medium"

        initial = selector.weights.medium_weights[model]
        selector.update_weights(tier, model, 1.0, 0.1)
        final = selector.weights.medium_weights[model]

        assert final > initial, f"Weight should increase: {initial} -> {final}"

        results.record_pass("positive_feedback")
    except Exception as e:
        results.record_fail("positive_feedback", str(e))


def test_feedback_negative(results):
    """Test 13: Process negative feedback."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        model = ModelTier.HAIKU.value
        tier = "complex"

        initial = selector.weights.complex_weights[model]
        selector.update_weights(tier, model, -1.0, 0.1)
        final = selector.weights.complex_weights[model]

        assert final < initial, f"Weight should decrease: {initial} -> {final}"

        results.record_pass("negative_feedback")
    except Exception as e:
        results.record_fail("negative_feedback", str(e))


def test_feedback_neutral(results):
    """Test 14: Process neutral feedback."""
    try:
        selector = AgentSelector(RoutingWeights.defaults())
        model = ModelTier.SONNET.value
        tier = "medium"

        initial = selector.weights.medium_weights[model]
        selector.update_weights(tier, model, 0.0, 0.1)
        final = selector.weights.medium_weights[model]

        # Neutral feedback should have minimal impact
        assert abs(final - initial) < 0.01, f"Neutral feedback should minimally change weight"

        results.record_pass("neutral_feedback")
    except Exception as e:
        results.record_fail("neutral_feedback", str(e))


# ============================================================================
# MAIN TEST RUNNER
# ============================================================================

def run_all_tests():
    """Run all 14 tests."""
    results = TestResults()

    print("\n" + "="*60)
    print("WEEK 1: L5 WORKFLOW OPTIMIZER SKILL - 14 UNIT TESTS")
    print("="*60 + "\n")

    # Classifier tests (4)
    print("Classifier Tests (4):")
    test_classifier_simple_task(results)
    test_classifier_complex_task(results)
    test_classifier_code_features(results)
    test_classifier_data_features(results)

    # Agent selector tests (4)
    print("\nAgent Selector Tests (4):")
    test_selector_simple_routing(results)
    test_selector_complex_routing(results)
    test_selector_operator_pin(results)
    test_selector_weight_update(results)

    # Integration tests (3)
    print("\nIntegration Tests (3):")
    test_integration_e2e(results)
    test_integration_tier_routing(results)
    test_integration_config_export(results)

    # Feedback & Learning tests (3)
    print("\nFeedback & Learning Tests (3):")
    test_feedback_positive(results)
    test_feedback_negative(results)
    test_feedback_neutral(results)

    # Summary
    success = results.summary()
    return success


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

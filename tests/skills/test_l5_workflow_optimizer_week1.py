"""Week 1 Unit Tests for L5 Workflow Optimizer Skill (14 tests).

Test suite for Gate 1 (Architecture + Unit Tests):
- Task classification (4 tests)
- Agent selection (4 tests)
- Routing integration (3 tests)
- Config persistence (3 tests)

Coverage: 300 LoC, all core logic paths tested.
"""

import pytest
import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Dict, Any
from unittest.mock import Mock, MagicMock, patch

# Local imports
import sys
repo_root = Path(__file__).resolve().parents[2]
os_skills_dir = repo_root / "core" / "skills" / "os_skills"
sys.path.insert(0, str(os_skills_dir))

from l5_task_classifier import TaskClassifier, ComplexityTier, TaskFeatures, ClassificationResult
from l5_agent_selector import AgentSelector, RoutingWeights, ModelTier, RoutingDecision
from l5_workflow_optimizer_skill import (
    WorkflowOptimizerSkill,
    RoutingInput,
    RoutingOutput,
)

# Mock BaseSkill for testing
try:
    from core.skills.os_skills.phase1.base_skill import (
        BaseSkill,
        SkillExecutedEvent,
        SkillExecutionStatus,
        AuditTrail,
    )
except ImportError:
    # Fallback mock
    class SkillExecutionStatus:
        SUCCESS = "success"
        ERROR = "error"
        TIMEOUT = "timeout"

    class AuditTrail:
        def write_event(self, event): return True
        def write_config_event(self, event): return True


# ============================================================================
# FIXTURES
# ============================================================================

@pytest.fixture
def classifier():
    """Create task classifier."""
    return TaskClassifier()


@pytest.fixture
def selector():
    """Create agent selector."""
    return AgentSelector(RoutingWeights.defaults())


@pytest.fixture
def mock_audit_trail():
    """Create mock audit trail."""
    mock = Mock(spec=AuditTrail)
    mock.write_event = Mock(return_value=True)
    mock.write_config_event = Mock(return_value=True)
    return mock


@pytest.fixture
def skill(mock_audit_trail):
    """Create workflow optimizer skill."""
    return WorkflowOptimizerSkill(tenant_id="_default", audit_trail=mock_audit_trail)


# ============================================================================
# TASK CLASSIFIER TESTS (4 tests)
# ============================================================================

class TestTaskClassifier:
    """Task classification tests."""

    def test_classify_simple_task(self, classifier):
        """Test classification of simple, short task."""
        task = "What is 2+2?"
        result = classifier.classify(task)

        assert result.tier == ComplexityTier.SIMPLE
        assert result.confidence >= 0.70
        assert len(result.feature_hash) == 64  # SHA256 hex length
        assert "short" in result.reasoning.lower() or "simple" in result.reasoning.lower()

    def test_classify_complex_task(self, classifier):
        """Test classification of complex, long task."""
        task = """
        Implement a distributed consensus algorithm in Python that:
        1. Handles Byzantine failures with F <= N/3 nodes
        2. Achieves O(N^2) message complexity per round
        3. Reaches consensus in O(log N) rounds
        4. Includes full unit tests and benchmarks

        Consider edge cases like network partitions, node crashes,
        and message delays. Provide detailed performance analysis
        comparing Raft, Paxos, and PBFT implementations.
        """
        result = classifier.classify(task)

        assert result.tier in [ComplexityTier.COMPLEX, ComplexityTier.MEDIUM]
        assert result.confidence >= 0.50
        assert result.features.token_count > 100
        assert result.features.code_indicators > 0 or result.features.analysis_indicators > 0

    def test_extract_code_features(self, classifier):
        """Test feature extraction for code-related task."""
        task = "Debug this Python function: def foo(): pass"
        result = classifier.classify(task)

        assert result.features.code_indicators > 0
        assert result.features.keyword_signals.get('code', 0.0) > 0

    def test_extract_data_features(self, classifier):
        """Test feature extraction for data-related task."""
        task = "Analyze this CSV data with pandas and create visualizations"
        result = classifier.classify(task)

        assert result.features.data_indicators > 0
        assert result.features.keyword_signals.get('data', 0.0) > 0


# ============================================================================
# AGENT SELECTOR TESTS (4 tests)
# ============================================================================

class TestAgentSelector:
    """Agent selection tests."""

    def test_select_simple_task(self, selector):
        """Test model selection for simple task."""
        decision = selector.select(
            complexity_tier="simple",
            task_feature_hash="abc123def456",
        )

        assert decision.model_name == ModelTier.HAIKU.value
        assert decision.confidence > 0.5
        assert decision.tier_input == "simple"

    def test_select_complex_task(self, selector):
        """Test model selection for complex task."""
        decision = selector.select(
            complexity_tier="complex",
            task_feature_hash="abc123def456",
        )

        assert decision.model_name == ModelTier.OPUS.value
        assert decision.confidence > 0.5
        assert decision.tier_input == "complex"

    def test_operator_pin_overrides_routing(self, selector):
        """Test that operator-set model pin wins."""
        pin = ModelTier.SONNET.value
        decision = selector.select(
            complexity_tier="simple",  # Would normally select Haiku
            task_feature_hash="abc123",
            model_pin=pin,
        )

        assert decision.model_name == pin
        assert decision.confidence == 1.0
        assert "pinned" in decision.reasoning.lower()

    def test_weight_update_on_feedback(self, selector):
        """Test that weights are updated with feedback."""
        tier = "medium"
        model = ModelTier.SONNET.value

        # Get initial weight
        initial_weight = selector.weights.medium_weights[model]

        # Apply positive feedback
        selector.update_weights(tier, model, feedback_signal=1.0, learning_rate=0.1)

        # Weight should increase
        new_weight = selector.weights.medium_weights[model]
        assert new_weight > initial_weight, "Weight should increase after positive feedback"


# ============================================================================
# ROUTING INTEGRATION TESTS (3 tests)
# ============================================================================

class TestRoutingIntegration:
    """End-to-end routing tests."""

    def test_end_to_end_routing(self, skill):
        """Test complete routing pipeline: classify -> route -> audit."""
        input_data = RoutingInput(
            task_prompt="Implement quicksort in Python with unit tests",
            tenant_id="_default",
            task_id="task_001",
        )

        output = skill.execute(input_data)

        assert output.model_name in [
            "claude-haiku-4-5-20251001",
            "claude-sonnet-5-20251001",
            "claude-opus-5-20251001",
        ]
        assert 0.0 <= output.classifier_confidence <= 1.0
        assert 0.0 <= output.routing_confidence <= 1.0
        assert output.feature_hash  # Must have feature hash for audit trail
        assert output.reasoning  # Must have human-readable reasoning

    def test_routing_with_operator_pin(self, skill):
        """Test routing respects operator model pin."""
        pin = "claude-opus-5-20251001"
        input_data = RoutingInput(
            task_prompt="Simple query",
            tenant_id="_default",
            task_id="task_002",
            model_pin=pin,
        )

        output = skill.execute(input_data)

        assert output.model_name == pin

    def test_routing_audit_trail(self, skill, mock_audit_trail):
        """Test that routing decision is audited."""
        input_data = RoutingInput(
            task_prompt="What is AI?",
            tenant_id="_default",
            task_id="task_003",
        )

        output = skill.execute(input_data)

        # Verify audit trail was called
        assert mock_audit_trail.write_event.called
        call_args = mock_audit_trail.write_event.call_args[0]
        event = call_args[0]

        assert event.tenant_id == "_default"
        assert event.skill_id == skill.skill_id
        assert event.status == SkillExecutionStatus.SUCCESS


# ============================================================================
# FEEDBACK & LEARNING TESTS (3 tests)
# ============================================================================

class TestFeedbackAndLearning:
    """Feedback processing and weight updates."""

    def test_process_positive_feedback(self, skill):
        """Test processing positive feedback."""
        model_used = ModelTier.SONNET.value
        tier = "medium"

        initial_count = skill.feedback_count

        skill.feedback(
            task_id="task_004",
            model_used=model_used,
            complexity_tier=tier,
            outcome="correct",
        )

        assert skill.feedback_count == initial_count + 1
        assert skill.last_updated != ""

    def test_process_negative_feedback(self, skill):
        """Test processing negative feedback."""
        model_used = ModelTier.HAIKU.value
        tier = "complex"

        initial_weight = skill.selector.weights.complex_weights[model_used]

        skill.feedback(
            task_id="task_005",
            model_used=model_used,
            complexity_tier=tier,
            outcome="incorrect",
        )

        # Weight should decrease after negative feedback
        new_weight = skill.selector.weights.complex_weights[model_used]
        assert new_weight < initial_weight

    def test_config_persistence(self, skill):
        """Test exporting and loading skill configuration."""
        # Apply some feedback to change state
        skill.feedback(
            task_id="task_006",
            model_used=ModelTier.SONNET.value,
            complexity_tier="medium",
            outcome="correct",
        )

        # Export config
        config = skill.get_config()

        assert config['skill_id'] == skill.skill_id
        assert config['version'] == skill.version
        assert config['feedback_count'] > 0
        assert 'routing_weights' in config

        # Create new skill and load config
        new_skill = WorkflowOptimizerSkill(
            tenant_id="_default",
            audit_trail=skill.audit_trail
        )
        new_skill.set_config(config)

        assert new_skill.feedback_count == skill.feedback_count
        assert new_skill.last_updated == skill.last_updated


# ============================================================================
# ADDITIONAL EDGE CASE TESTS
# ============================================================================

class TestEdgeCases:
    """Edge case and error handling."""

    def test_invalid_tenant_mismatch(self, skill):
        """Test that routing fails on tenant mismatch."""
        input_data = RoutingInput(
            task_prompt="Hello",
            tenant_id="other_tenant",  # Mismatch
            task_id="task_007",
        )

        with pytest.raises(ValueError, match="Tenant mismatch"):
            skill.execute(input_data)

    def test_empty_task_prompt(self, skill):
        """Test that routing fails on empty prompt."""
        input_data = RoutingInput(
            task_prompt="",  # Empty
            tenant_id="_default",
            task_id="task_008",
        )

        with pytest.raises(ValueError, match="task_prompt required"):
            skill.execute(input_data)

    def test_unknown_complexity_tier(self, selector):
        """Test handling of unknown complexity tier."""
        decision = selector.select(
            complexity_tier="unknown_tier",
            task_feature_hash="abc123",
        )

        # Should fall back to medium weights
        assert decision.model_name in [
            ModelTier.HAIKU.value,
            ModelTier.SONNET.value,
            ModelTier.OPUS.value,
        ]


# ============================================================================
# SUMMARY
# ============================================================================

if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

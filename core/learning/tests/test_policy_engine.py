"""Unit tests for ApprovalPolicyEngine (Feature 2, Week 3 L5 k=2).

Tests cover:
1. Rule creation and validation
2. Rule removal
3. Rule listing
4. Rule evaluation (all 5 types)
5. Rule precedence (first match wins)
6. Persistence and recovery
7. Audit integration
8. Conflict resolution
9. Invalid config rejection
10. Edge cases
"""

import pytest
from unittest.mock import Mock
from datetime import datetime, time
import tempfile
import json

from core.learning.policy_engine import (
    ApprovalPolicyEngine,
    RuleType,
    ApprovalRule,
    RuleEvaluationResult,
)
from core.skills.feedback_stability import OperatorApprovalGate


@pytest.fixture
def mock_audit_backend():
    """Mock audit backend."""
    backend = Mock()
    backend.write_event = Mock(return_value="event_id")
    return backend


@pytest.fixture
def approval_gate(mock_audit_backend, tmp_path):
    """Create OperatorApprovalGate with temp directory."""
    gate = OperatorApprovalGate(
        tenant_id="_default",
        auto_approval_confidence_threshold=0.8,
        audit_backend=mock_audit_backend,
        corvin_home=str(tmp_path),
    )
    return gate


@pytest.fixture
def policy_engine(approval_gate, tmp_path):
    """Create ApprovalPolicyEngine."""
    return ApprovalPolicyEngine(
        approval_gate=approval_gate,
        tenant_id="_default",
        corvin_home=str(tmp_path),
    )


# ============================================================================
# Test: Rule Creation & Validation
# ============================================================================


class TestRuleCreation:
    """Test rule creation and validation."""

    def test_create_confidence_threshold_rule(self, policy_engine):
        """Test creating a confidence_threshold rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )
        assert rule_id is not None
        assert len(rule_id) == 36  # UUID

    def test_create_magnitude_limit_rule(self, policy_engine):
        """Test creating a magnitude_limit rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MAGNITUDE_LIMIT,
            config={"limit": 0.5},
        )
        assert rule_id is not None

    def test_create_metric_whitelist_rule(self, policy_engine):
        """Test creating a metric_whitelist rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.METRIC_WHITELIST,
            config={"metrics": ["metric_1", "metric_2"]},
        )
        assert rule_id is not None

    def test_create_momentum_pattern_rule(self, policy_engine):
        """Test creating a momentum_pattern rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MOMENTUM_PATTERN,
            config={"min_count": 5},
        )
        assert rule_id is not None

    def test_create_time_window_rule(self, policy_engine):
        """Test creating a time_window rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.TIME_WINDOW,
            config={"start_hh": 9, "start_mm": 0, "end_hh": 17, "end_mm": 0},
        )
        assert rule_id is not None

    def test_invalid_confidence_threshold_missing_field(self, policy_engine):
        """Test that missing required field is rejected."""
        with pytest.raises(ValueError, match="requires 'threshold'"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.CONFIDENCE_THRESHOLD,
                config={},  # Missing 'threshold'
            )

    def test_invalid_confidence_threshold_out_of_range(self, policy_engine):
        """Test that out-of-range threshold is rejected."""
        with pytest.raises(ValueError, match="must be in"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.CONFIDENCE_THRESHOLD,
                config={"threshold": 1.5},
            )

    def test_invalid_magnitude_limit_negative(self, policy_engine):
        """Test that negative magnitude limit is rejected."""
        with pytest.raises(ValueError, match=">= 0.0"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.MAGNITUDE_LIMIT,
                config={"limit": -0.5},
            )

    def test_invalid_metric_whitelist_empty(self, policy_engine):
        """Test that empty metric whitelist is rejected."""
        with pytest.raises(ValueError, match="non-empty list"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.METRIC_WHITELIST,
                config={"metrics": []},
            )

    def test_invalid_time_window_invalid_hh(self, policy_engine):
        """Test that invalid hour is rejected."""
        with pytest.raises(ValueError, match="hh must be"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.TIME_WINDOW,
                config={"start_hh": 25, "start_mm": 0, "end_hh": 17, "end_mm": 0},
            )


# ============================================================================
# Test: Rule Removal
# ============================================================================


class TestRuleRemoval:
    """Test rule removal."""

    def test_remove_rule(self, policy_engine):
        """Test removing a rule."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        success = policy_engine.remove_rule("test.skill_1", rule_id)
        assert success is True

    def test_remove_nonexistent_rule(self, policy_engine):
        """Test removing non-existent rule."""
        success = policy_engine.remove_rule("test.skill_1", "nonexistent")
        assert success is False

    def test_remove_rule_from_empty_skill(self, policy_engine):
        """Test removing rule from skill with no rules."""
        success = policy_engine.remove_rule("nonexistent.skill", "some_rule_id")
        assert success is False


# ============================================================================
# Test: Rule Listing
# ============================================================================


class TestRuleListing:
    """Test rule listing."""

    def test_list_rules_empty(self, policy_engine):
        """Test listing rules for skill with no rules."""
        rules = policy_engine.list_rules("test.skill_1")
        assert rules == []

    def test_list_rules_multiple(self, policy_engine):
        """Test listing multiple rules for a skill."""
        rule_id_1 = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )
        rule_id_2 = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MAGNITUDE_LIMIT,
            config={"limit": 0.5},
        )

        rules = policy_engine.list_rules("test.skill_1")
        assert len(rules) == 2
        assert rules[0]["rule_id"] == rule_id_1
        assert rules[1]["rule_id"] == rule_id_2


# ============================================================================
# Test: Rule Evaluation
# ============================================================================


class TestRuleEvaluation:
    """Test rule evaluation."""

    def test_evaluate_confidence_threshold_match(self, policy_engine):
        """Test confidence_threshold rule matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.9,  # > 0.7
        )

        assert result.decision == "auto-approve"
        assert len(result.matched_rules) == 1

    def test_evaluate_confidence_threshold_no_match(self, policy_engine):
        """Test confidence_threshold rule not matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.5,  # < 0.7
        )

        assert result.decision == "pending"

    def test_evaluate_magnitude_limit_match(self, policy_engine):
        """Test magnitude_limit rule matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MAGNITUDE_LIMIT,
            config={"limit": 0.3},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.5,  # > 0.3
            confidence=0.9,
        )

        assert result.decision == "auto-reject"

    def test_evaluate_metric_whitelist_match(self, policy_engine):
        """Test metric_whitelist rule matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.METRIC_WHITELIST,
            config={"metrics": ["metric_1", "metric_2"]},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",  # In whitelist
            magnitude=0.5,
            confidence=0.5,
        )

        assert result.decision == "auto-approve"

    def test_evaluate_metric_whitelist_no_match(self, policy_engine):
        """Test metric_whitelist rule not matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.METRIC_WHITELIST,
            config={"metrics": ["metric_1", "metric_2"]},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_3",  # Not in whitelist
            magnitude=0.5,
            confidence=0.5,
        )

        assert result.decision == "pending"

    def test_evaluate_momentum_pattern_match(self, policy_engine):
        """Test momentum_pattern rule matching."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MOMENTUM_PATTERN,
            config={"min_count": 3},
        )

        recent_history = [
            {"approval_id": "1", "decision": "approved"},
            {"approval_id": "2", "decision": "approved"},
            {"approval_id": "3", "decision": "approved"},
            {"approval_id": "4", "decision": "rejected"},
        ]

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.5,
            recent_history=recent_history,
        )

        assert result.decision == "auto-approve"

    def test_evaluate_time_window_match(self, policy_engine):
        """Test time_window rule matching (simplified)."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.TIME_WINDOW,
            config={"start_hh": 0, "start_mm": 0, "end_hh": 23, "end_mm": 59},
        )

        # Any time should match this window
        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.5,
        )

        assert result.decision == "auto-approve"

    def test_evaluate_no_rules(self, policy_engine):
        """Test evaluating when no rules are configured."""
        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.5,
        )

        assert result.decision == "pending"
        assert result.matched_rules == []


# ============================================================================
# Test: Rule Precedence
# ============================================================================


class TestRulePrecedence:
    """Test rule precedence (first match wins)."""

    def test_first_match_wins(self, policy_engine):
        """Test that first matching rule wins."""
        # Add confidence threshold (will match)
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )
        # Add magnitude limit (also would match)
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MAGNITUDE_LIMIT,
            config={"limit": 0.2},
        )

        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.3,  # Would trigger magnitude rule
            confidence=0.9,  # Would trigger confidence rule
        )

        # First rule (confidence) matches and wins
        assert result.decision == "auto-approve"
        assert len(result.matched_rules) == 1


# ============================================================================
# Test: Persistence
# ============================================================================


class TestRulePersistence:
    """Test rule persistence and recovery."""

    def test_persist_rule(self, policy_engine):
        """Test that rules are persisted to disk."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        # Verify file exists
        assert policy_engine.rules_file.exists()
        with open(policy_engine.rules_file, "r") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["skill_id"] == "test.skill_1"
            assert data["rule_type"] == "confidence_threshold"

    def test_load_persisted_rules(self, approval_gate, tmp_path):
        """Test recovery of rules from disk."""
        # Create initial engine and add rule
        engine1 = ApprovalPolicyEngine(
            approval_gate=approval_gate,
            tenant_id="_default",
            corvin_home=str(tmp_path),
        )

        rule_id = engine1.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        # Create new engine (should load from disk)
        engine2 = ApprovalPolicyEngine(
            approval_gate=approval_gate,
            tenant_id="_default",
            corvin_home=str(tmp_path),
        )

        # Verify rule was loaded
        rules = engine2.list_rules("test.skill_1")
        assert len(rules) == 1
        assert rules[0]["rule_id"] == rule_id


# ============================================================================
# Test: Audit Integration
# ============================================================================


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_rule_creation_audit(self, policy_engine, approval_gate):
        """Test that rule creation is audited."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        # Verify audit was called
        approval_gate.audit_backend.write_event.assert_called()

    def test_audit_fail_closed(self, policy_engine, approval_gate):
        """Test fail-closed constraint: audit failure blocks creation."""
        approval_gate.audit_backend.write_event.side_effect = Exception("Audit failed")

        with pytest.raises(RuntimeError, match="FATAL.*audit failed"):
            policy_engine.add_rule(
                skill_id="test.skill_1",
                rule_type=RuleType.CONFIDENCE_THRESHOLD,
                config={"threshold": 0.7},
            )

    def test_rule_removal_audit(self, policy_engine, approval_gate):
        """Test that rule removal is audited."""
        rule_id = policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        # Reset mock to clear previous calls
        approval_gate.audit_backend.write_event.reset_mock()

        policy_engine.remove_rule("test.skill_1", rule_id)

        # Verify audit was called
        approval_gate.audit_backend.write_event.assert_called()


# ============================================================================
# Test: Edge Cases
# ============================================================================


class TestEdgeCases:
    """Test edge cases."""

    def test_evaluate_with_none_recent_history(self, policy_engine):
        """Test evaluation with no recent history."""
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.MOMENTUM_PATTERN,
            config={"min_count": 5},
        )

        # Call with recent_history=None
        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="metric_1",
            magnitude=0.1,
            confidence=0.5,
            recent_history=None,
        )

        # Should not crash
        assert result.decision == "pending"

    def test_rule_evaluation_with_invalid_rule_data(self, policy_engine):
        """Test that invalid rule data is handled gracefully."""
        # This tests robustness during evaluation
        policy_engine.add_rule(
            skill_id="test.skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )

        # Should not crash even with unusual input
        result = policy_engine.evaluate_rules(
            skill_id="test.skill_1",
            metric_name="",
            magnitude=-999,
            confidence=999,
        )

        assert result.decision in ["pending", "auto-approve", "auto-reject"]

    def test_multiple_skills_independent_rules(self, policy_engine):
        """Test that rules for different skills are independent."""
        policy_engine.add_rule(
            skill_id="skill_1",
            rule_type=RuleType.CONFIDENCE_THRESHOLD,
            config={"threshold": 0.7},
        )
        policy_engine.add_rule(
            skill_id="skill_2",
            rule_type=RuleType.MAGNITUDE_LIMIT,
            config={"limit": 0.3},
        )

        rules_1 = policy_engine.list_rules("skill_1")
        rules_2 = policy_engine.list_rules("skill_2")

        assert len(rules_1) == 1
        assert len(rules_2) == 1
        assert rules_1[0]["rule_type"] == "confidence_threshold"
        assert rules_2[0]["rule_type"] == "magnitude_limit"

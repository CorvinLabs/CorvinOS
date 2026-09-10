"""Unit tests for context_filter.py — Static Scoring (k=1)."""

import pytest
from datetime import datetime
from core.skills.os_skills.context_filter import (
    ContextBlock, FilterDecision, FilterConfig, ContextCategory,
    StaticScorer, filter_context, validate_no_pii
)


class TestStaticScorer:
    """Test static scoring logic."""

    def test_default_scores(self):
        """Verify default scores are correct."""
        scorer = StaticScorer()

        block = ContextBlock(
            id="task_history",
            content="task was started at 10am",
            size_tokens=10,
            category=ContextCategory.TASK_HISTORY,
            timestamp=datetime.utcnow(),
        )
        score = scorer.score(block)
        assert score == 0.9, "task_history should score 0.9"

    def test_all_categories(self):
        """Test scores for all context categories."""
        scorer = StaticScorer()
        expected_scores = {
            ContextCategory.TASK_HISTORY: 0.9,
            ContextCategory.USER_PROFILE: 0.7,
            ContextCategory.SESSION_STATE: 0.5,
            ContextCategory.CONVERSATION_RECALL: 0.8,
            ContextCategory.SYSTEM_MESSAGES: 0.95,
        }

        for category, expected_score in expected_scores.items():
            block = ContextBlock(
                id=category.value,
                content="test",
                size_tokens=10,
                category=category,
                timestamp=datetime.utcnow(),
            )
            score = scorer.score(block)
            assert score == expected_score, f"{category.value} should score {expected_score}, got {score}"

    def test_custom_config(self):
        """Test custom score configuration."""
        config = FilterConfig(
            static_scores={"task_history": 0.5}
        )
        scorer = StaticScorer(config)

        block = ContextBlock(
            id="task_history",
            content="task",
            size_tokens=10,
            category=ContextCategory.TASK_HISTORY,
            timestamp=datetime.utcnow(),
        )
        score = scorer.score(block)
        assert score == 0.5, "Custom score should override default"


class TestFilterContext:
    """Test filtering logic."""

    def test_high_score_included(self):
        """High-scoring blocks should be included."""
        blocks = [
            ContextBlock(
                id="system_messages",
                content="system prompt",
                size_tokens=100,
                category=ContextCategory.SYSTEM_MESSAGES,  # score=0.95
                timestamp=datetime.utcnow(),
            )
        ]

        included, decisions = filter_context(blocks)
        assert len(included) == 1
        assert included[0].id == "system_messages"
        assert decisions[0].action == "include"

    def test_low_score_filtered(self):
        """Low-scoring blocks (below 0.7 threshold) should be filtered."""
        blocks = [
            ContextBlock(
                id="session_state",
                content="session info",
                size_tokens=100,
                category=ContextCategory.SESSION_STATE,  # score=0.5
                timestamp=datetime.utcnow(),
            )
        ]

        included, decisions = filter_context(blocks)
        assert len(included) == 0  # Should be filtered (unless fallback applies)
        assert decisions[0].action == "include"  # But fallback applied (only block)

    def test_threshold_boundary(self):
        """Test filtering at the threshold boundary (0.7)."""
        config = FilterConfig(threshold=0.7)

        # Just at threshold (0.7) → should be included
        block_at = ContextBlock(
            id="user_profile",
            content="profile",
            size_tokens=100,
            category=ContextCategory.USER_PROFILE,  # score=0.7
            timestamp=datetime.utcnow(),
        )
        included, _ = filter_context([block_at], config)
        assert len(included) == 1, "Score 0.7 should be included (>= threshold)"

    def test_fallback_largest_block(self):
        """If all blocks filtered, fallback to largest."""
        blocks = [
            ContextBlock(
                id="state_1",
                content="small",
                size_tokens=10,
                category=ContextCategory.SESSION_STATE,  # score=0.5
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="state_2",
                content="larger state info",
                size_tokens=200,  # Largest
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
        ]

        included, decisions = filter_context(blocks)
        assert len(included) == 1
        assert included[0].id == "state_2", "Largest block should be fallback"
        assert decisions[-1].action == "fallback"

    def test_fallback_disabled(self):
        """If fallback disabled, all blocks can be filtered."""
        config = FilterConfig(include_fallback=False)
        blocks = [
            ContextBlock(
                id="session_state",
                content="session",
                size_tokens=100,
                category=ContextCategory.SESSION_STATE,  # score=0.5
                timestamp=datetime.utcnow(),
            )
        ]

        included, _ = filter_context(blocks, config)
        assert len(included) == 0, "All blocks filtered (no fallback)"

    def test_mixed_scores(self):
        """Test filtering with mixed high/low scores."""
        blocks = [
            ContextBlock(id="t1", content="task", size_tokens=50,
                        category=ContextCategory.TASK_HISTORY, timestamp=datetime.utcnow()),  # 0.9
            ContextBlock(id="s1", content="session", size_tokens=50,
                        category=ContextCategory.SESSION_STATE, timestamp=datetime.utcnow()),  # 0.5
            ContextBlock(id="m1", content="message", size_tokens=50,
                        category=ContextCategory.SYSTEM_MESSAGES, timestamp=datetime.utcnow()),  # 0.95
        ]

        included, decisions = filter_context(blocks)
        included_ids = {b.id for b in included}

        assert "t1" in included_ids, "task_history (0.9) should be included"
        assert "m1" in included_ids, "system_messages (0.95) should be included"
        assert "s1" not in included_ids, "session_state (0.5) should be filtered"


class TestPIIValidation:
    """Test PII detection."""

    def test_no_pii_safe(self):
        """Context with no PII should be safe."""
        context = {"task": "do work", "status": "in progress"}
        is_safe, pii_fields = validate_no_pii(context)
        assert is_safe is True
        assert len(pii_fields) == 0

    def test_pii_detected(self):
        """PII fields should be detected."""
        context = {"user_email": "test@example.com", "api_key": "secret"}
        is_safe, pii_fields = validate_no_pii(context)
        assert is_safe is False
        assert "user_email" in pii_fields
        assert "api_key" in pii_fields

    def test_case_insensitive_detection(self):
        """PII detection should be case-insensitive."""
        context = {"PASSWORD": "abc123", "SecretToken": "xyz"}
        is_safe, pii_fields = validate_no_pii(context)
        assert is_safe is False
        assert "PASSWORD" in pii_fields or "SecretToken" in pii_fields


class TestFilterDecision:
    """Test FilterDecision immutability and audit structure."""

    def test_immutable(self):
        """FilterDecision should be immutable."""
        decision = FilterDecision(
            block_id="test",
            score=0.8,
            threshold=0.7,
            action="include",
            reason="test",
        )

        with pytest.raises(AttributeError):
            decision.action = "filter"  # Should fail (frozen)

    def test_audit_event_structure(self):
        """FilterDecision carries all audit-required fields."""
        decision = FilterDecision(
            block_id="test",
            score=0.8,
            threshold=0.7,
            action="include",
            reason="test",
        )

        assert hasattr(decision, "timestamp")
        assert decision.timestamp is not None
        assert hasattr(decision, "reason")
        assert hasattr(decision, "action")

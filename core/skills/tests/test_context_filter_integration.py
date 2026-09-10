"""Integration tests for context_filter.py — CEL Pipeline Wiring (k=2)."""

import pytest
from datetime import datetime
from unittest.mock import MagicMock, patch
from core.skills.os_skills.context_filter import (
    ContextBlock, FilterConfig, ContextCategory,
    filter_context, ContextFilterAuditEvent, validate_no_pii
)


class TestContextFilterIntegration:
    """Test context_filter integrated with audit chain."""

    def test_full_pipeline_filtering(self):
        """Test full pipeline: snapshot → filter → audit → output."""
        # Simulate a full context snapshot from L10
        blocks = [
            ContextBlock(
                id="task_summary",
                content="User started task: build API endpoint",
                size_tokens=20,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="session_state",
                content="Session started 2 hours ago",
                size_tokens=10,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="user_prefs",
                content="User prefers detailed explanations",
                size_tokens=15,
                category=ContextCategory.USER_PROFILE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="system_msg",
                content="You are an AI assistant",
                size_tokens=5,
                category=ContextCategory.SYSTEM_MESSAGES,
                timestamp=datetime.utcnow(),
            ),
        ]

        # Filter
        included, decisions = filter_context(blocks)

        # Assertions
        assert len(included) == 3, "Should include: task_summary (0.9), user_prefs (0.7), system_msg (0.95)"
        assert len(decisions) == 4, "Should have decisions for all 4 blocks"

        # Verify filtered block
        filtered_ids = {d.block_id for d in decisions if d.action == "filter"}
        assert "session_state" in filtered_ids, "session_state (0.5) should be filtered"

    def test_audit_events_structure(self):
        """Test that decisions are audit-ready."""
        block = ContextBlock(
            id="test",
            content="test content",
            size_tokens=50,
            category=ContextCategory.TASK_HISTORY,
            timestamp=datetime.utcnow(),
        )

        included, decisions = filter_context([block])
        decision = decisions[0]

        # All audit-required fields present
        assert decision.block_id == "test"
        assert decision.score is not None
        assert decision.action is not None
        assert decision.reason is not None
        assert decision.timestamp is not None

        # Can serialize to audit event
        audit_event = ContextFilterAuditEvent.from_decision(
            decision,
            block_id=decision.block_id,
            tenant_id="_default",
        )
        assert audit_event["event_type"] == "context_filtered"
        assert audit_event["tenant_id"] == "_default"
        assert audit_event["action"] == "include"

    def test_pii_safety_gate(self):
        """Test PII validation before passing context downstream."""
        blocks = [
            ContextBlock(
                id="safe_context",
                content="Task: implement feature",
                size_tokens=50,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
        ]

        included, decisions = filter_context(blocks)

        # Validate included blocks have no PII
        for block in included:
            # Mock audit payload
            payload = {"content": block.content}
            is_safe, pii_fields = validate_no_pii(payload)
            assert is_safe, f"Block {block.id} has PII: {pii_fields}"

    def test_context_reduction_metric(self):
        """Test that filtering reduces context size."""
        blocks = [
            # High-scoring (included)
            ContextBlock(
                id="task",
                content="A" * 100,  # 100 chars
                size_tokens=50,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
            # Low-scoring (filtered)
            ContextBlock(
                id="session",
                content="B" * 200,  # 200 chars
                size_tokens=100,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
        ]

        included, decisions = filter_context(blocks)

        # Measure reduction
        total_size_before = sum(b.size_tokens for b in blocks)
        total_size_after = sum(b.size_tokens for b in included)
        reduction_pct = 100 * (1 - total_size_after / total_size_before)

        assert reduction_pct > 0, "Filtering should reduce context"
        assert reduction_pct >= 50, "Should achieve >50% reduction (1 of 2 blocks filtered)"

    def test_fallback_behavior_realistic(self):
        """Test fallback in realistic scenario where all blocks are low-relevance."""
        blocks = [
            ContextBlock(
                id="session_1",
                content="Session debug info",
                size_tokens=30,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="session_2",
                content="More session info",
                size_tokens=50,  # Largest
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
        ]

        included, decisions = filter_context(blocks)

        # Fallback should include largest
        assert len(included) == 1
        assert included[0].id == "session_2"

        # Last decision should indicate fallback
        fallback_decision = [d for d in decisions if d.action == "fallback"]
        assert len(fallback_decision) > 0, "Should have fallback action"

    def test_deterministic_scoring(self):
        """Test that scoring is deterministic (same input → same output)."""
        blocks = [
            ContextBlock(
                id="test",
                content="content",
                size_tokens=50,
                category=ContextCategory.USER_PROFILE,
                timestamp=datetime.utcnow(),
            ),
        ]

        # Run twice
        included_1, decisions_1 = filter_context(blocks)
        included_2, decisions_2 = filter_context(blocks)

        # Should be identical
        assert len(included_1) == len(included_2)
        assert decisions_1[0].score == decisions_2[0].score
        assert decisions_1[0].action == decisions_2[0].action

    def test_edge_case_empty_blocks(self):
        """Test edge case: empty block list."""
        included, decisions = filter_context([])

        assert len(included) == 0
        assert len(decisions) == 0

    def test_edge_case_all_high_scores(self):
        """Test edge case: all blocks are high-relevance."""
        blocks = [
            ContextBlock(
                id="task",
                content="task",
                size_tokens=50,
                category=ContextCategory.TASK_HISTORY,  # 0.9
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="recall",
                content="recall",
                size_tokens=50,
                category=ContextCategory.CONVERSATION_RECALL,  # 0.8
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="system",
                content="system",
                size_tokens=50,
                category=ContextCategory.SYSTEM_MESSAGES,  # 0.95
                timestamp=datetime.utcnow(),
            ),
        ]

        included, decisions = filter_context(blocks)

        # All should be included
        assert len(included) == 3
        for d in decisions:
            assert d.action == "include"


class TestContextFilterWithMockAuditChain:
    """Test integration with audit chain (mocked)."""

    @patch("core.skills.os_skills.context_filter.ContextFilterAuditEvent.from_decision")
    def test_audit_event_emission(self, mock_from_decision):
        """Test that filter decisions are emitted to audit chain."""
        mock_from_decision.return_value = {
            "event_type": "context_filtered",
            "action": "include",
            "block_id": "test",
        }

        block = ContextBlock(
            id="test",
            content="content",
            size_tokens=50,
            category=ContextCategory.TASK_HISTORY,
            timestamp=datetime.utcnow(),
        )

        included, decisions = filter_context([block])

        # Verify audit event creation was triggered
        assert len(decisions) > 0
        decision = decisions[0]
        assert decision.action == "include"

        # In production, this would call audit_backend.write_event()
        # For now, just verify the structure is correct
        audit_event = ContextFilterAuditEvent.from_decision(decision, block_id="test")
        assert "event_type" in audit_event
        assert "action" in audit_event

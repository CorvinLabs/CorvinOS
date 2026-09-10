"""E2E tests for context_filter.py — Real Routing Path (k=3)."""

import pytest
from datetime import datetime
from core.skills.os_skills.context_filter import (
    ContextBlock, FilterConfig, ContextCategory,
    filter_context, ContextFilterAuditEvent
)


class TestContextFilterE2E:
    """E2E tests: simulate full routing decision path."""

    def test_e2e_routing_with_filtering(self):
        """E2E: Simulate a real routing scenario with context filtering.

        Scenario: User asks for API design help.
        - Task history is high-value (0.9)
        - Session state is noise (0.5)
        - System message is mandatory (0.95)
        - Decision: filter session state, keep others
        """
        # 1. Snapshot phase (L10 context engineering)
        context_snapshot = [
            ContextBlock(
                id="current_task",
                content="User wants to design RESTful API with CRUD endpoints",
                size_tokens=30,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="session_metadata",
                content="Session ID: xyz, Started: 2h ago, Browser: Chrome",
                size_tokens=20,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="user_style",
                content="User prefers detailed, code-first explanations",
                size_tokens=15,
                category=ContextCategory.USER_PROFILE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="system_prompt",
                content="You are Claude, an AI assistant made by Anthropic",
                size_tokens=10,
                category=ContextCategory.SYSTEM_MESSAGES,
                timestamp=datetime.utcnow(),
            ),
        ]

        # 2. Filter phase (L10 context filter)
        filtered_blocks, decisions = filter_context(context_snapshot)

        # 3. Routing phase (L5 auto-routing receives filtered context)
        # Simulate routing decision
        routing_input = {
            "task": next(b.content for b in filtered_blocks if b.category == ContextCategory.TASK_HISTORY),
            "style": next((b.content for b in filtered_blocks if b.category == ContextCategory.USER_PROFILE), ""),
        }

        # 4. Verify filtering effectiveness
        assert len(filtered_blocks) == 3, "Should filter session_metadata (0.5 < 0.7)"
        assert all(b.id != "session_metadata" for b in filtered_blocks)

        # 5. Verify audit trail
        session_decision = next((d for d in decisions if d.block_id == "session_metadata"), None)
        assert session_decision is not None
        assert session_decision.action == "filter"

    def test_e2e_no_silent_filtering(self):
        """E2E: Ensure no context is silently dropped (audit-first principle)."""
        blocks = [
            ContextBlock(
                id="block1",
                content="Content 1",
                size_tokens=50,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="block2",
                content="Content 2",
                size_tokens=50,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
        ]

        filtered, decisions = filter_context(blocks)

        # Audit trail must account for every block
        assert len(decisions) == len(blocks), "Decision for every block"

        # No silent filtering: every decision is logged
        for block in blocks:
            decision = next(d for d in decisions if d.block_id == block.id)
            assert decision.action in ["include", "filter", "lm_ask", "fallback"]
            assert decision.reason != "", "Every decision has a reason"

    def test_e2e_fallback_ensures_prompt_not_empty(self):
        """E2E: Fallback ensures prompt is never empty (routing must have context)."""
        # Edge case: all blocks are low-relevance
        blocks = [
            ContextBlock(
                id="low_1",
                content="Session debug info",
                size_tokens=30,
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
            ContextBlock(
                id="low_2",
                content="Session debug info 2",
                size_tokens=50,  # Largest
                category=ContextCategory.SESSION_STATE,
                timestamp=datetime.utcnow(),
            ),
        ]

        filtered, decisions = filter_context(blocks)

        # Fallback must ensure included has ≥1 block
        assert len(filtered) > 0, "Fallback ensures prompt is not empty"
        assert filtered[0].id == "low_2", "Fallback includes largest block"

    def test_e2e_audit_event_can_be_logged(self):
        """E2E: Verify audit events can be serialized and logged."""
        block = ContextBlock(
            id="test",
            content="Test content",
            size_tokens=50,
            category=ContextCategory.TASK_HISTORY,
            timestamp=datetime.utcnow(),
        )

        filtered, decisions = filter_context([block])
        decision = decisions[0]

        # Create audit event (would be written to audit.jsonl in production)
        audit_event = ContextFilterAuditEvent.from_decision(
            decision,
            block_id=decision.block_id,
            tenant_id="_default",
        )

        # Verify audit event has all required fields
        assert "event_type" in audit_event
        assert audit_event["event_type"] == "context_filtered"
        assert "timestamp" in audit_event
        assert "tenant_id" in audit_event
        assert audit_event["tenant_id"] == "_default"
        assert "action" in audit_event
        assert "reason" in audit_event

    def test_e2e_multiple_routing_calls(self):
        """E2E: Verify consistency across multiple routing calls."""
        blocks = [
            ContextBlock(
                id="task",
                content="User task",
                size_tokens=50,
                category=ContextCategory.TASK_HISTORY,
                timestamp=datetime.utcnow(),
            ),
        ]

        # Call filter 3 times (simulating 3 routing decisions)
        results = []
        for _ in range(3):
            filtered, decisions = filter_context(blocks)
            results.append((len(filtered), decisions[0].score))

        # All calls should produce identical results (deterministic)
        assert all(r[0] == results[0][0] for r in results), "Consistent filtering"
        assert all(r[1] == results[0][1] for r in results), "Consistent scoring"

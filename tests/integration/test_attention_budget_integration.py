"""Integration tests for Attention Budget with Learning Events (ADR-0314 + ADR-0319).

Tests that attention budget operations are properly tracked in the learning event system.
"""

import pytest
from datetime import datetime
from core.learning.attention_budget import (
    AttentionBudget,
    AttentionTracker,
    BudgetStatus,
)
from core.learning.learning_events import LearningEvent, EventType


class TestAttentionBudgetLearningIntegration:
    """Test integration with learning events (ADR-0314)."""

    @pytest.fixture
    def budget(self):
        """Create budget."""
        return AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_context_tokens=8000,
        )

    @pytest.fixture
    def tracker(self, budget):
        """Create tracker."""
        return AttentionTracker(budget)

    def test_attention_consumed_event(self, tracker):
        """Attention consumption should be trackable as a learning event."""
        # Record consumption
        tracker.record_context_usage(1000)

        # In a real scenario, this would be emitted to the learning event system
        event = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker.budget.tenant_id,
            signal={
                "action": "consumed",
                "tokens": 1000,
                "total_consumed": tracker.usage.context_tokens_used,
                "remaining": tracker.get_remaining_context(),
            },
        )

        assert event.event_type == EventType.ATTENTION
        assert event.signal["action"] == "consumed"
        assert event.signal["tokens"] == 1000
        assert event.tenant_id == "_default"

    def test_attention_budget_status_event(self, tracker):
        """Budget status changes should be trackable as events."""
        tracker.record_context_usage(6400)

        stats = tracker.get_stats()
        event = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker.budget.tenant_id,
            signal={
                "action": "status_changed",
                "status": stats.status.value,
                "percent_used": stats.percent_used,
                "consumed": stats.consumed,
                "remaining": stats.remaining,
            },
        )

        assert event.signal["action"] == "status_changed"
        assert event.signal["status"] == "critical"
        assert event.signal["percent_used"] == 0.8

    def test_attention_refund_event(self, tracker):
        """Attention refunds should be trackable as events."""
        tracker.record_context_usage(5000)
        tracker.refund_unused(2000)

        event = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker.budget.tenant_id,
            signal={
                "action": "refunded",
                "refunded_tokens": 2000,
                "total_consumed": tracker.usage.context_tokens_used,
                "remaining": tracker.get_remaining_context(),
            },
        )

        assert event.signal["action"] == "refunded"
        assert event.signal["refunded_tokens"] == 2000
        assert tracker.usage.context_tokens_used == 3000

    def test_confidence_weighted_allocation_event(self, tracker):
        """Confidence-weighted allocation should be trackable."""
        allocated = tracker.allocate_confidence_weighted(1000, 0.75)

        event = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker.budget.tenant_id,
            signal={
                "action": "confidence_weighted_allocation",
                "base_tokens_requested": 1000,
                "confidence_score": 0.75,
                "allocated_tokens": allocated,
            },
        )

        assert event.signal["action"] == "confidence_weighted_allocation"
        assert event.signal["allocated_tokens"] == 750

    def test_budget_exhaustion_event(self, tracker):
        """Budget exhaustion should trigger an event."""
        tracker.record_context_usage(8000)

        stats = tracker.get_stats()
        assert stats.status == BudgetStatus.EXHAUSTED

        event = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker.budget.tenant_id,
            signal={
                "action": "exhausted",
                "total_budget": stats.total_budget,
                "consumed": stats.consumed,
                "percent_used": stats.percent_used,
            },
        )

        assert event.signal["action"] == "exhausted"
        assert event.signal["percent_used"] == 1.0

    def test_tenant_isolation_in_events(self):
        """Events respect tenant isolation."""
        budget_a = AttentionBudget(
            user_id="user-1",
            tenant_id="tenant-a",
            max_context_tokens=1000,
        )
        budget_b = AttentionBudget(
            user_id="user-1",
            tenant_id="tenant-b",
            max_context_tokens=2000,
        )

        tracker_a = AttentionTracker(budget_a)
        tracker_b = AttentionTracker(budget_b)

        tracker_a.record_context_usage(500)
        tracker_b.record_context_usage(1000)

        event_a = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker_a.budget.tenant_id,
            signal={"consumed": tracker_a.usage.context_tokens_used},
        )

        event_b = LearningEvent.create(
            event_type=EventType.ATTENTION,
            skill_id="os.attention_budget",
            tenant_id=tracker_b.budget.tenant_id,
            signal={"consumed": tracker_b.usage.context_tokens_used},
        )

        assert event_a.tenant_id == "tenant-a"
        assert event_b.tenant_id == "tenant-b"
        assert event_a.signal["consumed"] == 500
        assert event_b.signal["consumed"] == 1000

    def test_gdpr_compliance_payload(self, tracker):
        """Ensure audit payloads are GDPR-compliant."""
        tracker.record_context_usage(4000)
        stats = tracker.get_stats()

        # Get GDPR-safe payload
        payload = stats.to_payload()

        # No PII should be present
        assert "user_id" not in payload
        assert "password" not in payload

        # All fields are safe
        assert "total_budget" in payload
        assert "consumed" in payload
        assert "remaining" in payload
        assert "percent_used" in payload
        assert "status" in payload


class TestAttentionBudgetE2E:
    """End-to-end tests for attention budget operations."""

    def test_full_budget_lifecycle(self):
        """Test full lifecycle: allocation -> consumption -> refund -> reset."""
        budget = AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_context_tokens=10000,
            max_adrs_per_day=5,
            max_skills_per_day=10,
            max_decisions_per_session=50,
        )

        tracker = AttentionTracker(budget)

        # Phase 1: Initial state
        assert tracker.get_remaining_context() == 10000
        assert tracker.can_afford_context(5000)

        # Phase 2: Consume budget
        tracker.record_context_usage(5000)
        assert tracker.get_remaining_context() == 5000
        assert not tracker.can_afford_context(6000)

        # Phase 3: Check status
        stats = tracker.get_stats()
        assert stats.status == BudgetStatus.WARNING

        # Phase 4: Refund part of it
        tracker.refund_unused(2000)
        assert tracker.get_remaining_context() == 7000

        # Phase 5: Continue consuming to reach critical
        # After refund: consumed = 3000, remaining = 7000
        tracker.record_context_usage(4000)  # consumed = 7000, remaining = 3000
        assert tracker.get_remaining_context() == 3000

        stats = tracker.get_stats()
        assert stats.status == BudgetStatus.WARNING  # 70% usage = warning

        # Phase 6: Move to critical, then exhaust
        tracker.record_context_usage(1500)  # consumed = 8500, remaining = 1500 = 85%
        stats = tracker.get_stats()
        assert stats.status == BudgetStatus.CRITICAL

        # Phase 6b: Exhaust budget
        tracker.record_context_usage(1500)  # consumed = 10000, remaining = 0
        assert tracker.get_remaining_context() == 0
        assert tracker.should_truncate()

        # Phase 7: Reset session (note: context tokens don't reset per session)
        tracker.reset_session()
        assert tracker.get_remaining_decisions() == 50  # Decisions reset, but context tokens don't

    def test_adr_budget_enforcement(self):
        """Test ADR reading budget enforcement."""
        budget = AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_adrs_per_day=3,
        )

        tracker = AttentionTracker(budget)

        # Can read 3 ADRs per day
        assert tracker.record_adr_read()
        assert tracker.record_adr_read()
        assert tracker.record_adr_read()

        # Fourth fails
        assert not tracker.record_adr_read()

        # Check remaining
        assert tracker.get_remaining_adrs() == 0

    def test_skill_learning_budget_enforcement(self):
        """Test skill learning budget enforcement."""
        budget = AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_skills_per_day=2,
        )

        tracker = AttentionTracker(budget)

        # Can learn 2 skills per day
        assert tracker.record_skill_learned()
        assert tracker.record_skill_learned()

        # Third fails
        assert not tracker.record_skill_learned()

        assert tracker.get_remaining_skills() == 0

    def test_decision_budget_enforcement(self):
        """Test decision budget enforcement per session."""
        budget = AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_decisions_per_session=5,
        )

        tracker = AttentionTracker(budget)

        # Can make 5 decisions per session
        for i in range(5):
            assert tracker.record_decision()

        # Sixth fails
        assert not tracker.record_decision()

        # Reset session allows new decisions
        tracker.reset_session()
        assert tracker.record_decision()

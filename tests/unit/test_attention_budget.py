"""Tests for Attention Budget (ADR-0319)."""

import pytest
from datetime import datetime, timedelta
from core.learning.attention_budget import (
    AttentionBudget,
    AttentionUsage,
    AttentionTracker,
)


class TestAttentionBudget:
    """Test attention budget model."""

    def test_create_budget(self):
        """Create budget with defaults."""
        budget = AttentionBudget(user_id="user-1", tenant_id="_default")

        assert budget.max_context_tokens == 16000
        assert budget.max_adrs_per_day == 10
        assert budget.max_skills_per_day == 20
        assert budget.max_decisions_per_session == 100

    def test_create_budget_custom(self):
        """Create budget with custom limits."""
        budget = AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_context_tokens=8000,
            max_adrs_per_day=5,
            max_skills_per_day=10,
            max_decisions_per_session=50,
        )

        assert budget.max_context_tokens == 8000
        assert budget.max_adrs_per_day == 5


class TestAttentionUsage:
    """Test usage tracking."""

    def test_create_usage(self):
        """Create usage tracker."""
        usage = AttentionUsage(user_id="user-1", tenant_id="_default")

        assert usage.context_tokens_used == 0
        assert usage.adrs_read_today == 0
        assert usage.decisions_this_session == 0

    def test_is_stale(self):
        """Check staleness of daily counters."""
        usage = AttentionUsage(user_id="user-1", tenant_id="_default")
        assert not usage.is_stale()

        # Mark as old
        usage.last_reset = datetime.utcnow() - timedelta(days=2)
        assert usage.is_stale()

    def test_reset_daily_counters(self):
        """Reset daily counters."""
        usage = AttentionUsage(user_id="user-1", tenant_id="_default")
        usage.adrs_read_today = 5
        usage.skills_learned_today = 10

        usage.reset_daily_counters()

        assert usage.adrs_read_today == 0
        assert usage.skills_learned_today == 0


class TestAttentionTracker:
    """Test attention tracker."""

    @pytest.fixture
    def budget(self):
        """Create budget."""
        return AttentionBudget(
            user_id="user-1",
            tenant_id="_default",
            max_context_tokens=8000,
            max_adrs_per_day=3,
            max_skills_per_day=5,
            max_decisions_per_session=10,
        )

    @pytest.fixture
    def tracker(self, budget):
        """Create tracker."""
        return AttentionTracker(budget)

    def test_record_context_usage(self, tracker):
        """Track context token usage."""
        tracker.record_context_usage(1000)
        tracker.record_context_usage(2000)

        assert tracker.usage.context_tokens_used == 3000

    def test_can_afford_context(self, tracker):
        """Check context budget."""
        assert tracker.can_afford_context(4000)

        tracker.record_context_usage(5000)
        assert not tracker.can_afford_context(4000)

    def test_record_adr_read(self, tracker):
        """Record ADR reads."""
        assert tracker.record_adr_read() is True
        assert tracker.record_adr_read() is True
        assert tracker.record_adr_read() is True
        assert tracker.record_adr_read() is False  # Budget exceeded

    def test_can_afford_adr(self, tracker):
        """Check ADR budget."""
        assert tracker.can_afford_adr() is True

        tracker.usage.adrs_read_today = 3
        assert tracker.can_afford_adr() is False

    def test_record_skill_learned(self, tracker):
        """Record skill learning."""
        for i in range(5):
            assert tracker.record_skill_learned() is True

        assert tracker.record_skill_learned() is False  # Budget exceeded

    def test_can_afford_skill(self, tracker):
        """Check skill learning budget."""
        assert tracker.can_afford_skill() is True

        tracker.usage.skills_learned_today = 5
        assert tracker.can_afford_skill() is False

    def test_record_decision(self, tracker):
        """Record decisions."""
        for i in range(10):
            assert tracker.record_decision() is True

        assert tracker.record_decision() is False  # Budget exceeded

    def test_can_afford_decision(self, tracker):
        """Check decision budget."""
        assert tracker.can_afford_decision() is True

        tracker.usage.decisions_this_session = 10
        assert tracker.can_afford_decision() is False

    def test_get_remaining_context(self, tracker):
        """Get remaining context budget."""
        remaining = tracker.get_remaining_context()
        assert remaining == 8000

        tracker.record_context_usage(3000)
        remaining = tracker.get_remaining_context()
        assert remaining == 5000

    def test_get_remaining_adrs(self, tracker):
        """Get remaining ADR budget."""
        assert tracker.get_remaining_adrs() == 3

        tracker.record_adr_read()
        assert tracker.get_remaining_adrs() == 2

        tracker.record_adr_read()
        tracker.record_adr_read()
        assert tracker.get_remaining_adrs() == 0

    def test_get_remaining_skills(self, tracker):
        """Get remaining skill budget."""
        assert tracker.get_remaining_skills() == 5

        tracker.record_skill_learned()
        assert tracker.get_remaining_skills() == 4

    def test_get_remaining_decisions(self, tracker):
        """Get remaining decision budget."""
        assert tracker.get_remaining_decisions() == 10

        tracker.record_decision()
        assert tracker.get_remaining_decisions() == 9

    def test_reset_session(self, tracker):
        """Reset session counters."""
        tracker.usage.decisions_this_session = 5
        tracker.reset_session()

        assert tracker.usage.decisions_this_session == 0

    def test_daily_counter_auto_reset(self, tracker):
        """Daily counters auto-reset when stale."""
        tracker.usage.adrs_read_today = 3
        tracker.usage.last_reset = datetime.utcnow() - timedelta(days=2)

        assert tracker.can_afford_adr() is True
        assert tracker.usage.adrs_read_today == 0

    def test_budget_immutability(self, budget):
        """Budget is immutable."""
        with pytest.raises(AttributeError):
            budget.max_context_tokens = 10000

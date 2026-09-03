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

    def test_get_stats(self, tracker):
        """Get immutable budget statistics."""
        stats = tracker.get_stats()

        assert stats.total_budget == 8000
        assert stats.consumed == 0
        assert stats.remaining == 8000
        assert stats.percent_used == 0.0
        assert stats.status.value == "healthy"

        tracker.record_context_usage(4000)
        stats = tracker.get_stats()

        assert stats.consumed == 4000
        assert stats.remaining == 4000
        assert stats.percent_used == 0.5
        assert stats.status.value == "warning"

    def test_budget_status_transitions(self, tracker):
        """Budget status transitions through levels."""
        # Healthy
        stats = tracker.get_stats()
        assert stats.status.value == "healthy"

        # Warning (50%)
        tracker.record_context_usage(4000)
        stats = tracker.get_stats()
        assert stats.status.value == "warning"

        # Critical (80%)
        tracker.record_context_usage(2400)
        stats = tracker.get_stats()
        assert stats.status.value == "critical"

        # Exhausted (100%)
        tracker.record_context_usage(1600)
        stats = tracker.get_stats()
        assert stats.status.value == "exhausted"

    def test_should_warn(self, tracker):
        """Soft cap warning is issued once."""
        assert tracker.should_warn() is False

        # Push to critical
        tracker.record_context_usage(6400)
        assert tracker.should_warn() is True
        assert tracker._soft_cap_warned is True

        # Warning only once (even though still critical)
        assert tracker.should_warn() is False

        # Reset session clears warning flag, can warn again
        tracker.reset_session()
        # Still in critical state, so warning available again
        assert tracker.should_warn() is True
        assert tracker._soft_cap_warned is True

    def test_should_truncate(self, tracker):
        """Hard cap truncation when exhausted."""
        assert tracker.should_truncate() is False

        # Exhaust budget
        tracker.record_context_usage(8000)
        assert tracker.should_truncate() is True

    def test_truncate_to_remaining(self, tracker):
        """Adaptive truncation to remaining budget."""
        # Full budget available
        assert tracker.truncate_to_remaining(5000) == 5000

        tracker.record_context_usage(6000)
        # 2000 tokens remaining
        assert tracker.truncate_to_remaining(5000) == 2000

        # Over budget
        tracker.record_context_usage(2000)
        assert tracker.truncate_to_remaining(5000) == 0

    def test_allocate_confidence_weighted(self, tracker):
        """Allocate tokens with confidence weighting."""
        # High confidence (1.0)
        allocated = tracker.allocate_confidence_weighted(1000, 1.0)
        assert allocated == 1000

        # Medium confidence (0.5)
        allocated = tracker.allocate_confidence_weighted(1000, 0.5)
        assert allocated == 500

        # Low confidence (0.2) → min 10% = 100
        allocated = tracker.allocate_confidence_weighted(1000, 0.1)
        assert allocated == 100

        # Zero confidence → min 10% = 100
        allocated = tracker.allocate_confidence_weighted(1000, 0.0)
        assert allocated == 100

    def test_allocate_confidence_weighted_invalid(self, tracker):
        """Reject invalid confidence scores."""
        with pytest.raises(ValueError):
            tracker.allocate_confidence_weighted(1000, -0.1)

        with pytest.raises(ValueError):
            tracker.allocate_confidence_weighted(1000, 1.1)

    def test_allocate_confidence_weighted_respects_remaining(self, tracker):
        """Confidence allocation respects remaining budget."""
        tracker.record_context_usage(7500)
        # 500 tokens remaining

        allocated = tracker.allocate_confidence_weighted(1000, 1.0)
        assert allocated == 500  # Clamped to remaining

    def test_refund_unused(self, tracker):
        """Refund unused tokens at period boundary."""
        tracker.record_context_usage(5000)
        assert tracker.usage.context_tokens_used == 5000

        tracker.refund_unused(2000)
        assert tracker.usage.context_tokens_used == 3000

        # Can't refund more than used
        tracker.refund_unused(5000)
        assert tracker.usage.context_tokens_used == 0

    def test_budget_stats_to_payload(self, tracker):
        """BudgetStats converts to GDPR-safe payload."""
        tracker.record_context_usage(4000)
        stats = tracker.get_stats()

        payload = stats.to_payload()

        assert payload["total_budget"] == 8000
        assert payload["consumed"] == 4000
        assert payload["remaining"] == 4000
        assert payload["percent_used"] == "50.0%"
        assert payload["status"] == "warning"

    def test_multi_user_isolation(self):
        """Multiple users have independent budgets."""
        budget1 = AttentionBudget(user_id="user-1", tenant_id="_default")
        tracker1 = AttentionTracker(budget1)

        budget2 = AttentionBudget(user_id="user-2", tenant_id="_default")
        tracker2 = AttentionTracker(budget2)

        tracker1.record_context_usage(1000)
        tracker2.record_context_usage(2000)

        assert tracker1.usage.context_tokens_used == 1000
        assert tracker2.usage.context_tokens_used == 2000

    def test_multi_tenant_isolation(self):
        """Multiple tenants have independent budgets."""
        budget1 = AttentionBudget(user_id="user-1", tenant_id="tenant-a")
        tracker1 = AttentionTracker(budget1)

        budget2 = AttentionBudget(user_id="user-1", tenant_id="tenant-b")
        tracker2 = AttentionTracker(budget2)

        tracker1.record_context_usage(1000)
        tracker2.record_context_usage(2000)

        assert tracker1.usage.tenant_id == "tenant-a"
        assert tracker2.usage.tenant_id == "tenant-b"
        assert tracker1.usage.context_tokens_used == 1000
        assert tracker2.usage.context_tokens_used == 2000

"""Attention Budget — finite attention constraint (ADR-0319)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional


@dataclass(frozen=True)
class AttentionBudget:
    """User's daily attention budget."""

    user_id: str
    tenant_id: str
    max_context_tokens: int = 16000
    max_adrs_per_day: int = 10
    max_skills_per_day: int = 20
    max_decisions_per_session: int = 100


@dataclass
class AttentionUsage:
    """Track attention usage for a user."""

    user_id: str
    tenant_id: str
    context_tokens_used: int = 0
    adrs_read_today: int = 0
    skills_learned_today: int = 0
    decisions_this_session: int = 0
    last_reset: datetime = field(default_factory=datetime.utcnow)

    def is_stale(self) -> bool:
        """Check if daily counters are stale (reset needed)."""
        cutoff = datetime.utcnow() - timedelta(days=1)
        return self.last_reset < cutoff

    def reset_daily_counters(self) -> None:
        """Reset counters that renew daily."""
        self.adrs_read_today = 0
        self.skills_learned_today = 0
        self.last_reset = datetime.utcnow()


class AttentionTracker:
    """Track and enforce attention budget limits."""

    def __init__(self, budget: AttentionBudget):
        """Initialize tracker.

        Args:
            budget: User's attention budget
        """
        self.budget = budget
        self.usage = AttentionUsage(
            user_id=budget.user_id,
            tenant_id=budget.tenant_id,
        )

    def record_context_usage(self, tokens: int) -> None:
        """Record context tokens used.

        Args:
            tokens: Number of tokens consumed
        """
        self.usage.context_tokens_used += tokens

    def record_adr_read(self) -> bool:
        """Record reading an ADR.

        Returns:
            True if budget allows, False if budget exceeded
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        if self.usage.adrs_read_today >= self.budget.max_adrs_per_day:
            return False

        self.usage.adrs_read_today += 1
        return True

    def record_skill_learned(self) -> bool:
        """Record learning a new skill.

        Returns:
            True if budget allows, False if budget exceeded
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        if self.usage.skills_learned_today >= self.budget.max_skills_per_day:
            return False

        self.usage.skills_learned_today += 1
        return True

    def record_decision(self) -> bool:
        """Record a decision made.

        Returns:
            True if budget allows, False if budget exceeded
        """
        if self.usage.decisions_this_session >= self.budget.max_decisions_per_session:
            return False

        self.usage.decisions_this_session += 1
        return True

    def can_afford_context(self, tokens: int) -> bool:
        """Check if user has context budget for more tokens.

        Args:
            tokens: Number of tokens needed

        Returns:
            True if budget allows, False otherwise
        """
        return (
            self.usage.context_tokens_used + tokens
            <= self.budget.max_context_tokens
        )

    def can_afford_adr(self) -> bool:
        """Check if user has ADR budget remaining.

        Returns:
            True if budget allows, False otherwise
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        return self.usage.adrs_read_today < self.budget.max_adrs_per_day

    def can_afford_skill(self) -> bool:
        """Check if user has skill learning budget remaining.

        Returns:
            True if budget allows, False otherwise
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        return self.usage.skills_learned_today < self.budget.max_skills_per_day

    def can_afford_decision(self) -> bool:
        """Check if user has decision budget remaining this session.

        Returns:
            True if budget allows, False otherwise
        """
        return self.usage.decisions_this_session < self.budget.max_decisions_per_session

    def get_remaining_context(self) -> int:
        """Get remaining context token budget.

        Returns:
            Number of tokens remaining (0 if exceeded)
        """
        remaining = self.budget.max_context_tokens - self.usage.context_tokens_used
        return max(0, remaining)

    def get_remaining_adrs(self) -> int:
        """Get remaining ADR reads for today.

        Returns:
            Number of ADRs remaining (0 if exceeded)
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        remaining = self.budget.max_adrs_per_day - self.usage.adrs_read_today
        return max(0, remaining)

    def get_remaining_skills(self) -> int:
        """Get remaining skills to learn for today.

        Returns:
            Number of skills remaining (0 if exceeded)
        """
        if self.usage.is_stale():
            self.usage.reset_daily_counters()

        remaining = self.budget.max_skills_per_day - self.usage.skills_learned_today
        return max(0, remaining)

    def get_remaining_decisions(self) -> int:
        """Get remaining decisions for this session.

        Returns:
            Number of decisions remaining (0 if exceeded)
        """
        remaining = self.budget.max_decisions_per_session - self.usage.decisions_this_session
        return max(0, remaining)

    def reset_session(self) -> None:
        """Reset session-scoped counters (decisions)."""
        self.usage.decisions_this_session = 0

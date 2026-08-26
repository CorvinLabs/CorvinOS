"""Attention Budget — finite attention constraint (ADR-0319).

Attention budget model for managing finite cognitive resources:
- Token budget (per-session, per-phase)
- Attention allocation (confidence-weighted)
- Budget monitoring + alerts
- Adaptive truncation when threatened
- Per-user + per-tenant budgets
- Soft cap (80% warn) + hard cap (100% deny)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import Optional


class BudgetStatus(str, Enum):
    """Budget health status."""

    HEALTHY = "healthy"  # < 50%
    WARNING = "warning"  # 50-80%
    CRITICAL = "critical"  # 80-99%
    EXHAUSTED = "exhausted"  # 100%+


@dataclass(frozen=True)
class BudgetStats:
    """Immutable budget statistics snapshot."""

    total_budget: int
    consumed: int
    remaining: int
    percent_used: float
    status: BudgetStatus
    timestamp_utc: datetime

    def to_payload(self) -> dict:
        """Convert to learning event payload (GDPR Art. 5 safe)."""
        return {
            "total_budget": self.total_budget,
            "consumed": self.consumed,
            "remaining": self.remaining,
            "percent_used": f"{self.percent_used:.1%}",
            "status": self.status.value,
        }


@dataclass(frozen=True)
class AttentionBudget:
    """User's attention budget (immutable configuration)."""

    user_id: str
    tenant_id: str
    max_context_tokens: int = 16000
    max_adrs_per_day: int = 10
    max_skills_per_day: int = 20
    max_decisions_per_session: int = 100


@dataclass
class AttentionUsage:
    """Track attention usage for a user (mutable state)."""

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
    """Track and enforce attention budget limits.

    Implements soft cap (warn at 80%) and hard cap (deny at 100%) enforcement.
    """

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
        self._soft_cap_warned = False  # Track if warning has been issued

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
        self._soft_cap_warned = False

    def get_stats(self) -> BudgetStats:
        """Get immutable snapshot of budget statistics.

        Returns:
            BudgetStats with current consumption, remaining, status
        """
        remaining = self.get_remaining_context()
        consumed = self.usage.context_tokens_used
        percent_used = (
            consumed / self.budget.max_context_tokens
            if self.budget.max_context_tokens > 0
            else 0.0
        )

        # Determine status
        if percent_used >= 1.0:
            status = BudgetStatus.EXHAUSTED
        elif percent_used >= 0.80:
            status = BudgetStatus.CRITICAL
        elif percent_used >= 0.50:
            status = BudgetStatus.WARNING
        else:
            status = BudgetStatus.HEALTHY

        return BudgetStats(
            total_budget=self.budget.max_context_tokens,
            consumed=consumed,
            remaining=remaining,
            percent_used=percent_used,
            status=status,
            timestamp_utc=datetime.utcnow(),
        )

    def should_warn(self) -> bool:
        """Check if soft cap warning should be issued (only once per session).

        Returns:
            True if warning should be displayed and not yet shown
        """
        stats = self.get_stats()
        if stats.status == BudgetStatus.CRITICAL and not self._soft_cap_warned:
            self._soft_cap_warned = True
            return True
        return False

    def should_truncate(self) -> bool:
        """Check if adaptive truncation is needed (hard cap).

        Returns:
            True if budget exhausted and truncation required
        """
        stats = self.get_stats()
        return stats.status == BudgetStatus.EXHAUSTED

    def truncate_to_remaining(self, content_length: int) -> int:
        """Adaptively truncate content to fit remaining budget.

        Args:
            content_length: Length of content to potentially truncate (in tokens)

        Returns:
            Actual length allowed (min of content_length and remaining budget)
        """
        remaining = self.get_remaining_context()
        return min(content_length, max(0, remaining))

    def allocate_confidence_weighted(
        self,
        base_tokens: int,
        confidence: float,
    ) -> int:
        """Allocate tokens with confidence-weighting.

        Higher confidence tasks get proportionally more budget.
        Confidence must be 0.0-1.0 (typically from ADR-0315).

        Args:
            base_tokens: Base tokens requested
            confidence: Confidence score (0.0-1.0)

        Returns:
            Allocated tokens (base_tokens * confidence, clamped to remaining)

        Raises:
            ValueError: If confidence outside [0, 1]
        """
        if not (0.0 <= confidence <= 1.0):
            raise ValueError(f"Invalid confidence: {confidence}, must be in [0.0, 1.0]")

        # Scale allocation by confidence
        allocated = int(base_tokens * max(0.1, confidence))  # Min 10% even at low confidence
        allocated = self.truncate_to_remaining(allocated)
        return allocated

    def refund_unused(self, tokens: int) -> None:
        """Refund unused tokens at period boundary.

        Args:
            tokens: Tokens to refund (subtract from consumed)
        """
        self.usage.context_tokens_used = max(0, self.usage.context_tokens_used - tokens)

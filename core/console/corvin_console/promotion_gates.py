"""Automatic promotion gates for feature tiers (ADR-0286, ADR-0288).

Each tier has entry/exit criteria. Daemon checks these hourly.
Promotion is automatic when criteria are met; no maintainer approval needed.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import NamedTuple


class PromotionEvent(NamedTuple):
    """Event when a flag becomes eligible for promotion."""

    flag_id: str
    current_tier: str
    target_tier: str
    reason: str
    metrics_snapshot: dict


@dataclass
class PromotionGates:
    """Promotion criteria for each tier transition."""

    @staticmethod
    def check_alpha_to_beta(metrics: dict) -> tuple[bool, str]:
        """Check if flag can promote ALPHA → BETA.

        Criteria:
        - 7+ days in alpha
        - error_rate_24h < 5% (0.05)
        - invocation_count_24h > 0 (has real usage)

        Returns: (can_promote, reason)
        """
        days_in_tier = metrics.get("days_in_tier", 0)
        error_rate = metrics.get("error_rate_24h", 0.0)
        invocations = metrics.get("invocation_count_24h", 0)

        if days_in_tier < 7:
            return False, f"Only {days_in_tier} days in alpha (need 7)"
        if error_rate > 0.05:
            return False, f"Error rate {error_rate:.2%} exceeds 5% threshold"
        if invocations == 0:
            return False, "No invocations yet"

        return True, f"alpha→beta ready: {days_in_tier}d, {error_rate:.2%} error"

    @staticmethod
    def check_beta_to_stable(metrics: dict) -> tuple[bool, str]:
        """Check if flag can promote BETA → STABLE.

        Criteria:
        - 30+ consecutive days at error_rate < 1% (0.01)
        - adoption_rate > 5% (0.05) of users with beta enabled
        - invocation_count_24h > 100/day (real production use)

        Returns: (can_promote, reason)
        """
        days_in_tier = metrics.get("days_in_tier", 0)
        error_rate = metrics.get("error_rate_24h", 0.0)
        adoption = metrics.get("adoption_rate", 0.0)
        invocations = metrics.get("invocation_count_24h", 0)

        if days_in_tier < 30:
            return False, f"Only {days_in_tier} days in beta (need 30)"
        if error_rate > 0.01:
            return False, f"Error rate {error_rate:.2%} exceeds 1% threshold"
        if invocations < 100:
            return False, f"Only {invocations} invocations/day (need 100+)"
        if adoption < 0.05:
            return False, f"Adoption {adoption:.1%} below 5% threshold"

        return True, f"beta→stable ready: {days_in_tier}d, {error_rate:.2%} error, {adoption:.1%} adoption"

    @staticmethod
    def check_stable_to_production(metrics: dict) -> tuple[bool, str]:
        """Check if flag can promote STABLE → PRODUCTION.

        Criteria:
        - 60+ consecutive days at error_rate < 0.1% (0.001)
        - adoption_rate > 25% (0.25)
        - invocation_count_24h > 500/day (widespread use)
        - zero critical+high security issues

        Returns: (can_promote, reason)
        """
        days_in_tier = metrics.get("days_in_tier", 0)
        error_rate = metrics.get("error_rate_24h", 0.0)
        adoption = metrics.get("adoption_rate", 0.0)
        invocations = metrics.get("invocation_count_24h", 0)
        has_security_issues = metrics.get("has_critical_security_issues", False)

        if days_in_tier < 60:
            return False, f"Only {days_in_tier} days in stable (need 60)"
        if error_rate > 0.001:
            return False, f"Error rate {error_rate:.3%} exceeds 0.1% threshold"
        if invocations < 500:
            return False, f"Only {invocations} invocations/day (need 500+)"
        if adoption < 0.25:
            return False, f"Adoption {adoption:.1%} below 25% threshold"
        if has_security_issues:
            return False, "Has critical/high security issues"

        return True, f"stable→production ready: {days_in_tier}d, {error_rate:.3%} error, {adoption:.1%} adoption"


@dataclass
class DemotionGates:
    """Demotion criteria (auto-demotion on error spike)."""

    @staticmethod
    def check_beta_demotion(error_rate: float, consecutive_hours: int = 2) -> tuple[bool, str]:
        """Check if BETA flag should demote → ALPHA.

        Criteria:
        - error_rate_24h > 5% for 2+ consecutive hours

        Args:
            error_rate: current 24h error rate
            consecutive_hours: how many hours the threshold was exceeded

        Returns: (should_demote, reason)
        """
        if error_rate > 0.05 and consecutive_hours >= 2:
            return True, f"Error rate {error_rate:.2%} exceeded 5% for {consecutive_hours}h"
        return False, ""

    @staticmethod
    def check_stable_demotion(error_rate: float, consecutive_hours: int = 2) -> tuple[bool, str]:
        """Check if STABLE flag should demote → BETA.

        Criteria:
        - error_rate_24h > 1% for 2+ consecutive hours

        Returns: (should_demote, reason)
        """
        if error_rate > 0.01 and consecutive_hours >= 2:
            return True, f"Error rate {error_rate:.2%} exceeded 1% for {consecutive_hours}h"
        return False, ""

    @staticmethod
    def check_production_demotion(error_rate: float) -> tuple[bool, str]:
        """Check if PRODUCTION flag should demote → STABLE.

        Criteria:
        - error_rate_24h > 1% for ANY hour (fail-safe, immediate)

        Note: Production demotes immediately without waiting for consecutive hours.
        This is a fail-safe to prevent broken features from being in new user installs.

        Returns: (should_demote, reason)
        """
        if error_rate > 0.01:
            return True, f"Error rate {error_rate:.2%} exceeded 1% (fail-safe demotion)"
        return False, ""

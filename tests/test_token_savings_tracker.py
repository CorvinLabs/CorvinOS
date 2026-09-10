"""Token Savings Tracker — unit tests (Phase 4, ADR-0668)."""

from __future__ import annotations

import pytest

from core.learning.token_savings_tracker import TokenSavingsTracker, SavingsEvent
from core.skills.license_binding import UserLicense


@pytest.fixture
def tracker():
    return TokenSavingsTracker()


@pytest.fixture
def user():
    return UserLicense(user_id="user1", license_tier="paid")


class TestTokenSavingsTracking:
    """Test token savings tracking."""

    def test_track_simple_request_with_haiku(self, tracker, user):
        """Track simple request routed to haiku engine."""
        event = tracker.track_routing_decision(
            request_id="req1",
            request_complexity="simple",
            skill_id="os.delegation_router",
            skill_version="1.0.0",
            user=user,
            engine_chosen="haiku",
            confidence=0.95,
        )

        assert event.user_id == "user1"
        assert event.engine_chosen == "haiku"
        assert event.savings_tokens > 0

    def test_complex_request_haiku_vs_opus(self, tracker, user):
        """Complex requests save more with haiku than opus."""
        event_haiku = tracker.track_routing_decision(
            request_id="req_h",
            request_complexity="complex",
            skill_id="os.delegation_router",
            skill_version="1.0.0",
            user=user,
            engine_chosen="haiku",
        )

        event_opus = tracker.track_routing_decision(
            request_id="req_o",
            request_complexity="complex",
            skill_id="os.delegation_router",
            skill_version="1.0.0",
            user=user,
            engine_chosen="opus",
        )

        assert event_haiku.savings_tokens > event_opus.savings_tokens

    def test_savings_never_negative(self, tracker, user):
        """Savings are never negative."""
        event = tracker.track_routing_decision(
            request_id="req1",
            request_complexity="simple",
            skill_id="os.delegation_router",
            skill_version="1.0.0",
            user=user,
            engine_chosen="opus",
        )

        assert event.savings_tokens >= 0


class TestCostEstimation:
    """Test cost estimation models."""

    def test_baseline_cost_varies_by_complexity(self, tracker):
        """Baseline cost increases with complexity."""
        simple = tracker._estimate_cost_legacy("simple")
        moderate = tracker._estimate_cost_legacy("moderate")
        complex = tracker._estimate_cost_legacy("complex")

        assert simple < moderate < complex

    def test_optimized_cost_less_than_baseline(self, tracker):
        """Optimized cost is always less than baseline."""
        for complexity in ["simple", "moderate", "complex"]:
            baseline = tracker._estimate_cost_legacy(complexity)
            optimized = tracker._estimate_cost_optimized(complexity, "sonnet")
            assert optimized < baseline

    def test_haiku_cheaper_than_sonnet_cheaper_than_opus(self, tracker):
        """Engine cost ordering: haiku < sonnet < opus."""
        haiku = tracker._estimate_cost_optimized("moderate", "haiku")
        sonnet = tracker._estimate_cost_optimized("moderate", "sonnet")
        opus = tracker._estimate_cost_optimized("moderate", "opus")

        assert haiku < sonnet < opus


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

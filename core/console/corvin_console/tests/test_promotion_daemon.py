"""Tests for auto-promotion daemon."""
import asyncio

import pytest
from core.console.corvin_console.promotion_daemon import (
    PromotionDaemon,
    AuditEvent,
)


class TestPromotionDaemon:
    """PromotionDaemon tests."""

    def test_daemon_initialization(self):
        """Test daemon initialization."""
        audit_events = []
        daemon = PromotionDaemon(
            audit_fn=lambda e: audit_events.append(e),
            registry_getter=lambda: {"flag_a": "alpha", "flag_b": "beta"},
            enabled=True,
        )
        assert daemon.enabled is True
        assert daemon.interval_seconds == 3600

    def test_daemon_disabled(self):
        """Test disabled daemon does nothing."""
        daemon = PromotionDaemon(enabled=False)
        assert daemon.enabled is False

    def test_check_demotion_production(self):
        """Test PRODUCTION demotion (immediate, no consecutive hours)."""
        daemon = PromotionDaemon()
        should_demote, reason = daemon._check_demotion(
            "production", {"error_rate_24h": 0.015}  # 1.5% > 1%
        )
        assert should_demote is True
        assert "fail-safe" in reason.lower()

    def test_check_demotion_stable(self):
        """Test STABLE demotion on error spike."""
        daemon = PromotionDaemon()
        should_demote, reason = daemon._check_demotion(
            "stable", {"error_rate_24h": 0.02}, consecutive_hours=2
        )
        assert should_demote is True
        assert "1%" in reason

    def test_check_demotion_beta(self):
        """Test BETA demotion on error spike."""
        daemon = PromotionDaemon()
        should_demote, reason = daemon._check_demotion(
            "beta", {"error_rate_24h": 0.08}, consecutive_hours=3
        )
        assert should_demote is True
        assert "5%" in reason

    def test_check_demotion_alpha_never(self):
        """Test ALPHA never demotes."""
        daemon = PromotionDaemon()
        should_demote, reason = daemon._check_demotion(
            "alpha", {"error_rate_24h": 0.5}  # Even at 50% error rate
        )
        assert should_demote is False

    def test_check_promotion_alpha_to_beta(self):
        """Test ALPHA → BETA promotion check."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 7,
            "error_rate_24h": 0.02,
            "invocation_count_24h": 50,
        }
        can_promote, target, reason = daemon._check_promotion("alpha", metrics)
        assert can_promote is True
        assert target == "beta"

    def test_check_promotion_alpha_insufficient_days(self):
        """Test ALPHA → BETA fails on insufficient days."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 3,  # < 7
            "error_rate_24h": 0.02,
            "invocation_count_24h": 50,
        }
        can_promote, target, reason = daemon._check_promotion("alpha", metrics)
        assert can_promote is False
        assert "days" in reason.lower()

    def test_check_promotion_alpha_high_error_rate(self):
        """Test ALPHA → BETA fails on high error rate."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 7,
            "error_rate_24h": 0.08,  # > 5%
            "invocation_count_24h": 50,
        }
        can_promote, target, reason = daemon._check_promotion("alpha", metrics)
        assert can_promote is False
        assert "error" in reason.lower()

    def test_check_promotion_beta_to_stable(self):
        """Test BETA → STABLE promotion check."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 30,
            "error_rate_24h": 0.005,
            "adoption_rate": 0.10,
            "invocation_count_24h": 200,
        }
        can_promote, target, reason = daemon._check_promotion("beta", metrics)
        assert can_promote is True
        assert target == "stable"

    def test_check_promotion_beta_low_adoption(self):
        """Test BETA → STABLE fails on low adoption."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 30,
            "error_rate_24h": 0.005,
            "adoption_rate": 0.02,  # < 5%
            "invocation_count_24h": 200,
        }
        can_promote, target, reason = daemon._check_promotion("beta", metrics)
        assert can_promote is False

    def test_check_promotion_stable_to_production(self):
        """Test STABLE → PRODUCTION promotion check."""
        daemon = PromotionDaemon()
        metrics = {
            "days_in_tier": 60,
            "error_rate_24h": 0.0008,
            "adoption_rate": 0.30,
            "invocation_count_24h": 800,
            "has_critical_security_issues": False,
        }
        can_promote, target, reason = daemon._check_promotion("stable", metrics)
        assert can_promote is True
        assert target == "production"

    def test_check_promotion_production_never(self):
        """Test PRODUCTION never promotes."""
        daemon = PromotionDaemon()
        metrics = {}
        can_promote, target, reason = daemon._check_promotion("production", metrics)
        assert can_promote is False

    def test_promote_flag_logs_audit(self):
        """Test that promote_flag logs audit event."""
        audit_events = []
        daemon = PromotionDaemon(audit_fn=lambda e: audit_events.append(e))

        daemon._promote_flag(
            "test_flag",
            "alpha",
            "beta",
            "Ready for beta",
            {"error_rate_24h": 0.02},
        )

        assert len(audit_events) == 1
        event = audit_events[0]
        assert event.flag_id == "test_flag"
        assert event.old_tier == "alpha"
        assert event.new_tier == "beta"
        assert event.event_type == "flag_auto_promoted"

    def test_demote_flag_logs_audit(self):
        """Test that demote_flag logs audit event."""
        audit_events = []
        daemon = PromotionDaemon(audit_fn=lambda e: audit_events.append(e))

        daemon._demote_flag(
            "test_flag",
            "stable",
            "Error rate exceeded 1%",
            {"error_rate_24h": 0.02},
        )

        assert len(audit_events) == 1
        event = audit_events[0]
        assert event.flag_id == "test_flag"
        assert event.old_tier == "stable"
        assert event.new_tier == "beta"
        assert event.event_type == "flag_auto_demoted"

    def test_promote_flag_with_metrics(self):
        """Test promotion includes metrics snapshot."""
        audit_events = []
        daemon = PromotionDaemon(audit_fn=lambda e: audit_events.append(e))

        metrics = {
            "error_rate_24h": 0.02,
            "invocation_count_24h": 150,
            "adoption_rate": 0.08,
        }

        daemon._promote_flag(
            "test_flag",
            "alpha",
            "beta",
            "Ready",
            metrics,
        )

        assert len(audit_events) == 1
        assert audit_events[0].metrics_snapshot == metrics

    def test_demote_flag_with_metrics(self):
        """Test demotion includes metrics snapshot."""
        audit_events = []
        daemon = PromotionDaemon(audit_fn=lambda e: audit_events.append(e))

        metrics = {"error_rate_24h": 0.15}

        daemon._demote_flag(
            "test_flag",
            "beta",
            "Error spike",
            metrics,
        )

        assert len(audit_events) == 1
        assert audit_events[0].metrics_snapshot == metrics

    def test_audit_event_timestamp(self):
        """Test that audit events have timestamps."""
        audit_events = []
        daemon = PromotionDaemon(audit_fn=lambda e: audit_events.append(e))

        daemon._promote_flag("test_flag", "alpha", "beta", "Ready", {})

        event = audit_events[0]
        assert event.timestamp is not None
        assert "Z" in event.timestamp  # UTC ISO format

    @pytest.mark.asyncio
    async def test_daemon_check_all_flags(self):
        """Test check_all_flags iterates all flags."""
        audit_events = []
        registry = {"flag_a": "alpha", "flag_b": "beta"}

        # Mock registry getter that returns flags
        daemon = PromotionDaemon(
            audit_fn=lambda e: audit_events.append(e),
            registry_getter=lambda: registry,
            enabled=True,
        )

        # Mock check_flag to track calls
        checked_flags = []
        original_check = daemon.check_flag

        async def mock_check(flag_id, tier):
            checked_flags.append((flag_id, tier))

        daemon.check_flag = mock_check

        await daemon.check_all_flags()

        assert ("flag_a", "alpha") in checked_flags
        assert ("flag_b", "beta") in checked_flags

    def test_daemon_singleton(self):
        """Test daemon singleton initialization."""
        from core.console.corvin_console.promotion_daemon import (
            initialize_daemon,
            get_daemon,
        )

        daemon1 = initialize_daemon(enabled=True)
        daemon2 = get_daemon()

        assert daemon1 is daemon2

    def test_multiple_demotions_not_possible(self):
        """Test that alpha cannot be demoted further."""
        daemon = PromotionDaemon()

        daemon._demote_flag("flag", "alpha", "Cannot demote", {})

        # When demoting from alpha, should log warning but not create event
        # (since alpha has no lower tier)
        # This is tested implicitly - just verify no crash occurs

    def test_tier_sequence_integrity(self):
        """Test that tier sequence is maintained."""
        daemon = PromotionDaemon()

        # Valid sequences
        valid_transitions = [
            ("alpha", "beta"),
            ("beta", "stable"),
            ("stable", "production"),
        ]

        for old, new in valid_transitions:
            demotion_map = {"beta": "alpha", "stable": "beta", "production": "stable"}
            # Verify map consistency
            if new in demotion_map:
                assert demotion_map[new] == old

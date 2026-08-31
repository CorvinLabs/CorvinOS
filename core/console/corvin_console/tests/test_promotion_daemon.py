"""Tests for auto-promotion daemon."""
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

    def test_check_demotion_production(self):
        """Test PRODUCTION demotion (immediate, no consecutive hours)."""
        daemon = PromotionDaemon()
        should_demote, reason = daemon._check_demotion(
            "production", {"error_rate_24h": 0.015}  # 1.5% > 1%
        )
        assert should_demote is True
        assert "fail-safe" in reason.lower()

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

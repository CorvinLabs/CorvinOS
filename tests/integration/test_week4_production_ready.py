"""Week 4 production readiness smoke tests."""

import pytest
import tempfile
from core.vibe_engineering.notification_router import NotificationRouter, NotificationPreferences
from core.vibe_engineering.measurement import MeasurementCollector, MeasurementSample
from core.vibe_engineering.feature_flags_tier1 import FeatureFlagResolver


@pytest.mark.asyncio
async def test_notification_router_discord_config():
    """Test NotificationRouter Discord configuration."""
    router = NotificationRouter()
    prefs = NotificationPreferences(
        user_id="test-user",
        discord_webhook="https://discord.com/api/webhooks/1234/5678"
    )
    await router.set_preferences("test-user", prefs)

    retrieved = router.get_preferences("test-user")
    assert retrieved.discord_webhook == "https://discord.com/api/webhooks/1234/5678"
    assert retrieved.enabled is True


def test_measurement_collector_latency_recording():
    """Test measurement recording."""
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = MeasurementCollector(tmpdir)
        collector.record_session_renewal_latency("task-1", 5000.0)
        collector.record_notification_latency("task-1", "gather", 2000.0)

        metrics = collector.get_metrics()
        assert "session_renewal_latency" in metrics
        assert "notification_latency" in metrics
        assert metrics["session_renewal_latency"]["mean"] == 5000.0
        assert metrics["notification_latency"]["mean"] == 2000.0


def test_feature_flags_tier1_enabled():
    """Test Tier-1 feature flags are enabled for production."""
    assert FeatureFlagResolver.is_enabled("task_orchestrator_multiphase") is True
    assert FeatureFlagResolver.is_enabled("auto_session_renewal") is True
    assert FeatureFlagResolver.is_enabled("notification_system_v1") is False  # Opt-in


def test_feature_flags_env_override():
    """Test env var can override feature flag."""
    import os
    os.environ["CORVIN_FEATURE_NOTIFICATION_SYSTEM_V1"] = "1"
    try:
        assert FeatureFlagResolver.is_enabled("notification_system_v1") is True
    finally:
        del os.environ["CORVIN_FEATURE_NOTIFICATION_SYSTEM_V1"]


def test_production_readiness_checklist():
    """Validate production readiness criteria (ADR-0222)."""
    # All Tier-1 flags present
    flags = FeatureFlagResolver.get_all_flags()
    assert "task_orchestrator_multiphase" in flags
    assert "auto_session_renewal" in flags
    assert "notification_system_v1" in flags

    # Measurement collector can persist
    with tempfile.TemporaryDirectory() as tmpdir:
        collector = MeasurementCollector(tmpdir)
        collector.record_error("task-1", "phase-1", "RuntimeError")
        metrics = collector.get_metrics()
        assert "error_rate" in metrics
        assert metrics["error_rate"]["count"] > 0

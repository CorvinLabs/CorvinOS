"""Tests for feature stability metrics collection."""
import pytest
from datetime import datetime
from core.telemetry.stability_metrics import (
    FlagMetrics,
    get_flag_metrics,
    mark_invocation,
    mark_error,
    compute_digest,
    reset_metrics,
    _is_pii_safe_error,
    FeatureStabilityEvent,
)


class TestFlagMetrics:
    """FlagMetrics class tests."""

    def test_mark_invocation(self):
        """Test marking invocations."""
        metrics = FlagMetrics(flag_id="test")
        metrics.mark_invocation()
        metrics.mark_invocation()
        assert sum(metrics.hourly_invocations) == 2

    def test_mark_error(self):
        """Test marking errors."""
        metrics = FlagMetrics(flag_id="test")
        metrics.mark_error(ValueError("test error"))
        assert sum(metrics.hourly_errors) == 1
        assert metrics.last_error_time is not None

    def test_get_24h_stats_zero_invocations(self):
        """Test stats with zero invocations."""
        metrics = FlagMetrics(flag_id="test")
        stats = metrics.get_24h_stats()
        assert stats["invocation_count_24h"] == 0
        assert stats["error_count_24h"] == 0
        assert stats["error_rate_24h"] == 0.0

    def test_get_24h_stats_with_errors(self):
        """Test stats with some errors."""
        metrics = FlagMetrics(flag_id="test")
        for _ in range(100):
            metrics.mark_invocation()
        for _ in range(2):
            metrics.mark_error(ValueError("error"))

        stats = metrics.get_24h_stats()
        assert stats["invocation_count_24h"] == 100
        assert stats["error_count_24h"] == 2
        assert stats["error_rate_24h"] == 0.02


class TestGlobalMetrics:
    """Global metrics registry tests."""

    def setup_method(self):
        """Reset metrics before each test."""
        reset_metrics()

    def test_get_flag_metrics_creates_entry(self):
        """Test that getting metrics creates an entry."""
        metrics = get_flag_metrics("new_flag")
        assert metrics.flag_id == "new_flag"

    def test_mark_invocation_global(self):
        """Test marking invocations via global API."""
        mark_invocation("flag_a")
        mark_invocation("flag_a")
        mark_invocation("flag_b")

        metrics_a = get_flag_metrics("flag_a")
        metrics_b = get_flag_metrics("flag_b")
        assert sum(metrics_a.hourly_invocations) == 2
        assert sum(metrics_b.hourly_invocations) == 1

    def test_mark_error_global(self):
        """Test marking errors via global API."""
        mark_error("flag_a", ValueError("safe error"))
        metrics = get_flag_metrics("flag_a")
        assert sum(metrics.hourly_errors) == 1

    def test_pii_safe_error_blocks_unsafe(self):
        """Test that unsafe errors are blocked."""
        # Should NOT record the error
        mark_error("flag_a", ValueError("api_key=secret123"))
        metrics = get_flag_metrics("flag_a")
        # Error was dropped, not recorded
        assert sum(metrics.hourly_errors) == 0

    def test_pii_safe_error_blocks_email(self):
        """Test that emails are blocked."""
        mark_error("flag_a", ValueError("user@example.com failed"))
        metrics = get_flag_metrics("flag_a")
        assert sum(metrics.hourly_errors) == 0


class TestPIISafety:
    """PII safety validation tests."""

    def test_is_pii_safe_error_safe_message(self):
        """Test safe error messages."""
        assert _is_pii_safe_error(ValueError("Something went wrong")) is True
        assert _is_pii_safe_error(ValueError("Timeout after 30s")) is True

    def test_is_pii_safe_error_email(self):
        """Test email detection."""
        assert _is_pii_safe_error(ValueError("user@example.com")) is False

    def test_is_pii_safe_error_api_key(self):
        """Test API key detection."""
        assert _is_pii_safe_error(ValueError("api_key=xyz")) is False

    def test_is_pii_safe_error_password(self):
        """Test password detection."""
        assert _is_pii_safe_error(ValueError("password=secret")) is False

    def test_is_pii_safe_error_token(self):
        """Test token detection."""
        assert _is_pii_safe_error(ValueError("token: abc123")) is False

    def test_is_pii_safe_error_phone(self):
        """Test phone number detection."""
        assert _is_pii_safe_error(ValueError("+1 555-1234")) is False


class TestDigestComputation:
    """Feature stability digest tests."""

    def setup_method(self):
        """Reset metrics before each test."""
        reset_metrics()

    def test_compute_digest_empty(self):
        """Test digest with no metrics."""
        digest = compute_digest()
        assert digest.event_type == "feature_stability_digest"
        assert digest.flags_enabled == []

    def test_compute_digest_with_flags(self):
        """Test digest computation with flags."""
        mark_invocation("flag_a")
        mark_invocation("flag_a")
        mark_error("flag_a", ValueError("safe error"))

        digest = compute_digest(
            tenant_id="test-tenant",
            instance_id="test-instance",
            enabled_flag_ids=["flag_a"],
            release_tiers={"flag_a": "beta"},
        )

        assert digest.tenant_id == "test-tenant"
        assert digest.instance_id == "test-instance"
        assert len(digest.flags_enabled) == 1

        flag = digest.flags_enabled[0]
        assert flag["flag_id"] == "flag_a"
        assert flag["release_tier"] == "beta"
        assert flag["invocation_count_24h"] == 2
        assert flag["error_count_24h"] == 1
        assert flag["error_rate_24h"] == 0.5

    def test_digest_to_json(self):
        """Test serializing digest to JSON."""
        mark_invocation("flag_a")
        digest = compute_digest(enabled_flag_ids=["flag_a"])
        json_str = digest.to_json()
        assert "feature_stability_digest" in json_str
        assert "flag_a" in json_str

    def test_compute_digest_filters_flags(self):
        """Test that digest filters by enabled_flag_ids."""
        mark_invocation("flag_a")
        mark_invocation("flag_b")
        mark_invocation("flag_c")

        # Only enable flag_a
        digest = compute_digest(enabled_flag_ids=["flag_a"])
        assert len(digest.flags_enabled) == 1
        assert digest.flags_enabled[0]["flag_id"] == "flag_a"

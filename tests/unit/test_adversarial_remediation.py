"""Tests for adversarial review findings remediation."""

import time

import pytest

from core.skills.orchestration.remediation import (
    ManifestFreshnessValidator,
    PluginTimeoutError,
    enforce_tenant_id,
    get_model_key,
    validate_allowed_plugins,
    validate_feedback_matches_outcome,
)


class TestTenantIdEnforcement:
    """Finding #6: Tenant_id enforcement."""

    def test_enforce_tenant_id_valid(self):
        """Enforce non-empty tenant_id."""
        tenant_id = enforce_tenant_id("tenant_a")
        assert tenant_id == "tenant_a"

    def test_enforce_tenant_id_missing(self):
        """Reject empty tenant_id."""
        with pytest.raises(ValueError, match="tenant_id is required"):
            enforce_tenant_id(None)

        with pytest.raises(ValueError):
            enforce_tenant_id("")


class TestModelKeyTenantScoping:
    """Finding #7: Learning model tenant scoping."""

    def test_model_key_generation(self):
        """Generate tenant-scoped keys."""
        key1 = get_model_key("skill1", "tenant_a")
        key2 = get_model_key("skill1", "tenant_b")

        assert key1 == "tenant_a:skill1"
        assert key2 == "tenant_b:skill1"
        assert key1 != key2  # Different tenants, different keys

    def test_model_key_requires_tenant(self):
        """Require tenant_id in key generation."""
        with pytest.raises(ValueError, match="tenant_id required"):
            get_model_key("skill1", "")


class TestFeedbackValidation:
    """Finding #9: Feedback validation against outcome."""

    def test_feedback_consistent_with_success(self):
        """Good feedback on success is valid."""
        is_valid, note = validate_feedback_matches_outcome(
            feedback_rating="good",
            invocation_success=True,
            invocation_slo_met=True,
        )
        assert is_valid

    def test_feedback_inconsistent_good_on_failure(self):
        """Good feedback on failure is outlier."""
        is_valid, note = validate_feedback_matches_outcome(
            feedback_rating="good",
            invocation_success=False,
            invocation_slo_met=False,
        )
        assert not is_valid
        assert "outlier" in note

    def test_feedback_inconsistent_bad_on_success(self):
        """Bad feedback on success is outlier."""
        is_valid, note = validate_feedback_matches_outcome(
            feedback_rating="bad",
            invocation_success=True,
            invocation_slo_met=True,
        )
        assert not is_valid
        assert "outlier" in note

    def test_feedback_neutral_always_valid(self):
        """Neutral feedback is always valid."""
        is_valid, _ = validate_feedback_matches_outcome(
            feedback_rating="neutral",
            invocation_success=True,
            invocation_slo_met=True,
        )
        assert is_valid

        is_valid, _ = validate_feedback_matches_outcome(
            feedback_rating="neutral",
            invocation_success=False,
            invocation_slo_met=False,
        )
        assert is_valid


class TestManifestFreshness:
    """Finding #1: Staleness detection."""

    def test_manifest_fresh(self):
        """Fresh manifest within TTL."""
        validator = ManifestFreshnessValidator(ttl_seconds=10)
        validator.mark_manifest_verified("plugin1")

        assert validator.is_manifest_fresh("plugin1")

    def test_manifest_stale(self):
        """Stale manifest outside TTL."""
        validator = ManifestFreshnessValidator(ttl_seconds=1)
        validator.mark_manifest_verified("plugin1")

        time.sleep(1.1)  # Wait for TTL to expire

        assert not validator.is_manifest_fresh("plugin1")

    def test_manifest_age(self):
        """Get manifest age."""
        validator = ManifestFreshnessValidator()
        validator.mark_manifest_verified("plugin1")

        age = validator.get_manifest_age_seconds("plugin1")
        assert age is not None
        assert age >= 0.0

        # Unknown plugin returns None
        age_unknown = validator.get_manifest_age_seconds("unknown")
        assert age_unknown is None

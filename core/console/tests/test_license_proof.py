"""Comprehensive license limit proof test — generates visual HTML report.

Tests every FREE_TIER limit at the exact boundary (N passes, N+1 blocked),
then verifies tier upgrades unlock limits correctly.

Run:
    pytest core/console/tests/test_license_proof.py -v --tb=short
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

import pytest

# ── Path setup ────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[3]
# Add operator/ dir so that `license` is importable as a package
_OPERATOR_PATH = str(_REPO / "operator")
if _OPERATOR_PATH not in sys.path:
    sys.path.insert(0, _OPERATOR_PATH)
_LIC_PATH = str(_REPO / "operator" / "license")

from license import validator as _v  # noqa: E402
from license.limits import FREE_TIER, TIER_RESOURCE_LIMITS, LicenseLimitError  # noqa: E402


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_license():
    """Restore pristine FREE_TIER state before/after every test.

    Uses _set_active_license() so the ADR-0144 tamper-canary stays in sync.
    Direct ``_v._ACTIVE_LICENSE = ...`` assignment leaves _ACTIVE_LICENSE_CANARY
    stale, so _verified_license() treats the value as tampered and falls back to
    Free tier — which silently breaks every is_loaded()/active_tier()/get_limit()
    assertion below. The canary check is the very hardening this suite must prove
    works, so it must exercise the real setter.
    """
    orig = _v._ACTIVE_LICENSE
    orig_canary = _v._ACTIVE_LICENSE_CANARY
    orig_at = _v._LICENSE_LOADED_AT
    _v._set_active_license(None)
    _v._LICENSE_LOADED_AT = 0.0
    yield
    # Restore both the value and its canary together so the next test starts
    # from a consistent (value, canary) pair.
    _v._ACTIVE_LICENSE = orig
    _v._ACTIVE_LICENSE_CANARY = orig_canary
    _v._LICENSE_LOADED_AT = orig_at


def _set_tier(tier: str, overrides: dict | None = None) -> None:
    """Activate a specific tier with optional limit overrides."""
    limits = dict(TIER_RESOURCE_LIMITS.get(tier, {}))
    if overrides:
        limits.update(overrides)
    _v._set_active_license({
        "tier": tier,
        "iss": "corvinlabs.io",
        "type": "license",
        "limits": limits,
    })


# ── Phase 1: FREE_TIER boundary tests ─────────────────────────────────────────

class TestFreeTierBoundaries:
    """Every numeric FREE_TIER limit: N passes, N+1 raises LicenseLimitError."""

    def test_compute_units_exactly_at_limit_passes(self):
        """10 compute units → OK on FREE_TIER (ADR-0216 shared agentic pool)."""
        assert FREE_TIER["compute_units_per_day"] == 10
        _v.assert_limit("compute_units_per_day", 10)  # must not raise

    def test_compute_units_over_limit_blocked(self):
        """11th compute unit → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("compute_units_per_day", 11)
        assert "compute_units_per_day" in str(exc.value)
        assert "free" in str(exc.value).lower() or "tier" in str(exc.value).lower()

    def test_a2a_peers_exactly_at_limit_passes(self):
        """1 A2A peer → OK on FREE_TIER."""
        assert FREE_TIER["a2a_peers_max"] == 1
        _v.assert_limit("a2a_peers_max", 1)

    def test_a2a_peers_over_limit_blocked(self):
        """2nd A2A peer → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("a2a_peers_max", 2)
        assert "a2a_peers_max" in str(exc.value)

    def test_workflows_concurrent_exactly_at_limit_passes(self):
        """1 concurrent workflow → OK on FREE_TIER."""
        assert FREE_TIER["workflows_concurrent"] == 1
        _v.assert_limit("workflows_concurrent", 1)

    def test_workflows_concurrent_over_limit_blocked(self):
        """2nd concurrent workflow → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("workflows_concurrent", 2)
        assert "workflows_concurrent" in str(exc.value)

    def test_tenants_max_exactly_at_limit_passes(self):
        """1 tenant → OK on FREE_TIER."""
        assert FREE_TIER["tenants_max"] == 1
        _v.assert_limit("tenants_max", 1)

    def test_tenants_max_over_limit_blocked(self):
        """2nd tenant → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("tenants_max", 2)
        assert "tenants_max" in str(exc.value)

    def test_rag_providers_exactly_at_limit_passes(self):
        """1 RAG provider → OK on FREE_TIER."""
        assert FREE_TIER["rag_providers_max"] == 1
        _v.assert_limit("rag_providers_max", 1)

    def test_rag_providers_over_limit_blocked(self):
        """2nd RAG provider → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("rag_providers_max", 2)
        assert "rag_providers_max" in str(exc.value)

    def test_space_domains_exactly_at_limit_passes(self):
        """1 public domain → OK on FREE_TIER."""
        assert FREE_TIER["space_domains_max"] == 1
        _v.assert_limit("space_domains_max", 1)

    def test_space_domains_over_limit_blocked(self):
        """2nd public domain → LicenseLimitError on FREE_TIER."""
        with pytest.raises(LicenseLimitError) as exc:
            _v.assert_limit("space_domains_max", 2)
        assert "space_domains_max" in str(exc.value)


# ── Phase 2: Boolean feature gates ────────────────────────────────────────────

class TestBooleanFeatureGates:
    """Boolean features disabled on FREE_TIER."""

    def test_data_residency_false_on_free(self):
        assert _v.get_limit("data_residency") is False
        with pytest.raises(LicenseLimitError):
            _v.assert_limit("data_residency", True)

    def test_audit_export_false_on_free(self):
        assert _v.get_limit("audit_export") is False
        with pytest.raises(LicenseLimitError):
            _v.assert_limit("audit_export", True)

    def test_sso_enabled_false_on_free(self):
        assert _v.get_limit("sso_enabled") is False
        with pytest.raises(LicenseLimitError):
            _v.assert_limit("sso_enabled", True)

    def test_enterprise_portal_false_on_free(self):
        assert _v.get_limit("enterprise_portal") is False
        with pytest.raises(LicenseLimitError):
            _v.assert_limit("enterprise_portal", True)


# ── Phase 3: Tier upgrade unlocks limits ──────────────────────────────────────

class TestTierUpgrades:
    """Verify the paid tier unlocks everything.

    Operator decision 2026-06-23: only two tiers exist — free + member. Every
    legacy mid-tier name (personal/pro/business/enterprise/starter/professional/
    universal) canonicalizes to member, which is EVERYTHING unlocked (numeric
    limits None = unlimited, boolean flags True). The old per-mid-tier numeric
    ladder (personal=100 / pro=500 / business=2000) was removed; these tests now
    prove the canonicalization instead of the deleted ladder.
    """

    # Every legacy paid-tier name must resolve to member = all unlocked.
    _LEGACY_PAID_NAMES = ("personal", "pro", "professional", "starter",
                          "business", "enterprise", "universal", "member")
    _NUMERIC_LIMITS = ("compute_units_per_day", "a2a_peers_max",
                       "workflows_concurrent", "tenants_max",
                       "rag_providers_max", "space_domains_max")
    _BOOLEAN_FLAGS = ("data_residency", "audit_export", "sso_enabled",
                      "enterprise_portal")

    @pytest.mark.parametrize("name", _LEGACY_PAID_NAMES)
    def test_legacy_paid_name_unlocks_all_numeric_limits(self, name):
        _set_tier(name)
        for feature in self._NUMERIC_LIMITS:
            assert _v.get_limit(feature) is None, \
                f"{name!r}: {feature} should be unlimited (None) on member"
            _v.assert_limit(feature, 999_999)  # unlimited → must not raise

    @pytest.mark.parametrize("name", _LEGACY_PAID_NAMES)
    def test_legacy_paid_name_unlocks_all_boolean_flags(self, name):
        _set_tier(name)
        for feature in self._BOOLEAN_FLAGS:
            assert _v.get_limit(feature) is True, \
                f"{name!r}: {feature} should be True on member"
            _v.assert_limit(feature, True)  # must not raise

    def test_member_tier_space_domains_unlimited(self):
        """member tier: space_domains_max is None (unlimited) — license gate open."""
        _set_tier("member")
        assert _v.get_limit("space_domains_max") is None
        _v.assert_limit("space_domains_max", 999_999)  # must not raise

    def test_enterprise_tier_all_unlimited(self):
        _set_tier("enterprise")
        # All numeric limits are None (unlimited) on enterprise
        for feature in ("compute_units_per_day", "a2a_peers_max",
                        "workflows_concurrent", "tenants_max", "rag_providers_max",
                        "space_domains_max"):
            assert _v.get_limit(feature) is None, f"{feature} should be None on enterprise"
            _v.assert_limit(feature, 999_999)  # must not raise

    def test_enterprise_tier_all_booleans_true(self):
        _set_tier("enterprise")
        for feature in ("data_residency", "audit_export", "sso_enabled", "enterprise_portal"):
            assert _v.get_limit(feature) is True, f"{feature} should be True on enterprise"
            _v.assert_limit(feature, True)

    def test_member_tier_identical_to_enterprise(self):
        """member tier = universal = all unlimited."""
        _set_tier("member")
        for feature in ("compute_units_per_day", "a2a_peers_max",
                        "workflows_concurrent", "tenants_max", "rag_providers_max",
                        "space_domains_max"):
            assert _v.get_limit(feature) is None
        for feature in ("data_residency", "audit_export", "sso_enabled", "enterprise_portal"):
            assert _v.get_limit(feature) is True


# ── Phase 4: Backward-compat aliases ──────────────────────────────────────────

class TestBackwardCompatAliases:
    def test_universal_maps_to_member(self):
        _set_tier("universal")
        assert _v.get_limit("compute_units_per_day") is None

    def test_starter_maps_to_member(self):
        # Legacy "starter" canonicalizes to member = unlimited (not the old 100/5).
        _set_tier("starter")
        assert _v.get_limit("compute_units_per_day") is None
        assert _v.get_limit("a2a_peers_max") is None
        assert _v.active_tier() == "member"

    def test_professional_maps_to_member(self):
        _set_tier("professional")
        assert _v.get_limit("compute_units_per_day") is None
        assert _v.get_limit("a2a_peers_max") is None
        assert _v.active_tier() == "member"


# ── Phase 5: Security — token forgery ─────────────────────────────────────────

class TestTokenSecurity:
    """Verify that forged tokens are rejected."""

    def test_forged_token_rejected_gets_free_tier(self):
        """A crafted CORVIN- token with no valid signature → FREE_TIER."""
        import base64
        header = base64.b64encode(b'{"alg":"EdDSA","typ":"CORVIN-SesT"}').decode()
        payload = base64.b64encode(
            json.dumps({
                "tier": "enterprise",
                "sub": "attacker",
                "iss": "corvinlabs.io",
                "type": "license",
                "exp": 9_999_999_999,
            }).encode()
        ).decode()
        fake_sig = base64.b64encode(b"A" * 64).decode()
        fake_token = f"CORVIN-{header}.{payload}.{fake_sig}"

        result = _v._verify_ed25519(fake_token)
        assert result is None, f"Forged token must return None, got {result}"

    def test_forged_token_with_missing_issuer_rejected(self):
        """Token with wrong issuer → _validate_claims returns None."""
        fake_claims = {
            "tier": "enterprise",
            "iss": "evil.io",
            "type": "license",
        }
        result = _v._validate_claims(fake_claims)
        assert result is None

    def test_forged_token_with_wrong_type_rejected(self):
        """Token with wrong type → _validate_claims returns None."""
        fake_claims = {
            "tier": "enterprise",
            "iss": "corvinlabs.io",
            "type": "malicious_type",
        }
        result = _v._validate_claims(fake_claims)
        assert result is None

    def test_expired_token_rejected(self):
        """Expired token → _validate_claims returns None."""
        import time
        fake_claims = {
            "tier": "enterprise",
            "iss": "corvinlabs.io",
            "type": "license",
            "exp": time.time() - 86400,  # expired yesterday
        }
        result = _v._validate_claims(fake_claims)
        assert result is None

    def test_no_token_is_free_tier(self):
        """No token → FREE_TIER active."""
        assert _v._ACTIVE_LICENSE is None
        assert _v.active_tier() == "free"
        assert _v.is_loaded() is False
        assert _v.get_limit("compute_units_per_day") == 10


# ── Phase 6: Compute quota counter ────────────────────────────────────────────

class TestComputeQuotaCounter:
    """Verify the file-based daily compute counter enforces limits."""

    def test_free_tier_first_job_passes(self, tmp_path):
        from license.compute_quota import increment_and_check
        corvin_home = tmp_path / ".corvin"
        # First call → counter goes to 1, FREE_TIER limit is 1 → OK
        increment_and_check(corvin_home, channel="test", chat_key="test")

    def test_free_tier_over_quota_job_blocked(self, tmp_path):
        from license.compute_quota import increment_and_check
        from license.validator import get_limit
        corvin_home = tmp_path / ".corvin"
        # Burn the whole shared daily pool (ADR-0216: free tier = 10/day,
        # read dynamically so a future tier change doesn't rot this test).
        for _ in range(int(get_limit("compute_units_per_day"))):
            increment_and_check(corvin_home, channel="test", chat_key="test")
        # Next call → LicenseLimitError
        with pytest.raises(LicenseLimitError):
            increment_and_check(corvin_home, channel="test", chat_key="test")

    def test_member_tier_allows_unlimited_jobs(self, tmp_path):
        # Legacy "pro" canonicalizes to member = unlimited compute (no daily cap).
        _set_tier("pro")
        from license.compute_quota import increment_and_check
        corvin_home = tmp_path / ".corvin"
        # member has no cap — well past the old 500 ceiling must all pass.
        for i in range(600):
            increment_and_check(corvin_home, channel="test", chat_key=f"test-{i}")

    def test_enterprise_tier_allows_unlimited_jobs(self, tmp_path):
        _set_tier("enterprise")
        from license.compute_quota import increment_and_check
        corvin_home = tmp_path / ".corvin"
        # Enterprise = None (unlimited) — 100 calls must all pass
        for i in range(100):
            increment_and_check(corvin_home, channel="test", chat_key=f"test-{i}")

    def test_counter_file_created_mode_0600(self, tmp_path):
        from license.compute_quota import increment_and_check
        corvin_home = tmp_path / ".corvin"
        increment_and_check(corvin_home, channel="test", chat_key="test")
        quota_file = corvin_home / "global" / "license" / "compute_quota.json"
        assert quota_file.exists()
        import stat
        mode = stat.S_IMODE(quota_file.stat().st_mode)
        assert mode == 0o600, f"Expected 0600, got {oct(mode)}"

    def test_rejected_job_does_not_increment_counter(self, tmp_path):
        """Counter must not be incremented on an over-quota rejection."""
        from license.compute_quota import increment_and_check, get_today_count
        from license.validator import get_limit
        corvin_home = tmp_path / ".corvin"
        limit = int(get_limit("compute_units_per_day"))
        for _ in range(limit):
            increment_and_check(corvin_home, channel="test", chat_key="test")
        assert get_today_count(corvin_home) == limit
        # Over-quota call fails, counter stays at the cap
        with pytest.raises(LicenseLimitError):
            increment_and_check(corvin_home, channel="test", chat_key="test")
        assert get_today_count(corvin_home) == limit, "Counter must not change on rejection"


# ── Phase 7: get_limit resolution order ──────────────────────────────────────

class TestGetLimitResolutionOrder:
    """Resolution order: SesT limits dict → tier defaults → FREE_TIER."""

    def test_sest_override_beats_tier_default(self):
        """Per-customer override in SesT takes priority over tier default."""
        _v._set_active_license({
            "tier": "personal",
            "iss": "corvinlabs.io",
            "type": "license",
            "limits": {"compute_units_per_day": 42},  # custom override
        })
        assert _v.get_limit("compute_units_per_day") == 42

    def test_tier_default_beats_free_when_sest_absent(self):
        """Tier default used (not free) when no per-customer override.

        member's unlimited (None) must win over the FREE_TIER value, proving the
        tier default — not the free fallback — is applied for a loaded license.
        """
        _v._set_active_license({
            "tier": "member",
            "iss": "corvinlabs.io",
            "type": "license",
        })
        assert _v.get_limit("compute_units_per_day") is None
        assert _v.get_limit("compute_units_per_day") != FREE_TIER["compute_units_per_day"]

    def test_free_tier_fallback_when_no_license(self):
        """No license → FREE_TIER fallback for every feature."""
        assert _v._ACTIVE_LICENSE is None
        for feature, value in FREE_TIER.items():
            assert _v.get_limit(feature) == value, \
                f"get_limit({feature!r}) should be {value!r}, got {_v.get_limit(feature)!r}"


# ── Phase 8: active_tier() and is_loaded() ────────────────────────────────────

class TestPublicAPI:
    def test_active_tier_free_when_no_license(self):
        assert _v.active_tier() == "free"

    def test_active_tier_returns_loaded_tier(self):
        # Any legacy paid name canonicalizes to the single paid tier, member.
        _set_tier("pro")
        assert _v.active_tier() == "member"

    def test_is_loaded_false_without_license(self):
        assert _v.is_loaded() is False

    def test_is_loaded_true_with_license(self):
        _set_tier("member")
        assert _v.is_loaded() is True

    def test_is_feature_allowed_audit_export_pro(self):
        _set_tier("pro")
        assert _v.is_feature_allowed("audit_export") is True

    def test_is_feature_allowed_sso_business(self):
        _set_tier("business")
        assert _v.is_feature_allowed("sso_enabled") is True

    def test_is_feature_allowed_sso_free_false(self):
        assert _v.is_feature_allowed("sso_enabled") is False

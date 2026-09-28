"""Licensing 1.0.0 — capability matrix, tier vocabulary and the daily quota counter.

These used to be ``requests`` calls to a live ``localhost:8765``. The HTTP
endpoint they targeted (``/v1/licensing/verify``) is not served at that path and
answers ``enforcement_unavailable`` for everything where it IS mounted — see
test_licensing_1_0_0_e2e.py. The semantics those tests meant to check live in
``capability_api.require_capability`` (ADR-0703 §2 — the single gate every
chokepoint calls) and ``quota_counter`` (ADR-0703 class-L per-day counter), and
are tested there directly. Free tier = scratch CORVIN_HOME with no licence key.

Dropped empty stubs (no subject in the product): Member-Credential TTL issuance
(``active_credential`` is a stub that returns None), CRL-unavailable denial for
new A2A peers, "chokepoints wired" grep guard, and quota persistence "across a
console restart" as a separate case (the counter is a file; covered by
``test_counter_is_file_backed``).
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import member_tier  # noqa: E402

import corvin_operator.license.capability_api as ca  # noqa: E402
from corvin_operator.license import limits, quota_counter, validator  # noqa: E402

BASELINE = ["chat.turns", "voice.summaries", "bridges.all", "engines.all",
            "skills.run_vetted", "skills.run_local", "telemetry.opt_out"]
MEMBER_ONLY = ["forge.create", "a2a.network", "marketplace.publish"]


def _decide(capability: str, requested: int = 1):
    return ca.require_capability(capability, requested, tenant_id="_default",
                                 entry_point="tests/license/gate3")


def _denied(capability: str, requested: int = 1) -> ca.LicenseDenied:
    with pytest.raises(ca.LicenseDenied) as exc:
        _decide(capability, requested)
    return exc.value


# ── Tier vocabulary (ADR-0700 §1) ───────────────────────────────────────────

def test_scratch_home_is_free_tier():
    assert validator.active_tier() == "free"
    assert _decide("chat.turns").tier is ca.Tier.FREE


def test_only_two_canonical_tiers():
    assert {t.value for t in ca.Tier} == {"free", "member"}
    for legacy in ("universal", "starter", "personal", "professional", "pro",
                   "business", "enterprise"):
        assert validator.canonical_tier(legacy) == "member"
    for garbage in ("super_member", "platinum", "", "MEMBER", "admin"):
        assert validator.canonical_tier(garbage) == "free"


def test_unrecognised_resolver_output_is_treated_as_free():
    with mock.patch.object(ca, "active_tier", lambda: "platinum"):
        assert _denied("forge.create").tier is ca.Tier.FREE


def test_resolver_exception_falls_back_to_the_free_allowance():
    def boom():
        raise RuntimeError("licence store unreadable")

    with mock.patch.object(ca, "active_tier", boom):
        assert _decide("chat.turns").decision is ca.Decision.ALLOW
        assert _denied("forge.create").reason == "not_available_in_tier"


# ── Capability matrix (ADR-0700 §2.1) ───────────────────────────────────────

@pytest.mark.parametrize("capability", BASELINE)
def test_baseline_is_unlimited_on_both_tiers(capability):
    free = _decide(capability)
    assert free.decision is ca.Decision.ALLOW and free.allowed is None
    with member_tier():
        member = _decide(capability)
    assert member.decision is ca.Decision.ALLOW and member.tier is ca.Tier.MEMBER


@pytest.mark.parametrize("capability", MEMBER_ONLY)
def test_member_only_capabilities(capability):
    denied = _denied(capability)
    assert (denied.capability, denied.tier, denied.reason) == (
        capability, ca.Tier.FREE, "not_available_in_tier")
    with member_tier():
        ok = _decide(capability)
    assert ok.decision is ca.Decision.ALLOW and ok.allowed is None and ok.reason is None


def test_compute_run_free_allowance_is_ten():
    assert _decide("compute.run", 10).allowed == 10
    assert _denied("compute.run", 11).reason == "quota_exceeded"
    with member_tier():
        big = _decide("compute.run", 999_999)
    assert big.decision is ca.Decision.ALLOW and big.allowed is None


def test_unknown_capability_is_denied_even_for_a_member():
    with member_tier():
        assert _denied("fake.capability.xyz").reason == "unknown_capability"


def test_matrix_shape_matches_limits_py():
    assert ca.CAPABILITIES is limits.CAPABILITIES
    for name, spec in limits.CAPABILITIES.items():
        assert spec["class"] in ("B", "L", "N"), name
        assert set(spec) == {"class", "free", "member"}, name
        assert spec["member"]["limit"] is None, name   # member is unlimited everywhere
    assert {n for n, s in limits.CAPABILITIES.items() if s["free"]["limit"] == 0} == set(MEMBER_ONLY)
    assert all(limits.CAPABILITIES[c]["class"] == "B" for c in BASELINE)


@pytest.mark.parametrize("requested", [0, -1, -10**9, True, 1.5, "1", None])
def test_non_positive_or_non_int_requested_is_rejected(requested):
    """Was KNOWN GAP: a zero/negative quantity passed ``limit >= requested`` and
    was ALLOWED on every finite tier (a caller that debits ``requested`` would
    have been crediting). Now a ValueError, on every tier."""
    for tier in ("free", "member"):
        with mock.patch.object(ca, "active_tier", lambda t=tier: t):
            with pytest.raises(ValueError, match="positive integer"):
                _decide("compute.run", requested)


def test_invalid_tenant_raises_license_denied():
    """Was: a non-ALLOW decision came back as a RETURN value for an invalid
    tenant, which G1 and G5 ignored. Every non-allow outcome now raises."""
    with pytest.raises(ca.LicenseDenied) as exc:
        ca.require_capability("forge.create", tenant_id="../x", entry_point="t")
    assert exc.value.reason == "invalid_tenant"
    with mock.patch.object(ca, "active_tier", lambda: "member"):
        with pytest.raises(ca.LicenseDenied):
            ca.require_capability("chat.turns", tenant_id="", entry_point="t")


# ── Daily quota counter (class L, per tenant, per UTC day) ──────────────────

@pytest.fixture
def qhome(tmp_path):
    return tmp_path / "corvin_home"


def test_free_boundary_is_exact(qhome):
    with mock.patch.object(quota_counter, "get_limit", lambda f: 10):
        counts = [quota_counter.increment_and_check(qhome, "compute_units_per_day", "_default")
                  for _ in range(10)]
        assert counts == list(range(1, 11))
        with pytest.raises(limits.LicenseLimitError):
            quota_counter.increment_and_check(qhome, "compute_units_per_day", "_default")
    assert quota_counter.get_today_count(qhome, "compute_units_per_day", "_default") == 10


def test_unlimited_limit_writes_no_counter(qhome):
    with mock.patch.object(quota_counter, "get_limit", lambda f: None):
        assert quota_counter.increment_and_check(qhome, "compute_units_per_day", "_default") == 0
    assert quota_counter.get_today_count(qhome, "compute_units_per_day", "_default") == 0


def test_counter_rolls_over_on_the_utc_date(qhome):
    with mock.patch.object(quota_counter, "get_limit", lambda f: 1):
        with mock.patch.object(quota_counter, "_today_utc", lambda: "2026-09-26"):
            quota_counter.increment_and_check(qhome, "f", "_default")
            with pytest.raises(limits.LicenseLimitError):
                quota_counter.increment_and_check(qhome, "f", "_default")
        with mock.patch.object(quota_counter, "_today_utc", lambda: "2026-09-27"):
            assert quota_counter.increment_and_check(qhome, "f", "_default") == 1


def test_today_is_the_utc_date():
    from datetime import datetime, timezone

    assert quota_counter._today_utc() == datetime.now(timezone.utc).strftime("%Y-%m-%d")


def test_counter_is_per_tenant(qhome):
    with mock.patch.object(quota_counter, "get_limit", lambda f: 1):
        quota_counter.increment_and_check(qhome, "f", "tenant_a")
        assert quota_counter.increment_and_check(qhome, "f", "tenant_b") == 1


def test_counter_is_file_backed(qhome):
    with mock.patch.object(quota_counter, "get_limit", lambda f: 5):
        for _ in range(3):
            quota_counter.increment_and_check(qhome, "f", "_default")
    files = list((qhome / "quotas").glob("_default_f_*.json"))
    assert len(files) == 1
    assert (files[0].stat().st_mode & 0o777) == 0o600
    assert quota_counter._load(files[0]) == {"count": 3}

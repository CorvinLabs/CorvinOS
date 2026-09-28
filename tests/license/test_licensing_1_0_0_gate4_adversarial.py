"""Licensing 1.0.0 — adversarial cases against the real gate and counter, in-process.

Formerly ``requests`` against a live ``localhost:8765``. Every case here runs the
real ``capability_api.require_capability`` / ``quota_counter`` code in a scratch
CORVIN_HOME. HTTP-level adversarial cases (no session, no CSRF, forged body
``tier``) live in test_licensing_1_0_0_e2e.py and test_g3_gates_e2e.py.

Dropped empty stubs (no subject in the product): federation counter replay /
HMAC window, clone detection by counter reversion, device-fingerprint re-bind
rate limit, offline Member-Credential expiry, expired-licence-JWT handling
(needs a production-signed token), CRL outage for new peers, and "audit write is
non-blocking" (capability decisions are not audited at all — see the KNOWN BUG in
test_licensing_1_0_0_e2e.py).
"""
from __future__ import annotations

import json
import os
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _license_console_sandbox import member_tier  # noqa: E402

import corvin_operator.license.capability_api as ca  # noqa: E402
from corvin_operator.license import limits, quota_counter  # noqa: E402


def _decide(capability: str, requested: int = 1, tenant_id: str = "_default"):
    return ca.require_capability(capability, requested, tenant_id=tenant_id,
                                 entry_point="tests/license/gate4")


# ── forged inputs ───────────────────────────────────────────────────────────

@pytest.mark.parametrize("capability", ["fake.unlimited_access", "forge.create ",
                                        "FORGE.CREATE", "forge.*", ""])
def test_forged_capability_names_are_denied(capability):
    with member_tier():
        with pytest.raises(ca.LicenseDenied) as exc:
            _decide(capability)
    assert exc.value.reason == "unknown_capability"


@pytest.mark.parametrize("tenant", ["", "../_default", "_default/../x", "a" * 500, "*"])
def test_forged_tenant_never_yields_allow(tenant):
    with member_tier():
        with pytest.raises(ca.LicenseDenied) as exc:
            _decide("forge.create", tenant_id=tenant)
    assert exc.value.reason == "invalid_tenant"


def test_free_forge_limit_is_zero_not_negative():
    assert limits.CAPABILITIES["forge.create"]["free"]["limit"] == 0
    with pytest.raises(ca.LicenseDenied) as exc:
        _decide("forge.create")
    assert exc.value.reason == "not_available_in_tier"


def test_member_unlimited_is_none_not_a_large_number():
    with member_tier():
        decision = _decide("compute.run", 10**12)
    assert decision.decision is ca.Decision.ALLOW and decision.allowed is None


def test_capability_matrix_cannot_be_mutated_at_runtime():
    with pytest.raises(TypeError):
        limits.CAPABILITIES["forge.create"] = {"class": "L", "free": {"limit": None}}  # type: ignore[index]
    with pytest.raises(TypeError):
        limits.CAPABILITIES["forge.create"]["free"]["limit"] = None  # type: ignore[index]


def test_decision_objects_are_immutable():
    import dataclasses

    decision = _decide("chat.turns")
    with pytest.raises(dataclasses.FrozenInstanceError):
        decision.decision = ca.Decision.ALLOW  # type: ignore[misc]


# ── concurrency ─────────────────────────────────────────────────────────────

def test_fifty_concurrent_checks_agree():
    def check(i):
        try:
            _decide("forge.create")
            return "allow"
        except ca.LicenseDenied as exc:
            return exc.reason

    with ThreadPoolExecutor(max_workers=50) as pool:
        results = list(pool.map(check, range(50)))
    assert results == ["not_available_in_tier"] * 50


def test_fifty_concurrent_increments_lose_no_update(tmp_path):
    home = tmp_path / "h"
    with mock.patch.object(quota_counter, "get_limit", lambda f: 1000):
        with ThreadPoolExecutor(max_workers=50) as pool:
            counts = list(pool.map(
                lambda _: quota_counter.increment_and_check(home, "f", "_default"), range(50)))
    assert sorted(counts) == list(range(1, 51))
    assert quota_counter.get_today_count(home, "f", "_default") == 50


def test_concurrent_increments_never_overshoot_the_limit(tmp_path):
    home = tmp_path / "h"
    ok, refused = [], []
    lock = threading.Lock()

    def one(_):
        try:
            n = quota_counter.increment_and_check(home, "f", "_default")
            with lock:
                ok.append(n)
        except limits.LicenseLimitError:
            with lock:
                refused.append(1)

    with mock.patch.object(quota_counter, "get_limit", lambda f: 10):
        with ThreadPoolExecutor(max_workers=50) as pool:
            list(pool.map(one, range(50)))
    assert sorted(ok) == list(range(1, 11))
    assert len(refused) == 40


# ── fail-closed counter ─────────────────────────────────────────────────────

@pytest.mark.skipif(os.geteuid() == 0, reason="root ignores directory permissions")
def test_unwritable_counter_denies_a_finite_limit(tmp_path):
    home = tmp_path / "h"
    (home / "quotas").mkdir(parents=True)
    os.chmod(home / "quotas", 0o500)
    try:
        with mock.patch.object(quota_counter, "get_limit", lambda f: 10):
            with pytest.raises(limits.LicenseLimitError):
                quota_counter.increment_and_check(home, "f", "_default")
    finally:
        os.chmod(home / "quotas", 0o700)


def test_malformed_limit_fails_closed(tmp_path):
    with mock.patch.object(quota_counter, "get_limit", lambda f: "lots"):
        with pytest.raises(limits.LicenseLimitError):
            quota_counter.increment_and_check(tmp_path / "h", "f", "_default")


@pytest.mark.parametrize("payload", ['{"count": -100}', '{"count": "9"}', "[1,2]", "not json"])
def test_tampered_counter_file_is_read_as_zero(tmp_path, payload):
    """Documented behaviour: an unreadable/garbled counter starts fresh at 0.
    (The local counter is not a security boundary — deleting the file has the
    same effect — so this pins that tampering buys nothing BEYOND that.)"""
    home = tmp_path / "h"
    path = quota_counter._quota_path(home, "_default", "f", quota_counter._today_utc())
    path.write_text(payload)
    os.chmod(path, 0o600)
    with mock.patch.object(quota_counter, "get_limit", lambda f: 10):
        assert quota_counter.increment_and_check(home, "f", "_default") == 1
    assert json.loads(path.read_text())["count"] == 1

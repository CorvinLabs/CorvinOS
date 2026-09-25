"""A2A manifest in-process cache expiry (A2A review r4, finding 3).

`load_manifest()` kept its FIRST result for the whole process lifetime —
including the empty permissive fallback after a failed fetch at boot — and
nothing passes `force_refresh`. So a network blip at startup disabled
revocation enforcement until restart, and a newly published revocation never
reached a long-running receiver.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_manifest as m  # noqa: E402

_SIGNED = {"issued_at": 1, "revoked_pairing_ids": ["P"]}


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    clock = [1000.0]
    calls = [0]
    state = {"fetch": None, "verify": False}

    def fetch():
        calls[0] += 1
        return state["fetch"]

    monkeypatch.setattr(m, "_monotonic", lambda: clock[0])
    monkeypatch.setattr(m, "_fetch_manifest", fetch)
    monkeypatch.setattr(m, "_verify_manifest_signature", lambda raw: state["verify"])
    monkeypatch.setattr(m, "_manifest_age_days", lambda raw: 0.1)
    monkeypatch.setattr(m, "_save_cache", lambda raw: None)
    monkeypatch.setattr(m, "_load_cache", lambda: None)
    monkeypatch.setattr(m, "_emit_audit", lambda *a, **k: None)
    m.clear_cached()
    yield clock, calls, state
    m.clear_cached()


def test_empty_fallback_expires_after_five_minutes(env):
    clock, calls, state = env
    a = m.load_manifest()                       # network down at boot
    assert a.is_empty and not a.sig_verified and calls[0] == 1

    state.update(fetch=dict(_SIGNED), verify=True)   # network is back
    clock[0] += 60
    assert m.load_manifest() is a and calls[0] == 1  # still inside 5 min

    clock[0] += m._FALLBACK_TTL_S
    b = m.load_manifest()
    assert calls[0] == 2
    assert b.sig_verified and "P" in b.revoked_pairing_ids


def test_verified_manifest_is_kept_one_hour_then_refetched(env):
    clock, calls, state = env
    state.update(fetch=dict(_SIGNED), verify=True)
    a = m.load_manifest()
    assert a.sig_verified and calls[0] == 1

    clock[0] += m._FALLBACK_TTL_S + 1                # past 5 min, inside 1 h
    assert m.load_manifest() is a and calls[0] == 1

    state["fetch"] = {"issued_at": 2, "revoked_pairing_ids": ["P", "Q"]}
    clock[0] += m._VERIFIED_TTL_S
    b = m.load_manifest()
    assert calls[0] == 2 and "Q" in b.revoked_pairing_ids


def test_disk_cache_fallback_uses_short_ttl(env, monkeypatch):
    clock, calls, state = env
    state.update(fetch=None, verify=True)
    monkeypatch.setattr(m, "_load_cache", lambda: dict(_SIGNED))
    a = m.load_manifest()
    assert a.from_cache and a.sig_verified
    clock[0] += m._FALLBACK_TTL_S + 1
    m.load_manifest()
    assert calls[0] == 2


def test_ttls_are_one_hour_and_five_minutes():
    assert m._VERIFIED_TTL_S == 3600.0
    assert m._FALLBACK_TTL_S == 300.0


def test_force_refresh_still_bypasses_cache(env):
    clock, calls, state = env
    m.load_manifest()
    m.load_manifest(force_refresh=True)
    assert calls[0] == 2

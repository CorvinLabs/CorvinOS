"""The shared worker-engine routing rule (one source of truth).

The routing decision must be identical on every surface (console + all bridges).
This pins the matrix so the console wrapper and the bridge caller cannot
diverge.

Run: python3 -m pytest tests/test_delegation_policy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from delegation_policy import (  # noqa: E402
    WORKER_ENGINE_DEFAULT,
    WORKER_ENGINE_MODES,
    delegation_engine_target,
    worker_engine_target,
)


def _t(**kw):
    base = dict(mode="native", force_delegate=False, is_big_data=False,
                tde_available=True, quota_ok=True)
    base.update(kw)
    return worker_engine_target(**base)


# ── Defaults ──────────────────────────────────────────────────────────────

def test_modes_and_default():
    assert WORKER_ENGINE_MODES == ("native", "acs", "tde")
    assert WORKER_ENGINE_DEFAULT == "native"


def test_default_mode_is_native():
    """A stock install runs the turn in-process — no delegation engine."""
    assert _t() == "native"


def test_native_never_reaches_tde_even_when_available():
    assert _t(mode="native", tde_available=True, quota_ok=True) == "native"


# ── The two auto-delegation triggers that survive in every mode ───────────

def test_explicit_delegate_forces_acs_in_every_mode():
    for mode in WORKER_ENGINE_MODES:
        assert _t(mode=mode, force_delegate=True) == "acs"


def test_big_data_forces_acs_in_every_mode():
    for mode in WORKER_ENGINE_MODES:
        assert _t(mode=mode, is_big_data=True) == "acs"


def test_big_data_beats_tde_selection():
    assert _t(mode="tde", is_big_data=True, tde_available=True, quota_ok=True) == "acs"


# ── Explicit selections ───────────────────────────────────────────────────

def test_acs_mode_routes_to_acs():
    assert _t(mode="acs") == "acs"


def test_tde_mode_routes_to_tde_when_available():
    assert _t(mode="tde") == "tde"


def test_tde_unavailable_degrades_to_native_not_acs():
    """Every degrade ends at native — an unavailable engine must not be
    silently swapped for a different delegation engine."""
    assert _t(mode="tde", tde_available=False) == "native"


def test_tde_pool_exhausted_degrades_to_native():
    assert _t(mode="tde", quota_ok=False) == "native"


def test_unknown_mode_degrades_to_native():
    assert _t(mode="nonsense") == "native"
    assert _t(mode="") == "native"


# ── Full matrix ───────────────────────────────────────────────────────────

def test_full_matrix_is_stable():
    for mode in (*WORKER_ENGINE_MODES, "bogus"):
        for fd in (False, True):
            for bd in (False, True):
                for av in (False, True):
                    for q in (False, True):
                        r = worker_engine_target(mode=mode, force_delegate=fd,
                                                 is_big_data=bd, tde_available=av,
                                                 quota_ok=q)
                        if fd or bd:
                            expected = "acs"
                        elif mode == "acs":
                            expected = "acs"
                        elif mode == "tde":
                            expected = "tde" if (av and q) else "native"
                        else:
                            expected = "native"
                        assert r == expected, (mode, fd, bd, av, q, r)


# ── Legacy wrapper (pre-worker-engine callers) ────────────────────────────

def test_legacy_wrapper_keeps_old_tde_first_behavior():
    """Out-of-tree callers that never migrated keep the ADR-0217 semantics:
    tde|acs only, and TDE-unavailable degrades to ACS rather than native."""
    assert delegation_engine_target(force_delegate=False, is_big_data=False,
                                    tde_available=True, quota_ok=True) == "tde"
    assert delegation_engine_target(force_delegate=True, is_big_data=False,
                                    tde_available=True, quota_ok=True) == "acs"
    assert delegation_engine_target(force_delegate=False, is_big_data=True,
                                    tde_available=True, quota_ok=True) == "acs"
    assert delegation_engine_target(force_delegate=False, is_big_data=False,
                                    tde_available=False, quota_ok=True) == "acs"
    assert delegation_engine_target(force_delegate=False, is_big_data=False,
                                    tde_available=True, quota_ok=False) == "acs"


# ── Console wrapper routes through the shared module ──────────────────────

def test_console_wrapper_routes_via_shared_module():
    sys.path.insert(0, str(_REPO / "core" / "console"))
    from corvin_console import chat_runtime  # noqa: PLC0415
    # native mode: a substantive prompt stays in-process …
    assert chat_runtime._worker_engine_target(
        "summarize this", mode="native", force_delegate=False) == "native"
    # … but big-data shape still fans out to ACS.
    assert chat_runtime._worker_engine_target(
        "process this 5 million row dataset", mode="native",
        force_delegate=False) == "acs"
    # acs mode delegates.
    assert chat_runtime._worker_engine_target(
        "summarize this", mode="acs", force_delegate=False) == "acs"


def test_console_wrapper_skips_tde_probes_off_tde_mode(monkeypatch):
    """A native install must not pay for the TDE import/pool probes."""
    sys.path.insert(0, str(_REPO / "core" / "console"))
    from corvin_console import chat_runtime  # noqa: PLC0415

    def _boom():  # pragma: no cover — must never be called
        raise AssertionError("TDE probe ran outside tde mode")

    monkeypatch.setattr(chat_runtime, "_tde_available", _boom)
    monkeypatch.setattr(chat_runtime, "_tde_quota_peek_ok", _boom)
    assert chat_runtime._worker_engine_target(
        "summarize this", mode="native", force_delegate=False) == "native"
    assert chat_runtime._worker_engine_target(
        "summarize this", mode="acs", force_delegate=False) == "acs"
    # …and not even in tde mode when an earlier rung already decided.
    assert chat_runtime._worker_engine_target(
        "summarize this", mode="tde", force_delegate=True) == "acs"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

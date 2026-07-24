"""ADR-0221 P1: the shared delegation-routing rule (one source of truth).

The routing decision must be identical on every surface (console + all bridges).
This pins the matrix so the console wrapper and the future bridge caller cannot
diverge.

Run: python3 -m pytest tests/test_delegation_policy.py
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))

from delegation_policy import delegation_engine_target  # noqa: E402


def _t(**kw):
    base = dict(force_delegate=False, is_big_data=False, tde_available=True, quota_ok=True)
    base.update(kw)
    return delegation_engine_target(**base)


def test_default_is_tde():
    assert _t() == "tde"


def test_explicit_delegate_forces_acs():
    assert _t(force_delegate=True) == "acs"


def test_big_data_forces_acs():
    assert _t(is_big_data=True) == "acs"


def test_tde_unavailable_falls_to_acs():
    assert _t(tde_available=False) == "acs"


def test_pool_exhausted_falls_to_acs():
    assert _t(quota_ok=False) == "acs"


def test_precedence_force_beats_everything():
    # force_delegate wins even when TDE would otherwise be eligible
    assert _t(force_delegate=True, is_big_data=False, tde_available=True, quota_ok=True) == "acs"


def test_full_matrix_is_stable():
    # Only the exact "all-clear" combination yields TDE; every other row is ACS.
    tde_rows = 0
    for fd in (False, True):
        for bd in (False, True):
            for av in (False, True):
                for q in (False, True):
                    r = delegation_engine_target(force_delegate=fd, is_big_data=bd,
                                                 tde_available=av, quota_ok=q)
                    if r == "tde":
                        tde_rows += 1
                        assert (fd, bd, av, q) == (False, False, True, True)
                    else:
                        assert r == "acs"
    assert tde_rows == 1


def test_console_wrapper_still_routes_via_shared_module():
    # The console's _delegation_engine_target must now delegate to the shared
    # rule — a big-data prompt still lands on ACS, a plain one on TDE.
    sys.path.insert(0, str(_REPO / "core" / "console"))
    from corvin_console import chat_runtime  # noqa: PLC0415
    assert chat_runtime._delegation_engine_target(
        "summarize this", force_delegate=False, tde_available=True, quota_ok=True) == "tde"
    assert chat_runtime._delegation_engine_target(
        "process this 5 million row dataset", force_delegate=False,
        tde_available=True, quota_ok=True) == "acs"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

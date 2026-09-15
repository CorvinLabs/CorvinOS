"""ADR-0150 LIC-ENG-USE-02: the ACS worker/manager spawn chokepoint enforces the
license engines_allowed limit fail-closed (parallel to the OS-turn + delegate
paths) — a SesT restricting engines must block a forbidden engine on the ACS path.
"""
import os
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent
for _p in (str(_SHARED), str(_SHARED.parents[1])):  # shared + operator
    if _p not in sys.path:
        sys.path.insert(0, _p)

import acs_runtime as A
import license.validator as _v


@pytest.fixture(autouse=True)
def _no_bypass_and_restore(monkeypatch):
    # Ensure the dual-env test bypass is NOT both-set so the gate fires.
    for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST"):
        monkeypatch.delenv(k, raising=False)
    orig, orig_can = _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY
    yield
    _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY = orig, orig_can


def test_forbidden_engine_blocked_on_acs_path():
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    with pytest.raises(RuntimeError) as ei:
        A._assert_engine_licensed("hermes")
    assert "engine-not-allowed-by-license" in str(ei.value)


def test_allowed_engine_passes_on_acs_path():
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["hermes"]}})
    A._assert_engine_licensed("hermes")  # must not raise


def test_no_limit_allows_any_engine():
    _v._set_active_license(None)  # free tier: engines_allowed = None → no-op
    A._assert_engine_licensed("hermes")  # must not raise


def test_dual_env_bypass_requires_both(monkeypatch):
    # A single bypass var must NOT disable the gate.
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    monkeypatch.setenv("CORVIN_AGENTS_SKIP_LIVE", "1")  # only one of the two
    with pytest.raises(RuntimeError):
        A._assert_engine_licensed("hermes")

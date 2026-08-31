"""ADR-0150 (structural): the license engines_allowed gate lives in
engine_registry.get_engine(), so every get_engine consumer — the make_factory
worker-engine factory and the AWP DAG walker — inherits it by construction.
"""
import sys
from pathlib import Path

import pytest

_SHARED = Path(__file__).resolve().parent
for _p in (str(_SHARED), str(_SHARED.parents[1])):  # shared + operator
    if _p not in sys.path:
        sys.path.insert(0, _p)

import engine_registry as ER
import license.validator as _v


@pytest.fixture(autouse=True)
def _no_bypass_restore(monkeypatch):
    # Ensure the dual-env bypass is NOT both-set so the gate fires.
    for k in ("CORVIN_AGENTS_SKIP_LIVE", "CORVIN_INTEGRATION_TEST"):
        monkeypatch.delenv(k, raising=False)
    orig, can = _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY
    yield
    _v._ACTIVE_LICENSE, _v._ACTIVE_LICENSE_CANARY = orig, can


def test_helper_denies_forbidden_engine():
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    assert ER._engine_allowed_by_license("codex_cli") is False
    assert ER._engine_allowed_by_license("claude_code") is True


def test_helper_no_limit_allows_any():
    _v._set_active_license(None)  # engines_allowed=None → no-op → allow
    assert ER._engine_allowed_by_license("codex_cli") is True


def test_get_engine_returns_none_for_license_denied():
    # A denied engine yields None from get_engine REGARDLESS of CLI availability —
    # the license gate runs before the builder, so the factory/walker get None.
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    assert ER.get_engine("codex_cli") is None


def test_make_factory_inherits_the_gate():
    # The worker-engine factory consumer inherits the gate by construction.
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    factory = ER.make_factory("codex_cli", persona="p", channel="c", chat_key="k")
    assert factory("codex_cli") is None, "factory must return None for a license-denied engine"


def test_dual_env_bypass_requires_both(monkeypatch):
    _v._set_active_license({"tier": "pro", "limits": {"engines_allowed": ["claude_code"]}})
    monkeypatch.setenv("CORVIN_AGENTS_SKIP_LIVE", "1")  # only one → gate still fires
    assert ER._engine_allowed_by_license("codex_cli") is False

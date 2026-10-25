#!/usr/bin/env python3
"""The claude_code spawn env and the L35 host agree, and engine_models reads the same home (T-0230).

Defects fixed, measured red first:

1. ``engine_models`` resolved the tenant file from ``CORVIN_HOME`` or ``~/.corvin``;
   ``spawn_gates`` resolves ``CORVIN_HOME`` or the checkout's repo-marker home. On a checkout
   without ``CORVIN_HOME`` the two read different tenant files, so the provider assignment the
   L35 gate validated was not the one the spawn used.
2. The console built its ``claude`` env from ``os.environ`` and never injected the provider
   redirect, while ``check_l35`` validated the PROVIDER host. A turn could be admitted for the
   assigned provider and still go to ``api.anthropic.com`` (or to an inherited
   ``ANTHROPIC_BASE_URL``).

``apply_claude_code_provider_env`` is the one function both surfaces use.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import engine_models as em  # type: ignore  # noqa: E402
import spawn_gates  # type: ignore  # noqa: E402


def _provider(**kw):
    base = dict(id="acme_llm", label="Acme LLM", base_url="https://llm.acme.example/v1",
                model_source="static", credential_env="ACME_LLM_KEY", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


@pytest.fixture()
def tenant(monkeypatch, tmp_path):
    """A tenant whose claude_code is assigned a provider, via the real tenant file."""
    home = tmp_path / "home"
    cfg = home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("ACME_LLM_KEY", "k-test")

    def assign(provider_id: str | None, providers: dict):
        spec = {"engine_models": {"claude_code": {"provider": provider_id}}} if provider_id else {}
        import yaml
        cfg.write_text(yaml.safe_dump({"spec": spec}), encoding="utf-8")
        cfg.chmod(0o600)
        monkeypatch.setattr(em, "load_providers", lambda force_reload=False: providers)
    return assign


# ── 1. one home resolver ──────────────────────────────────────────────────────

def test_engine_models_reads_the_home_spawn_gates_reads(monkeypatch):
    monkeypatch.delenv("CORVIN_HOME", raising=False)
    assert em._corvin_home() == spawn_gates._resolve_corvin_home(None)


def test_both_honour_corvin_home(monkeypatch, tmp_path):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    assert em._corvin_home() == spawn_gates._resolve_corvin_home(None) == tmp_path


# ── 2. the spawn env follows the assignment ───────────────────────────────────

def test_an_assigned_provider_redirects_the_spawn_and_agrees_with_l35(tenant):
    tenant("acme_llm", {"acme_llm": _provider()})
    env = {"PATH": "/usr/bin", "ANTHROPIC_BASE_URL": "https://stale.example"}
    problem = em.apply_claude_code_provider_env(env, "acme")
    assert problem is None
    assert env["ANTHROPIC_BASE_URL"] == "https://llm.acme.example/v1"
    assert env["CORVIN_CC_PROVIDER"] == "acme_llm"
    assert em.resolve_engine_egress_host("acme", "claude_code") == "llm.acme.example"


def test_no_assignment_drops_an_inherited_redirect_so_the_spawn_goes_native(tenant):
    tenant(None, {})
    env = {"ANTHROPIC_BASE_URL": "https://stale.example", "CORVIN_CC_PROVIDER": "old",
           "ANTHROPIC_API_KEY": "sk-native", "PATH": "/usr/bin"}
    assert em.apply_claude_code_provider_env(env, "acme") is None
    assert "ANTHROPIC_BASE_URL" not in env and "CORVIN_CC_PROVIDER" not in env
    assert env["ANTHROPIC_API_KEY"] == "sk-native", "a native key is the operator's, never stripped"
    assert env["PATH"] == "/usr/bin"
    assert em.resolve_engine_egress_host("acme", "claude_code") is None


def test_anthropic_as_provider_is_native_too(tenant):
    tenant("anthropic", {"anthropic": _provider(id="anthropic", base_url="https://api.anthropic.com")})
    env = {"ANTHROPIC_BASE_URL": "https://stale.example"}
    assert em.apply_claude_code_provider_env(env, "acme") is None
    assert "ANTHROPIC_BASE_URL" not in env


def test_a_platform_provider_sets_its_flag_and_never_a_base_url(tenant):
    ps = _provider(id="bedrock", base_url="", auth_mode="platform", credential_env="",
                   platform_env={"enable_var": "CLAUDE_CODE_USE_BEDROCK", "region_env": "AWS_REGION",
                                 "default_region": "eu-central-1",
                                 "host_template": "bedrock-runtime.{region}.amazonaws.com"})
    tenant("bedrock", {"bedrock": ps})
    env = {"ANTHROPIC_BASE_URL": "https://stale.example"}
    assert em.apply_claude_code_provider_env(env, "acme") is None
    assert env.get("CLAUDE_CODE_USE_BEDROCK") == "1"
    assert "ANTHROPIC_BASE_URL" not in env


def test_a_provider_that_resolves_to_no_route_refuses_instead_of_going_native(tenant, monkeypatch):
    """L35 validates the provider host; an empty route would spawn on api.anthropic.com."""
    tenant("acme_llm", {"acme_llm": _provider()})
    monkeypatch.setattr(em, "resolve_claude_code_provider_env", lambda tid: {})
    env = {"ANTHROPIC_BASE_URL": "https://stale.example"}
    problem = em.apply_claude_code_provider_env(env, "acme")
    assert isinstance(problem, str) and "acme_llm" in problem
    assert "ANTHROPIC_BASE_URL" not in env, "the stale redirect must be gone even when refusing"


def test_a_resolver_error_refuses_when_a_provider_is_assigned(tenant, monkeypatch):
    tenant("acme_llm", {"acme_llm": _provider()})

    def boom(tid):
        raise RuntimeError("vault down")
    monkeypatch.setattr(em, "resolve_claude_code_provider_env", boom)
    assert isinstance(em.apply_claude_code_provider_env({}, "acme"), str)


def test_a_resolver_error_without_an_assignment_is_not_a_refusal(tenant, monkeypatch):
    tenant(None, {})

    def boom(tid):
        raise RuntimeError("never reached for a native tenant")
    monkeypatch.setattr(em, "resolve_claude_code_provider_env", boom)
    assert em.apply_claude_code_provider_env({}, "acme") is None


def test_the_bridge_builder_uses_the_same_function():
    """_build_spawn_env must not keep a private copy of the redirect (the bridge is the other surface)."""
    src = (HERE / "adapter.py").read_text(encoding="utf-8")
    assert "apply_claude_code_provider_env" in src

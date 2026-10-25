#!/usr/bin/env python3
"""resolve_provider_env_strict: the spawn env of a REROUTED turn (T-0229, ADR-2268 Decision 3).

``resolve_claude_code_provider_env`` is best-effort by design: a missing credential, a proxy that
cannot start or an unresolved base URL degrades to ``{}`` ("CC falls through to its existing
routing"), which for claude_code means api.anthropic.com. Fine for a routing preference, fatal for
a sensitive turn. The strict function raises ``ProviderRouteError`` instead, starts from an env
stripped of every inherited redirect or region variable, and verifies the HOST the env will really
reach against the host the operator declared and L35 validated.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import engine_models as em  # type: ignore  # noqa: E402

HOST = "llm.acme.example"
BEDROCK_HOST = "bedrock-runtime.eu-central-1.amazonaws.com"


def _direct(**kw):
    base = dict(id="acme_llm", label="Acme", base_url=f"https://{HOST}/v1", model_source="static",
                credential_env="ACME_KEY", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


def _bedrock():
    return em.ProviderSpec(
        id="bedrock", label="Bedrock", base_url="", model_source="bedrock", credential_env="", kind="cloud",
        auth_mode="platform", platform_env={
            "enable_var": "CLAUDE_CODE_USE_BEDROCK", "region_env": ["AWS_REGION", "AWS_DEFAULT_REGION"],
            "default_region": "us-east-1", "host_template": "https://bedrock.{region}.amazonaws.com",
            "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com",
            "credential_vars": ["AWS_PROFILE", "AWS_REGION", "AWS_DEFAULT_REGION", "AWS_ACCESS_KEY_ID",
                                "AWS_SECRET_ACCESS_KEY"]})


@pytest.fixture()
def home(monkeypatch, tmp_path):
    h = tmp_path / "home"
    monkeypatch.setenv("CORVIN_HOME", str(h))
    cfg = h / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)

    def declare(*entries, providers):
        cfg.write_text(yaml.safe_dump({"spec": {"data_classification": {"provider_compliance": list(entries)}}}))
        cfg.chmod(0o600)
        monkeypatch.setattr(em, "load_providers", lambda force_reload=False: providers)
    return declare


def _decl(**kw):
    base = {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external", "endpoint_host": HOST}
    base.update(kw)
    return base


def _strict(provider="acme_llm", model="vendor/Model-1", base_env=None):
    return em.resolve_provider_env_strict("acme", provider, model, base_env=base_env if base_env is not None else {})


class TestDirectProvider:
    def test_the_env_reaches_the_declared_host_with_the_route_credential(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": _direct()})
        monkeypatch.setenv("ACME_KEY", "k-acme")
        env, host = _strict()
        assert env["ANTHROPIC_BASE_URL"] == f"https://{HOST}/v1"
        assert env["ANTHROPIC_API_KEY"] == "k-acme"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "k-acme"
        assert host == HOST

    def test_a_missing_credential_is_an_error_never_a_native_spawn(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": _direct()})
        monkeypatch.delenv("ACME_KEY", raising=False)
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict()
        assert ei.value.code == "missing_credential"

    def test_a_keyless_provider_gets_a_placeholder_not_the_operators_anthropic_key(self, home):
        home(_decl(), providers={"acme_llm": _direct(credential_env="")})
        env, _ = _strict(base_env={"ANTHROPIC_API_KEY": "sk-ant-operators-own-key"})
        assert env["ANTHROPIC_API_KEY"] == "provider"
        assert "sk-ant-operators-own-key" not in repr(env)

    @pytest.mark.parametrize("planted", [
        "ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL", "ANTHROPIC_BEDROCK_BASE_URL", "ANTHROPIC_VERTEX_PROJECT_ID",
        "CLAUDE_CODE_USE_BEDROCK", "CLAUDE_CODE_USE_VERTEX", "CLAUDE_CODE_USE_FOUNDRY",
        "CORVIN_CC_PROVIDER", "CORVIN_CC_LOCAL_MODE", "AWS_REGION", "AWS_DEFAULT_REGION",
        "CLOUD_ML_REGION", "ANTHROPIC_VERTEX_REGION", "VERTEX_REGION_CLAUDE_SONNET_5",
    ])
    def test_every_inherited_redirect_or_region_variable_is_stripped(self, home, monkeypatch, planted):
        home(_decl(), providers={"acme_llm": _direct(), "bedrock": _bedrock()})
        monkeypatch.setenv("ACME_KEY", "k-acme")
        env, _ = _strict(base_env={planted: "from-the-parent-process", "PATH": "/usr/bin"})
        if planted in ("ANTHROPIC_BASE_URL", "ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN", "CORVIN_CC_PROVIDER"):
            assert env[planted] != "from-the-parent-process"      # rewritten from the route
        else:
            assert planted not in env, planted
        assert env["PATH"] == "/usr/bin", "unrelated variables stay"

    def test_a_changed_endpoint_is_refused(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": _direct(base_url="https://moved.example/v1")})
        monkeypatch.setenv("ACME_KEY", "k")
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict()
        # Eligibility names it first; the host comparison at the end is the second line of defence.
        assert ei.value.code in ("endpoint_changed", "host_mismatch")

    def test_an_undeclared_or_unknown_provider_is_an_error(self, home):
        home(providers={"acme_llm": _direct()})
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict()
        assert ei.value.code == "not_declared"
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict(provider="ghost")
        assert ei.value.code == "unknown_provider"

    def test_it_never_returns_an_empty_route(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": _direct(base_url="")})
        monkeypatch.setenv("ACME_KEY", "k")
        with pytest.raises(em.ProviderRouteError):
            _strict()


class TestTranslatingProxy:
    def _ollama(self):
        return _direct(model_source="ollama", base_url=f"https://{HOST}")

    def test_the_proxy_starts_with_the_ROUTE_model_not_the_tenant_model(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": self._ollama()})
        monkeypatch.setenv("ACME_KEY", "k")
        seen = {}
        import anthropic_openai_bridge as bridge  # type: ignore

        def fake_ensure(target):
            seen["model"], seen["url"] = target.model, target.chat_completions_url
            return "http://127.0.0.1:43111"
        monkeypatch.setattr(bridge, "ensure_proxy", fake_ensure)
        monkeypatch.setattr(em, "get_tenant_engine_model", lambda *a, **k: "tenant-os-model")
        env, host = _strict(model="route-model:7b")
        assert seen["model"] == "route-model:7b"
        assert env["ANTHROPIC_BASE_URL"] == "http://127.0.0.1:43111"
        assert host == HOST, "the verified host is the UPSTREAM the proxy talks to, not the loopback"

    def test_a_proxy_that_cannot_start_is_an_error(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": self._ollama()})
        monkeypatch.setenv("ACME_KEY", "k")
        import anthropic_openai_bridge as bridge  # type: ignore
        monkeypatch.setattr(bridge, "ensure_proxy", lambda target: (_ for _ in ()).throw(OSError("port busy")))
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict()
        assert ei.value.code == "proxy_failed"

    def test_a_non_loopback_proxy_url_is_refused(self, home, monkeypatch):
        home(_decl(), providers={"acme_llm": self._ollama()})
        monkeypatch.setenv("ACME_KEY", "k")
        import anthropic_openai_bridge as bridge  # type: ignore
        monkeypatch.setattr(bridge, "ensure_proxy", lambda target: "https://elsewhere.example")
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict()
        assert ei.value.code == "proxy_not_loopback"


class TestPlatformProvider:
    def _declared(self, **kw):
        return _decl(provider="bedrock", platform=True, region="eu-central-1",
                     models=["anthropic.claude-sonnet-5"], endpoint_host=BEDROCK_HOST, **kw)

    def test_the_declared_region_wins_over_the_ambient_environment(self, home):
        home(self._declared(), providers={"bedrock": _bedrock()})
        env, host = _strict("bedrock", "anthropic.claude-sonnet-5", base_env={
            "AWS_REGION": "us-east-1", "AWS_DEFAULT_REGION": "us-west-2",
            "AWS_ACCESS_KEY_ID": "AKIA-FROM-PARENT", "AWS_PROFILE": "corp"})
        assert env["AWS_REGION"] == "eu-central-1" and env["AWS_DEFAULT_REGION"] == "eu-central-1"
        assert env["CLAUDE_CODE_USE_BEDROCK"] == "1"
        assert env["AWS_ACCESS_KEY_ID"] == "AKIA-FROM-PARENT" and env["AWS_PROFILE"] == "corp"
        assert "ANTHROPIC_BASE_URL" not in env, "a platform provider is never reached through a base URL"
        assert host == BEDROCK_HOST

    def test_a_model_outside_the_declared_models_is_an_error(self, home):
        home(self._declared(), providers={"bedrock": _bedrock()})
        with pytest.raises(em.ProviderRouteError) as ei:
            _strict("bedrock", "us.anthropic.claude-global-profile")
        assert ei.value.code == "model_not_declared"

    def test_a_platform_declaration_without_a_region_is_an_error(self, home):
        decl = self._declared()
        del decl["region"]
        home(decl, providers={"bedrock": _bedrock()})
        with pytest.raises(em.ProviderRouteError):
            _strict("bedrock", "anthropic.claude-sonnet-5")


def test_provider_route_error_carries_a_stable_code_and_no_secret():
    err = em.ProviderRouteError("missing_credential", "detail without secrets")
    assert err.code == "missing_credential"
    assert "missing_credential" in str(err)

#!/usr/bin/env python3
"""The L34 reroute decision (T-0219, ADR-2268 Decisions 2-3, outcome table).

``resolve_l34_route`` answers one question for one spawn: run the engine as chosen (native), run
on the operator's sensitive provider (reroute), or refuse. It is the only place that decides, and
the callers must not re-derive it. Native is returned exactly where today's ``check_l34`` allows:
a PUBLIC turn, an allowed turn, or a tenant without a policy.
"""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import engine_models as em  # type: ignore  # noqa: E402
import spawn_gates  # type: ignore  # noqa: E402

HOST = "llm.acme.example"


def _spec(**kw):
    base = dict(id="acme_llm", label="Acme", base_url=f"https://{HOST}/v1", model_source="static",
                credential_env="K", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


@pytest.fixture()
def tenant(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cfg = home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    providers = {"acme_llm": _spec()}
    monkeypatch.setattr(em, "load_providers", lambda force_reload=False: providers)

    def write(dcfg: dict | None):
        # ``None`` means NO tenant file at all (the opt-in default). A file with no
        # data_classification section is a policy with the default matrix, not "no policy".
        spawn_gates._l34_cache.clear()
        if dcfg is None:
            if cfg.exists():
                cfg.unlink()
            return
        cfg.write_text(yaml.safe_dump({"spec": {"data_classification": dcfg}}), encoding="utf-8")
        cfg.chmod(0o600)
    return write


def _route(**extra):
    return {"enabled": True, "provider": "acme_llm", "model_id": "vendor/Model-1", **extra}


def _declared(**extra):
    return {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external",
            "endpoint_host": HOST, **extra}


def _resolve(prompt="harmless", classification=None, engine="claude_code"):
    return spawn_gates.decide_l34_route(engine, "acme", prompt=prompt, classification=classification,
                                         persona="assistant")


class TestNative:
    def test_no_policy_is_native_as_today(self, tenant):
        tenant(None)
        assert _resolve("mail jane.doe@example.com").kind == "native"

    def test_a_public_turn_is_native_and_never_changes_path(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()]})
        assert _resolve("write a haiku about spring", classification="PUBLIC").kind == "native"

    def test_an_allowed_turn_is_native_even_with_a_route_configured(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()],
                "matrix": {"CONFIDENTIAL": ["local", "us_cloud"]}})
        assert _resolve(classification="CONFIDENTIAL").kind == "native"


class TestReroute:
    def test_a_confidential_turn_on_a_us_pin_reroutes_to_the_declared_provider(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()]})
        out = _resolve(classification="CONFIDENTIAL")
        assert out.kind == "reroute"
        assert (out.provider, out.model_id) == ("acme_llm", "vendor/Model-1")
        assert out.engine == "claude_code"

    def test_a_detector_raised_turn_reroutes_too(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()]})
        out = _resolve("Bitte an jane.doe@example.com antworten")
        assert out.kind == "reroute" and out.classification == "CONFIDENTIAL"

    def test_an_internal_turn_reroutes_when_the_operator_narrowed_its_row(self, tenant):
        # The provider must be admitted under the UNCHANGED matrix for the level (ADR-2268):
        # narrowing INTERNAL to local means only a local provider can take the turn.
        tenant({"sensitive_route": _route(),
                "provider_compliance": [_declared(locality="local", network_egress="local")],
                "matrix": {"INTERNAL": ["local"], "CONFIDENTIAL": ["local"]}})
        assert _resolve(classification="INTERNAL").kind == "reroute"

    def test_a_provider_outside_the_narrowed_row_refuses(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()],
                "matrix": {"INTERNAL": ["local"], "CONFIDENTIAL": ["local"]}})
        out = _resolve(classification="INTERNAL")
        assert out.kind == "refuse" and out.reason == "matrix"


class TestRefuse:
    def test_a_disabled_route_refuses_as_today(self, tenant):
        tenant({"sensitive_route": {"enabled": False}, "provider_compliance": [_declared()]})
        out = _resolve("mail jane.doe@example.com")
        assert out.kind == "refuse" and out.reason == "matrix"

    def test_an_absent_route_refuses_as_today(self, tenant):
        tenant({"provider_compliance": [_declared()]})
        assert _resolve(classification="CONFIDENTIAL").kind == "refuse"

    def test_secret_is_never_rerouted(self, tenant):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()]})
        out = _resolve("key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789")
        assert out.kind == "refuse" and out.classification == "SECRET"

    def test_an_undeclared_target_refuses(self, tenant):
        tenant({"sensitive_route": _route()})
        out = _resolve(classification="CONFIDENTIAL")
        assert out.kind == "refuse" and out.reason == "not_declared"

    def test_a_target_whose_endpoint_changed_refuses(self, tenant, monkeypatch):
        tenant({"sensitive_route": _route(), "provider_compliance": [_declared()]})
        monkeypatch.setattr(em, "load_providers", lambda force_reload=False: {"acme_llm": _spec(base_url="https://moved.example/v1")})
        out = _resolve(classification="CONFIDENTIAL")
        assert out.kind == "refuse" and out.reason == "endpoint_changed"

    def test_a_model_the_provider_does_not_serve_is_refused_not_substituted(self, tenant):
        platform = _spec(id="acme_llm", base_url="", auth_mode="platform", credential_env="", platform_env={
            "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com"})
        tenant({"sensitive_route": _route(), "provider_compliance": [
            _declared(platform=True, region="eu-central-1", models=["declared-model"],
                      endpoint_host="bedrock-runtime.eu-central-1.amazonaws.com")]})
        import builtins  # noqa: F401
        em.load_providers = lambda force_reload=False: {"acme_llm": platform}  # type: ignore[assignment]
        out = _resolve(classification="CONFIDENTIAL")
        assert out.kind == "refuse" and out.reason == "target_not_eligible"

    def test_a_malformed_route_refuses_every_turn_of_the_tenant(self, tenant):
        tenant({"sensitive_route": {"enabled": "true", "provider": "acme_llm", "model_id": "m"}})
        out = _resolve("write a haiku about spring", classification="PUBLIC")
        assert out.kind == "refuse" and out.reason == "route_malformed"

    def test_a_refusal_never_names_the_prompt(self, tenant):
        tenant({"sensitive_route": {"enabled": False}})
        out = _resolve("mail jane.doe@example.com")
        assert "jane.doe" not in repr(out)


class TestPolicyErrors:
    def test_an_unloadable_policy_is_a_refusal_not_native(self, tenant, monkeypatch):
        tenant({"sensitive_route": _route()})
        monkeypatch.setattr(spawn_gates, "_load_l34_guard",
                            lambda tid, home: spawn_gates._L34GuardLoadFailed("RuntimeError"))
        out = _resolve(classification="CONFIDENTIAL")
        assert out.kind == "refuse" and out.reason == "gate_error"

    def test_a_classifier_failure_refuses_not_public(self, tenant, monkeypatch):
        tenant({"sensitive_route": _route()})
        import data_classification as dc  # type: ignore
        monkeypatch.setattr(dc, "classify_task", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("x")))
        out = _resolve("some prompt")
        assert out.kind == "refuse" and out.reason == "classifier_error"


def test_the_decision_is_the_only_public_entry_for_rerouting():
    assert callable(spawn_gates.decide_l34_route)
    assert hasattr(spawn_gates, "RouteOutcome")

#!/usr/bin/env python3
"""validate_provider and the provider-aware L35 host (T-0228 part 2, ADR-2268).

A rerouted turn keeps the engine ``claude_code`` and changes only the endpoint. L34 therefore
judges the pair (claude_code, provider) from the operator's ``provider_compliance`` and never
from the engine's own locality, and L35 judges the PROVIDER's host, not the engine default.
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

import data_classification as dc  # type: ignore  # noqa: E402
import engine_models as em  # type: ignore  # noqa: E402
import spawn_gates  # type: ignore  # noqa: E402

HOST = "llm.acme.example"


def _declared(**kw) -> dict:
    base = {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external",
            "endpoint_host": HOST}
    base.update(kw)
    return base


def _guard(*entries, matrix=None):
    dcfg = {"provider_compliance": list(entries)}
    if matrix:
        dcfg["matrix"] = matrix
    events: list = []
    g = dc.DataFlowGuard.from_tenant_config(
        {"spec": {"data_classification": dcfg}},
        audit_writer=lambda et, sev, details: events.append((et, sev, dict(details))))
    g._events = events  # type: ignore[attr-defined]
    return g


def _v(g, cls, *, provider="acme_llm", host=HOST, engine="claude_code"):
    return g.validate_provider(classification=cls, engine_id=engine, provider=provider,
                               current_host=host)


class TestValidateProvider:
    def test_an_eu_provider_takes_confidential_and_internal(self):
        g = _guard(_declared())
        for c in ("INTERNAL", "CONFIDENTIAL"):
            d = _v(g, c)
            assert d.allowed, (c, d.reason)

    def test_the_matrix_still_decides(self):
        g = _guard(_declared(locality="us_cloud"))
        d = _v(g, "CONFIDENTIAL")
        assert not d.allowed and d.matched_rule == "matrix"
        assert _v(g, "INTERNAL").allowed   # default matrix: INTERNAL admits us_cloud

    def test_secret_is_never_admitted_even_for_a_local_provider(self):
        g = _guard(_declared(locality="local", network_egress="local"))
        d = _v(g, "SECRET")
        assert not d.allowed and d.matched_rule == "secret_provider"

    def test_an_undeclared_provider_is_denied(self):
        d = _v(_guard(_declared()), "CONFIDENTIAL", provider="other")
        assert not d.allowed and d.matched_rule == "provider_not_declared"

    def test_a_rejected_declaration_is_not_a_declaration(self):
        g = _guard(_declared(locality="unknown"))
        assert g.provider_compliance_rejected and not g.provider_compliance
        assert _v(g, "CONFIDENTIAL").matched_rule == "provider_not_declared"

    def test_a_changed_endpoint_makes_the_provider_ineligible(self):
        d = _v(_guard(_declared()), "CONFIDENTIAL", host="moved.acme.example")
        assert not d.allowed and d.matched_rule == "endpoint_changed"

    def test_an_unknown_current_host_is_denied_not_assumed(self):
        d = _v(_guard(_declared()), "CONFIDENTIAL", host=None)
        assert not d.allowed and d.matched_rule == "endpoint_unknown"

    def test_the_host_comparison_ignores_case_and_a_trailing_dot(self):
        assert _v(_guard(_declared()), "CONFIDENTIAL", host="LLM.Acme.Example.").allowed

    def test_only_claude_code_can_use_a_provider(self):
        d = _v(_guard(_declared()), "CONFIDENTIAL", engine="codex_cli")
        assert not d.allowed and d.matched_rule == "provider_engine"

    def test_an_unparseable_classification_is_denied(self):
        d = _v(_guard(_declared()), "nonsense")
        assert not d.allowed

    def test_one_audit_event_per_call_names_engine_and_provider_without_text(self):
        g = _guard(_declared())
        _v(g, "CONFIDENTIAL")
        _v(g, "SECRET")
        kinds = [e[0] for e in g._events]
        assert kinds == ["data_flow.approved", "data_flow.blocked"]
        for _et, _sev, details in g._events:
            assert details["engine_id"] == "claude_code@acme_llm"
            assert set(details) <= {"channel", "chat_key", "classification", "engine_id",
                                    "matched_rule", "persona", "reason"}


# ── L35 against the provider host ─────────────────────────────────────────────

def _provider_spec():
    return em.ProviderSpec(id="acme_llm", label="Acme", base_url=f"https://{HOST}/v1",
                           model_source="static", credential_env="", kind="cloud")


@pytest.fixture()
def l35_tenant(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cfg = home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setattr(em, "load_providers", lambda force_reload=False: {"acme_llm": _provider_spec()})

    def write(allowed):
        cfg.write_text(yaml.safe_dump({"spec": {"egress": {
            "enabled": True, "default_action": "deny", "allowed_hosts": allowed,
            "forbidden_hosts": ["api.anthropic.com"]}}}), encoding="utf-8")
        cfg.chmod(0o600)
        spawn_gates._l35_cache.clear() if hasattr(spawn_gates, "_l35_cache") else None
    return write


class TestProviderAwareL35:
    def test_resolve_engine_egress_host_takes_an_explicit_provider(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
        monkeypatch.setattr(em, "load_providers", lambda force_reload=False: {"acme_llm": _provider_spec()})
        assert em.resolve_engine_egress_host("acme", "claude_code") is None          # nothing assigned
        assert em.resolve_engine_egress_host("acme", "claude_code", provider="acme_llm") == HOST
        assert em.resolve_engine_egress_host("acme", "claude_code", provider="nope") is None

    def test_the_gate_admits_the_provider_host_that_policy_allows(self, l35_tenant):
        l35_tenant([HOST])
        assert spawn_gates.check_l35("claude_code", "acme", provider="acme_llm") is None

    def test_the_gate_refuses_a_provider_host_the_policy_does_not_allow(self, l35_tenant):
        l35_tenant(["other.example"])
        refusal = spawn_gates.check_l35("claude_code", "acme", provider="acme_llm")
        assert isinstance(refusal, str) and "egress" in refusal

    def test_without_a_provider_the_engine_default_host_is_still_judged(self, l35_tenant):
        l35_tenant([HOST])     # api.anthropic.com is forbidden: the default route is refused
        assert isinstance(spawn_gates.check_l35("claude_code", "acme"), str)

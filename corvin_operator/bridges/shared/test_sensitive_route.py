#!/usr/bin/env python3
"""spec.data_classification.sensitive_route: parsing predicates and provider eligibility (T-0218).

ADR-2268 "Normative core": ROUTE_BLOCK / ROUTE_ON / ROUTE_MALFORMED are defined once and
read by the gate and by the console routes through this module, so they cannot disagree.
Eligibility is stricter than matrix admissibility: an explicit operator declaration with
locality exactly local or eu_cloud, never the native anthropic provider, never an aggregator
or a proxy provider, and the declaration is bound to the endpoint it was made for.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data_classification as dc  # type: ignore  # noqa: E402
import engine_models as em  # type: ignore  # noqa: E402
import sensitive_route as sr  # type: ignore  # noqa: E402

HOST = "llm.acme.example"


def _cfg(route=..., declared=()):
    dcfg: dict = {}
    if route is not ...:
        dcfg["sensitive_route"] = route
    if declared:
        dcfg["provider_compliance"] = list(declared)
    return {"spec": {"data_classification": dcfg}}


class TestParse:
    @pytest.mark.parametrize("cfg", [None, {}, {"spec": None}, {"spec": {"data_classification": None}},
                                     _cfg(), _cfg(None), _cfg("x"), _cfg(["a"]), _cfg(5)])
    def test_no_block_when_absent_null_or_not_a_mapping(self, cfg):
        p = sr.parse_sensitive_route(cfg)
        assert (p.block, p.on, p.malformed) == (False, False, False)

    def test_a_block_without_enabled_is_off_and_well_formed(self):
        p = sr.parse_sensitive_route(_cfg({"provider": "x"}))
        assert (p.block, p.on, p.malformed) == (True, False, False)

    def test_enabled_false_is_off(self):
        p = sr.parse_sensitive_route(_cfg({"enabled": False}))
        assert (p.block, p.on, p.malformed) == (True, False, False)

    @pytest.mark.parametrize("value", ["true", 1, 0, "yes", [], {}])
    def test_a_non_boolean_enabled_is_malformed(self, value):
        p = sr.parse_sensitive_route(_cfg({"enabled": value, "provider": "p", "model_id": "m"}))
        assert p.malformed and not p.on

    @pytest.mark.parametrize("route", [
        {"enabled": True},
        {"enabled": True, "provider": "p"},
        {"enabled": True, "model_id": "m"},
        {"enabled": True, "provider": 5, "model_id": "m"},
        {"enabled": True, "provider": "p", "model_id": ["m"]},
        {"enabled": True, "provider": "Bad Id!", "model_id": "m"},
        {"enabled": True, "provider": "p", "model_id": "has space"},
        {"enabled": True, "provider": "p", "model_id": "x" * 201},
        {"enabled": True, "provider": "p", "model_id": ""},
    ])
    def test_an_enabled_block_with_a_bad_target_is_malformed(self, route):
        p = sr.parse_sensitive_route(_cfg(route))
        assert p.malformed and not p.on and p.route is None

    def test_a_good_enabled_block_is_on(self):
        p = sr.parse_sensitive_route(_cfg({"enabled": True, "provider": "acme_llm",
                                           "model_id": "meta-llama/Llama-3.3-70B-Instruct:v1@x"}))
        assert (p.block, p.on, p.malformed) == (True, True, False)
        assert p.route == sr.SensitiveRoute("acme_llm", "meta-llama/Llama-3.3-70B-Instruct:v1@x")

    def test_the_model_id_is_kept_verbatim(self):
        mid = "Vendor/Model.Name-1.5B"
        assert sr.parse_sensitive_route(_cfg({"enabled": True, "provider": "p", "model_id": mid})).route.model_id == mid


# ── eligibility ───────────────────────────────────────────────────────────────

def _spec(**kw):
    base = dict(id="acme_llm", label="Acme LLM", base_url=f"https://{HOST}/v1", model_source="static",
                credential_env="K", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


def _decl(**kw):
    base = {"provider": "acme_llm", "locality": "eu_cloud", "network_egress": "external",
            "endpoint_host": HOST}
    base.update(kw)
    return base


def _check(spec, decl, provider="acme_llm"):
    declared, _ = dc.parse_provider_compliance(_cfg(declared=[decl]))
    return sr.provider_eligibility(provider, declared=declared, providers={spec.id: spec})


class TestEligibility:
    def test_a_declared_eu_provider_is_eligible(self):
        ok, reason = _check(_spec(), _decl())
        assert ok and reason == ""

    @pytest.mark.parametrize("locality", ["local", "eu_cloud"])
    def test_local_and_eu_are_the_only_eligible_localities(self, locality):
        assert _check(_spec(), _decl(locality=locality))[0]

    def test_a_us_cloud_declaration_is_not_admissible(self):
        ok, reason = _check(_spec(), _decl(locality="us_cloud"))
        assert not ok and reason == "locality_not_admissible"

    def test_an_undeclared_provider_is_ineligible(self):
        ok, reason = sr.provider_eligibility("acme_llm", declared={}, providers={"acme_llm": _spec()})
        assert (ok, reason) == (False, "not_declared")

    def test_an_unknown_provider_is_ineligible(self):
        ok, reason = sr.provider_eligibility("ghost", declared={}, providers={})
        assert (ok, reason) == (False, "unknown_provider")

    def test_the_native_provider_is_never_eligible(self):
        spec = _spec(id="anthropic", base_url="https://api.anthropic.com")
        ok, reason = _check(spec, _decl(provider="anthropic", endpoint_host="api.anthropic.com"), "anthropic")
        assert (ok, reason) == (False, "native_provider")

    def test_an_aggregator_is_ineligible(self):
        ok, reason = _check(_spec(model_source="openrouter"), _decl())
        assert (ok, reason) == (False, "aggregator")

    def test_a_provider_with_a_proxy_is_ineligible(self):
        ok, reason = _check(_spec(proxy_base_url="https://proxy.example/v1"), _decl())
        assert (ok, reason) == (False, "proxy_provider")

    def test_a_changed_endpoint_makes_the_declaration_stale(self):
        ok, reason = _check(_spec(base_url="https://moved.example/v1"), _decl())
        assert (ok, reason) == (False, "endpoint_changed")

    def test_a_platform_provider_needs_region_and_models(self):
        platform = _spec(id="bedrock", base_url="", auth_mode="platform", credential_env="", platform_env={
            "enable_var": "CLAUDE_CODE_USE_BEDROCK", "region_env": ["AWS_REGION"], "default_region": "us-east-1",
            "host_template": "https://bedrock.{region}.amazonaws.com",
            "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com"})
        no_region = _decl(provider="bedrock", platform=True, endpoint_host="bedrock-runtime.eu-central-1.amazonaws.com")
        assert not _check(platform, no_region, "bedrock")[0]
        complete = dict(no_region, region="eu-central-1", models=["anthropic.claude-sonnet-5"])
        assert _check(platform, complete, "bedrock") == (True, "")

    def test_a_platform_host_comes_from_the_declared_region_not_the_environment(self, monkeypatch):
        monkeypatch.setenv("AWS_REGION", "us-east-1")
        platform = _spec(id="bedrock", base_url="", auth_mode="platform", credential_env="", platform_env={
            "region_env": ["AWS_REGION"], "default_region": "us-east-1",
            "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com"})
        decl = _decl(provider="bedrock", platform=True, region="eu-central-1", models=["m"],
                     endpoint_host="bedrock-runtime.eu-central-1.amazonaws.com")
        assert _check(platform, decl, "bedrock") == (True, "")

    def test_the_model_of_a_platform_provider_must_be_declared(self):
        platform = _spec(id="bedrock", base_url="", auth_mode="platform", credential_env="", platform_env={
            "runtime_host_template": "https://bedrock-runtime.{region}.amazonaws.com"})
        decl = _decl(provider="bedrock", platform=True, region="eu-central-1", models=["declared-model"],
                     endpoint_host="bedrock-runtime.eu-central-1.amazonaws.com")
        declared, _ = dc.parse_provider_compliance(_cfg(declared=[decl]))
        assert sr.model_allowed("bedrock", "declared-model", declared=declared, providers={"bedrock": platform})
        assert not sr.model_allowed("bedrock", "other-model", declared=declared, providers={"bedrock": platform})

    def test_a_non_platform_provider_accepts_any_well_formed_model(self):
        declared, _ = dc.parse_provider_compliance(_cfg(declared=[_decl()]))
        assert sr.model_allowed("acme_llm", "any/Model:1", declared=declared, providers={"acme_llm": _spec()})


class TestEligibleList:
    def test_the_list_carries_label_locality_and_admitted_classes(self):
        cfg = _cfg(declared=[_decl(), _decl(provider="skip_me", locality="us_cloud")])
        providers = {"acme_llm": _spec(), "skip_me": _spec(id="skip_me")}
        rows = sr.eligible_providers(cfg, providers)
        assert [r["provider"] for r in rows] == ["acme_llm"]
        row = rows[0]
        assert row["label"] == "Acme LLM" and row["locality"] == "eu_cloud"
        assert row["network_egress"] == "external"
        assert row["admitted_classes"] == ["INTERNAL", "CONFIDENTIAL"]

    def test_a_narrowed_matrix_changes_the_admitted_classes(self):
        cfg = _cfg(declared=[_decl(locality="local", network_egress="local")])
        cfg["spec"]["data_classification"]["matrix"] = {"INTERNAL": ["local"], "CONFIDENTIAL": ["local"]}
        rows = sr.eligible_providers(cfg, {"acme_llm": _spec()})
        assert rows[0]["admitted_classes"] == ["INTERNAL", "CONFIDENTIAL"]

    def test_no_declaration_gives_an_empty_list_never_a_sample(self):
        assert sr.eligible_providers({}, {"acme_llm": _spec()}) == []

#!/usr/bin/env python3
"""resolve_l34_route: the verdict a spawn site acts on (T-0219 / T-0221, ADR-2268 Decision 3).

Where nothing is rerouted the answer IS the unchanged ``check_l34`` / ``check_l35`` answer, byte
for byte. A turn is rerouted only at a switchable site, with an enabled route, and only if the
second pass (L34 on the provider's current host, L35 on that host) also passes. The audit trail
is read back from the tenant's real hash chain.
"""
from __future__ import annotations

import json
import sys
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
MAIL = "Bitte antworte an jane.doe@example.com"


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
    monkeypatch.setattr(em, "load_providers", lambda force_reload=False: {"acme_llm": _spec()})

    def write(dcfg=None, egress=None):
        spawn_gates._l34_cache.clear()
        if hasattr(spawn_gates, "_l35_cache"):
            spawn_gates._l35_cache.clear()
        spec: dict = {}
        if dcfg is not None:
            spec["data_classification"] = dcfg
        if egress is not None:
            spec["egress"] = egress
        cfg.write_text(yaml.safe_dump({"spec": spec}), encoding="utf-8")
        cfg.chmod(0o600)
    write.home = home
    return write


def _on(**extra):
    return {"sensitive_route": {"enabled": True, "provider": "acme_llm", "model_id": "vendor/Model-1"},
            "provider_compliance": [{"provider": "acme_llm", "locality": "eu_cloud",
                                     "network_egress": "external", "endpoint_host": HOST}], **extra}


def _events(tenant, since: int = 0) -> list[dict]:
    path = dc._guard_audit_chain("acme", tenant.home)
    if not Path(path).is_file():
        return []
    return [json.loads(line) for line in Path(path).read_text(encoding="utf-8").splitlines()[since:] if line.strip()]


def _count(tenant) -> int:
    path = Path(dc._guard_audit_chain("acme", tenant.home))
    return len(path.read_text(encoding="utf-8").splitlines()) if path.is_file() else 0


def _resolve(**kw):
    args = dict(prompt=MAIL, persona="assistant", channel="web", chat_key="c1", switchable=True)
    args.update(kw)
    return spawn_gates.resolve_l34_route("claude_code", "acme", **args)


def _names(events):
    return [e.get("event_type") for e in events]


class TestUnchangedWhereNothingIsRerouted:
    @pytest.mark.parametrize("prompt,classification", [
        ("write a haiku", None), (MAIL, None), ("anything", "PUBLIC"), ("anything", "CONFIDENTIAL"),
        ("anything", "SECRET"),
    ])
    def test_without_a_route_the_answer_is_check_l34_byte_for_byte(self, tenant, prompt, classification):
        tenant({})
        expected = spawn_gates.check_l34("claude_code", "acme", prompt=prompt, persona="assistant",
                                         classification=classification) \
            or spawn_gates.check_l35("claude_code", "acme", persona="assistant")
        got = _resolve(prompt=prompt, classification=classification)
        assert got.refusal == expected
        assert not got.rerouted and got.provider is None and got.model_id is None

    def test_no_policy_file_allows_as_today(self, tenant):
        spawn_gates._l34_cache.clear()
        assert _resolve().refusal is None

    def test_a_route_does_not_touch_a_public_turn(self, tenant):
        tenant(_on())
        got = _resolve(prompt="write a haiku about spring")
        assert got.refusal is None and not got.rerouted

    def test_secret_keeps_todays_refusal(self, tenant):
        tenant(_on())
        key = "key sk-ant-api03-AbCdEfGhIjKlMnOpQrStUvWxYz0123456789"
        got = _resolve(prompt=key)
        assert got.refusal and "SECRET" in got.refusal and not got.rerouted

    def test_the_refusal_text_is_the_unchanged_check_l34_text_for_a_disabled_route(self, tenant):
        tenant({"sensitive_route": {"enabled": False}})
        expected = spawn_gates.check_l34("claude_code", "acme", prompt=MAIL, persona="assistant")
        assert expected and _resolve().refusal == expected


class TestReroute:
    def test_a_switchable_site_gets_the_provider_and_the_verbatim_model(self, tenant):
        tenant(_on())
        got = _resolve()
        assert got.refusal is None and got.rerouted
        assert (got.provider, got.model_id, got.engine_id) == ("acme_llm", "vendor/Model-1", "claude_code")

    def test_the_audit_trail_shows_one_reroute_and_no_block(self, tenant):
        tenant(_on())
        before = _count(tenant)
        _resolve()
        evs = _events(tenant, before)
        names = _names(evs)
        assert names.count("data_flow.rerouted") == 1
        assert "data_flow.blocked" not in names, names
        assert "data_flow.reroute_refused" not in names
        rer = next(e for e in evs if e["event_type"] == "data_flow.rerouted")
        d = rer["details"]
        assert (d["provider"], d["model_id"], d["engine_id"], d["classification"]) == \
            ("acme_llm", "vendor/Model-1", "claude_code", "CONFIDENTIAL")
        assert "jane.doe" not in json.dumps(rer), "the prompt must never reach the audit record"
        approved = [e for e in evs if e["event_type"] == "data_flow.approved"]
        assert [e["details"]["engine_id"] for e in approved] == ["claude_code@acme_llm"]

    def test_a_site_that_cannot_switch_refuses_and_says_why(self, tenant):
        tenant(_on())
        before = _count(tenant)
        got = _resolve(switchable=False)
        assert got.refusal and not got.rerouted
        names = _names(_events(tenant, before))
        assert "data_flow.rerouted" not in names, "no reroute record for a refused site (row R7)"
        refused = [e for e in _events(tenant, before) if e["event_type"] == "data_flow.reroute_refused"]
        assert [e["details"]["reason"] for e in refused] == ["site_not_switchable"]

    def test_the_default_site_is_not_switchable(self, tenant):
        tenant(_on())
        got = spawn_gates.resolve_l34_route("claude_code", "acme", prompt=MAIL, persona="assistant")
        assert got.refusal and not got.rerouted

    def test_l35_judges_the_provider_host_on_the_second_pass(self, tenant):
        tenant(_on(), egress={"enabled": True, "default_action": "deny", "allowed_hosts": ["other.example"],
                              "forbidden_hosts": ["api.anthropic.com"]})
        before = _count(tenant)
        got = _resolve()
        assert got.refusal and "egress" in got.refusal and not got.rerouted
        names = _names(_events(tenant, before))
        assert "data_flow.rerouted" not in names
        assert "data_flow.reroute_refused" in names

    def test_l35_admits_the_provider_host_the_policy_allows(self, tenant):
        tenant(_on(), egress={"enabled": True, "default_action": "deny", "allowed_hosts": [HOST],
                              "forbidden_hosts": ["api.anthropic.com"]})
        got = _resolve()
        assert got.refusal is None and got.rerouted


class TestRefusedRoute:
    def test_an_undeclared_target_is_refused_with_a_reroute_record(self, tenant):
        d = _on()
        d["provider_compliance"] = []
        tenant(d)
        before = _count(tenant)
        got = _resolve()
        assert got.refusal and not got.rerouted
        refused = [e for e in _events(tenant, before) if e["event_type"] == "data_flow.reroute_refused"]
        assert [e["details"]["reason"] for e in refused] == ["not_declared"]

    def test_a_malformed_route_refuses_every_turn(self, tenant):
        tenant({"sensitive_route": {"enabled": "true", "provider": "acme_llm", "model_id": "m"}})
        got = _resolve(prompt="write a haiku about spring")
        assert got.refusal and "malformed" in got.refusal and not got.rerouted

    def test_a_disabled_route_writes_no_reroute_record(self, tenant):
        tenant({"sensitive_route": {"enabled": False}})
        before = _count(tenant)
        _resolve()
        assert not [n for n in _names(_events(tenant, before)) if n.startswith("data_flow.reroute")]


def test_route_events_are_registered_with_a_severity_and_an_allowlist():
    from forge import security_events as se  # type: ignore
    assert se.EVENT_SEVERITY["data_flow.rerouted"] == "INFO"
    assert se.EVENT_SEVERITY["data_flow.reroute_refused"] == "WARNING"
    assert {"provider", "model_id", "engine_id", "classification", "reason"} <= set(se._EVENT_ALLOWLIST["data_flow.rerouted"])
    assert "model_id" not in se._EVENT_ALLOWLIST["data_flow.reroute_refused"]

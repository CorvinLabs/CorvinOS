"""Adversarial and false-negative tests for the sensitive-data route (T-0223, ADR-2268).

Pins four properties, each measured against the real code:

1. The detector sees through character-level obfuscation that costs a human nothing to read
   (zero-width characters, full-width ``@``), and its remaining blind spots are recorded here
   as KNOWN LIMITS instead of being hidden (spelled-out addresses are not caught).
2. A route that cannot be taken never degrades to the default endpoint: no credential, an
   unknown provider, an undeclared one, a model the declaration does not list, a host that
   changed after the declaration - every case raises or refuses; none returns a usable env.
3. A pin on ``claude_code`` does not let a sensitive turn through to the pinned provider: the
   verdict is the reroute's, judged for the route target, not for the pin.
4. The re-supplied history (ADR-2102) is judged by the same detector, so an earlier
   sensitive turn is withheld from a ``claude_code`` view.
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import data_classification as dc  # type: ignore  # noqa: E402
import engine_models as em  # type: ignore  # noqa: E402
import sensitive_detector as sd  # type: ignore  # noqa: E402
import spawn_gates  # type: ignore  # noqa: E402

HOST = "llm.acme.example"
MAIL = "Please reply to jane.doe@example.com"


# ── 1. detector: obfuscation ──────────────────────────────────────────────────

@pytest.mark.parametrize("text", [
    "mail jane.doe​@example.com",           # zero-width space inside the address
    "mail jane‍.doe@example.com",           # zero-width joiner
    "mail jane.doe﻿@example.com",           # BOM / zero-width no-break space
    "mail jane.doe＠example.com",            # full-width commercial at
    "mail ｊane.doe@example.com",                # full-width letter
    "MAIL JANE.DOE@EXAMPLE.COM",
    "DE89 3704 0044​0532 0130 00",          # zero-width space inside an IBAN
    "sk-ant-api03-AbCdEfGhIj​KlMnOpQrStUvWxYz0123456789",
])
def test_character_level_obfuscation_is_still_detected(text):
    assert sd.detect(text), text


@pytest.mark.parametrize("text", [
    "mail jane dot doe at example dot com",
    "mail jane . doe @ example . com",
])
def test_known_limit_spelled_out_addresses_are_not_detected(text):
    """Recorded, not endorsed: a human-readable respelling defeats a regex detector. The
    mitigation is the operator's ``[class:...]`` marker and the matrix, not this detector."""
    assert sd.detect(text) == ()


def test_the_detector_only_ever_raises_a_class():
    assert sd.detect("write a haiku about spring") == ()
    assert sd.detected_class("write a haiku about spring") is None


# ── 2. no silent fallback ─────────────────────────────────────────────────────

def _spec(**kw):
    base = dict(id="acme_llm", label="Acme", base_url=f"https://{HOST}/v1", model_source="static",
                credential_env="ACME_KEY", kind="cloud")
    base.update(kw)
    return em.ProviderSpec(**base)


@pytest.fixture()
def tenant(monkeypatch, tmp_path):
    home = tmp_path / "home"
    cfg = home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    cfg.parent.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("ACME_KEY", "k-test")
    monkeypatch.setenv("ANTHROPIC_BASE_URL", "https://stale.example")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-original")
    state = {"providers": {"acme_llm": _spec()}}
    monkeypatch.setattr(em, "load_providers", lambda force_reload=False: state["providers"])

    def write(*, declared=True, model="vendor/Model-1", provider="acme_llm", pin=None,
              host=HOST, models=None, egress=None):
        spawn_gates._l34_cache.clear()
        if hasattr(spawn_gates, "_l35_cache"):
            spawn_gates._l35_cache.clear()
        d = {"sensitive_route": {"enabled": True, "provider": provider, "model_id": model},
             "provider_compliance": ([{"provider": "acme_llm", "locality": "eu_cloud",
                                       "network_egress": "external", "endpoint_host": host,
                                       **({"models": models} if models else {})}]
                                     if declared else [])}
        spec = {"data_classification": d}
        if pin:
            spec["engine_models"] = {"claude_code": {"provider": pin}}
        if egress is not None:
            spec["egress"] = egress
        cfg.write_text(yaml.safe_dump({"spec": spec}), encoding="utf-8")
        cfg.chmod(0o600)
    write.providers = state
    write.home = home
    return write


def _strict(tenant, **kw):
    return em.resolve_provider_env_strict("acme", kw.get("provider", "acme_llm"),
                                          kw.get("model", "vendor/Model-1"), base_env=dict(kw.get("env", {})))


def test_the_happy_path_control(tenant):
    tenant()
    env, host = _strict(tenant)
    assert host == HOST and env["ANTHROPIC_BASE_URL"].startswith(f"https://{HOST}")
    assert "sk-original" not in env.values() and "stale.example" not in " ".join(env.values())


def test_a_missing_credential_raises_and_never_returns_the_original_key(tenant, monkeypatch):
    tenant()
    monkeypatch.delenv("ACME_KEY")
    with pytest.raises(em.ProviderRouteError):
        _strict(tenant, env={"ANTHROPIC_API_KEY": "sk-original"})


@pytest.mark.parametrize("case", ["unknown_provider", "undeclared", "illegal_model", "host_changed"])
def test_a_route_that_cannot_be_taken_raises(tenant, case):
    if case == "unknown_provider":
        tenant()
        tenant.providers["providers"] = {}
    elif case == "undeclared":
        tenant(declared=False)
    elif case == "illegal_model":
        # a non-platform provider admits any id of the verbatim charset - and nothing else
        tenant()
        with pytest.raises(em.ProviderRouteError):
            _strict(tenant, model="vendor/Model;rm -rf /")
        return
    else:
        tenant(host="old.acme.example")           # the declaration names another host
    with pytest.raises(em.ProviderRouteError):
        _strict(tenant)


def test_resolve_never_reroutes_to_a_us_cloud_or_unknown_locality(tenant):
    for locality in ("us_cloud", "unknown", ""):
        spawn_gates._l34_cache.clear()
        cfg = tenant.home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
        tenant()
        raw = yaml.safe_load(cfg.read_text())
        raw["spec"]["data_classification"]["provider_compliance"][0]["locality"] = locality
        cfg.write_text(yaml.safe_dump(raw))
        cfg.chmod(0o600)
        spawn_gates._l34_cache.clear()
        got = spawn_gates.resolve_l34_route("claude_code", "acme", prompt=MAIL, persona="assistant",
                                            channel="web", chat_key="c", switchable=True)
        assert not got.rerouted, locality
        assert got.refusal, locality


# ── 3. pin conflict ───────────────────────────────────────────────────────────

def test_a_pinned_provider_does_not_decide_a_sensitive_turn(tenant):
    """claude_code is pinned to acme_llm, the route names acme_llm too; the verdict is the
    reroute's. With the route off the pin alone must not admit a CONFIDENTIAL turn."""
    tenant(pin="acme_llm")
    got = spawn_gates.resolve_l34_route("claude_code", "acme", prompt=MAIL, persona="assistant",
                                        channel="web", chat_key="c", switchable=True)
    assert got.rerouted and got.provider == "acme_llm"
    cfg = tenant.home / "tenants" / "acme" / "global" / "tenant.corvin.yaml"
    raw = yaml.safe_load(cfg.read_text())
    raw["spec"]["data_classification"]["sensitive_route"]["enabled"] = False
    cfg.write_text(yaml.safe_dump(raw))
    cfg.chmod(0o600)
    spawn_gates._l34_cache.clear()
    off = spawn_gates.resolve_l34_route("claude_code", "acme", prompt=MAIL, persona="assistant",
                                        channel="web", chat_key="c", switchable=True)
    assert off.refusal and not off.rerouted


def test_a_site_that_cannot_switch_never_spawns_on_the_pin(tenant):
    tenant(pin="acme_llm")
    got = spawn_gates.resolve_l34_route("claude_code", "acme", prompt=MAIL, persona="assistant",
                                        channel="web", chat_key="c", switchable=False)
    assert got.refusal and not got.rerouted


# ── 4. history ────────────────────────────────────────────────────────────────

def test_the_history_gate_withholds_a_sensitive_turn_from_claude_code(tenant):
    """ADR-2102: each recorded turn is judged on its own against the engine that will read it.
    The detector is on for this tenant, so the address turn is withheld, a public one is not."""
    tenant()
    assert spawn_gates.detector_on("acme") is True
    spawn_gates._l34_cache.clear()
    assert spawn_gates.check_l34("claude_code", "acme", prompt=MAIL, persona="assistant",
                                 channel="web", chat_key="c"), "address turn must be refused"
    assert spawn_gates.check_l34("claude_code", "acme", prompt="write a haiku", persona="assistant",
                                 channel="web", chat_key="c") is None

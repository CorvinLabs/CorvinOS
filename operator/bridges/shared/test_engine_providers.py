"""ADR-0181 — provider abstraction + live model fetch."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import engine_models as EM  # type: ignore
import engine_providers as EP  # type: ignore


# ── registry: providers + supported_providers ───────────────────────────────────

def test_providers_registry_loaded():
    prov = EM.providers_as_dict(force_reload=True)
    assert set(prov) == {"anthropic", "openai", "ollama_local", "ollama_cloud", "openrouter"}
    assert prov["openrouter"]["kind"] == "cloud"
    assert prov["ollama_local"]["kind"] == "local"
    # credential_env is a NAME, never a secret value
    assert prov["openrouter"]["credential_env"] == "OPENROUTER_API_KEY"


def test_supported_providers_per_engine():
    reg = EM.registry_as_dict(force_reload=True)
    cc = {p["provider"]: p for p in reg["claude_code"]["supported_providers"]}
    assert cc["anthropic"]["native"] is True
    assert cc["ollama_local"]["native"] is False   # via built-in translating proxy
    assert cc["ollama_cloud"]["native"] is False   # via built-in translating proxy
    assert cc["openrouter"]["native"] is False      # via built-in translating proxy
    oc = [p["provider"] for p in reg["opencode"]["supported_providers"]]
    assert set(oc) == {"anthropic", "openai", "ollama_local", "ollama_cloud", "openrouter"}
    assert reg["copilot"]["supported_providers"] == []


# ── live model fetch ─────────────────────────────────────────────────────────────

def test_fetch_static_provider_has_no_live_list():
    r = EP.fetch_models("anthropic", base_url="https://api.anthropic.com", model_source="static")
    assert r["reachable"] is True and r["models"] == [] and "static" in r["note"]


def test_fetch_ollama(monkeypatch):
    monkeypatch.setattr(EP, "_get_json",
                        lambda url, **k: {"models": [{"name": "qwen3:8b"}, {"name": "llama3"}]})
    r = EP.fetch_models("ollama_local", base_url="http://x:11434", model_source="ollama")
    assert r["reachable"] and r["count"] == 2
    assert [m["id"] for m in r["models"]] == ["qwen3:8b", "llama3"]


def test_fetch_openrouter(monkeypatch):
    monkeypatch.setattr(EP, "_get_json",
                        lambda url, **k: {"data": [{"id": "anthropic/claude-sonnet-5", "name": "Sonnet 5"}]})
    r = EP.fetch_models("openrouter", base_url="https://openrouter.ai/api/v1", model_source="openrouter")
    assert r["reachable"] and r["count"] == 1
    assert r["models"][0]["id"] == "anthropic/claude-sonnet-5"
    assert r["models"][0]["label"] == "Sonnet 5"


def test_fetch_unreachable_is_clean(monkeypatch):
    def boom(url, **k):
        raise OSError("connection refused")
    monkeypatch.setattr(EP, "_get_json", boom)
    r = EP.fetch_models("ollama_cloud", base_url="https://ollama.com", model_source="ollama")
    assert r["reachable"] is False and r["models"] == [] and "unreachable" in r["error"]


def test_fetch_http_401_hints_api_key(monkeypatch):
    import urllib.error
    def unauth(url, **k):
        raise urllib.error.HTTPError(url, 401, "Unauthorized", {}, None)  # type: ignore[arg-type]
    monkeypatch.setattr(EP, "_get_json", unauth)
    r = EP.fetch_models("openrouter", base_url="https://openrouter.ai/api/v1", model_source="openrouter")
    assert r["reachable"] is False and "API key" in (r["error"] or "")


def test_fetch_credential_read_from_env_name(monkeypatch):
    captured = {}
    def cap(url, *, bearer="", **k):
        captured["bearer"] = bearer
        return {"data": []}
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-secret")
    monkeypatch.setattr(EP, "_get_json", cap)
    EP.fetch_models("openrouter", base_url="https://openrouter.ai/api/v1",
                    model_source="openrouter", credential_env="OPENROUTER_API_KEY")
    assert captured["bearer"] == "sk-or-secret"   # read from the NAMED env var


def test_fetch_credential_read_from_service_env_not_just_process_env(tmp_path, monkeypatch):
    """Regression: fetch_models used to read bare os.environ[credential_env]
    only — a key an operator just saved through Settings -> API Keys (which
    writes to service.env) was invisible to an already-running console
    process until it was restarted. It must resolve via provider_keys
    (env override first, then service.env) instead."""
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
    import provider_keys  # type: ignore
    provider_keys.write_key("openrouter_api_key", "sk-or-from-service-env",
                            path_override=tmp_path / "service.env")

    captured = {}
    def cap(url, *, bearer="", **k):
        captured["bearer"] = bearer
        return {"data": []}
    monkeypatch.setattr(EP, "_get_json", cap)
    EP.fetch_models("openrouter", base_url="https://openrouter.ai/api/v1",
                    model_source="openrouter", credential_env="OPENROUTER_API_KEY")
    assert captured["bearer"] == "sk-or-from-service-env"


def test_bad_reload_does_not_wipe_good_cache(tmp_path):
    """Review MEDIUM: a transient unreadable file on force_reload must not clobber
    a previously-good cache."""
    import importlib
    importlib.reload(EM)
    EM.load_registry(force_reload=True)
    good = len(EM.load_providers())
    assert good == 5
    orig = EM._REGISTRY_FILE
    try:
        EM._REGISTRY_FILE = tmp_path / "missing.yaml"
        EM.load_registry(force_reload=True)          # unreadable
        assert len(EM.load_providers()) == good      # preserved, not wiped
        assert len(EM.load_registry()) > 0
    finally:
        EM._REGISTRY_FILE = orig
        EM.load_registry(force_reload=True)


def test_fetch_skips_nondict_items_without_discarding_list(monkeypatch):
    """Review LOW: one malformed item must not throw away the whole list."""
    monkeypatch.setattr(EP, "_get_json",
                        lambda url, **k: {"models": [{"name": "ok"}, "garbage", {"noname": 1}]})
    r = EP.fetch_models("ollama_local", base_url="http://x:11434", model_source="ollama")
    assert r["reachable"] and [m["id"] for m in r["models"]] == ["ok"]


# ── M3: provider egress resolution ───────────────────────────────────────────────

def test_resolve_engine_egress_host_from_provider(monkeypatch):
    """A per-tenant provider assignment resolves the egress host to the provider
    (the L35 fix: hermes→ollama_cloud must resolve to ollama.com, not localhost)."""
    monkeypatch.setattr(EM, "_load_tenant_spec",
                        lambda tid: {"engine_models": {"hermes": {"provider": "ollama_cloud"}}})
    host = EM.resolve_engine_egress_host("_default", "hermes")
    assert host == "ollama.com"


def test_resolve_egress_host_prefers_proxy(monkeypatch):
    monkeypatch.setattr(EM, "_load_tenant_spec",
                        lambda tid: {"engine_models": {"claude_code": {"provider": "openrouter"}}})
    # provider with a proxy endpoint → egress goes to the proxy host
    provs = dict(EM.load_providers(force_reload=True))
    provs["openrouter"] = EM.ProviderSpec(
        id="openrouter", label="OpenRouter", base_url="https://openrouter.ai/api/v1",
        model_source="openrouter", credential_env="OPENROUTER_API_KEY", kind="cloud",
        proxy_base_url="https://proxy.internal/anthropic")
    monkeypatch.setattr(EM, "load_providers", lambda force_reload=False: provs)
    assert EM.resolve_engine_egress_host("_default", "claude_code") == "proxy.internal"


def test_resolve_egress_none_without_provider(monkeypatch):
    monkeypatch.setattr(EM, "_load_tenant_spec", lambda tid: {"engine_models": {}})
    assert EM.resolve_engine_egress_host("_default", "claude_code") is None


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))

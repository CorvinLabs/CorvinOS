"""test_adapter_openrouter_routing.py — ADR-0181 M3 provider-based routing
branch of adapter._build_spawn_env, specifically the OpenRouter/Ollama
"no model selected" edge case (adversarial review, 2026-07-14).

Regression: when a provider's model_source is "openrouter" and no os_model
is configured for claude_code, the code logged "no safe default exists —
pick a model" but then proceeded anyway with model="auto" -- not a valid
OpenRouter model id (the real slug is "openrouter/auto") -- causing every
subsequent turn to fail with an opaque upstream 400 instead of failing fast
with the already-detected clear error.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import adapter  # type: ignore  # noqa: E402
import engine_models  # type: ignore  # noqa: E402


def _provider_spec(**overrides):
    kwargs = dict(
        id="openrouter", label="OpenRouter", base_url="https://openrouter.ai/api/v1",
        model_source="openrouter", credential_env="OPENROUTER_API_KEY",
        kind="cloud", proxy_base_url="",
    )
    kwargs.update(overrides)
    return engine_models.ProviderSpec(**kwargs)


def test_openrouter_no_model_selected_does_not_start_proxy_with_auto(monkeypatch):
    monkeypatch.setattr(engine_models, "get_tenant_engine_provider",
                         lambda tid, engine_id: "openrouter")
    monkeypatch.setattr(engine_models, "get_tenant_engine_model",
                         lambda tid, engine_id, role: None)
    monkeypatch.setattr(engine_models, "load_providers",
                         lambda: {"openrouter": _provider_spec()})
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    calls = []
    import anthropic_openai_bridge  # type: ignore
    def _fake_ensure_proxy(target):
        calls.append(target.model)
        return "http://127.0.0.1:9/should-not-be-called"
    monkeypatch.setattr(anthropic_openai_bridge, "ensure_proxy", _fake_ensure_proxy)

    env = adapter._build_spawn_env(bridge="discord", chat_key="chat-1", base={})

    assert calls == [], (
        f"ensure_proxy must not be called with a bogus model when none is "
        f"configured, but was called with: {calls}"
    )
    assert "ANTHROPIC_BASE_URL" not in env, (
        "with no safe model, CC must fall through to its existing routing "
        "instead of being redirected to a proxy that will fail every call"
    )


def test_openrouter_with_model_selected_starts_proxy_with_real_model(monkeypatch):
    monkeypatch.setattr(engine_models, "get_tenant_engine_provider",
                         lambda tid, engine_id: "openrouter")
    monkeypatch.setattr(engine_models, "get_tenant_engine_model",
                         lambda tid, engine_id, role: "anthropic/claude-3.5-sonnet")
    monkeypatch.setattr(engine_models, "load_providers",
                         lambda: {"openrouter": _provider_spec()})
    monkeypatch.setenv("OPENROUTER_API_KEY", "sk-or-test")

    calls = []
    import anthropic_openai_bridge  # type: ignore
    def _fake_ensure_proxy(target):
        calls.append(target.model)
        return "http://127.0.0.1:12345"
    monkeypatch.setattr(anthropic_openai_bridge, "ensure_proxy", _fake_ensure_proxy)

    env = adapter._build_spawn_env(bridge="discord", chat_key="chat-2", base={})

    assert calls == ["anthropic/claude-3.5-sonnet"]
    assert env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:12345"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

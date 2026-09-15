"""test_adapter_openrouter_routing.py — ADR-0181 M3 provider-based routing
branch of adapter._build_spawn_env, specifically the OpenRouter/Ollama
"no model selected" edge case (adversarial review, 2026-07-14).

Regression: when a provider's model_source is "openrouter" and no os_model
is configured for claude_code, the code logged "no safe default exists —
pick a model" but then proceeded anyway with model="auto" -- not a valid
OpenRouter model id (the real slug is "openrouter/auto") -- causing every
subsequent turn to fail with an opaque upstream 400 instead of failing fast
with the already-detected clear error.

All tests here monkeypatch ``adapter._read_cc_local_cfg`` to always return
None. Without it, these tests are NOT hermetic: on any host with a real
``claude_code_local`` (ADR-0126) redirect enabled in ``~/.corvin`` (a
genuinely common local-dev setup for this project), ``_build_spawn_env``
takes the ``if _cc_cfg:`` branch before ever reaching the provider-routing
code under test, and both tests fail against ambient machine state instead
of exercising the intended branch (found during adversarial review,
2026-07-14 — reproduced live on a dev machine with local mode configured).
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
    monkeypatch.setattr(adapter, "_read_cc_local_cfg", lambda tid: None)
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
    monkeypatch.setattr(adapter, "_read_cc_local_cfg", lambda tid: None)
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


def test_acs_manager_worker_redirect_shares_adapter_ssot(monkeypatch):
    """acs_runtime._apply_provider_redirect must go through the exact same
    engine_models.resolve_claude_code_provider_env adapter._build_spawn_env
    uses (adversarial review, 2026-07-14) — before this fix acs_runtime.py
    had its own copy that read the credential via a bare os.environ.get
    (missing a key an operator just saved through Settings -> API Keys until
    the daemon restarted) and never started the translating proxy for
    ollama/openrouter, pointing ANTHROPIC_BASE_URL straight at their
    OpenAI-format base_url — which claude_code cannot speak — breaking every
    ACS-delegated (manager/worker) turn for exactly those providers."""
    import acs_runtime  # type: ignore

    monkeypatch.setattr(engine_models, "get_tenant_engine_provider",
                         lambda tid, engine_id: "openrouter")
    monkeypatch.setattr(engine_models, "get_tenant_engine_model",
                         lambda tid, engine_id, role: "anthropic/claude-3.5-sonnet")
    monkeypatch.setattr(engine_models, "load_providers",
                         lambda: {"openrouter": _provider_spec()})
    # The credential is saved to service.env, NOT exported into this
    # process's os.environ — proving resolution goes through
    # provider_keys.resolve_by_env_var (env, then service.env) rather than
    # a bare os.environ.get, which would find nothing and silently redirect
    # with a placeholder key.
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    import provider_keys  # type: ignore
    monkeypatch.setattr(provider_keys, "resolve_by_env_var",
                         lambda env_var: "sk-or-from-service-env" if env_var == "OPENROUTER_API_KEY" else None)

    calls = []
    import anthropic_openai_bridge  # type: ignore
    def _fake_ensure_proxy(target):
        calls.append((target.model, target.api_key))
        return "http://127.0.0.1:23456"
    monkeypatch.setattr(anthropic_openai_bridge, "ensure_proxy", _fake_ensure_proxy)

    env: dict[str, str] = {}
    acs_runtime._apply_provider_redirect(env, "_default")

    assert calls == [("anthropic/claude-3.5-sonnet", "sk-or-from-service-env")], (
        "the ACS manager/worker path must start the same translating proxy "
        "with the same live-resolved credential the OS-turn path does"
    )
    assert env.get("ANTHROPIC_BASE_URL") == "http://127.0.0.1:23456"
    assert env.get("ANTHROPIC_API_KEY") == "sk-or-from-service-env"
    assert env.get("CORVIN_CC_PROVIDER") == "openrouter"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))

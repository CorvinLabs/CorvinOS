#!/usr/bin/env python3
"""test_openrouter_ollama_keys.py — OpenRouter/Ollama BYOK + provider-routing wiring.

Feature request: an operator must be able to save an OpenRouter or Ollama
Cloud API key under Settings -> API Keys, and then run the "Claude Code"
engine through that provider (Engines page -> Provider dropdown, ADR-0181).

Before this fix:
  - "openrouter_api_key"/"ollama_api_key" were not valid BYOK key names
    (operator/agent/byok.py::validate_key_name rejected them), and the
    console's API Keys page had no fields for them at all.
  - Even if a value ended up in service.env by hand, adapter.py's
    claude_code provider-routing read the credential via bare
    os.environ.get(...) — a key saved live through the console (which
    writes to service.env, not the running bridge daemon's os.environ)
    would be invisible until the daemon was restarted.

This file covers the key-storage half end-to-end. It does NOT cover (and
this fix does NOT build) the Anthropic-Messages<->OpenAI-format translating
proxy that OpenRouter/raw-Ollama would still need for Claude Code to
actually authenticate a real request — see ADR-0181's own "HONEST REMAINING
REQUIREMENT" note; that's a separate, larger piece of work.
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

_REPO = Path(__file__).resolve().parents[1]
for _p in (
    _REPO,
    _REPO / "operator",
    _REPO / "operator" / "bridges",
    _REPO / "operator" / "bridges" / "shared",
):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))

from agent import byok as agent_byok  # type: ignore  # operator/agent/byok.py
import provider_keys  # type: ignore  # operator/bridges/shared/provider_keys.py


# ── byok.py: the new key names are now valid ───────────────────────────────


def test_openrouter_key_name_is_valid():
    agent_byok.validate_key_name("openrouter_api_key")  # must not raise


def test_ollama_key_name_is_valid():
    agent_byok.validate_key_name("ollama_api_key")  # must not raise


def test_openrouter_key_shape_enforced():
    with pytest.raises(ValueError):
        agent_byok._check_key_shape("openrouter_api_key", "not-a-real-key")
    agent_byok._check_key_shape("openrouter_api_key", "sk-or-v1-abc123")  # must not raise


def test_ollama_key_has_no_shape_restriction():
    # Ollama Cloud has no single documented key prefix — any non-empty
    # value must be accepted (mirrors stt_local_whisper_api_key today).
    agent_byok._check_key_shape("ollama_api_key", "anything-goes-here")


# ── provider_keys.py: round-trip through service.env with the EXACT env-var
# names engine_model_registry.yaml's credential_env fields expect ─────────


def _isolated_service_env(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VOICE_CONFIG_DIR", str(tmp_path))
    for var in ("OPENROUTER_API_KEY", "OLLAMA_API_KEY", "XDG_CONFIG_HOME"):
        monkeypatch.delenv(var, raising=False)


def test_openrouter_key_round_trips_to_canonical_env_var(tmp_path, monkeypatch):
    _isolated_service_env(tmp_path, monkeypatch)
    service_env = tmp_path / "service.env"
    provider_keys.write_key("openrouter_api_key", "sk-or-v1-test123", path_override=service_env)
    assert "OPENROUTER_API_KEY=sk-or-v1-test123" in service_env.read_text()
    assert provider_keys.resolve_key("openrouter_api_key") == "sk-or-v1-test123"


def test_ollama_key_round_trips_to_canonical_env_var(tmp_path, monkeypatch):
    _isolated_service_env(tmp_path, monkeypatch)
    service_env = tmp_path / "service.env"
    provider_keys.write_key("ollama_api_key", "ollama-cloud-secret", path_override=service_env)
    assert "OLLAMA_API_KEY=ollama-cloud-secret" in service_env.read_text()
    assert provider_keys.resolve_key("ollama_api_key") == "ollama-cloud-secret"


def test_resolve_by_env_var_matches_credential_env_names(tmp_path, monkeypatch):
    """These exact strings ("OPENROUTER_API_KEY", "OLLAMA_API_KEY") are what
    operator/bundle/config-templates/engine_model_registry.yaml declares as
    credential_env for the openrouter / ollama_cloud providers — the whole
    point of resolve_by_env_var is that adapter.py can look a value up by
    THAT string without knowing the logical BYOK key name."""
    _isolated_service_env(tmp_path, monkeypatch)
    service_env = tmp_path / "service.env"
    provider_keys.write_key("openrouter_api_key", "sk-or-v1-abc", path_override=service_env)
    assert provider_keys.resolve_by_env_var("OPENROUTER_API_KEY") == "sk-or-v1-abc"
    assert provider_keys.resolve_by_env_var("OLLAMA_API_KEY") is None  # not written yet


def test_resolve_by_env_var_unknown_name_returns_none():
    assert provider_keys.resolve_by_env_var("SOME_RANDOM_ENV_VAR") is None


def test_resolve_by_env_var_prefers_live_process_env_over_file(tmp_path, monkeypatch):
    """A key saved moments ago via the console (service.env) must be picked
    up even though the bridge daemon's own os.environ still lacks it — but
    an EXPLICIT env var override (operator-set, e.g. in a systemd unit) must
    still win, matching resolve_key's documented precedence."""
    _isolated_service_env(tmp_path, monkeypatch)
    service_env = tmp_path / "service.env"
    provider_keys.write_key("openrouter_api_key", "from-file", path_override=service_env)
    assert provider_keys.resolve_by_env_var("OPENROUTER_API_KEY") == "from-file"
    monkeypatch.setenv("OPENROUTER_API_KEY", "from-env")
    assert provider_keys.resolve_by_env_var("OPENROUTER_API_KEY") == "from-env"


# ── console route: /byok/secrets now reports presence for both new keys ──


def test_console_byok_route_lists_openrouter_and_ollama():
    import importlib
    byok_route = importlib.import_module("corvin_console.routes.byok")
    src = Path(byok_route.__file__).read_text()
    assert '"openrouter_api_key"' in src
    assert '"ollama_api_key"' in src


# ── adapter.py: claude_code provider routing resolves the LIVE value ─────


def test_build_spawn_env_resolves_provider_credential_via_resolve_by_env_var(monkeypatch):
    """Regression: _build_spawn_env used to read os.environ.get(credential_env)
    directly. A key saved live through the console (which writes to
    service.env) would be invisible to an already-running bridge daemon
    until it was restarted. It must now go through
    provider_keys.resolve_by_env_var, which re-reads service.env every call."""
    with tempfile.TemporaryDirectory(prefix="adapter-provider-route-") as tmp:
        home = Path(tmp)
        prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        try:
            try:
                import adapter  # type: ignore  # noqa: PLC0415
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"adapter import unavailable: {e}")

            class _FakeProviderSpec:
                proxy_base_url = "http://localhost:9999"
                base_url = "http://localhost:9999"
                credential_env = "OPENROUTER_API_KEY"

            with (
                mock.patch.object(
                    adapter, "_provider_keys",
                    mock.Mock(resolve_by_env_var=mock.Mock(return_value="sk-or-v1-live-value")),
                ),
            ):
                # Patch the lazily-imported engine_models functions the way
                # _build_spawn_env imports them (`from engine_models import ...`).
                import engine_models  # type: ignore  # noqa: PLC0415
                with (
                    mock.patch.object(engine_models, "get_tenant_engine_provider", return_value="openrouter"),
                    mock.patch.object(engine_models, "load_providers", return_value={"openrouter": _FakeProviderSpec()}),
                ):
                    env = adapter._build_spawn_env(
                        bridge="discord", chat_key="chat-provider-test",
                        base={"PATH": "/usr/bin"},
                        profile=None,
                    )
            assert env.get("ANTHROPIC_API_KEY") == "sk-or-v1-live-value", (
                "must use the LIVE-resolved credential, not a stale/absent os.environ read"
            )
            assert env.get("CORVIN_CC_PROVIDER") == "openrouter"
            assert env.get("ANTHROPIC_BASE_URL") == "http://localhost:9999"
        finally:
            if prev_home is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = prev_home


def test_build_spawn_env_auto_starts_local_proxy_when_no_proxy_base_url_configured():
    """The actual feature ask: with NO operator-configured proxy_base_url, an
    OpenAI-format provider (ollama_local/ollama_cloud/openrouter) must get the
    built-in translating proxy auto-started and used — not base_url directly
    (which doesn't speak the Anthropic Messages API at all) and not a no-op."""
    import http.server
    import json as _json
    import threading
    import urllib.request

    class _FakeOllama(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            length = int(self.headers.get("Content-Length", "0"))
            body = _json.loads(self.rfile.read(length) or b"{}")
            assert body["model"] == "qwen3:8b"
            resp = {"choices": [{"message": {"role": "assistant", "content": "hi"}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 1, "completion_tokens": 1}}
            payload = _json.dumps(resp).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    fake = http.server.HTTPServer(("127.0.0.1", 0), _FakeOllama)
    fake_thread = threading.Thread(target=fake.serve_forever, daemon=True)
    fake_thread.start()
    fake_base = f"http://127.0.0.1:{fake.server_address[1]}"

    with tempfile.TemporaryDirectory(prefix="adapter-auto-proxy-") as tmp:
        home = Path(tmp)
        prev_home = os.environ.get("CORVIN_HOME")
        os.environ["CORVIN_HOME"] = str(home)
        try:
            try:
                import adapter  # type: ignore  # noqa: PLC0415
                import anthropic_openai_bridge  # type: ignore  # noqa: PLC0415
            except Exception as e:  # noqa: BLE001
                pytest.skip(f"adapter/bridge import unavailable: {e}")

            class _FakeOllamaLocalSpec:
                proxy_base_url = ""  # no operator override — must auto-start
                base_url = fake_base
                model_source = "ollama"
                credential_env = ""  # ollama_local needs no key

            import engine_models  # type: ignore  # noqa: PLC0415
            with (
                mock.patch.object(engine_models, "get_tenant_engine_provider", return_value="ollama_local"),
                mock.patch.object(engine_models, "get_tenant_engine_model", return_value=""),
                mock.patch.object(engine_models, "load_providers", return_value={"ollama_local": _FakeOllamaLocalSpec()}),
            ):
                env = adapter._build_spawn_env(
                    bridge="discord", chat_key="chat-auto-proxy-test",
                    base={"PATH": "/usr/bin"},
                    profile=None,
                )

            base_url = env.get("ANTHROPIC_BASE_URL", "")
            assert base_url.startswith("http://127.0.0.1:"), (
                f"expected a local auto-started proxy URL, got {base_url!r}"
            )
            assert base_url != fake_base, "must NOT be the raw Ollama base_url (wrong API format)"

            # Prove the returned base URL is a genuinely working Anthropic-format
            # endpoint by sending it a real Anthropic-shaped request.
            req_body = _json.dumps({
                "model": "claude-sonnet-5", "max_tokens": 50,
                "messages": [{"role": "user", "content": "hi"}],
            }).encode("utf-8")
            req = urllib.request.Request(
                f"{base_url}/v1/messages", data=req_body,
                headers={"Content-Type": "application/json"}, method="POST",
            )
            with urllib.request.urlopen(req, timeout=5) as resp:
                out = _json.loads(resp.read().decode("utf-8"))
            assert out["type"] == "message"
            assert out["content"] == [{"type": "text", "text": "hi"}]
        finally:
            anthropic_openai_bridge.shutdown_all()
            fake.shutdown()
            fake_thread.join(timeout=2)
            if prev_home is None:
                os.environ.pop("CORVIN_HOME", None)
            else:
                os.environ["CORVIN_HOME"] = prev_home


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))

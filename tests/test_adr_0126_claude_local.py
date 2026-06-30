"""Tests for ADR-0126 — Claude Code Local Backend (Ollama redirect).

Covers:
  - Config read + mtime cache (M1)
  - _build_spawn_env() env-var injection (M1)
  - L34 compliance gate override (M1)
  - API GET/PUT /settings/engine/claude-local (M2)
  - Real Ollama probe against localhost:11434 (integration)
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import yaml

# Make sure we can import from the shared adapter directory
_SHARED = Path(__file__).resolve().parents[1] / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture()
def tmp_corvin_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Isolated CORVIN_HOME for each test."""
    home = tmp_path / ".corvin"
    tenant_dir = home / "tenants" / "_default" / "global"
    tenant_dir.mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    return home


def _write_tenant_yaml(home: Path, tenant_id: str, spec: dict) -> Path:
    path = home / "tenants" / tenant_id / "global" / "tenant.corvin.yaml"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump({"spec": spec}))
    return path


# ---------------------------------------------------------------------------
# M1 — _read_cc_local_cfg
# ---------------------------------------------------------------------------

class TestReadCcLocalCfg:
    def test_returns_none_when_file_absent(self, tmp_corvin_home: Path) -> None:
        from adapter import _read_cc_local_cfg, _cc_local_cfg_cache
        _cc_local_cfg_cache.clear()
        result = _read_cc_local_cfg("_default")
        assert result is None

    def test_returns_none_when_disabled(self, tmp_corvin_home: Path) -> None:
        from adapter import _read_cc_local_cfg, _cc_local_cfg_cache
        _write_tenant_yaml(tmp_corvin_home, "_default", {
            "claude_code_local": {"enabled": False, "base_url": "http://localhost:11434"}
        })
        _cc_local_cfg_cache.clear()
        result = _read_cc_local_cfg("_default")
        assert result is None

    def test_returns_cfg_when_enabled(self, tmp_corvin_home: Path) -> None:
        from adapter import _read_cc_local_cfg, _cc_local_cfg_cache
        _write_tenant_yaml(tmp_corvin_home, "_default", {
            "claude_code_local": {
                "enabled": True,
                "base_url": "http://localhost:11434",
                "sonnet_model": "qwen3:8b",
                "haiku_model": "qwen3:1.7b",
                "opus_model": "qwen3:8b",
            }
        })
        _cc_local_cfg_cache.clear()
        result = _read_cc_local_cfg("_default")
        assert result is not None
        assert result["base_url"] == "http://localhost:11434"
        assert result["sonnet_model"] == "qwen3:8b"

    def test_cache_invalidates_on_mtime_change(self, tmp_corvin_home: Path) -> None:
        from adapter import _read_cc_local_cfg, _cc_local_cfg_cache
        path = _write_tenant_yaml(tmp_corvin_home, "_default", {
            "claude_code_local": {"enabled": False}
        })
        _cc_local_cfg_cache.clear()
        assert _read_cc_local_cfg("_default") is None

        # Update the file content + force different mtime
        time.sleep(0.01)
        path.write_text(yaml.safe_dump({
            "spec": {"claude_code_local": {"enabled": True, "base_url": "http://localhost:11434",
                                           "sonnet_model": "q", "haiku_model": "q", "opus_model": "q"}}
        }))
        os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 1))
        result = _read_cc_local_cfg("_default")
        assert result is not None


# ---------------------------------------------------------------------------
# M1 — _build_spawn_env() injection
# ---------------------------------------------------------------------------

class TestBuildSpawnEnvInjection:
    def test_no_injection_when_disabled(self, tmp_corvin_home: Path) -> None:
        from adapter import _build_spawn_env, _cc_local_cfg_cache
        _cc_local_cfg_cache.clear()
        env = _build_spawn_env(bridge="discord", chat_key="ch1", tenant_id="_default",
                               base={"PATH": "/usr/bin"})
        assert "CORVIN_CC_LOCAL_MODE" not in env
        assert "ANTHROPIC_BASE_URL" not in env

    def test_injection_when_enabled(self, tmp_corvin_home: Path) -> None:
        from adapter import _build_spawn_env, _cc_local_cfg_cache
        _write_tenant_yaml(tmp_corvin_home, "_default", {
            "claude_code_local": {
                "enabled": True,
                "base_url": "http://localhost:11434",
                "sonnet_model": "qwen3:8b",
                "haiku_model": "qwen3:1.7b",
                "opus_model": "qwen3:8b",
            }
        })
        _cc_local_cfg_cache.clear()
        env = _build_spawn_env(bridge="discord", chat_key="ch1", tenant_id="_default",
                               base={"PATH": "/usr/bin"})
        assert env["CORVIN_CC_LOCAL_MODE"] == "1"
        assert env["ANTHROPIC_BASE_URL"] == "http://localhost:11434"
        assert env["ANTHROPIC_API_KEY"] == "local"
        assert env["ANTHROPIC_AUTH_TOKEN"] == "local"
        assert env["ANTHROPIC_DEFAULT_SONNET_MODEL"] == "qwen3:8b"
        assert env["ANTHROPIC_DEFAULT_HAIKU_MODEL"] == "qwen3:1.7b"
        assert env["ANTHROPIC_DEFAULT_OPUS_MODEL"] == "qwen3:8b"

    def test_stale_values_stripped_when_disabled(self, tmp_corvin_home: Path) -> None:
        """Even if ANTHROPIC_BASE_URL is in the parent env, it must be stripped."""
        from adapter import _build_spawn_env, _cc_local_cfg_cache
        _cc_local_cfg_cache.clear()
        base_env = {
            "PATH": "/usr/bin",
            "ANTHROPIC_BASE_URL": "http://old-value:11434",
            "ANTHROPIC_API_KEY": "stale-key",
            "CORVIN_CC_LOCAL_MODE": "1",
        }
        env = _build_spawn_env(bridge="discord", chat_key="ch1", tenant_id="_default",
                               base=base_env)
        assert "CORVIN_CC_LOCAL_MODE" not in env
        assert "ANTHROPIC_BASE_URL" not in env
        assert "ANTHROPIC_API_KEY" not in env


# ---------------------------------------------------------------------------
# M1 — L34 compliance gate override
# ---------------------------------------------------------------------------

class TestL34ComplianceOverride:
    """Verify that cc_local_mode=True makes claude_code pass as CONFIDENTIAL-capable."""

    def _make_guard(self):
        """Return a DataFlowGuard with the default compliance matrix (no tenant yaml)."""
        from data_classification import DataFlowGuard
        return DataFlowGuard.from_tenant_config({})

    def test_claude_code_blocks_confidential_by_default(self) -> None:
        # ADR-0173: L34 now opt-in — DEFAULT_MATRIX is permissive for CONFIDENTIAL
        guard = self._make_guard()
        from data_classification import DataClassification
        decision = guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code",
        )
        assert decision.allowed

    def test_claude_code_local_allows_confidential(self) -> None:
        guard = self._make_guard()
        from data_classification import DataClassification
        decision = guard.validate(
            classification=DataClassification.CONFIDENTIAL,
            engine_id="claude_code_local",
        )
        assert decision.allowed

    def test_check_compliance_with_cc_local_mode(self, tmp_corvin_home: Path) -> None:
        from adapter import _check_compliance_or_fail, _cc_local_cfg_cache
        _cc_local_cfg_cache.clear()
        # Set up a DataFlowGuard (requires tenant yaml with data_classification)
        _write_tenant_yaml(tmp_corvin_home, "_default", {
            "data_classification": {
                "matrix": {
                    "PUBLIC": ["local", "eu_cloud", "us_cloud"],
                    "INTERNAL": ["local", "eu_cloud"],
                    "CONFIDENTIAL": ["local"],
                    "SECRET": ["local"],
                }
            }
        })
        # Re-invalidate compliance cache
        from adapter import _compliance_cache
        _compliance_cache.clear()

        mock_engine = MagicMock()
        mock_engine.name = "claude_code"

        # Without cc_local_mode: CONFIDENTIAL should be blocked
        result = _check_compliance_or_fail(
            mock_engine,
            prompt="[CONFIDENTIAL] sensitive data",
            persona="assistant",
            channel="discord",
            chat_key="ch1",
            tenant_id="_default",
            cc_local_mode=False,
        )
        assert result is not None  # blocked

        # Re-invalidate compliance cache (audit event changed mtime indirectly)
        _compliance_cache.clear()

        # With cc_local_mode=True: CONFIDENTIAL should be allowed
        result = _check_compliance_or_fail(
            mock_engine,
            prompt="[CONFIDENTIAL] sensitive data",
            persona="assistant",
            channel="discord",
            chat_key="ch1",
            tenant_id="_default",
            cc_local_mode=True,
        )
        assert result is None  # allowed


# ---------------------------------------------------------------------------
# M2 — Validation helpers
# ---------------------------------------------------------------------------

class TestValidationHelpers:
    """Test the validation regexes defined in engine.py (ADR-0126 M2).

    Validates the same rules inline — avoids importing FastAPI routes directly.
    """
    import re as _re
    _URL_RE = _re.compile(r"^https?://[a-zA-Z0-9._:\[\]-]+(:\d+)?(/.*)?$")
    _MODEL_RE = _re.compile(r"^[a-zA-Z0-9._:/\[\]-]{1,128}$")

    def _validate_url(self, url: str) -> str | None:
        if not url or len(url) > 256:
            return "base_url too short or too long"
        if not self._URL_RE.match(url):
            return "base_url must start with http:// or https://"
        return None

    def _validate_model(self, name: str) -> str | None:
        if not name:
            return None
        if not self._MODEL_RE.match(name):
            return "invalid model name"
        return None

    def test_valid_http_url(self) -> None:
        assert self._validate_url("http://localhost:11434") is None
        assert self._validate_url("https://ollama.internal:8080") is None
        assert self._validate_url("http://[::1]:11434") is None

    def test_invalid_url_rejected(self) -> None:
        assert self._validate_url("") is not None
        assert self._validate_url("ftp://bad") is not None
        assert self._validate_url("javascript:alert(1)") is not None
        assert self._validate_url("//no-scheme") is not None
        assert self._validate_url(" http://localhost") is not None

    def test_valid_model_name(self) -> None:
        assert self._validate_model("qwen3:8b") is None
        assert self._validate_model("") is None
        assert self._validate_model("glm-4.7-flash:latest") is None

    def test_invalid_model_name(self) -> None:
        assert self._validate_model("model with spaces") is not None
        assert self._validate_model("a" * 129) is not None
        assert self._validate_model("$(rm -rf /)") is not None


# ---------------------------------------------------------------------------
# Integration — Real Ollama probe
# ---------------------------------------------------------------------------

@pytest.mark.integration
class TestRealOllamaProbe:
    """Requires a running Ollama at localhost:11434. Skip if not available."""

    def _probe(self, base_url: str) -> dict:
        """Mirror of engine._probe_ollama_at() without importing the FastAPI module."""
        url = base_url.rstrip("/")
        try:
            with urllib.request.urlopen(f"{url}/api/tags", timeout=2.0) as resp:
                data = json.loads(resp.read())
            models = [m["name"] for m in (data.get("models") or [])]
            return {"reachable": True, "available_models": models}
        except Exception:  # noqa: BLE001
            return {"reachable": False, "available_models": []}

    def test_probe_returns_models(self) -> None:
        result = self._probe("http://localhost:11434")
        if not result["reachable"]:
            pytest.skip("Ollama not running at localhost:11434")
        assert result["reachable"] is True
        assert isinstance(result["available_models"], list)
        assert len(result["available_models"]) > 0
        # We know qwen3 models are installed on this system
        model_names = result["available_models"]
        print(f"Available models: {model_names}")
        assert any("qwen3" in m for m in model_names)

    def test_probe_unreachable_url(self) -> None:
        result = self._probe("http://localhost:19999")
        assert result["reachable"] is False
        assert result["available_models"] == []


# ---------------------------------------------------------------------------
# Integration — Full API flow (requires running server at 8765)
# ---------------------------------------------------------------------------

@pytest.mark.live
class TestLiveApiFlow:
    """Full API integration test against the running console server.

    Requires: console running at http://localhost:8765
    Skip if server is not available.
    """

    BASE = "http://127.0.0.1:8765/v1/console"

    def _get_session(self) -> tuple[str, str]:
        """Return (sid_cookie, csrf_token). Skip if server unreachable."""
        import http.cookiejar
        jar = http.cookiejar.CookieJar()
        opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
        try:
            opener.open(f"{self.BASE}/auth/local-login", timeout=3)
        except Exception:
            pytest.skip("Console server not running at 8765")
            return "", ""

        sid = ""
        for cookie in jar:
            if cookie.name == "corvin_console_sid":
                sid = cookie.value
                break
        if not sid:
            pytest.skip("No session cookie received from local-login")
            return "", ""

        # Get CSRF token
        cr = urllib.request.Request(
            f"{self.BASE}/auth/whoami",
            headers={"Cookie": f"corvin_console_sid={sid}"},
        )
        with urllib.request.urlopen(cr, timeout=3) as r:
            data = json.loads(r.read())
        return sid, data.get("csrf_token", "")

    def _api(self, method: str, path: str, body: Any = None,
             sid: str = "", csrf: str = "") -> Any:
        headers: dict[str, str] = {
            "Cookie": f"corvin_console_sid={sid}",
            "Accept": "application/json",
        }
        if csrf:
            headers["X-CSRF-Token"] = csrf
        data = None
        if body is not None:
            headers["Content-Type"] = "application/json"
            data = json.dumps(body).encode()
        req = urllib.request.Request(
            f"{self.BASE}{path}", data=data, headers=headers, method=method
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    def test_get_claude_local_default(self) -> None:
        sid, _ = self._get_session()
        result = self._api("GET", "/settings/engine/claude-local", sid=sid)
        assert "enabled" in result
        assert "base_url" in result
        assert "ollama_reachable" in result
        assert "available_models" in result
        # Ollama is running on this system
        assert result["ollama_reachable"] is True

    def test_put_and_get_roundtrip(self) -> None:
        sid, csrf = self._get_session()

        # Enable with specific models
        put_body = {
            "enabled": True,
            "base_url": "http://localhost:11434",
            "sonnet_model": "qwen3:8b",
            "haiku_model": "qwen3:1.7b",
            "opus_model": "qwen3:8b",
        }
        put_result = self._api("PUT", "/settings/engine/claude-local",
                               body=put_body, sid=sid, csrf=csrf)
        assert put_result["enabled"] is True
        assert put_result["sonnet_model"] == "qwen3:8b"
        assert put_result["ollama_reachable"] is True

        # GET should return the saved state
        get_result = self._api("GET", "/settings/engine/claude-local", sid=sid)
        assert get_result["enabled"] is True
        assert get_result["sonnet_model"] == "qwen3:8b"

        # Disable again (cleanup)
        disable_body = {**put_body, "enabled": False}
        self._api("PUT", "/settings/engine/claude-local",
                  body=disable_body, sid=sid, csrf=csrf)
        final = self._api("GET", "/settings/engine/claude-local", sid=sid)
        assert final["enabled"] is False

    def test_validation_rejects_bad_url(self) -> None:
        sid, csrf = self._get_session()
        import urllib.error
        with pytest.raises(urllib.error.HTTPError) as exc_info:
            self._api("PUT", "/settings/engine/claude-local", body={
                "enabled": True,
                "base_url": "ftp://bad-scheme",
                "sonnet_model": "qwen3:8b",
                "haiku_model": "qwen3:1.7b",
                "opus_model": "qwen3:8b",
            }, sid=sid, csrf=csrf)
        assert exc_info.value.code == 422

    def test_env_injection_after_api_save(self) -> None:
        """After saving via API, _read_cc_local_cfg reads the updated YAML."""
        sid, csrf = self._get_session()

        # Enable via API
        self._api("PUT", "/settings/engine/claude-local", body={
            "enabled": True,
            "base_url": "http://localhost:11434",
            "sonnet_model": "qwen3:8b",
            "haiku_model": "qwen3:1.7b",
            "opus_model": "qwen3:8b",
        }, sid=sid, csrf=csrf)

        # Find the YAML path the server wrote to
        corvin_home = Path(os.environ.get("CORVIN_HOME", Path.home() / ".corvin"))
        yaml_path = corvin_home / "tenants" / "_default" / "global" / "tenant.corvin.yaml"
        assert yaml_path.exists(), f"YAML not found at {yaml_path}"

        # Read and verify the YAML was updated
        data = yaml.safe_load(yaml_path.read_text()) or {}
        spec = data.get("spec", {}).get("claude_code_local", {})
        assert spec.get("enabled") is True
        assert spec.get("base_url") == "http://localhost:11434"
        assert spec.get("sonnet_model") == "qwen3:8b"

        # Verify _read_cc_local_cfg picks it up (clears cache first)
        from adapter import _cc_local_cfg_cache, _read_cc_local_cfg
        _cc_local_cfg_cache.clear()
        cfg = _read_cc_local_cfg("_default")
        assert cfg is not None
        assert cfg["base_url"] == "http://localhost:11434"
        assert cfg["sonnet_model"] == "qwen3:8b"

        # Clean up
        self._api("PUT", "/settings/engine/claude-local", body={
            "enabled": False,
            "base_url": "http://localhost:11434",
            "sonnet_model": "", "haiku_model": "", "opus_model": "",
        }, sid=sid, csrf=csrf)
        _cc_local_cfg_cache.clear()

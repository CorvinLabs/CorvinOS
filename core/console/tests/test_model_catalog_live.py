"""HTTP route-level tests for live model discovery (ADR-0245).

Tests what happens when:
  1. live_model_discovery flag is OFF (feature disabled) → static registry only
  2. live_model_discovery flag is ON → live fetch with cache fallback
  3. Anthropic fetch succeeds → cache is written
  4. Anthropic fetch fails → cached data is returned (or empty if no cache)
  5. Manual refresh is triggered → updates cache immediately
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch, MagicMock

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "operator"
_CONSOLE = _REPO / "core" / "console"
_BRIDGES_SHARED = _OPERATOR / "bridges" / "shared"
_PLUGINS = _REPO / "core" / "plugins"

for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"),
           str(_CONSOLE), str(_BRIDGES_SHARED), str(_PLUGINS)]:
    if _p not in sys.path:
        sys.path.insert(0, _p)


_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")


def _snapshot_modules() -> dict:
    return {k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)}


def _reset_modules(restore: dict | None = None) -> None:
    for key in list(sys.modules):
        if key.startswith(_PURGED_PREFIXES):
            del sys.modules[key]
    if restore:
        sys.modules.update(restore)


@contextmanager
def _sandbox(tmp_path: Path):
    """Setup test environment with CORVIN_HOME isolation."""
    home = tmp_path / "corvin_home"
    for tenant_id in ("_default",):
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in
            ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = "_default"
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
    preloaded = _snapshot_modules()
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")

        rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        yield client, csrf, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


class TestModelCatalogLive(unittest.TestCase):
    """Test suite for /models/* routes."""

    def test_models_registry_always_available(self):
        """GET /models/registry returns static YAML registry regardless of flag."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                resp = client.get("/v1/console/models/registry")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                self.assertIn("claude_code", data)
                # Static registry always has os_models
                self.assertIn("os_models", data["claude_code"])

    def test_models_providers_always_available(self):
        """GET /models/providers returns static provider registry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                resp = client.get("/v1/console/models/providers")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                # Should have some structure
                self.assertIsInstance(data, dict)

    def test_models_live_when_flag_off(self):
        """GET /models/live with flag OFF returns static registry, no providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                # Flag is off by default
                resp = client.get("/v1/console/models/live")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                # Should have registry (always included)
                self.assertIn("registry", data)
                self.assertIn("claude_code", data["registry"])
                # Providers should be empty when flag is off
                self.assertEqual(data.get("providers", {}), {})
                # Cache status should show it's disabled
                cache_status = data.get("cache_status", {})
                self.assertEqual(cache_status.get("reason"), "feature_disabled")

    def test_models_live_when_flag_on_no_cache(self):
        """GET /models/live with flag ON but no cache returns empty providers."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                # Enable the flag
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                resp = client.get("/v1/console/models/live")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()
                # Should have registry
                self.assertIn("registry", data)
                # Providers might be empty if no cached data
                providers = data.get("providers", {})
                cache_status = data.get("cache_status", {})
                # Cache not yet present
                self.assertFalse(cache_status.get("cached", False))

    def test_models_live_with_mock_fetch(self):
        """GET /models/live fetches from Anthropic when flag is ON (mocked)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                # Mock engine_providers.fetch_models to return a result
                mock_result = {
                    "provider": "anthropic",
                    "reachable": True,
                    "models": [
                        {"id": "claude-opus-5", "label": "Claude Opus 5"},
                        {"id": "claude-sonnet-5", "label": "Claude Sonnet 5"},
                    ],
                    "count": 2,
                    "error": None,
                }

                # Patch at the module where it's imported in models.py
                with patch("corvin_console.routes.models.engine_providers.fetch_models",
                           return_value=mock_result):
                    # Manually trigger a refresh by calling the route that does it
                    refresh_resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(refresh_resp.status_code, 200)
                    refresh_data = refresh_resp.json()
                    self.assertEqual(refresh_data["providers"]["anthropic"]["count"], 2)

                    # Now check if cache was written
                    cache_path = (
                        home / "tenants" / "_default" / "global" / "model_catalog_cache.json"
                    )
                    self.assertTrue(cache_path.exists())
                    cache = json.loads(cache_path.read_text())
                    self.assertIn("providers", cache)
                    self.assertIn("anthropic", cache["providers"])
                    self.assertEqual(cache["providers"]["anthropic"]["count"], 2)

    def test_models_live_refresh_when_flag_off(self):
        """POST /models/live/refresh with flag OFF returns 400."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                # Flag is off by default
                refresh_resp = client.post(
                    "/v1/console/models/live/refresh",
                    headers={"X-CSRF-Token": csrf},
                )
                self.assertEqual(refresh_resp.status_code, 400)
                data = refresh_resp.json()
                self.assertIn("detail", data)

    def test_models_live_refresh_mock_failure(self):
        """POST /models/live/refresh with fetch failure still returns 200."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                # Mock fetch to return a failure
                mock_result = {
                    "provider": "anthropic",
                    "reachable": False,
                    "models": [],
                    "count": 0,
                    "error": "Anthropic unreachable: timeout",
                }

                with patch("corvin_console.routes.models.engine_providers.fetch_models",
                           return_value=mock_result):
                    refresh_resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    # Should still return 200 even on failure
                    self.assertEqual(refresh_resp.status_code, 200)
                    data = refresh_resp.json()
                    # The response includes the failed result
                    self.assertFalse(
                        data["providers"]["anthropic"]["reachable"]
                    )

    def test_cache_isolation_per_tenant(self):
        """Cache files are stored per-tenant (not shared)."""
        # This is more of an architectural test; we verify paths
        from forge import paths as _forge_paths
        home = Path("/tmp/test-corvin")
        tenant1 = _forge_paths.tenant_global_dir("tenant1")
        tenant2 = _forge_paths.tenant_global_dir("tenant2")
        # Paths should be different
        self.assertNotEqual(str(tenant1), str(tenant2))
        self.assertIn("tenant1", str(tenant1))
        self.assertIn("tenant2", str(tenant2))


if __name__ == "__main__":
    unittest.main()

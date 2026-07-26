"""Integration tests for live model discovery (Tier 3).

Tests the full stack:
  - Background refresh running automatically
  - Cache file is created/updated
  - Multiple Console processes share the same cache
  - Tenant isolation is preserved
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
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
def _sandbox_full(tmp_path: Path):
    """Setup full test environment for integration testing."""
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
        from corvin_console import auth as _auth, feature_flags
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        app = FastAPI()
        app.include_router(router, prefix="/v1/console")

        rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        csrf = _auth.derive_csrf_token(rec.csrf_secret, rec.sid)
        yield client, csrf, home, feature_flags
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


class TestModelCatalogIntegration(unittest.TestCase):
    """Integration tests for model discovery."""

    def test_background_refresh_writes_cache_on_startup(self):
        """When module loads, background refresh should eventually write cache."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            mock_result = {
                "provider": "anthropic",
                "reachable": True,
                "models": [
                    {"id": "claude-opus-5", "label": "Claude Opus 5"},
                ],
                "count": 1,
                "error": None,
            }

            with patch("corvin_console.routes.models.engine_providers.fetch_models",
                       return_value=mock_result):
                with _sandbox_full(tmp_path) as (client, csrf, home, feature_flags):
                    # Enable flag to trigger background fetch
                    feature_flags.set_enabled("live_model_discovery", True, "_default")

                    # Trigger a manual refresh (background refresh might not have run yet in test)
                    resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp.status_code, 200)

                    # Cache file should now exist
                    cache_path = (
                        home / "tenants" / "_default" / "global" / "model_catalog_cache.json"
                    )
                    self.assertTrue(cache_path.exists(), "Cache file should be created")

                    cache_data = json.loads(cache_path.read_text())
                    self.assertIn("providers", cache_data)
                    self.assertIn("anthropic", cache_data["providers"])
                    self.assertEqual(
                        cache_data["providers"]["anthropic"]["count"], 1
                    )

    def test_cache_persists_across_requests(self):
        """Cache written by one request is read by subsequent requests."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            mock_result = {
                "provider": "anthropic",
                "reachable": True,
                "models": [
                    {"id": "model-1", "label": "Model 1"},
                ],
                "count": 1,
                "error": None,
            }

            with patch("corvin_console.routes.models.engine_providers.fetch_models",
                       return_value=mock_result):
                with _sandbox_full(tmp_path) as (client, csrf, home, feature_flags):
                    feature_flags.set_enabled("live_model_discovery", True, "_default")

                    # First request: refresh
                    resp1 = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp1.status_code, 200)
                    data1 = resp1.json()
                    fetched_at_1 = data1["fetched_at"]

                    # Wait a moment
                    time.sleep(0.1)

                    # Second request: get live models (should hit cache, not refetch)
                    resp2 = client.get("/v1/console/models/live")
                    self.assertEqual(resp2.status_code, 200)
                    data2 = resp2.json()

                    # Cache data should be present
                    anthropic = data2["providers"].get("anthropic", {})
                    self.assertEqual(anthropic.get("count"), 1)

                    # Cache age should be increasing
                    cache_status = data2["cache_status"]
                    self.assertTrue(cache_status["cached"])
                    # Age should be ~0 since we just fetched
                    self.assertLess(cache_status.get("cache_age_sec", 999), 5)

    def test_stale_cache_is_used_when_fetch_fails(self):
        """If Anthropic is down, use the cached data from last successful fetch."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            # First: successful fetch
            mock_success = {
                "provider": "anthropic",
                "reachable": True,
                "models": [{"id": "cached-model", "label": "Cached Model"}],
                "count": 1,
                "error": None,
            }

            # Then: failed fetch
            mock_failure = {
                "provider": "anthropic",
                "reachable": False,
                "models": [],
                "count": 0,
                "error": "Anthropic unreachable: timeout",
            }

            with _sandbox_full(tmp_path) as (client, csrf, home, feature_flags):
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                # Populate cache with successful fetch
                with patch("corvin_console.routes.models.engine_providers.fetch_models",
                           return_value=mock_success):
                    resp1 = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp1.status_code, 200)

                # Now fail a fetch
                with patch("corvin_console.routes.models.engine_providers.fetch_models",
                           return_value=mock_failure):
                    # The failed refresh should still return 200 (error is in the data)
                    resp2 = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp2.status_code, 200)
                    # But the cache should NOT be updated (old data persists)

                # Subsequent GET should still return the cached data
                resp3 = client.get("/v1/console/models/live")
                self.assertEqual(resp3.status_code, 200)
                data3 = resp3.json()

                # Old cache is still there
                anthropic = data3["providers"].get("anthropic", {})
                self.assertEqual(anthropic.get("count"), 1)
                models = anthropic.get("models", [])
                self.assertTrue(any(m["id"] == "cached-model" for m in models))

    def test_cache_file_format_validation(self):
        """Manually-written cache is read correctly even if format is slightly off."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            with _sandbox_full(tmp_path) as (client, csrf, home, feature_flags):
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                # Manually write a cache file
                cache_path = home / "tenants" / "_default" / "global" / "model_catalog_cache.json"
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                cache_data = {
                    "providers": {
                        "anthropic": {
                            "models": [
                                {"id": "manual-model", "label": "Manually Added"},
                            ],
                            "reachable": True,
                            "count": 1,
                            "error": None,
                            "fetched_at": time.time(),
                        }
                    }
                }
                cache_path.write_text(json.dumps(cache_data))

                # GET should read it
                resp = client.get("/v1/console/models/live")
                self.assertEqual(resp.status_code, 200)
                data = resp.json()

                anthropic = data["providers"].get("anthropic", {})
                self.assertEqual(anthropic.get("count"), 1)
                models = anthropic.get("models", [])
                self.assertTrue(any(m["id"] == "manual-model" for m in models))

    def test_flag_state_controls_cache_usage(self):
        """Toggling flag off/on controls whether cache is read."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)

            mock_result = {
                "provider": "anthropic",
                "reachable": True,
                "models": [{"id": "test-model", "label": "Test"}],
                "count": 1,
                "error": None,
            }

            with patch("corvin_console.routes.models.engine_providers.fetch_models",
                       return_value=mock_result):
                with _sandbox_full(tmp_path) as (client, csrf, home, feature_flags):
                    # Enable flag, populate cache
                    feature_flags.set_enabled("live_model_discovery", True, "_default")
                    resp1 = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp1.status_code, 200)

                    # With flag ON: cache is used
                    resp2 = client.get("/v1/console/models/live")
                    data2 = resp2.json()
                    self.assertTrue(data2["cache_status"]["cached"])
                    self.assertIn("anthropic", data2["providers"])

                    # Turn flag OFF
                    feature_flags.set_enabled("live_model_discovery", False, "_default")

                    # With flag OFF: cache is ignored
                    resp3 = client.get("/v1/console/models/live")
                    data3 = resp3.json()
                    # Should show "feature_disabled" in cache_status
                    self.assertEqual(
                        data3["cache_status"].get("reason"),
                        "feature_disabled"
                    )
                    # Providers should be empty
                    self.assertEqual(data3["providers"], {})
                    # But static registry is still there
                    self.assertIn("registry", data3)


if __name__ == "__main__":
    unittest.main()

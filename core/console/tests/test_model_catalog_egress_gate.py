"""L35 egress gate must guard EVERY outbound fetch the live model-catalog
route makes to a provider (ADR-0043/0181/0245).

Two paths reach ``engine_providers.fetch_models()`` with a real network
base_url:
  1. ``POST /models/live/refresh`` — the operator-triggered manual refresh.
  2. ``_refresh_once_impl()`` — the 5-minute background timer.

Neither called ``_egress_denied()`` before this fix; an EU_PRODUCTION tenant
with ``api.anthropic.com`` on ``forbidden_hosts`` would still have the console
reach out to it on a timer. These tests prove:

  * an EXPLICIT policy denial refuses the refresh and never calls
    ``fetch_models()`` (no outbound request attempted) — for both paths;
  * the manual endpoint returns 403 with an explanatory detail;
  * the denial is audited (console action_denied / system_event);
  * an unconfigured/disabled policy still fails OPEN (existing behaviour
    preserved — no tenant.corvin.yaml written == old behaviour).
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

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


_FORBID_ANTHROPIC_YAML = """
spec:
  egress:
    enabled: true
    default_action: allow
    forbidden_hosts:
      - api.anthropic.com
"""


@contextmanager
def _sandbox(tmp_path: Path, *, egress_yaml: str | None = None):
    """Setup test environment with CORVIN_HOME isolation. When ``egress_yaml``
    is given it is written as the tenant's tenant.corvin.yaml BEFORE the
    console app is imported, so ``load_egress_gate_for_tenant`` picks it up."""
    home = tmp_path / "corvin_home"
    global_dir = home / "tenants" / "_default" / "global"
    for sub in ("auth", "forge", "console/sessions"):
        (global_dir / sub).mkdir(parents=True)

    if egress_yaml is not None:
        (global_dir / "tenant.corvin.yaml").write_text(egress_yaml, encoding="utf-8")

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


class TestManualRefreshEgressGate(unittest.TestCase):
    """POST /models/live/refresh must honour an explicit L35 denial."""

    def test_denied_host_refuses_refresh_and_never_fetches(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path, egress_yaml=_FORBID_ANTHROPIC_YAML) as (client, csrf, home):
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                with patch(
                    "corvin_console.routes.models.engine_providers.fetch_models"
                ) as mock_fetch:
                    resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp.status_code, 403)
                    self.assertIn("api.anthropic.com", resp.json()["detail"])
                    mock_fetch.assert_not_called()

                # No cache should have been written either.
                cache_path = home / "tenants" / "_default" / "global" / "model_catalog_cache.json"
                self.assertFalse(cache_path.exists())

    def test_denied_refresh_is_audited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path, egress_yaml=_FORBID_ANTHROPIC_YAML) as (client, csrf, home):
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                with patch("corvin_console.routes.models.engine_providers.fetch_models"):
                    resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp.status_code, 403)

                audit_path = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
                self.assertTrue(audit_path.exists(), "no audit chain was written at all")
                lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
                events = {rec.get("event_type") for rec in lines}
                # Either the console-level denial or the L35 gate's own
                # egress.blocked event (or both) must be present.
                denial_events = {
                    e for e in events
                    if e in ("console.action_denied", "egress.blocked")
                }
                self.assertTrue(denial_events, f"no denial event recorded; saw {events}")

    def test_allowed_host_still_refreshes(self):
        """Sanity check: an explicit allow (or no policy) must not regress."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                from corvin_console import feature_flags
                feature_flags.set_enabled("live_model_discovery", True, "_default")

                mock_result = {
                    "provider": "anthropic",
                    "reachable": True,
                    "models": [{"id": "claude-opus-5", "label": "Claude Opus 5"}],
                    "count": 1,
                    "error": None,
                }
                with patch(
                    "corvin_console.routes.models.engine_providers.fetch_models",
                    return_value=mock_result,
                ):
                    resp = client.post(
                        "/v1/console/models/live/refresh",
                        headers={"X-CSRF-Token": csrf},
                    )
                    self.assertEqual(resp.status_code, 200)
                    self.assertEqual(resp.json()["providers"]["anthropic"]["count"], 1)


class TestBackgroundRefreshEgressGate(unittest.TestCase):
    """The 5-minute background timer (_refresh_once_impl) shares the same
    gate — it is real outbound egress on a timer, not just on a button."""

    def test_denied_host_is_never_contacted_by_background_refresh(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path, egress_yaml=_FORBID_ANTHROPIC_YAML) as (client, csrf, home):
                from corvin_console import feature_flags
                from corvin_console.routes import models as M

                feature_flags.set_enabled("live_model_discovery", True, "_default")

                # Prevent the real reschedule from spawning a live daemon
                # timer thread that outlives the test.
                started = {}

                class _FakeTimer:
                    def __init__(self, *_a, **_k):
                        pass

                    def start(self):
                        started["scheduled"] = True

                    daemon = True

                with patch.object(M.threading, "Timer", _FakeTimer), \
                     patch.object(M.engine_providers, "fetch_models") as mock_fetch:
                    M._refresh_once_impl("_default")
                    mock_fetch.assert_not_called()

                self.assertTrue(started.get("scheduled"), "must still reschedule itself")

                cache_path = home / "tenants" / "_default" / "global" / "model_catalog_cache.json"
                self.assertFalse(cache_path.exists())

    def test_denied_background_refresh_is_audited(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path, egress_yaml=_FORBID_ANTHROPIC_YAML) as (client, csrf, home):
                from corvin_console import feature_flags
                from corvin_console.routes import models as M

                feature_flags.set_enabled("live_model_discovery", True, "_default")

                class _FakeTimer:
                    def __init__(self, *_a, **_k):
                        pass

                    def start(self):
                        pass

                    daemon = True

                with patch.object(M.threading, "Timer", _FakeTimer), \
                     patch.object(M.engine_providers, "fetch_models"):
                    M._refresh_once_impl("_default")

                audit_path = home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
                self.assertTrue(audit_path.exists())
                lines = [json.loads(line) for line in audit_path.read_text().splitlines() if line.strip()]
                events = {rec.get("event_type") for rec in lines}
                denial_events = {
                    e for e in events
                    if e in ("model_catalog_refresh_blocked", "egress.blocked")
                }
                self.assertTrue(denial_events, f"no denial event recorded; saw {events}")

    def test_no_policy_still_fails_open_unchanged_behaviour(self):
        """No tenant.corvin.yaml == no egress policy == old fail-open
        behaviour must be preserved (regression guard)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            with _sandbox(tmp_path) as (client, csrf, home):
                from corvin_console import feature_flags
                from corvin_console.routes import models as M

                feature_flags.set_enabled("live_model_discovery", True, "_default")

                mock_result = {
                    "provider": "anthropic",
                    "reachable": True,
                    "models": [{"id": "claude-opus-5", "label": "Claude Opus 5"}],
                    "count": 1,
                    "error": None,
                }

                class _FakeTimer:
                    def __init__(self, *_a, **_k):
                        pass

                    def start(self):
                        pass

                    daemon = True

                with patch.object(M.threading, "Timer", _FakeTimer), \
                     patch.object(M.engine_providers, "fetch_models", return_value=mock_result) as mock_fetch:
                    M._refresh_once_impl("_default")
                    mock_fetch.assert_called_once()


if __name__ == "__main__":
    unittest.main()

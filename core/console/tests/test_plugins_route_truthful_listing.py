"""R2-A9: ``GET /v1/console/plugins`` must report what actually happened.

Three ways the listing disagreed with the process it claims to describe:

1. **Provenance was guessed, not read.** Every plugin found in either plugin
   root was stamped ``origin="builtin"``. ``bridge_adapter`` and ``cowork_hub``
   load from the Corvin-Marketplace checkout, which does NOT ship in the wheel —
   the chain records them as ``vetted`` (ADR-0643). The console said ``builtin``,
   i.e. it disagreed with the audit trail about a trust boundary.
2. **Loaded plugins were missing.** The list was ``registry.yaml`` plus a
   filesystem scan, so the bundled bridge supervisors — loaded from a
   declaration, with no manifest in a scanned root — never appeared, though
   eight of them were running.
3. **Refused plugins were invisible.** A plugin refused a provider slot
   (ADR-0250) or refused by the trust gate (ADR-0249) was simply absent, which
   looks exactly like "never installed" to an operator.

Driven through the REAL router with a TestClient (only the session/auth
dependency is satisfied the way the existing route tests do it).
"""
from __future__ import annotations

import os
import sys
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path

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
    home = tmp_path / "corvin_home"
    tenant_id = "_default"
    (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
    (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in
            ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenant_id
    # R2-A3: the resolver's own path under this CORVIN_HOME.
    os.environ["VOICE_AUDIT_PATH"] = str(
        home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    )
    preloaded = _snapshot_modules()
    try:
        _reset_modules()
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, _auth.derive_csrf_token(rec.csrf_secret, rec.sid), home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


def _enable_surface(client, csrf: str) -> None:
    """``plugin_console_surface`` ships dark; the routes 404 until it is on."""
    resp = client.put(
        "/v1/console/settings/features/plugin_console_surface",
        json={"enabled": True}, headers={"X-CSRF-Token": csrf},
    )
    assert resp.status_code == 200, resp.text


class _FakePlugin:
    plugin_id = "r2a9_fake_bridge"
    plugin_type = "bridge_supervisor"
    version = "9.9.9"
    display_name = "R2A9 Fake Bridge"

    def on_load(self, ctx):
        self.ctx = ctx

    def on_unload(self):
        pass

    def health_check(self):
        from corvin_plugins.protocol import HealthStatus

        return HealthStatus(ok=True, message="fake")


class TestListingMatchesTheProcess(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_a_registered_plugin_with_no_manifest_is_listed(self):
        """The bridge-supervisor case: registered, no manifest in a scanned root."""
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.registry import get_registry, register

        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            _enable_surface(client, csrf)
            plugin = _FakePlugin()
            ctx = build_context(
                plugin_id=plugin.plugin_id, tenant_id="_default",
                corvin_home=home, config={},
            )
            register(plugin, ctx, origin="vetted", source="marketplace_root:x/y")
            try:
                resp = client.get("/v1/console/plugins")
                self.assertEqual(resp.status_code, 200, resp.text)
                body = resp.json()
                entry = next((p for p in body["plugins"]
                              if p["plugin_id"] == plugin.plugin_id), None)
                self.assertIsNotNone(
                    entry,
                    "a plugin registered in THIS process is missing from the listing",
                )
                # …and its provenance is the registry's, not a filesystem guess.
                self.assertEqual(entry["origin"], "vetted", entry)
            finally:
                get_registry().unregister(plugin.plugin_id)

    def test_origin_comes_from_the_registry_not_from_a_guess(self):
        """A plugin registered as ``vetted`` must never be shown as ``builtin``."""
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.registry import get_registry, register, provenance_of

        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            _enable_surface(client, csrf)
            plugin = _FakePlugin()
            ctx = build_context(
                plugin_id=plugin.plugin_id, tenant_id="_default",
                corvin_home=home, config={},
            )
            register(plugin, ctx, origin="vetted", source="marketplace_root:x/y")
            try:
                self.assertEqual(provenance_of(plugin.plugin_id),
                                 ("vetted", "marketplace_root:x/y"))
                body = client.get("/v1/console/plugins").json()
                entry = next(p for p in body["plugins"]
                             if p["plugin_id"] == plugin.plugin_id)
                self.assertNotEqual(entry["origin"], "builtin", entry)
            finally:
                get_registry().unregister(plugin.plugin_id)

    def test_refused_plugins_are_reported_with_their_reason(self):
        """A refusal must be visible, not indistinguishable from 'not installed'."""
        from corvin_plugins import bootstrap

        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            _enable_surface(client, csrf)
            bootstrap._clear_refusals()
            bootstrap._audit_degradation("_default", "plugin.provider_slot_refused", {
                "plugin_id": "r2a9-refused", "tenant_id": "_default",
                "plugin_type": "audit_backend", "origin": "community",
                "reason": "multi_tenant_provider_slot", "tenant_count": 3,
            })
            try:
                body = client.get("/v1/console/plugins").json()
                self.assertGreaterEqual(body["refused_total"], 1, body)
                entry = next((r for r in body["refused"]
                              if r["plugin_id"] == "r2a9-refused"), None)
                self.assertIsNotNone(entry, body)
                self.assertEqual(entry["reason"], "multi_tenant_provider_slot", entry)
                self.assertEqual(entry["event_type"], "plugin.provider_slot_refused",
                                 entry)
            finally:
                bootstrap._clear_refusals()

    def test_listing_is_empty_of_refusals_on_a_clean_boot(self):
        """The field must not become noise: nothing refused, nothing reported."""
        from corvin_plugins import bootstrap

        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            _enable_surface(client, csrf)
            bootstrap._clear_refusals()
            body = client.get("/v1/console/plugins").json()
            self.assertEqual(body["refused"], [], body)
            self.assertEqual(body["refused_total"], 0, body)


if __name__ == "__main__":
    unittest.main()

"""HTTP route-level tests for the plugin registry surface (ADR-0233 Phase 4).

Mirrors the ``_sandbox`` TestClient pattern from test_features_route.py.

The point of these tests is the two flag states: with ``plugin_console_surface``
off the routes must be ABSENT (404), and with the surface on but
``plugin_runtime_lifecycle`` off the read side must work while every mutation is
refused.  A flag that is only ever tested in one state rots (CLAUDE.md).
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


def _reset_modules():
    for key in list(sys.modules):
        if any(key.startswith(p) for p in
               ("corvin_console", "corvin_gateway", "forge", "corvin_plugins")):
            del sys.modules[key]


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
    # Keep the real GDPR chain out of the test run (tests/conftest.py convention).
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
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
        _reset_modules()


_RECORD = {
    "plugin_id": "acme-notify",
    "version": "1.0.0",
    "display_name": "Acme Notify",
    "plugin_type": "notification_backend",
    "origin": "vetted",
    "pii_risk": "low",
    "settings_schema": {
        "type": "object",
        "properties": {"channel": {"type": "string", "default": "ops"}},
        "required": ["channel"],
        "additionalProperties": False,
    },
    "settings": {"channel": "ops"},
}


class _Base(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.mkdtemp()

    def tearDown(self):
        import shutil

        shutil.rmtree(self._tmp, ignore_errors=True)

    def _hdr(self, csrf: str) -> dict[str, str]:
        return {"X-CSRF-Token": csrf}

    def _flag(self, client, csrf, flag_id: str, on: bool) -> None:
        resp = client.put(
            f"/v1/console/settings/features/{flag_id}",
            json={"enabled": on},
            headers=self._hdr(csrf),
        )
        assert resp.status_code == 200, resp.text


# ── Flag OFF ──────────────────────────────────────────────────────────────────


class TestSurfaceFlagOff(_Base):
    def test_every_route_404s_on_a_fresh_install(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            for method, path in (
                ("get", "/v1/console/plugins"),
                ("get", "/v1/console/plugins/health"),
                ("get", "/v1/console/plugins/acme-notify"),
                ("get", "/v1/console/plugins/acme-notify/schema-defaults"),
            ):
                resp = getattr(client, method)(path)
                self.assertEqual(resp.status_code, 404, f"{path}: {resp.text}")

            for path in (
                "/v1/console/plugins",
                "/v1/console/plugins/acme-notify/enable",
                "/v1/console/plugins/acme-notify/disable",
                "/v1/console/plugins/acme-notify/settings",
            ):
                resp = client.post(path, json={}, headers=self._hdr(csrf))
                self.assertEqual(resp.status_code, 404, f"{path}: {resp.text}")

            resp = client.delete(
                "/v1/console/plugins/acme-notify", headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_the_flag_ships_dark(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            features = {f["id"]: f for f in
                        client.get("/v1/console/settings/features").json()["features"]}
            for fid in ("plugin_console_surface", "plugin_runtime_lifecycle",
                        "plugin_health_monitoring"):
                self.assertIn(fid, features)
                self.assertFalse(features[fid]["enabled"], f"{fid} must default to off")

    def test_no_registry_file_is_created_while_off(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            self.assertFalse(
                (home / "tenants" / "_default" / "plugins" / "registry.yaml").exists()
            )


# ── Surface ON, lifecycle OFF ─────────────────────────────────────────────────


class TestLifecycleFlagOff(_Base):
    def test_read_works_but_mutations_are_refused(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)

            resp = client.get("/v1/console/plugins")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["plugins"], [])
            self.assertEqual(body["total"], 0)
            self.assertFalse(body["lifecycle_enabled"])

            resp = client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 403, resp.text)
            self.assertIn("plugin_runtime_lifecycle", resp.json()["detail"])

    def test_health_without_monitoring_reports_breakers_only(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            resp = client.get("/v1/console/plugins/health")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertFalse(body["monitoring_enabled"])
            self.assertIn("breakers", body)
            self.assertNotIn("plugins", body, "no plugin must be called while off")


# ── Surface ON, lifecycle ON ──────────────────────────────────────────────────


class TestFullLifecycle(_Base):
    @contextmanager
    def _live(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            yield client, csrf, home

    def test_install_enable_configure_disable_uninstall(self):
        with self._live() as (client, csrf, _home):
            resp = client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertFalse(resp.json()["enabled"], "install must not enable")

            resp = client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["enabled"])

            resp = client.post(
                "/v1/console/plugins/acme-notify/settings",
                json={"settings": {"channel": "alerts"}},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["settings"], {"channel": "alerts"})

            resp = client.post(
                "/v1/console/plugins/acme-notify/disable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertFalse(resp.json()["enabled"])

            resp = client.delete(
                "/v1/console/plugins/acme-notify", headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["audit_retained"])

            self.assertEqual(client.get("/v1/console/plugins").json()["total"], 0)

    def test_community_plugin_needs_the_consent_flag(self):
        with self._live() as (client, csrf, _home):
            payload = {**_RECORD, "origin": "community"}
            client.post("/v1/console/plugins", json=payload, headers=self._hdr(csrf))

            resp = client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 409, resp.text)

            resp = client.post(
                "/v1/console/plugins/acme-notify/enable",
                json={"consent_granted": True},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["enabled"])

    def test_invalid_settings_are_422_and_do_not_persist(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            resp = client.post(
                "/v1/console/plugins/acme-notify/settings",
                json={"settings": {"channel": 42}},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)
            current = client.get("/v1/console/plugins/acme-notify").json()
            self.assertEqual(current["settings"], {"channel": "ops"})

    def test_unknown_plugin_type_is_422(self):
        with self._live() as (client, csrf, _home):
            resp = client.post(
                "/v1/console/plugins",
                json={**_RECORD, "plugin_type": "skill"},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)

    def test_unknown_plugin_is_404(self):
        with self._live() as (client, csrf, _home):
            self.assertEqual(client.get("/v1/console/plugins/nope").status_code, 404)
            resp = client.post(
                "/v1/console/plugins/nope/enable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_double_install_is_409(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            resp = client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 409, resp.text)

    def test_uninstall_of_an_enabled_plugin_is_409(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            resp = client.delete(
                "/v1/console/plugins/acme-notify", headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 409, resp.text)

    def test_schema_defaults(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            resp = client.get("/v1/console/plugins/acme-notify/schema-defaults")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["defaults"], {"channel": "ops"})

    def test_registry_file_is_0600(self):
        import stat

        with self._live() as (client, csrf, home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            path = home / "tenants" / "_default" / "plugins" / "registry.yaml"
            self.assertTrue(path.exists())
            self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)

    def test_corrupt_registry_is_500_not_an_empty_list(self):
        with self._live() as (client, csrf, home):
            path = home / "tenants" / "_default" / "plugins" / "registry.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("plugins: {broken")
            resp = client.get("/v1/console/plugins")
            self.assertEqual(resp.status_code, 500, resp.text)
            self.assertEqual(path.read_text(), "plugins: {broken", "must not be rewritten")


# ── CSRF ──────────────────────────────────────────────────────────────────────


class TestCsrf(_Base):
    def test_mutations_require_the_csrf_header(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            for path in (
                "/v1/console/plugins",
                "/v1/console/plugins/acme-notify/enable",
                "/v1/console/plugins/acme-notify/disable",
                "/v1/console/plugins/acme-notify/settings",
            ):
                resp = client.post(path, json={})
                self.assertIn(resp.status_code, (401, 403), f"{path}: {resp.status_code}")
            resp = client.delete("/v1/console/plugins/acme-notify")
            self.assertIn(resp.status_code, (401, 403))

    def test_unauthenticated_requests_are_rejected(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            client.cookies.clear()
            resp = client.get("/v1/console/plugins")
            self.assertIn(resp.status_code, (401, 403), resp.text)


if __name__ == "__main__":
    unittest.main()

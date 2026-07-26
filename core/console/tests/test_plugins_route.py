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
            # A community plugin needs BOTH gates satisfied: an egress declaration
            # (L35) and explicit consent. This one talks to nothing, isolating the
            # consent gate for this test.
            payload = {**_RECORD, "origin": "community", "network_egress": "none"}
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


class TestMalformedPluginId(_Base):
    """A rejected plugin_id must read as unprocessable input, not as a conflict."""

    def test_backslash_traversal_is_422(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            resp = client.post(
                "/v1/console/plugins",
                json={**_RECORD, "plugin_id": "..\\..\\windows"},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertFalse(
                (home / "tenants" / "_default" / "plugins" / "registry.yaml").exists(),
                "a rejected id must not create a registry",
            )

    def test_uppercase_id_is_422(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            resp = client.post(
                "/v1/console/plugins",
                json={**_RECORD, "plugin_id": "Acme-Notify"},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)


class TestFlowDeclarationsOverTheApi(_Base):
    """The Console must see and be able to set the L34/L35 declarations."""

    @contextmanager
    def _live(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            yield client, csrf, home

    def test_install_defaults_are_least_trusted(self):
        with self._live() as (client, csrf, _home):
            resp = client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["locality"], "unknown")
            self.assertEqual(body["network_egress"], "external")
            self.assertEqual(body["egress_hosts"], [])

    def test_declarations_round_trip(self):
        with self._live() as (client, csrf, _home):
            payload = {
                **_RECORD,
                "locality": "eu_cloud",
                "network_egress": "external",
                "egress_hosts": ["hooks.example.com"],
            }
            client.post("/v1/console/plugins", json=payload, headers=self._hdr(csrf))
            body = client.get("/v1/console/plugins/acme-notify").json()
            self.assertEqual(body["locality"], "eu_cloud")
            self.assertEqual(body["egress_hosts"], ["hooks.example.com"])

    def test_undeclared_community_egress_is_409(self):
        with self._live() as (client, csrf, _home):
            client.post(
                "/v1/console/plugins",
                json={**_RECORD, "origin": "community"},
                headers=self._hdr(csrf),
            )
            resp = client.post(
                "/v1/console/plugins/acme-notify/enable",
                json={"consent_granted": True},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 409, resp.text)
            self.assertIn("egress_hosts", resp.json()["detail"])

    def test_contradictory_declaration_is_422(self):
        with self._live() as (client, csrf, _home):
            resp = client.post(
                "/v1/console/plugins",
                json={**_RECORD, "locality": "us_cloud", "network_egress": "none"},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 409, resp.text)
            self.assertIn("contradicts", resp.json()["detail"])


class TestHealthAndMetrics(_Base):
    """ADR-0231 Stage 2 surfaces, in both flag states."""

    @contextmanager
    def _live(self, monitoring: bool = False):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            if monitoring:
                self._flag(client, csrf, "plugin_health_monitoring", True)
            yield client, csrf, home

    def test_metrics_endpoint_serves_prometheus_text(self):
        with self._live() as (client, _csrf, _home):
            resp = client.get("/v1/console/plugins/metrics")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertIn("text/plain", resp.headers["content-type"])
            self.assertIn("# TYPE corvin_plugin_health_ok gauge", resp.text)

    def test_metrics_404s_while_the_surface_flag_is_off(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home):
            self.assertEqual(
                client.get("/v1/console/plugins/metrics").status_code, 404
            )

    def test_health_reports_no_collector_when_monitoring_is_off(self):
        with self._live(monitoring=False) as (client, _csrf, _home):
            body = client.get("/v1/console/plugins/health").json()
            self.assertFalse(body["monitoring_enabled"])
            self.assertIn("breakers", body)

    def test_health_with_monitoring_on_but_no_collector_falls_back(self):
        """The flag alone must not make the route claim a collector it lacks."""
        with self._live(monitoring=True) as (client, _csrf, _home):
            body = client.get("/v1/console/plugins/health").json()
            self.assertTrue(body["monitoring_enabled"])
            self.assertNotIn("collector_running", body)

    def test_health_serves_the_collector_snapshot_when_one_is_running(self):
        import sys as _sys

        with self._live(monitoring=True) as (client, _csrf, _home):
            from corvin_console.routes import plugins as route_mod

            class _FakeCollector:
                running = True

                def snapshot(self):
                    class _Snap:
                        @staticmethod
                        def to_dict():
                            return {"taken_at": 1.0, "plugins": {"p.ok": {"ok": True}}}

                    return _Snap()

            route_mod.set_collector(_FakeCollector())
            try:
                body = client.get("/v1/console/plugins/health").json()
                self.assertTrue(body["collector_running"])
                self.assertIn("p.ok", body["plugins"])
            finally:
                route_mod.set_collector(None)
            del _sys


class TestRuntimeStateIsVisible(_Base):
    """Review finding: after healing, `enabled` and reality diverge.

    Healing must not rewrite the operator's registry, so a contained plugin stays
    `enabled: true` on disk while being unloaded in the process. Reporting only
    `enabled` would be the same silent false display that hot-reload removed.
    """

    @contextmanager
    def _live(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            yield client, csrf, home

    def test_a_record_without_a_class_path_reports_not_running(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            body = client.get("/v1/console/plugins/acme-notify").json()
            self.assertTrue(body["enabled"])
            self.assertFalse(
                body["runtime_loaded"],
                "nothing was loadable, so the UI must not claim it is running",
            )

    def test_a_disabled_plugin_reports_no_containment_reason(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            body = client.get("/v1/console/plugins/acme-notify").json()
            self.assertFalse(body["enabled"])
            self.assertIsNone(
                body["contained_by"], "a plugin nobody enabled is not 'contained'"
            )

    def test_breaker_state_surfaces_as_containment(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            # Open a breaker for this id and register the plugin so it counts as
            # loaded — that is the "running but contained" case.
            from corvin_plugins import circuit_breaker as cb
            from corvin_plugins.bootstrap import build_context
            from corvin_plugins.protocol import HealthStatus
            from corvin_plugins.registry import get_registry

            class _Live:
                plugin_id = "acme-notify"
                plugin_type = "notification_backend"
                version = "1.0.0"
                display_name = "Acme"

                def on_load(self, ctx):
                    pass

                def on_unload(self):
                    pass

                def health_check(self):
                    return HealthStatus(ok=True)

                def notify(self, *a, **k):
                    pass

            get_registry().register(
                _Live(),
                build_context(
                    plugin_id="acme-notify", tenant_id="_default", corvin_home=Path("/tmp")
                ),
            )
            try:
                breaker = cb.get_breaker("acme-notify", failure_threshold=1)
                breaker.record_failure(RuntimeError())
                client.post(
                    "/v1/console/plugins/acme-notify/enable",
                    json={},
                    headers=self._hdr(csrf),
                )
                body = client.get("/v1/console/plugins/acme-notify").json()
                self.assertTrue(body["runtime_loaded"])
                self.assertTrue(
                    (body["contained_by"] or "").startswith("breaker_"),
                    f"expected a breaker reason, got {body['contained_by']!r}",
                )
            finally:
                try:
                    get_registry().unregister("acme-notify")
                except Exception:
                    pass
                cb.forget("acme-notify")

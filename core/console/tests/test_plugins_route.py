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


#: corvin_plugins is deliberately NOT purged. It resolves CORVIN_HOME per call, so
#: reloading it buys nothing — and it costs a lot: operator/bridges/shared/audit.py
#: binds `_audit_sink` to the audit_backend MODULE at import time and never
#: re-resolves it, so a copy created inside the purge window becomes the sink that
#: every later audit_event() fans out into, while the plugin tests hold the original.
#: Measured: two fan-out tests failed with 0 deliveries whenever this file ran first.
#: Purging it also forks every enum, breaking `origin is PluginOrigin.COMMUNITY`.
_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")


def _snapshot_modules() -> dict:
    return {
        k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)
    }


def _reset_modules(restore: dict | None = None) -> None:
    """Purge the app modules so the next import re-reads this test's CORVIN_HOME.

    ``restore`` puts back exactly the objects that were loaded before. Purging
    WITHOUT restoring poisons the rest of the run: every test module bound its
    names at collection time, so a later re-import hands out a SECOND copy of
    corvin_plugins, and `record.origin is PluginOrigin.COMMUNITY` compares two
    different enum classes. Measured: 9 plugin tests failed when this file ran
    first, and passed when it ran last — a suite whose green depends on collection
    order is not a passing suite.
    """
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
    # Keep the real GDPR chain out of the test run (tests/conftest.py convention).
    os.environ["VOICE_AUDIT_PATH"] = str(home / "audit.jsonl")
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


class TestContainmentReasonIsDerived(_Base):
    """Review finding: "not loaded" was reported as "healing_unloaded".

    That is a false statement in an operator-facing surface — a record with no
    class_path was never loadable, and a boot that has not reached the plugin has
    not healed anything. The reason must come from the healer's history.
    """

    @contextmanager
    def _live(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            yield client, csrf, home

    def test_a_never_loaded_plugin_is_not_blamed_on_healing(self):
        with self._live() as (client, csrf, _home):
            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            body = client.get("/v1/console/plugins/acme-notify").json()
            self.assertFalse(body["runtime_loaded"])
            self.assertEqual(
                body["contained_by"], "not_loaded",
                "no class_path means nothing loaded it — not that healing removed it",
            )

    def test_healing_disable_is_reported_as_such(self):
        with self._live() as (client, csrf, _home):
            from corvin_console.routes import plugins as route_mod
            from corvin_plugins.healing import HealingAction, HealingRecord

            class _FakeHealer:
                def history(self, plugin_id):
                    return [
                        HealingRecord(
                            plugin_id=plugin_id,
                            action=HealingAction.DISABLE,
                            reason="unhealthy",
                        )
                    ]

            class _FakeCollector:
                running = True
                _healer = _FakeHealer()

                def snapshot(self):
                    class _S:
                        @staticmethod
                        def to_dict():
                            return {"taken_at": 0.0, "plugins": {}}

                    return _S()

            client.post("/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf))
            client.post(
                "/v1/console/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            route_mod.set_collector(_FakeCollector())
            try:
                body = client.get("/v1/console/plugins/acme-notify").json()
                self.assertEqual(body["contained_by"], "healing_unloaded")
            finally:
                route_mod.set_collector(None)


class TestScaffoldedPlugins(_Base):
    """`/plugins/scaffolded` (ADR-0253) — Plugin-Builder scaffolds, gated by
    `plugin_builder_enabled` independently of `plugin_console_surface`."""

    def test_404s_while_plugin_builder_flag_is_off(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_console_surface", True)
            resp = client.get("/v1/console/plugins/scaffolded")
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_empty_list_when_nothing_scaffolded_yet(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home):
            self._flag(client, csrf, "plugin_builder_enabled", True)
            resp = client.get("/v1/console/plugins/scaffolded")
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json(), {"scaffolds": [], "total": 0})

    def test_a_recorded_scaffold_is_listed(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_builder_enabled", True)

            from plugin_builder import index_store
            from plugin_builder.generators.scaffold import ScaffoldResult
            from plugin_builder.models import (
                Classification,
                Constraints,
                DependencySpec,
                PluginIdea,
                PluginKind,
                ProblemStatement,
                Tier,
            )

            idea = PluginIdea(
                plugin_name="Postgres Connector",
                problem=ProblemStatement("query postgres", "analysts", "none", "mvp"),
                dependencies=DependencySpec(),
                constraints=Constraints(),
            )
            classification = Classification(
                kind=PluginKind.PROVIDER, tier=Tier.B_COMPUTE, confidence=1.0,
                rationale="test", plugin_type="data_connector",
            )
            result = ScaffoldResult(
                dest=home / "plugin-builder" / "community_postgres_connector",
                plugin_id="community.postgres-connector",
                classification=classification,
                doc_files=(), scaffold_files=(), warnings=(),
            )
            index_store.record("_default", idea, result)

            resp = client.get("/v1/console/plugins/scaffolded")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["total"], 1)
            entry = body["scaffolds"][0]
            self.assertEqual(entry["plugin_id"], "community.postgres-connector")
            self.assertEqual(entry["display_name"], "Postgres Connector")
            self.assertEqual(entry["plugin_type"], "data_connector")


class TestTheSandboxDoesNotPoisonTheRun(unittest.TestCase):
    """Review finding: this file's module purge made other suites fail.

    _sandbox() purges the app modules so the lifespan re-reads its CORVIN_HOME. It
    used to purge corvin_plugins too, and never restored anything. Measured: 9 plugin
    tests failed when this file ran first and passed when it ran last — a suite whose
    green depends on collection order is not a passing suite, and every "all green"
    report before this was order-luck.

    Two distinct mechanisms, both worth pinning:
      * a second copy of corvin_plugins forks every enum, so
        `record.origin is PluginOrigin.COMMUNITY` compares two different classes;
      * operator/bridges/shared/audit.py binds `_audit_sink` to the audit_backend
        MODULE at import time and never re-resolves it, so a copy created inside the
        purge window silently becomes the sink every later audit_event() fans into.
    """

    def test_corvin_plugins_is_never_purged(self):
        self.assertNotIn(
            "corvin_plugins", _PURGED_PREFIXES,
            "purging corvin_plugins forks its enums and steals audit.py's fan-out sink",
        )

    def test_what_is_purged_is_restored(self):
        import corvin_plugins.manifest as manifest_before
        from corvin_plugins.manifest import PluginOrigin as origin_before

        before = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)}
        with tempfile.TemporaryDirectory() as tmp:
            with _sandbox(Path(tmp)):
                pass
        after = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED_PREFIXES)}

        swapped = [k for k, v in before.items() if after.get(k) is not v]
        self.assertEqual(swapped, [], f"module identity changed for {swapped}")

        import corvin_plugins.manifest as manifest_after
        from corvin_plugins.manifest import PluginOrigin as origin_after

        self.assertIs(manifest_before, manifest_after)
        self.assertIs(
            origin_before, origin_after,
            "a forked enum breaks `is` comparisons in every later test",
        )

    def test_the_fanout_sink_still_points_at_the_live_registry(self):
        """audit.py binds the sink module once; the sandbox must not swap it out."""
        with tempfile.TemporaryDirectory() as tmp:
            with _sandbox(Path(tmp)):
                pass
        try:
            import audit as bridge_audit  # type: ignore[import-not-found]
        except ImportError:
            self.skipTest("bridge audit module not importable in this layout")
        if bridge_audit._audit_sink is None:
            self.skipTest("plugin package absent in this layout")
        from corvin_plugins.providers import audit_backend as live

        self.assertIs(
            bridge_audit._audit_sink, live,
            "audit_event() would fan out into a registry nobody reads",
        )

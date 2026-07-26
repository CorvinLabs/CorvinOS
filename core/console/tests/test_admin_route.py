"""HTTP route-level tests for the admin control plane (ADR-0239/0243, Phase 4).

Mirrors the ``_sandbox`` TestClient pattern from test_plugins_route.py.

What these tests are FOR, in order of how much they would hurt if they were
missing:

* the compliance layer must answer 403 on disable and write an audit event — not
  200 with a silent no-op, and not 404 because the plugin happens to have no
  registry record;
* the target tenant must come from the authenticated session and from nowhere
  else — a query parameter, a header or a body field must not move it;
* with ``admin_control_plane`` off, all six routes must be ABSENT (404).  A flag
  that is only ever tested in one state rots (CLAUDE.md).
"""
from __future__ import annotations

import json
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


#: Same rule as test_plugins_route.py: corvin_plugins is NEVER purged.  A second
#: copy forks every enum (``origin is PluginOrigin.COMMUNITY`` then compares two
#: classes) and steals the audit fan-out sink that bridges/shared/audit.py bound
#: at import time.
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
def _sandbox(tmp_path: Path, *, tenants: tuple[str, ...] = ("_default",)):
    home = tmp_path / "corvin_home"
    for tenant_id in tenants:
        (home / "tenants" / tenant_id / "global" / "auth").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "forge").mkdir(parents=True)
        (home / "tenants" / tenant_id / "global" / "console" / "sessions").mkdir(parents=True)

    prev = {k: os.environ.get(k) for k in
            ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH")}
    os.environ["CORVIN_HOME"] = str(home)
    os.environ["CORVIN_TENANT_ID"] = tenants[0]
    # Keep the real GDPR chain out of the test run (tests/conftest.py convention).
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

        clients = {}
        for tenant_id in tenants:
            rec = _auth.create_session(tenant_id=tenant_id, token_fingerprint="test-fp")
            client = TestClient(app, raise_server_exceptions=False)
            client.cookies.set("corvin_console_sid", rec.sid)
            clients[tenant_id] = (
                client,
                _auth.derive_csrf_token(rec.csrf_secret, rec.sid),
            )
        first = clients[tenants[0]]
        yield first[0], first[1], home, clients
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        _reset_modules(restore=preloaded)


_ADMIN = "/v1/console/api/admin"

_RECORD = {
    "plugin_id": "acme-notify",
    "version": "1.0.0",
    "display_name": "Acme Notify",
    "plugin_type": "notification_backend",
    "origin": "vetted",
    "pii_risk": "low",
    "network_egress": "none",
    "locality": "local",
    "settings_schema": {
        "type": "object",
        "properties": {"channel": {"type": "string", "default": "ops"}},
        "required": ["channel"],
        "additionalProperties": False,
    },
    "settings": {"channel": "ops"},
}


def _audit_events(home: Path, tenant_id: str = "_default") -> list[dict]:
    """Every console audit record written for a tenant, oldest first."""
    chain = home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    out = []
    for line in chain.read_text().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return out


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

    def _install(self, client, csrf, record: dict | None = None) -> None:
        """Install a record through the (separate) plugin console surface."""
        self._flag(client, csrf, "plugin_console_surface", True)
        resp = client.post(
            "/v1/console/plugins", json=record or _RECORD, headers=self._hdr(csrf)
        )
        assert resp.status_code == 200, resp.text

    @contextmanager
    def _live(self, tenants: tuple[str, ...] = ("_default",)):
        """Admin plane + lifecycle on, one installed record."""
        with _sandbox(Path(self._tmp), tenants=tenants) as (client, csrf, home, clients):
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            self._flag(client, csrf, "admin_control_plane", True)
            yield client, csrf, home, clients


# ── Flag OFF ──────────────────────────────────────────────────────────────────


class TestFlagOff(_Base):
    def test_all_six_routes_404_on_a_fresh_install(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home, _all):
            hdr = self._hdr(csrf)
            for method, path, kwargs in (
                ("get", f"{_ADMIN}/plugins", {}),
                ("get", f"{_ADMIN}/plugins/acme-notify", {}),
                ("get", f"{_ADMIN}/health", {}),
                ("post", f"{_ADMIN}/plugins/acme-notify/enable",
                 {"json": {}, "headers": hdr}),
                ("post", f"{_ADMIN}/plugins/acme-notify/disable",
                 {"json": {}, "headers": hdr}),
                ("put", f"{_ADMIN}/plugins/acme-notify/config",
                 {"json": {"settings": {}}, "headers": hdr}),
            ):
                resp = getattr(client, method)(path, **kwargs)
                self.assertEqual(resp.status_code, 404, f"{method} {path}: {resp.text}")

    def test_a_malformed_body_still_404s_rather_than_422(self):
        """A dark feature must be indistinguishable from an absent one.

        The gate is a DEPENDENCY: an in-body check would let FastAPI's body
        validation answer 422 first and thereby confirm the route exists.
        """
        with _sandbox(Path(self._tmp)) as (client, csrf, _home, _all):
            resp = client.put(
                f"{_ADMIN}/plugins/acme-notify/config",
                json={"not_a_field": 1},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 404, resp.text)

    def test_the_flag_ships_dark(self):
        with _sandbox(Path(self._tmp)) as (client, _csrf, _home, _all):
            features = {f["id"]: f for f in
                        client.get("/v1/console/settings/features").json()["features"]}
            self.assertIn("admin_control_plane", features)
            self.assertFalse(features["admin_control_plane"]["enabled"])
            self.assertFalse(features["admin_control_plane"]["default"])

    def test_flag_off_writes_nothing(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, home, _all):
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            client.post(
                f"{_ADMIN}/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            self.assertFalse(
                (home / "tenants" / "_default" / "plugins" / "registry.yaml").exists()
            )


# ── Flag ON — the happy paths ─────────────────────────────────────────────────


class TestReadSurface(_Base):
    def test_list_is_empty_on_a_fresh_tenant(self):
        with self._live() as (client, _csrf, _home, _all):
            resp = client.get(f"{_ADMIN}/plugins")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["total"], 0)
            self.assertEqual(body["tenant_id"], "_default")
            self.assertTrue(body["lifecycle_enabled"])

    def test_list_and_detail_carry_the_administration_fields(self):
        with self._live() as (client, csrf, _home, _all):
            self._install(client, csrf)

            body = client.get(f"{_ADMIN}/plugins").json()
            self.assertEqual(body["total"], 1)
            entry = body["plugins"][0]
            for key in ("plugin_id", "version", "boot_layer", "origin", "enabled",
                        "can_disable", "health"):
                self.assertIn(key, entry)
            self.assertEqual(entry["plugin_id"], "acme-notify")
            self.assertEqual(entry["boot_layer"], "installed")
            self.assertEqual(entry["origin"], "vetted")
            self.assertFalse(entry["enabled"], "install must not enable")
            self.assertTrue(entry["can_disable"])
            self.assertEqual(entry["source"], "registry")

            detail = client.get(f"{_ADMIN}/plugins/acme-notify")
            self.assertEqual(detail.status_code, 200, detail.text)
            self.assertEqual(detail.json()["settings"], {"channel": "ops"})
            self.assertIn("channel", detail.json()["settings_schema"]["properties"])

    def test_aggregated_health(self):
        with self._live() as (client, csrf, _home, _all):
            self._install(client, csrf)
            resp = client.get(f"{_ADMIN}/health")
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertTrue(body["ok"])
            self.assertEqual(body["total"], 1)
            self.assertEqual(body["by_boot_layer"], {"installed": 1})
            # Nothing is loadable (no class_path), so health was never measured —
            # and must not be reported as healthy.
            self.assertFalse(body["plugins"]["acme-notify"]["checked"])
            self.assertIsNone(body["plugins"]["acme-notify"]["ok"])
            self.assertEqual(body["unchecked"], 1)


class TestMutations(_Base):
    def test_enable_disable_round_trip_is_audited(self):
        with self._live() as (client, csrf, home, _all):
            self._install(client, csrf)

            resp = client.post(
                f"{_ADMIN}/plugins/acme-notify/enable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertTrue(resp.json()["enabled"])

            resp = client.post(
                f"{_ADMIN}/plugins/acme-notify/disable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertFalse(resp.json()["enabled"])

            actions = [
                e["details"].get("target_id")
                for e in _audit_events(home)
                if e.get("event_type") == "console.action_performed"
                and e["details"].get("action", "").startswith("admin.plugin_")
            ]
            self.assertIn("acme-notify=enabled", actions)
            self.assertIn("acme-notify=disabled", actions)

    def test_config_writes_settings_and_never_their_values(self):
        with self._live() as (client, csrf, home, _all):
            self._install(client, csrf)
            resp = client.put(
                f"{_ADMIN}/plugins/acme-notify/config",
                json={"settings": {"channel": "s3cr3t-webhook-value"}},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertEqual(resp.json()["settings"], {"channel": "s3cr3t-webhook-value"})

            blob = json.dumps(_audit_events(home))
            self.assertNotIn(
                "s3cr3t-webhook-value", blob,
                "a settings VALUE reached the append-only audit chain",
            )

    def test_invalid_settings_are_422_and_do_not_persist(self):
        with self._live() as (client, csrf, _home, _all):
            self._install(client, csrf)
            resp = client.put(
                f"{_ADMIN}/plugins/acme-notify/config",
                json={"settings": {"channel": 42}},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertIn("channel", resp.json()["detail"])
            current = client.get(f"{_ADMIN}/plugins/acme-notify").json()
            self.assertEqual(current["settings"], {"channel": "ops"})

    def test_unknown_plugin_is_404_everywhere(self):
        with self._live() as (client, csrf, _home, _all):
            hdr = self._hdr(csrf)
            self.assertEqual(client.get(f"{_ADMIN}/plugins/nope").status_code, 404)
            self.assertEqual(
                client.post(f"{_ADMIN}/plugins/nope/enable", json={}, headers=hdr)
                .status_code, 404)
            self.assertEqual(
                client.post(f"{_ADMIN}/plugins/nope/disable", json={}, headers=hdr)
                .status_code, 404)
            self.assertEqual(
                client.put(f"{_ADMIN}/plugins/nope/config",
                           json={"settings": {}}, headers=hdr).status_code, 404)

    def test_lifecycle_flag_off_is_409_not_500(self):
        with _sandbox(Path(self._tmp)) as (client, csrf, _home, _all):
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            self._flag(client, csrf, "admin_control_plane", True)
            self._install(client, csrf)
            self._flag(client, csrf, "plugin_runtime_lifecycle", False)

            # The read side keeps working — only mutation is refused.
            self.assertEqual(client.get(f"{_ADMIN}/plugins").status_code, 200)

            for method, path, payload in (
                ("post", f"{_ADMIN}/plugins/acme-notify/enable", {}),
                ("post", f"{_ADMIN}/plugins/acme-notify/disable", {}),
                ("put", f"{_ADMIN}/plugins/acme-notify/config", {"settings": {}}),
            ):
                resp = getattr(client, method)(
                    path, json=payload, headers=self._hdr(csrf)
                )
                self.assertEqual(resp.status_code, 409, f"{path}: {resp.text}")
                self.assertIn("plugin_runtime_lifecycle", resp.json()["detail"])


# ── The compliance layer is not disableable ───────────────────────────────────


class TestComplianceLayerIsRefused(_Base):
    """CLAUDE.md § Compliance Baseline — "disable" is not an option here.

    Two shapes, because a compliance plugin can reach the admin plane through
    either source: a registry record that declares ``layer: compliance``, and a
    plugin that the boot path registered with that layer and that has no record
    at all.  The second one is the shape a registry-only implementation would
    answer 404 for — which would leave the guard unreachable.
    """

    def _write_compliance_record(self, home: Path) -> None:
        """Write a record CLAIMING the compliance boot layer into registry.yaml.

        This does not produce a compliance plugin, and that is the point. A
        per-tenant ``registry.yaml`` is operator-writable state on the tenant
        side of the trust boundary, so ``TenantRegistry.load()`` downgrades a
        privileged claim to ``installed`` and audits it. Before that guard
        existed, this file was enough to mint an entry the admin API refused to
        disable forever — an un-removable plugin from one line of YAML.

        Kept as a helper because the downgrade is worth asserting from the API's
        point of view, not only from the registry's.
        """
        from corvin_plugins.manifest import (
            BootLayer,
            Locality,
            NetworkEgress,
            PluginOrigin,
            PluginRecord,
        )
        from corvin_plugins.state import TenantRegistry, registry_path

        registry = TenantRegistry(registry_path(tenant_id="_default"))
        registry.records["audit-writer"] = PluginRecord(
            plugin_id="audit-writer",
            version="1.0.0",
            display_name="Audit Writer",
            plugin_type="audit_backend",
            boot_layer=BootLayer.COMPLIANCE,
            origin=PluginOrigin.BUILTIN,
            locality=Locality.LOCAL,
            network_egress=NetworkEgress.NONE,
            enabled=True,
        )
        registry.save()

    @contextmanager
    def _runtime_compliance_plugin(self):
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.protocol import HealthStatus
        from corvin_plugins.registry import get_registry

        class _Writer:
            plugin_id = "audit-writer"
            plugin_type = "audit_backend"
            version = "1.0.0"
            display_name = "Audit Writer"

            def on_load(self, ctx):
                pass

            def on_unload(self):
                pass

            def health_check(self):
                return HealthStatus(ok=True)

            def write(self, *a, **k):
                pass

        get_registry().register(
            _Writer(),
            build_context(
                plugin_id="audit-writer", tenant_id="_default", corvin_home=Path("/tmp")
            ),
            boot_layer="compliance",
        )
        try:
            yield
        finally:
            try:
                get_registry().unregister("audit-writer")
            except Exception:  # noqa: BLE001
                pass

    def test_a_tenant_written_compliance_claim_is_downgraded(self):
        """The other half of the guarantee: YAML cannot mint a protected plugin.

        The three tests below prove the compliance layer is un-disableable. That
        protection is only safe if a tenant cannot ASSIGN itself that layer —
        otherwise "un-disableable" becomes a self-service feature and the admin
        API refuses to remove an entry the tenant wrote by hand.
        """
        with self._live() as (client, csrf, home, _all):
            self._write_compliance_record(home)

            listed = {p["plugin_id"]: p for p in
                      client.get(f"{_ADMIN}/plugins").json()["plugins"]}
            self.assertIn("audit-writer", listed)
            self.assertEqual(
                listed["audit-writer"]["boot_layer"], "installed",
                "a compliance claim from registry.yaml must be downgraded",
            )
            self.assertTrue(
                listed["audit-writer"]["can_disable"],
                "the downgraded entry must be removable — otherwise the tenant "
                "just minted an un-deletable plugin",
            )

    def test_a_compliance_record_cannot_be_disabled(self):
        with self._live() as (client, csrf, home, _all), \
                self._runtime_compliance_plugin():
            listed = {p["plugin_id"]: p for p in
                      client.get(f"{_ADMIN}/plugins").json()["plugins"]}
            self.assertIn("audit-writer", listed)
            self.assertEqual(listed["audit-writer"]["boot_layer"], "compliance")
            self.assertFalse(listed["audit-writer"]["can_disable"])

            resp = client.post(
                f"{_ADMIN}/plugins/audit-writer/disable", json={}, headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 403, resp.text)

            # 403 must mean "refused", not "quietly did it anyway".
            from corvin_plugins.registry import get_registry
            self.assertIn("audit-writer", get_registry().discover())

            denials = [
                e for e in _audit_events(home)
                if e.get("event_type") == "console.action_denied"
                and e["details"].get("action") == "admin.plugin_disable"
            ]
            self.assertTrue(denials, "a refused disable must be audited")
            self.assertEqual(denials[-1]["details"]["target_id"], "audit-writer")
            self.assertEqual(denials[-1]["details"]["reason"], "compliance-layer")
            self.assertEqual(denials[-1]["details"]["tenant_id"], "_default")

    def test_a_compliance_record_cannot_be_reconfigured(self):
        """Config is the same hole with an extra step.

        "Where does the audit writer write" is not an operator setting: a route
        that refuses to switch the mechanism off while letting it be
        reconfigured has not protected anything.
        """
        with self._live() as (client, csrf, home, _all), \
                self._runtime_compliance_plugin():
            resp = client.put(
                f"{_ADMIN}/plugins/audit-writer/config",
                json={"settings": {"audit_path": "/tmp/elsewhere.jsonl"}},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 403, resp.text)
            self.assertEqual(
                client.get(f"{_ADMIN}/plugins/audit-writer").json()["settings"], {}
            )

            denials = [
                e for e in _audit_events(home)
                if e.get("event_type") == "console.action_denied"
                and e["details"].get("action") == "admin.plugin_config"
            ]
            self.assertTrue(denials, "a refused config write must be audited")
            self.assertEqual(denials[-1]["details"]["reason"], "compliance-layer")

    def test_the_refusal_does_not_depend_on_a_feature_flag(self):
        """403, not 409 "turn the lifecycle flag on".

        The second answer is technically true about the request and exactly the
        wrong thing to imply about the audit writer.
        """
        with _sandbox(Path(self._tmp)) as (client, csrf, home, _all), \
                self._runtime_compliance_plugin():
            self._flag(client, csrf, "admin_control_plane", True)
            # plugin_runtime_lifecycle stays OFF.
            for method, path, payload in (
                ("post", f"{_ADMIN}/plugins/audit-writer/disable", {}),
                ("put", f"{_ADMIN}/plugins/audit-writer/config",
                 {"settings": {"audit_path": "/tmp/elsewhere.jsonl"}}),
            ):
                resp = getattr(client, method)(
                    path, json=payload, headers=self._hdr(csrf)
                )
                self.assertEqual(resp.status_code, 403, f"{path}: {resp.text}")

    def test_a_runtime_only_compliance_plugin_is_403_not_404(self):
        with self._live() as (client, csrf, home, _all):
            with self._runtime_compliance_plugin():
                listed = {p["plugin_id"]: p for p in
                          client.get(f"{_ADMIN}/plugins").json()["plugins"]}
                self.assertIn(
                    "audit-writer", listed,
                    "a compliance plugin with no registry record must still be visible",
                )
                self.assertEqual(listed["audit-writer"]["source"], "runtime")
                self.assertFalse(listed["audit-writer"]["can_disable"])
                self.assertIsNone(listed["audit-writer"]["origin"])

                resp = client.post(
                    f"{_ADMIN}/plugins/audit-writer/disable",
                    json={},
                    headers=self._hdr(csrf),
                )
                self.assertEqual(resp.status_code, 403, resp.text)

                from corvin_plugins.registry import get_registry

                self.assertIn(
                    "audit-writer", get_registry().discover(),
                    "the refused disable must not have unloaded the plugin",
                )

            denials = [
                e for e in _audit_events(home)
                if e.get("event_type") == "console.action_denied"
                and e["details"].get("action") == "admin.plugin_disable"
            ]
            self.assertTrue(denials, "a refused disable must be audited")

    def test_a_disableable_runtime_plugin_is_actually_unloaded(self):
        """The counterpart: the guard refuses compliance, not everything.

        Without this, ``can_disable: False`` for every plugin would also make
        the compliance test pass — a guard that refuses everything is not a
        guard, it is a broken route.
        """
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.protocol import HealthStatus
        from corvin_plugins.registry import get_registry

        class _Bundled:
            plugin_id = "bundled-thing"
            plugin_type = "notification_backend"
            version = "2.0.0"
            display_name = "Bundled Thing"

            def on_load(self, ctx):
                pass

            def on_unload(self):
                pass

            def health_check(self):
                return HealthStatus(ok=True)

            def notify(self, *a, **k):
                pass

        with self._live() as (client, csrf, _home, _all):
            # A bundled plugin belongs to the tenant that enabled it, so it is
            # listed only when THIS tenant also has a record for it. Treating
            # `bundled` as process-global let tenant B see (and stop) tenant A's
            # bridge, so the record is now part of what makes it visible.
            self._install(client, csrf, {**_RECORD, "plugin_id": "bundled-thing",
                                         "plugin_type": "notification_backend",
                                         "version": "2.0.0",
                                         "display_name": "Bundled Thing"})
            get_registry().register(
                _Bundled(),
                build_context(
                    plugin_id="bundled-thing",
                    tenant_id="_default",
                    corvin_home=Path("/tmp"),
                ),
                boot_layer="bundled",
            )
            try:
                listed = {p["plugin_id"]: p for p in
                          client.get(f"{_ADMIN}/plugins").json()["plugins"]}
                self.assertTrue(listed["bundled-thing"]["can_disable"])
                self.assertTrue(listed["bundled-thing"]["runtime_loaded"])

                resp = client.post(
                    f"{_ADMIN}/plugins/bundled-thing/disable",
                    json={},
                    headers=self._hdr(csrf),
                )
                self.assertEqual(resp.status_code, 200, resp.text)
                self.assertNotIn("bundled-thing", get_registry().discover())
            finally:
                try:
                    get_registry().unregister("bundled-thing")
                except Exception:  # noqa: BLE001
                    pass


# ── Tenant binding ────────────────────────────────────────────────────────────


class TestTenantComesFromTheSession(_Base):
    """CLAUDE.md § Multi-tenant axis — ``rec.tenant_id`` and nothing else."""

    def test_query_and_header_do_not_move_the_target_tenant(self):
        with self._live(tenants=("_default", "tenant-b")) as (client, csrf, _home, all_):
            self._install(client, csrf)

            resp = client.get(
                f"{_ADMIN}/plugins",
                params={"tenant_id": "tenant-b"},
                headers={"X-Tenant-Id": "tenant-b", "X-Corvin-Tenant": "tenant-b"},
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            body = resp.json()
            self.assertEqual(body["tenant_id"], "_default")
            self.assertEqual([p["plugin_id"] for p in body["plugins"]], ["acme-notify"])

    def test_a_body_field_is_rejected_rather_than_silently_dropped(self):
        with self._live() as (client, csrf, _home, _all):
            self._install(client, csrf)
            resp = client.post(
                f"{_ADMIN}/plugins/acme-notify/enable",
                json={"tenant_id": "tenant-b", "consent_granted": True},
                headers=self._hdr(csrf),
            )
            self.assertEqual(resp.status_code, 422, resp.text)
            self.assertFalse(
                client.get(f"{_ADMIN}/plugins/acme-notify").json()["enabled"]
            )

    def test_another_tenants_session_sees_its_own_registry(self):
        """The real proof: the same process, two sessions, two answers."""
        with self._live(tenants=("_default", "tenant-b")) as (client, csrf, _home, all_):
            self._install(client, csrf)

            other, other_csrf = all_["tenant-b"]
            self._flag(other, other_csrf, "admin_control_plane", True)
            body = other.get(f"{_ADMIN}/plugins").json()
            self.assertEqual(body["tenant_id"], "tenant-b")
            self.assertEqual(
                body["plugins"], [],
                "tenant-b must not see a plugin installed for _default",
            )

    def test_a_shared_plugin_id_does_not_hand_over_another_tenants_instance(self):
        """Owning the RECORD is not owning the OBJECT.

        The process registry is keyed by plugin_id alone, so two tenants who
        install the same marketplace plugin share one loaded instance — it runs
        with whichever tenant's context loaded it first. Attaching that object
        to the second tenant's record let them disable it with an ordinary 200,
        which stops the FIRST tenant's plugin and writes nothing into that
        tenant's audit chain. Hiding foreign IDs never covered this case,
        because the id is not foreign — it is the same.
        """
        from corvin_plugins.bootstrap import build_context
        from corvin_plugins.protocol import HealthStatus
        from corvin_plugins.registry import get_registry

        class _Shared:
            plugin_id = "acme-notify"
            plugin_type = "notification_backend"
            version = "1.0.0"
            display_name = "Acme Notify"

            def on_load(self, ctx):
                pass

            def on_unload(self):
                pass

            def health_check(self):
                return HealthStatus(ok=True)

            def notify(self, *a, **k):
                pass

        with self._live(tenants=("_default", "tenant-b")) as (client, csrf, _home, all_):
            self._install(client, csrf)
            other, other_csrf = all_["tenant-b"]
            self._flag(other, other_csrf, "plugin_runtime_lifecycle", True)
            self._flag(other, other_csrf, "admin_control_plane", True)
            self._install(other, other_csrf)  # same plugin_id, tenant-b's record

            # The instance is loaded for _default.
            get_registry().register(
                _Shared(),
                build_context(
                    plugin_id="acme-notify",
                    tenant_id="_default",
                    corvin_home=Path("/tmp"),
                ),
                boot_layer="installed",
            )
            try:
                mine = client.get(f"{_ADMIN}/plugins/acme-notify").json()
                self.assertTrue(
                    mine["runtime_loaded"], "the owner must see it as loaded"
                )

                theirs = other.get(f"{_ADMIN}/plugins/acme-notify").json()
                self.assertFalse(
                    theirs["runtime_loaded"],
                    "tenant-b owns a record, not the running object",
                )

                resp = other.post(
                    f"{_ADMIN}/plugins/acme-notify/disable",
                    json={},
                    headers=self._hdr(other_csrf),
                )
                # Whatever the status, the other tenant's instance must survive.
                self.assertIn(
                    "acme-notify", get_registry().discover(),
                    f"tenant-b unloaded _default's running plugin "
                    f"(status {resp.status_code})",
                )
            finally:
                try:
                    get_registry().unregister("acme-notify")
                except Exception:  # noqa: BLE001
                    pass

    def test_the_env_var_does_not_win_over_the_session(self):
        with self._live(tenants=("_default", "tenant-b")) as (client, csrf, _home, all_):
            self._install(client, csrf)
            prev = os.environ.get("CORVIN_TENANT_ID")
            os.environ["CORVIN_TENANT_ID"] = "tenant-b"
            try:
                body = client.get(f"{_ADMIN}/plugins").json()
                self.assertEqual(body["tenant_id"], "_default")
                self.assertEqual(
                    [p["plugin_id"] for p in body["plugins"]], ["acme-notify"],
                    "the env var must not redirect an authenticated session",
                )
            finally:
                if prev is None:
                    os.environ.pop("CORVIN_TENANT_ID", None)
                else:
                    os.environ["CORVIN_TENANT_ID"] = prev


# ── Auth ──────────────────────────────────────────────────────────────────────


class TestAuth(_Base):
    def test_unauthenticated_requests_are_rejected(self):
        with self._live() as (client, csrf, _home, _all):
            hdr = self._hdr(csrf)
            client.cookies.clear()
            for method, path, kwargs in (
                ("get", f"{_ADMIN}/plugins", {}),
                ("get", f"{_ADMIN}/plugins/acme-notify", {}),
                ("get", f"{_ADMIN}/health", {}),
                ("post", f"{_ADMIN}/plugins/acme-notify/enable",
                 {"json": {}, "headers": hdr}),
                ("post", f"{_ADMIN}/plugins/acme-notify/disable",
                 {"json": {}, "headers": hdr}),
                ("put", f"{_ADMIN}/plugins/acme-notify/config",
                 {"json": {"settings": {}}, "headers": hdr}),
            ):
                resp = getattr(client, method)(path, **kwargs)
                self.assertIn(
                    resp.status_code, (401, 403), f"{method} {path}: {resp.status_code}"
                )

    def test_mutations_require_the_csrf_header(self):
        with self._live() as (client, _csrf, _home, _all):
            for method, path, payload in (
                ("post", f"{_ADMIN}/plugins/acme-notify/enable", {}),
                ("post", f"{_ADMIN}/plugins/acme-notify/disable", {}),
                ("put", f"{_ADMIN}/plugins/acme-notify/config", {"settings": {}}),
            ):
                resp = getattr(client, method)(path, json=payload)
                self.assertIn(
                    resp.status_code, (401, 403), f"{path}: {resp.status_code}"
                )


if __name__ == "__main__":
    unittest.main()

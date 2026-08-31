"""Two doors onto one mechanism must give one answer (ADR-0233/0239/0243).

``POST /v1/console/plugins/{id}/disable`` (the older console surface) and
``POST /v1/console/api/admin/plugins/{id}/disable`` (the admin control plane)
both end at the same guard: ``registry.disable()`` refuses to unload a plugin
that occupies the *compliance* boot layer and raises ``PluginDisableRefused``.

The admin door answered 403 plus a ``console.action_denied`` audit event.  The
console door answered **500 "plugin operation failed"** and wrote nothing —
because ``PluginDisableRefused`` inherits from ``PermissionError``, not from
``PluginError``, so it matched none of the mapped branches in
``_mutation_error()`` and fell through to the catch-all.  An operator asking
whether the audit writer can be switched off was told "internal error", and the
GDPR Art. 30 chain has no record that they asked.

The protection itself was never missing (the plugin stays loaded either way).
What was missing is the *answer* and the *trail*, which is precisely what makes
a refusal auditable rather than a crash.
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

#: corvin_plugins is NEVER purged (test_plugins_route.py convention): a second
#: copy forks every enum and steals the audit fan-out sink that
#: operator/bridges/shared/audit.py bound to the module at import time.
_PURGED_PREFIXES = ("corvin_console", "corvin_gateway", "forge")


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

    preloaded = {k: v for k, v in sys.modules.items()
                 if k.startswith(_PURGED_PREFIXES)}
    for key in list(sys.modules):
        if key.startswith(_PURGED_PREFIXES):
            del sys.modules[key]
    try:
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
        for key in list(sys.modules):
            if key.startswith(_PURGED_PREFIXES):
                del sys.modules[key]
        sys.modules.update(preloaded)


#: The registry record. plugin_type/origin are ordinary — the compliance status
#: comes from the RUNTIME registration below, never from anything a tenant may
#: write into its own registry.yaml (a self-assigned compliance layer is
#: downgraded on read, see test_admin_route.py).
_RECORD = {
    "plugin_id": "audit-writer",
    "version": "1.0.0",
    "display_name": "Audit Writer",
    "plugin_type": "audit_backend",
    "origin": "vetted",
    "pii_risk": "low",
    "locality": "local",
    "network_egress": "none",
    "settings_schema": {},
    "settings": {},
}


def _audit_events(home: Path, tenant_id: str = "_default") -> list[dict]:
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


@contextmanager
def _runtime_compliance_plugin(plugin_id: str = "audit-writer"):
    """Register *plugin_id* on the compliance boot layer of the live registry."""
    from corvin_plugins.bootstrap import build_context
    from corvin_plugins.protocol import HealthStatus
    from corvin_plugins.registry import get_registry

    # A privileged registration is remembered ACROSS ALL EPOCHS by design
    # (ADR-0233 D5, registry.py:424 branch (a)): once `audit-writer` has held
    # the compliance layer in this PROCESS, any later re-registration is a
    # re-escalation attempt and is downgraded to `installed` — correctly. A
    # real boot is a fresh PROCESS, so the honest simulation is to forget this
    # id's privilege history, not to advance the epoch (that only guarantees a
    # DIFFERENT epoch and thus trips branch (a) even harder). Without this the
    # SECOND test in the process registers a non-compliance plugin and then
    # asserts the compliance refusal, which reads as "the compliance layer has
    # no off switch — broken" when the product is fine.
    _reg = get_registry()
    _reg._privileged_registration_epoch.pop(plugin_id, None)
    _reg._unregistered_this_epoch.pop(plugin_id, None)

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
            plugin_id=plugin_id, tenant_id="_default", corvin_home=Path("/tmp")
        ),
        boot_layer="compliance",
    )
    try:
        yield
    finally:
        try:
            get_registry().unregister(plugin_id)
        except Exception:  # noqa: BLE001
            pass


class TestConsoleDisableRefusal(unittest.TestCase):
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

    @contextmanager
    def _live(self):
        """Console plugin surface + lifecycle on, one installed + enabled record."""
        with _sandbox(Path(self._tmp)) as (client, csrf, home):
            self._flag(client, csrf, "plugin_console_surface", True)
            self._flag(client, csrf, "plugin_runtime_lifecycle", True)
            resp = client.post(
                "/v1/console/plugins", json=_RECORD, headers=self._hdr(csrf)
            )
            assert resp.status_code == 200, resp.text
            resp = client.post(
                "/v1/console/plugins/audit-writer/enable",
                json={},
                headers=self._hdr(csrf),
            )
            assert resp.status_code == 200, resp.text
            yield client, csrf, home

    def test_disabling_a_compliance_plugin_is_403_not_500(self):
        with self._live() as (client, csrf, _home), _runtime_compliance_plugin():
            resp = client.post(
                "/v1/console/plugins/audit-writer/disable", headers=self._hdr(csrf)
            )
            self.assertEqual(
                resp.status_code, 403,
                f"the console door must refuse like the admin door does: {resp.text}",
            )
            self.assertIn("compliance layer", resp.json()["detail"])

    def test_the_refusal_is_audited_like_the_admin_door(self):
        with self._live() as (client, csrf, home), _runtime_compliance_plugin():
            client.post(
                "/v1/console/plugins/audit-writer/disable", headers=self._hdr(csrf)
            )
            denials = [
                e for e in _audit_events(home)
                if e.get("event_type") == "console.action_denied"
                and e["details"].get("target_kind") == "plugin"
            ]
            self.assertTrue(denials, "a refused disable must leave a trail")
            details = denials[-1]["details"]
            # Same event type, same target shape, same reason vocabulary as
            # routes/admin.py::_audit_denied — the two doors must be diffable.
            self.assertEqual(details["target_id"], "audit-writer")
            self.assertEqual(details["reason"], "compliance-layer")
            self.assertEqual(details["tenant_id"], "_default")
            self.assertEqual(details["action"], "plugin.disable")

    def test_403_means_refused_not_quietly_done_anyway(self):
        from corvin_plugins.registry import get_registry

        with self._live() as (client, csrf, _home), _runtime_compliance_plugin():
            client.post(
                "/v1/console/plugins/audit-writer/disable", headers=self._hdr(csrf)
            )
            self.assertIn(
                "audit-writer", get_registry().discover(),
                "the plugin must still be loaded after a refused disable",
            )
            # And the registry record must not have been flipped either: the
            # refusal fires inside the locked mutation, before save().
            listed = client.get("/v1/console/plugins/audit-writer").json()
            self.assertTrue(
                listed["enabled"],
                "a refused disable must not leave the record disabled",
            )

    def test_an_ordinary_plugin_still_disables(self):
        """Counter-test: the mapping must not turn every disable into a 403."""
        with self._live() as (client, csrf, _home):
            resp = client.post(
                "/v1/console/plugins/audit-writer/disable", headers=self._hdr(csrf)
            )
            self.assertEqual(resp.status_code, 200, resp.text)
            self.assertFalse(resp.json()["enabled"])


if __name__ == "__main__":
    unittest.main()

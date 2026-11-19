"""ADR-2331 - private plugin sources, end to end through the real console routes.

A private source (CORVIN_PRIVATE_PLUGIN_ROOTS or the sibling CorvinOS-Apps/plugins) is searched after every trusted
root. A plugin installed from it is origin `community` (consent needed to enable), its console panel appears in the
sidebar manifest under its group once enabled, it can never take the id of a marketplace/builtin plugin, and an empty
CORVIN_PRIVATE_PLUGIN_ROOTS disables private sources. The fixture plugin is written into a temp dir; no real private
repository is read.
"""
from __future__ import annotations

import os
import sys
import tempfile
import textwrap
import unittest
from contextlib import contextmanager
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_REPO = _HERE.parents[2]
_OPERATOR = _REPO / "corvin_operator"
for _p in [str(_OPERATOR), str(_OPERATOR / "license"), str(_OPERATOR / "forge"), str(_REPO / "core" / "console"),
           str(_OPERATOR / "bridges" / "shared"), str(_REPO / "core" / "plugins")]:
    if _p not in sys.path:
        sys.path.insert(0, _p)

_MKT_BUILDIN = _REPO.parent / "Corvin-Marketplace" / "plugins" / "buildin"
_BASE = "/v1/console/api/v1/marketplace/plugins"
_PURGED = ("corvin_console", "corvin_gateway", "forge")

MANIFEST = """\
plugin_id: {pid}
plugin_type: web_surface
version: 0.1.0
display_name: Private Demo
description: private test plugin
origin: vetted
boot_layer: installed
locality: local
network_egress: none
pii_risk: low
requires_consent: false
audit_required: true
console_panel:
  label: Private Demo
  route: private-demo
  icon: Package
  group: marketplace
"""


@contextmanager
def _sandbox(tmp: Path, private_roots: str | None):
    home = tmp / "home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    keys = ("CORVIN_HOME", "CORVIN_TENANT_ID", "VOICE_AUDIT_PATH", "CORVIN_MARKETPLACE_ROOT",
            "CORVIN_PRIVATE_PLUGIN_ROOTS")
    prev = {k: os.environ.get(k) for k in keys}
    os.environ.update({"CORVIN_HOME": str(home), "CORVIN_TENANT_ID": "_default",
                       "VOICE_AUDIT_PATH": str(home / "audit.jsonl"), "CORVIN_MARKETPLACE_ROOT": str(_MKT_BUILDIN)})
    if private_roots is None:
        os.environ.pop("CORVIN_PRIVATE_PLUGIN_ROOTS", None)
    else:
        os.environ["CORVIN_PRIVATE_PLUGIN_ROOTS"] = private_roots
    saved = {k: v for k, v in sys.modules.items() if k.startswith(_PURGED)}
    try:
        for k in list(sys.modules):
            if k.startswith(_PURGED):
                del sys.modules[k]
        from corvin_console import auth as _auth
        from corvin_console.app import router
        from fastapi import FastAPI
        from fastapi.testclient import TestClient

        rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
        app = FastAPI()
        app.include_router(router, prefix="/v1/console")
        client = TestClient(app, raise_server_exceptions=False)
        client.cookies.set("corvin_console_sid", rec.sid)
        yield client, {"X-CSRF-Token": _auth.derive_csrf_token(rec.csrf_secret, rec.sid)}, home
    finally:
        for k, v in prev.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for k in list(sys.modules):
            if k.startswith(_PURGED):
                del sys.modules[k]
        sys.modules.update(saved)


def _private_tree(tmp: Path, pid: str = "private_demo", tier: str = "contributor") -> Path:
    plugins = tmp / "apps" / "plugins"
    d = plugins / tier / "orchestration" / pid
    d.mkdir(parents=True)
    (d / "plugin.yaml").write_text(MANIFEST.format(pid=pid))
    return plugins


@unittest.skipUnless(_MKT_BUILDIN.is_dir(), "sibling Corvin-Marketplace checkout not present")
class TestPrivatePluginSources(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _installed(self, client):
        r = client.get("/v1/console/plugins")
        self.assertEqual(r.status_code, 200, r.text)
        doc = r.json()
        items = doc.get("plugins", doc) if isinstance(doc, dict) else doc
        return {p["plugin_id"]: p for p in items}

    def test_install_is_community_consent_gated_and_puts_the_panel_in_marketplace(self):
        roots = _private_tree(self.tmp)
        with _sandbox(self.tmp, str(roots)) as (client, hdr, _home):
            r = client.post(f"{_BASE}/plugin:contributor-orchestration-private_demo/install", json={"wait": True},
                            headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotEqual(r.json().get("status"), "failed", r.text)
            rec = self._installed(client)["private_demo"]
            self.assertEqual(rec["origin"], "community")             # the manifest said vetted; the location decides
            self.assertFalse(rec["enabled"])
            refused = client.post("/v1/console/plugins/private_demo/enable", json={}, headers=hdr)
            self.assertGreaterEqual(refused.status_code, 400, refused.text)   # community needs explicit consent
            ok = client.post("/v1/console/plugins/private_demo/enable", json={"consent_granted": True}, headers=hdr)
            self.assertEqual(ok.status_code, 200, ok.text)
            man = client.get("/v1/console/capabilities/manifest").json()
            panel = next((p for p in man["panels"] if p["id"] == "plugin-private_demo"), None)
            self.assertIsNotNone(panel, [p["id"] for p in man["panels"]])
            self.assertEqual(panel["route"], "private-demo")
            self.assertEqual(panel["element"]["kind"], "plugin-inspector")   # no plugin code in the browser
            group = next(g for g in man["nav_groups"] if g["id"] == "marketplace")
            self.assertIn("plugin-private_demo", [i["panel_id"] for i in group["items"]])

    def test_a_private_buildin_folder_is_still_community(self):
        roots = _private_tree(self.tmp, pid="private_b", tier="buildin")
        with _sandbox(self.tmp, str(roots)) as (client, hdr, _home):
            r = client.post(f"{_BASE}/plugin:buildin-orchestration-private_b/install", json={"wait": True}, headers=hdr)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(self._installed(client)["private_b"]["origin"], "community")

    def test_a_private_plugin_cannot_take_a_marketplace_plugin_id(self):
        roots = _private_tree(self.tmp, pid="video_producer")
        with _sandbox(self.tmp, str(roots)) as (client, hdr, _home):
            r = client.post(f"{_BASE}/plugin:contributor-orchestration-video_producer/install", json={"wait": True},
                            headers=hdr)
            self.assertEqual(r.json().get("status"), "failed", r.text)
            self.assertIn("reuses the id of a marketplace or builtin plugin", r.json().get("error", ""))
            self.assertNotIn("video_producer", self._installed(client))

    def test_an_empty_setting_disables_private_sources(self):
        _private_tree(self.tmp)
        with _sandbox(self.tmp, "") as (client, hdr, _home):
            r = client.post(f"{_BASE}/plugin:contributor-orchestration-private_demo/install", json={"wait": True},
                            headers=hdr)
            self.assertEqual(r.json().get("status"), "failed", r.text)
            self.assertIn("not in the marketplace index", r.json().get("error", ""))
            self.assertNotIn("private_demo", self._installed(client))

    def test_positive_control_the_same_tree_installs_when_the_root_is_configured(self):
        roots = _private_tree(self.tmp)
        with _sandbox(self.tmp, str(roots)) as (client, hdr, _home):
            r = client.post(f"{_BASE}/plugin:contributor-orchestration-private_demo/install", json={"wait": True},
                            headers=hdr)
            self.assertEqual(r.json().get("status"), "completed", r.text)


if __name__ == "__main__":
    unittest.main()

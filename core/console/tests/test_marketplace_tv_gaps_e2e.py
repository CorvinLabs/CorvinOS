"""HTTP proof for the Marketplace "Packages" tab and the CorvinTV settings / default-style audit
(release-readiness gaps A-1 .. A-4, 2026-10-24).

Driven through the REAL console router (TestClient, real session cookie + CSRF, a temp CORVIN_HOME),
the same harness as ``test_video_producer_routes_e2e``. Nothing is mocked on the paths under test:
the package registry, the plugin registry record that stores the CorvinTV settings, the tenant
audit chain.

RED on origin/main (3029088f8-era 925406772):
  packages.py:60   registry dir = Path.home()/.corvin/packages  -> nothing lands under CORVIN_HOME
  packages.py:569  "TODO ... proceed with deletion"             -> a required package is deleted
  packages.py:584  rmtree without archive                       -> docstring promise broken
  video_producer_api.py:493,1695  settings in a process dict    -> lost on restart, no audit record
  video_producer_api.py:2352-2360 PUT /styles/default           -> no audit record
"""
from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_video_producer_routes_e2e as _routes  # noqa: E402
from test_video_producer_routes_e2e import _chain_text, _route_module, _sandbox  # noqa: E402
from test_video_producer_styles_e2e import _wire  # noqa: E402

P = "/v1/console/packages"
V = "/v1/console/video"
H = lambda csrf: {"X-CSRF-Token": csrf}  # noqa: E731


def _zip(name: str, version: str = "1.0.0", dependencies: list[str] | None = None) -> bytes:
    manifest = {"name": name, "version": version, "display_name": f"{name} package",
                "description": "A realistic skill package", "author": "Acme Analytics",
                "license": "Apache-2.0", "permissions": ["network:http"],
                "dependencies": dependencies or []}
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("manifest.json", json.dumps(manifest))
        zf.writestr("skills/report.md", "# Weekly report skill\nSummarise the sales table.\n")
    return buf.getvalue()


def _upload(client, csrf, data: bytes, filename="pkg.zip"):
    return client.post(f"{P}/upload", files={"file": (filename, data, "application/zip")}, headers=H(csrf))


def _events(home: Path, tenant: str, event: str) -> list[dict]:
    chain = home / "tenants" / tenant / "global" / "forge" / "audit.jsonl"
    out = []
    for ln in chain.read_text().splitlines() if chain.exists() else []:
        try:
            rec = json.loads(ln)
        except ValueError:
            continue
        if rec.get("event_type") == event or rec.get("event") == event:
            out.append(rec)
    return out


class PackagesTabE2E(unittest.TestCase):
    def test_packages_live_under_corvin_home_and_uninstall_is_safe(self):
        fake_home = Path(tempfile.mkdtemp())
        with mock.patch.dict(os.environ, {"HOME": str(fake_home)}), \
                _sandbox(Path(tempfile.mkdtemp()), tenants=("tenant_a", "tenant_b")) as (_c, _t, home, clients):
            from corvin_console.routes import packages as packages_mod
            ca, csrf = clients["tenant_a"]
            cb, _csrf_b = clients["tenant_b"]

            r = _upload(ca, csrf, _zip("sales-core"))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "installed")
            # the data is under the configured root, per tenant - and NOT under the real home
            installed = home / "tenants" / "tenant_a" / "packages" / "installed" / "sales-core"
            self.assertTrue((installed / "metadata.json").is_file(), "package not stored under CORVIN_HOME/tenants/<id>")
            self.assertTrue((installed / "skills" / "report.md").is_file())
            self.assertFalse((fake_home / ".corvin" / "packages").exists(),
                             "package leaked into the real ~/.corvin")

            # list / details through HTTP, tenant isolated
            lst = ca.get(P).json()
            self.assertEqual([p["package_id"] for p in lst["packages"]], ["sales-core"])
            self.assertEqual(cb.get(P).json()["total"], 0)
            self.assertEqual(cb.get(f"{P}/sales-core/details").status_code, 404)
            d = ca.get(f"{P}/sales-core/details")
            self.assertEqual(d.status_code, 200, d.text)
            self.assertEqual(d.json()["permissions"][0]["permission"], "network:http")
            self.assertEqual(d.json()["version"], "1.0.0")

            # same version again -> 409; invalid zip -> 400
            self.assertEqual(_upload(ca, csrf, _zip("sales-core")).status_code, 409)
            self.assertEqual(_upload(ca, csrf, b"not a zip").status_code, 400)

            # a dependent package pins the first one
            self.assertEqual(_upload(ca, csrf, _zip("sales-dash", dependencies=["sales-core>=1.0"])).status_code, 200)
            blocked = ca.delete(f"{P}/sales-core", headers=H(csrf))
            self.assertEqual(blocked.status_code, 409, blocked.text)
            self.assertIn("sales-dash", blocked.json()["detail"])
            self.assertTrue(installed.is_dir(), "a required package was deleted")

            # a path-traversal id is refused and deletes nothing
            root = home / "tenants" / "tenant_a" / "packages"
            for evil in ("%2e%2e", "..", "%2e"):
                self.assertIn(ca.delete(f"{P}/{evil}", headers=H(csrf)).status_code, (404, 422), evil)
            # the id pattern itself (reaches the handler only through a client that does not normalise)
            self.assertRegex("sales-core", packages_mod._PACKAGE_ID_PATTERN)
            self.assertNotRegex("..", packages_mod._PACKAGE_ID_PATTERN)
            self.assertEqual(ca.get(f"{P}/../details").status_code in (404, 422), True)
            self.assertTrue((root / "installed").is_dir())

            # uninstall the dependent, then the base: archived, not erased
            self.assertEqual(ca.delete(f"{P}/sales-dash", headers=H(csrf)).status_code, 200)
            gone = ca.delete(f"{P}/sales-core", headers=H(csrf))
            self.assertEqual(gone.status_code, 200, gone.text)
            self.assertTrue(gone.json()["archived"])
            self.assertFalse(installed.exists())
            archived = sorted((root / "archive").glob("sales-core-1.0.0-*"))
            self.assertEqual(len(archived), 1, "uninstall must keep the manifest in an archive")
            kept = json.loads((archived[0] / "metadata.json").read_text())
            self.assertEqual(kept["manifest"]["name"], "sales-core")
            self.assertEqual(ca.get(P).json()["total"], 0)
            self.assertEqual(ca.delete(f"{P}/sales-core", headers=H(csrf)).status_code, 404)

            # every mutation is on the tenant's chain
            text = _chain_text(home)
            self.assertEqual(text.count('"package.install"'), 2)
            self.assertEqual(text.count('"package.uninstall"'), 2)
            self.assertEqual(len(_events(home, "tenant_b", "console.action_performed")), 0)


class CorvinTvSettingsE2E(unittest.TestCase):
    def test_settings_persist_in_the_plugin_record_and_are_audited(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            g = client.get(f"{V}/settings").json()
            self.assertEqual((g["tts_engine"], g["max_duration_minutes"]), ("auto", 60))

            r = client.put(f"{V}/settings", json={"tts_engine": "gtts", "max_duration_minutes": 7}, headers=H(csrf))
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["settings"]["tts_engine"], "gtts")

            # single source of truth: the plugin registry record carries the very same values
            from corvin_plugins.state import TenantRegistry
            rec = TenantRegistry.load(tenant_id="_default").get("video_producer")
            self.assertEqual(rec.settings["tts_engine"], "gtts")
            self.assertEqual(rec.settings["max_video_length_minutes"], 7)

            # a "restart" of the panel state (nothing in process memory is consulted any more)
            mod = _route_module()
            self.assertFalse(hasattr(mod, "_settings"), "settings must not live in a process dict")
            again = client.get(f"{V}/settings").json()
            self.assertEqual((again["tts_engine"], again["max_duration_minutes"]), ("gtts", 7))

            # audited with values and previous values
            ev = _events(home, "_default", "video_producer.settings_changed")
            self.assertEqual(len(ev), 1, _chain_text(home)[-800:])
            det = ev[0]["details"]
            self.assertEqual((det["tts_engine"], det["previous_tts_engine"]), ("gtts", "auto"))
            self.assertEqual((det["max_duration_minutes"], det["previous_max_duration_minutes"]), (7, 60))

            # an unchanged PUT writes no record; a bad value is refused and changes nothing
            client.put(f"{V}/settings", json={"tts_engine": "gtts"}, headers=H(csrf))
            self.assertEqual(len(_events(home, "_default", "video_producer.settings_changed")), 1)
            self.assertEqual(client.put(f"{V}/settings", json={"max_duration_minutes": 61}, headers=H(csrf)).status_code, 422)
            self.assertEqual(client.get(f"{V}/settings").json()["max_duration_minutes"], 7)

    def test_settings_reach_the_job_the_runner_receives(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _clients):
            mod = _route_module()
            runner = _routes._RecordingRunner()
            mod.get_runner = lambda: runner
            client.put(f"{V}/settings", json={"tts_engine": "gtts", "max_duration_minutes": 5}, headers=H(csrf))
            holder = _routes.VideoProducerRoutesE2E("run")
            holder.setUp()
            try:
                r = holder._create(client, csrf)
            finally:
                holder.tearDown()
            self.assertEqual(r.status_code, 200, r.text)
            cfg = runner.calls[0][2]
            self.assertEqual((cfg["tts_engine"], cfg["max_duration_minutes"]), ("gtts", 5))


class DefaultStyleAuditE2E(unittest.TestCase):
    def test_changing_the_default_style_is_audited_first(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _clients):
            mod = _route_module()

            async def _no_previews(style, themes=None):
                return {"previews": [], "notes": []}

            mod._previews = _no_previews
            holder = _routes.VideoProducerRoutesE2E("run")
            holder.setUp()
            try:
                holder._benign_l44()
                r = client.post(f"{V}/styles", json={"draft": _wire()}, headers=H(csrf))
            finally:
                holder.tearDown()
            self.assertEqual(r.status_code, 201, r.text)
            sid = r.json()["style"]["id"]
            self.assertEqual(len(_events(home, "_default", "video_producer.style_default_changed")), 0)

            self.assertEqual(client.put(f"{V}/styles/default", json={"style_id": sid}, headers=H(csrf)).status_code, 200)
            ev = _events(home, "_default", "video_producer.style_default_changed")
            self.assertEqual(len(ev), 1)
            self.assertEqual(ev[0]["details"]["style_id"], sid)

            # clearing it is a change too; repeating it is not
            self.assertEqual(client.put(f"{V}/styles/default", json={"style_id": None}, headers=H(csrf)).status_code, 200)
            self.assertEqual(client.put(f"{V}/styles/default", json={"style_id": None}, headers=H(csrf)).status_code, 200)
            ev = _events(home, "_default", "video_producer.style_default_changed")
            self.assertEqual(len(ev), 2)
            self.assertEqual(ev[1]["details"]["previous_style_id"], sid)
            self.assertNotIn("style_id", ev[1]["details"])
            self.assertIsNone(client.get(f"{V}/styles").json()["default_style_id"])

            # an unknown style is refused and records nothing
            self.assertEqual(client.put(f"{V}/styles/default", json={"style_id": "sty_deadbeef"}, headers=H(csrf)).status_code, 404)
            self.assertEqual(len(_events(home, "_default", "video_producer.style_default_changed")), 2)


if __name__ == "__main__":
    unittest.main()

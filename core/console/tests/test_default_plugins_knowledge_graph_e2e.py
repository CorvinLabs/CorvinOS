"""E2E: the Knowledge Graph is installed AND enabled by boot, with no operator step (ADR-2265).

The call path under test is the one a booting host runs: ``default_plugins.provision`` (what
``default_plugins.start`` calls in its boot thread) -> tenant plugin registry -> the HTTP surface
(``/marketplace/default-plugins``, ``/kb/sync``, ``/kb/status``, the plugin list) -> the board
projector's ``kb_init.py`` subprocess -> ``<tenant_home>/knowledge``.

* fresh tenant: one ``provision`` and the plugin is installed, enabled, vetted, needs no consent;
  the next projector tick creates the KB; entities made with its own ``kb.py`` reach the board;
* migration: a tenant that still carries the pre-1.2 community record (1.1.0, consent) is upgraded
  to the vetted listing and enabled — no escalation approval, version history kept;
* respect: a predecessor the operator uninstalled is not brought back; a second boot is a no-op.

Needs the plugin source (sibling Corvin-Marketplace checkout or CORVIN_MARKETPLACE_ROOT).
"""
from __future__ import annotations

import dataclasses
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_TT = "/v1/console/task-tracking"
_NEW = "plugin:buildin-knowledge_management-corvin_knowledge"
_OLD = "plugin:contributor-knowledge_management-corvin_knowledge"


def _entry(dp):
    return [e for e in dp.load_list() if e["index_id"] == _NEW]


class KnowledgeGraphIsProvisionedAndEnabledByBoot(unittest.TestCase):
    def setUp(self):
        from corvin_console import default_plugins, kb_projection
        from corvin_console.routes import marketplace_resolve

        try:
            src = marketplace_resolve.resolve_builtin_dir(_NEW) / "src" / "kb_init.py"
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Knowledge Graph plugin source not reachable: {exc}")
        if not src.is_file():
            self.skipTest("the reachable Knowledge Graph plugin predates kb_init.py")
        self.dp, self.kp = default_plugins, kb_projection
        self.saved = {k: os.environ.get(k) for k in ("CORVIN_KB_REPO", "CORVIN_KB_TENANT")}
        os.environ.pop("CORVIN_KB_REPO", None)
        os.environ.pop("CORVIN_KB_TENANT", None)
        self.kp._kg_ensured.discard("_default")
        self.tmp = Path(tempfile.mkdtemp(prefix="kg-boot-"))

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        self.kp._kg_ensured.discard("_default")
        self.kp._state.pop("_default", None)

    def _sync(self, client, csrf) -> dict:
        r = client.post(f"{_TT}/kb/sync", headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _kg(self, client) -> dict:
        r = client.get("/v1/console/plugins")
        self.assertEqual(r.status_code, 200, r.text)
        return {p["plugin_id"]: p for p in r.json()["plugins"]}.get("corvin_knowledge")

    def test_shipped_list_enables_it_and_names_its_predecessor(self):
        (e,) = _entry(self.dp)
        self.assertTrue(e["enable"])
        self.assertEqual(e["replaces"], _OLD)
        self.assertEqual(e["min_version"], "1.2.0")

    def test_fresh_tenant_boot_installs_enables_and_binds_without_a_step(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            kb = home / "tenants" / "_default" / "knowledge"
            self.assertIsNone(self.kp.kb_repo())
            res = {r["index_id"]: r for r in self.dp.provision("_default", entries=_entry(self.dp))}[_NEW]
            self.assertEqual((res["status"], res["enabled"], res["requires_consent"]), ("installed", True, False), res)
            self.assertNotIn("enable_error", res)

            rec = self._kg(client)
            self.assertTrue(rec["enabled"], rec)
            self.assertEqual(rec["origin"], "vetted")
            self.assertFalse(rec["requires_consent"], rec)
            listed = {p["index_id"]: p for p in
                      client.get("/v1/console/api/v1/marketplace/default-plugins").json()["plugins"]}[_NEW]
            self.assertTrue(listed["installed"] and listed["enabled"], listed)

            # the projector's next tick creates the KB — the same call the loop makes
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "ok", st)
            self.assertEqual(self.kp.kb_repo(), kb)
            self.assertTrue((kb / "scripts" / "kb.py").is_file() and (kb / ".git").exists())
            self.assertEqual(client.get(f"{_TT}/kb/status").json().get("enabled"), True)

            # audit: the boot enable and the KB creation are in the tenant chain
            blob = "\n".join(json.dumps(e) for e in _audit_events(home))
            self.assertIn("marketplace.enable", blob)
            self.assertIn("kb.tenant_created", blob)

            # the three gates are usable at once: the operator's first entities reach the board
            kbpy = [sys.executable, str(kb / "scripts" / "kb.py"), "--repo", str(kb)]
            env = {**os.environ, "KB_ACTOR": "human:operator"}

            def run(*a):
                r = subprocess.run([*kbpy, *map(str, a)], capture_output=True, text=True, env=env)
                self.assertEqual(r.returncode, 0, f"{a}\n{r.stdout}\n{r.stderr}")

            run("new", "initiative", "--title", "Onboarding")
            run("new", "concept", "--title", "Sign-in flow")
            run("new", "decision", "--title", "Sign in with OIDC", "--link", "formalized_as=CONCEPT-0001")
            run("new", "epic", "--title", "Build OIDC sign-in", "--initiative", "I-01", "--link", "implements=ADR-0001")
            run("new", "task", "--title", "Callback route", "--epic", "E-001", "--dod", "route answers 200")
            self.assertEqual(self._sync(client, csrf)["state"], "ok")
            items = {i["title"].split(" · ")[0] for i in client.get(f"{_TT}/items").json()["items"]
                     if str(i.get("external_ref") or "").startswith("kb:")}
            self.assertEqual(items, {"I-01", "E-001", "T-0001"})
            check = self.tmp / "check.json"
            subprocess.run([*kbpy, "check", "--json", str(check)], capture_output=True)
            gates = {k for k in json.loads(check.read_text())["issues"] if k.startswith("G")}
            self.assertNotIn("G1_no_concept", gates)        # the concept gate holds
            self.assertIn("G2_no_backedge", gates)          # a gate that reports, not a stub

            # a second boot changes nothing
            again = {r["index_id"]: r for r in self.dp.provision("_default", entries=_entry(self.dp))}[_NEW]
            self.assertEqual(again["status"], "already_offered")

    def test_a_pre_1_2_community_record_is_upgraded_and_enabled(self):
        from corvin_console.routes import marketplace_install as mi, marketplace_resolve as mr
        from corvin_plugins.manifest import PluginOrigin  # type: ignore[import-not-found]
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]

        with _sandbox(self.tmp) as (client, csrf, home, _):
            plugin_dir, manifest = mr.load_manifest(_NEW)
            current = mr.record_from_manifest(manifest, plugin_dir=plugin_dir)
            legacy = dataclasses.replace(current, version="1.1.0", origin=PluginOrigin.COMMUNITY,
                                         requires_consent=True)
            self.assertTrue(legacy.consent_required())
            mi._lifecycle("_default").install(legacy, installed_by="test:legacy")
            self.dp._write_ledger("_default", {"offered": {_OLD: {"registry_id": "corvin_knowledge", "version": "1.1.0"}}})
            before = TenantRegistry.load(tenant_id="_default").records["corvin_knowledge"]
            self.assertFalse(before.enabled)

            res = {r["index_id"]: r for r in self.dp.provision("_default", entries=_entry(self.dp))}[_NEW]
            self.assertEqual(res["status"], "installed", res)
            self.assertEqual(res["upgraded_from"], "1.1.0")
            self.assertTrue(res["enabled"], res)

            after = TenantRegistry.load(tenant_id="_default").records["corvin_knowledge"]
            self.assertEqual((str(after.version), after.origin, after.enabled, after.consent_required()),
                             (str(current.version), PluginOrigin.VETTED, True, False))
            self.assertEqual(self._sync(client, csrf)["state"], "ok")
            self.assertTrue((home / "tenants" / "_default" / "knowledge" / "scripts" / "kb.py").is_file())

    def test_an_operator_uninstall_of_the_predecessor_is_kept(self):
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]

        with _sandbox(self.tmp) as (client, csrf, home, _):
            # offered under the old listing, no record any more == the operator uninstalled it
            self.dp._write_ledger("_default", {"offered": {_OLD: {"registry_id": "corvin_knowledge", "version": "1.0.0"}}})
            res = {r["index_id"]: r for r in self.dp.provision("_default", entries=_entry(self.dp))}[_NEW]
            self.assertEqual(res["status"], "skipped", res)
            self.assertNotIn("corvin_knowledge", TenantRegistry.load(tenant_id="_default").records)
            self.assertEqual(self._sync(client, csrf)["state"], "off")
            self.assertFalse((home / "tenants" / "_default" / "knowledge").exists())


if __name__ == "__main__":
    unittest.main()

"""E2E: CorvinTV (formerly Video Producer) is installed AND enabled by boot, with no operator step.

Like the Knowledge Graph (ADR-2265) it is a first-party plugin of the marketplace's ``buildin`` tier, so a booting host
installs and enables it. The call path is the one a booting host runs: ``default_plugins.provision`` (what
``default_plugins.start`` calls in its boot thread) -> tenant plugin registry -> the real HTTP surface.

* fresh tenant: one ``provision`` and CorvinTV AND the Knowledge Graph are installed, enabled, vetted, need no consent;
  the plugin source loads through the console route and the settings it answers carry the default narration chain
  (OpenAI first, edge-tts as the fallback);
* migration: a tenant that still carries the pre-1.7 community record ``Video Producer`` (1.6.0, consent) is upgraded to
  the vetted listing and enabled; the registry id did not change, so its data folder stays valid;
* respect: a predecessor the operator uninstalled is not brought back; a second boot is a no-op.

Needs the plugin source (sibling Corvin-Marketplace checkout or CORVIN_MARKETPLACE_ROOT).
"""
from __future__ import annotations

import dataclasses
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_NEW = "plugin:buildin-media-corvintv"
_OLD = "plugin:contributor-media-video_producer"
_KG = "plugin:buildin-knowledge_management-corvin_knowledge"


class CorvinTVIsProvisionedAndEnabledByBoot(unittest.TestCase):
    def setUp(self):
        from corvin_console import default_plugins
        from corvin_console.routes import marketplace_resolve

        try:
            marketplace_resolve.resolve_builtin_dir(_NEW)
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"CorvinTV plugin source not reachable (buildin tier): {exc}")
        self.dp = default_plugins
        self.tmp = Path(tempfile.mkdtemp(prefix="ctv-boot-"))

    def _entries(self, *ids):
        return [e for e in self.dp.load_list() if e["index_id"] in ids]

    def _plugins(self, client) -> dict:
        r = client.get("/v1/console/plugins")
        self.assertEqual(r.status_code, 200, r.text)
        return {p["plugin_id"]: p for p in r.json()["plugins"]}

    def test_shipped_list_enables_it_names_its_predecessor_and_still_ships_the_knowledge_graph(self):
        by = {e["index_id"]: e for e in self.dp.load_list()}
        self.assertEqual(list(by)[:2], [_NEW, _KG], "both standard plugins, CorvinTV first")
        e = by[_NEW]
        self.assertTrue(e["enable"])
        self.assertEqual((e["replaces"], e["registry_id"], e["min_version"]), (_OLD, "video_producer", "1.7.0"))
        self.assertNotIn(_OLD, by, "the old listing is no longer offered under its own id")
        self.assertTrue(by[_KG]["enable"])

    def test_fresh_tenant_boot_installs_and_enables_both_standard_plugins_without_a_step(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            results = {r["index_id"]: r for r in self.dp.provision("_default", entries=self._entries(_NEW, _KG))}
            for iid in (_NEW, _KG):
                r = results[iid]
                self.assertEqual((r["status"], r["enabled"], r["requires_consent"]), ("installed", True, False), r)
                self.assertNotIn("enable_error", r)

            plugins = self._plugins(client)
            tv = plugins["video_producer"]
            self.assertTrue(tv["enabled"], tv)
            self.assertEqual((tv["origin"], tv["requires_consent"], tv["display_name"]), ("vetted", False, "CorvinTV"))
            self.assertTrue(plugins["corvin_knowledge"]["enabled"])

            listed = {p["index_id"]: p for p in
                      client.get("/v1/console/api/v1/marketplace/default-plugins").json()["plugins"]}
            for iid in (_NEW, _KG):
                self.assertTrue(listed[iid]["installed"] and listed[iid]["enabled"], listed[iid])

            # functional, not just registered: the console route loads the plugin source and answers, with the
            # default narration chain (an exhausted or missing OpenAI key must not stop a video)
            r = client.get("/v1/console/video/settings")
            self.assertEqual(r.status_code, 200, r.text)
            s = r.json()
            self.assertEqual(s["tts_engine"], "auto")
            self.assertIn("auto", s["tts_engines"])
            self.assertEqual(client.get("/v1/console/video/jobs").status_code, 200)

            blob = "\n".join(str(e) for e in _audit_events(home))
            self.assertIn("marketplace.enable", blob)

            again = {r["index_id"]: r for r in self.dp.provision("_default", entries=self._entries(_NEW, _KG))}
            self.assertEqual(again[_NEW]["status"], "already_offered")
            self.assertEqual(again[_KG]["status"], "already_offered")

    def test_a_pre_1_7_community_record_is_upgraded_and_enabled(self):
        from corvin_console.routes import marketplace_install as mi, marketplace_resolve as mr
        from corvin_plugins.manifest import PluginOrigin  # type: ignore[import-not-found]
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]

        with _sandbox(self.tmp) as (client, csrf, home, _):
            plugin_dir, manifest = mr.load_manifest(_NEW)
            current = mr.record_from_manifest(manifest, plugin_dir=plugin_dir)
            legacy = dataclasses.replace(current, version="1.6.0", origin=PluginOrigin.COMMUNITY,
                                         requires_consent=True, display_name="Video Producer")
            self.assertTrue(legacy.consent_required())
            mi._lifecycle("_default").install(legacy, installed_by="test:legacy")
            self.dp._write_ledger("_default", {"offered": {_OLD: {"registry_id": "video_producer", "version": "1.6.0"}}})
            self.assertFalse(TenantRegistry.load(tenant_id="_default").records["video_producer"].enabled)

            res = {r["index_id"]: r for r in self.dp.provision("_default", entries=self._entries(_NEW))}[_NEW]
            self.assertEqual(res["status"], "installed", res)
            self.assertEqual(res["upgraded_from"], "1.6.0")
            self.assertTrue(res["enabled"], res)

            after = TenantRegistry.load(tenant_id="_default").records["video_producer"]
            self.assertEqual((str(after.version), after.origin, after.enabled, after.consent_required()),
                             (str(current.version), PluginOrigin.VETTED, True, False))
            self.assertEqual(after.display_name, "CorvinTV")
            self.assertEqual(client.get("/v1/console/video/settings").status_code, 200)

    def test_an_operator_uninstall_of_the_predecessor_is_kept(self):
        from corvin_plugins.state import TenantRegistry  # type: ignore[import-not-found]

        with _sandbox(self.tmp) as (client, csrf, home, _):
            # offered under the old listing, no record any more == the operator uninstalled it
            self.dp._write_ledger("_default", {"offered": {_OLD: {"registry_id": "video_producer", "version": "1.5.0"}}})
            res = {r["index_id"]: r for r in self.dp.provision("_default", entries=self._entries(_NEW))}[_NEW]
            self.assertEqual(res["status"], "skipped", res)
            self.assertNotIn("video_producer", TenantRegistry.load(tenant_id="_default").records)


if __name__ == "__main__":
    unittest.main()

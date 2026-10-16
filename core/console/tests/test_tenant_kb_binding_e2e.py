"""E2E: the tenant's own knowledge base, bound by enabling the Knowledge Graph plugin (ADR-2265).

A sandboxed console with NO Corvin-Knowledge checkout (sandboxed CORVIN_HOME, no
CORVIN_KB_REPO) — what every fresh install is. Every step goes through HTTP:

* marketplace install of the Knowledge Graph plugin (a stand-in for "installed but disabled",
  e.g. by the operator -- boot itself installs AND enables it, see
  test_default_plugins_knowledge_graph_e2e.py) -> the board says one click is
  missing (``/kb/status``: state off, ``hint: enable_plugin``);
* enable WITHOUT consent is refused, the KB stays absent;
* enable with consent -> the next projector tick runs the plugin's ``kb_init.py``:
  ``<tenant_home>/knowledge`` is a git repository with the engine, audited;
* entities created with that KB's own ``scripts/kb.py`` reach the board, the graph panel
  reads the same KB, and the gate findings (G1-G3) are reported by ``kb check``;
* disable -> the KB is no longer projected; its files stay.

Needs the plugin source (sibling Corvin-Marketplace checkout or CORVIN_MARKETPLACE_ROOT).
"""
from __future__ import annotations

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
_MP = "/v1/console/api/v1/marketplace/plugins"
_KG = "plugin:buildin-knowledge_management-corvin_knowledge"


def _kb(repo: Path, *args) -> dict:
    r = subprocess.run([sys.executable, str(repo / "scripts" / "kb.py"), "--repo", str(repo), *map(str, args)],
                       capture_output=True, text=True, env={**os.environ, "KB_ACTOR": "human:operator"})
    if r.returncode != 0:
        raise AssertionError(f"kb {args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return json.loads(r.stdout) if r.stdout.strip().startswith("{") else {}


class TenantKnowledgeBaseBinding(unittest.TestCase):
    def setUp(self):
        from corvin_console import kb_projection
        from corvin_console.routes import marketplace_resolve

        try:
            src = marketplace_resolve.resolve_builtin_dir(_KG) / "src" / "kb_init.py"
        except Exception as exc:  # noqa: BLE001
            self.skipTest(f"Knowledge Graph plugin source not reachable: {exc}")
        if not src.is_file():
            self.skipTest("the reachable Knowledge Graph plugin predates kb_init.py")
        self.kp = kb_projection
        self.saved = {k: os.environ.get(k) for k in ("CORVIN_KB_REPO", "CORVIN_KB_TENANT")}
        os.environ.pop("CORVIN_KB_REPO", None)
        os.environ.pop("CORVIN_KB_TENANT", None)
        self.kp._kg_ensured.discard("_default")
        self.tmp = Path(tempfile.mkdtemp(prefix="tenant-kb-"))

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

    def test_enable_binds_the_tenant_kb_and_its_gates_reach_the_board(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            h = {"X-CSRF-Token": csrf}
            kb = home / "tenants" / "_default" / "knowledge"
            # never the operator's sibling: a sandboxed home resolves no KB before the plugin
            self.assertIsNone(self.kp.kb_repo())

            r = client.post(f"{_MP}/{_KG}/install", headers=h, json={})
            self.assertEqual(r.status_code, 200, r.text)
            st = self._sync(client, csrf)
            self.assertEqual((st["state"], st.get("hint")), ("off", "enable_plugin"), st)
            self.assertEqual(client.get(f"{_TT}/kb/status").json().get("hint"), "enable_plugin")

            self.assertFalse(kb.exists())
            rec = {p["plugin_id"]: p for p in client.get("/v1/console/plugins").json()["plugins"]}["corvin_knowledge"]
            self.assertEqual(rec["origin"], "vetted")
            self.assertFalse(rec["requires_consent"], rec)

            r = client.patch(f"{_MP}/{_KG}/enable", headers=h, json={})
            self.assertEqual(r.status_code, 200, r.text)   # no consent body: the plugin needs none
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "ok", st)
            self.assertEqual(self.kp.kb_repo(), kb)
            self.assertTrue((kb / "scripts" / "kb.py").is_file() and (kb / ".git").exists())
            status = client.get(f"{_TT}/kb/status").json()
            self.assertTrue(status["enabled"], status)
            acts = [e for e in _audit_events(home)
                    if "kb.tenant_created" in json.dumps(e) and "console.action_performed" in json.dumps(e)]
            self.assertEqual(len(acts), 1, "the creation is audited exactly once in the tenant chain")

            # the operator works in their KB: initiative, concept, decision, plan (epic), task
            _kb(kb, "new", "initiative", "--title", "Onboarding")
            _kb(kb, "new", "concept", "--title", "Sign-in flow")
            _kb(kb, "new", "decision", "--title", "Sign in with OIDC", "--link", "formalized_as=CONCEPT-0001")
            _kb(kb, "new", "epic", "--title", "Build OIDC sign-in", "--initiative", "I-01",
                "--link", "implements=ADR-0001")
            _kb(kb, "new", "task", "--title", "Callback route", "--epic", "E-001", "--dod", "route answers 200")
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "ok", st)
            items = {i["title"].split(" · ")[0]: i for i in client.get(f"{_TT}/items").json()["items"]
                     if str(i.get("external_ref") or "").startswith("kb:")}
            self.assertEqual({k: v["kind"] for k, v in items.items()},
                             {"I-01": "initiative", "E-001": "epic", "T-0001": "task"})
            self.assertNotIn("ready", items["E-001"]["labels"])       # G2-G4 do not hold yet

            check = self.tmp / "check.json"
            subprocess.run([sys.executable, str(kb / "scripts" / "kb.py"), "--repo", str(kb), "check",
                            "--json", str(check)], capture_output=True)
            gates = {k for k in json.loads(check.read_text())["issues"] if k.startswith("G")}
            self.assertNotIn("G1_no_concept", gates)
            self.assertIn("G2_no_backedge", gates)

            # the Knowledge Graph panel reads the same KB (repo_path unset -> the bound KB)
            g = client.get("/v1/console/plugins/corvin-knowledge/graph").json()
            self.assertEqual(sorted(e["label"] for e in g["entities"]),
                             ["ADR-0001", "CONCEPT-0001", "E-001", "I-01", "T-0001"])
            self.assertEqual(client.get("/v1/console/plugins/corvin-knowledge/config").json()["repo_path"], str(kb))

            # a second process boot refreshes nothing when the engine is current
            self.kp._kg_ensured.discard("_default")
            self.assertEqual(self.kp.ensure_tenant_kb("_default")["status"], "ok")

            # disable: the KB is no longer projected, its files stay
            r = client.patch(f"{_MP}/{_KG}/disable", headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIsNone(self.kp.kb_repo())
            self.assertEqual(self._sync(client, csrf)["state"], "off")
            self.assertTrue((kb / "concepts" / "CONCEPT-0001-sign-in-flow.md").is_file())


if __name__ == "__main__":
    unittest.main()

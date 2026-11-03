"""E2E (fast, no LLM): the Tasks board works against each tenant's OWN knowledge base (2026-11-03).

Fixes proven here, every step through HTTP of the real console router:

* every host starts the KB projector -- ``corvin_console.standalone`` (what every Windows
  install and ``corvinos-serve`` run) never did, so no tenant KB was ever created there;
* the binding is per tenant: ``kb_repo(tenant)`` -- two tenants, two KBs, neither sees the other's
  items, and a board move of one tenant's card from the other tenant is refused;
* ``POST /task-tracking/items`` of an initiative / epic / task creates it IN the KB (``kb new``) and
  returns its projection -- the board has one writer; the state machine runs through
  ``/kb-transition`` up to ``done`` with a definition of done.

Needs the plugin source (sibling Corvin-Marketplace checkout or CORVIN_MARKETPLACE_ROOT).
"""
from __future__ import annotations

import ast
import os
import sys
import tempfile
import unittest
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

_TT = "/v1/console/task-tracking"
_MP = "/v1/console/api/v1/marketplace/plugins"
_KG = "plugin:buildin-knowledge_management-corvin_knowledge"
_REPO = Path(__file__).resolve().parents[3]
# ~25 kb.py/git subprocesses per tenant (each 0.5-2 s on Windows): above the suite's 30-s default
pytestmark = pytest.mark.timeout(300)


class EveryHostStartsTheProjector(unittest.TestCase):
    def test_every_host_starts_the_projector_of_its_tenant(self):
        for rel in ("core/console/corvin_console/standalone.py", "core/console/corvin_console/app.py",
                    "core/gateway/corvin_gateway/app.py"):
            tree = ast.parse((_REPO / rel).read_text(encoding="utf-8"))
            called = {n.func.attr for n in ast.walk(tree)
                      if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)}
            self.assertIn("start_process_tenant", called, f"{rel} never starts the KB projector")


class TenantBoard(unittest.TestCase):
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
        for t in ("_default", "acme"):
            self.kp._kg_ensured.discard(t)
            self.kp._kg_failed.pop(t, None)
        self.tmp = Path(tempfile.mkdtemp(prefix="kb-tenant-board-"))

    def tearDown(self):
        for k, v in self.saved.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v
        for t in ("_default", "acme"):
            self.kp._kg_ensured.discard(t)
            self.kp._state.pop(t, None)

    def _enable(self, client, csrf) -> dict:
        h = {"X-CSRF-Token": csrf}
        r = client.post(f"{_MP}/{_KG}/install", headers=h, json={})
        self.assertEqual(r.status_code, 200, r.text)
        r = client.patch(f"{_MP}/{_KG}/enable", headers=h, json={})
        self.assertEqual(r.status_code, 200, r.text)
        r = client.post(f"{_TT}/kb/sync", headers=h)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    @staticmethod
    def _as_process(tenant: str):
        """Run the next requests as the host process OF *tenant* (CORVIN_TENANT_ID): the audit
        chain only accepts the process tenant's records, so each tenant has its own host."""
        import contextlib

        @contextlib.contextmanager
        def _cm():
            prev = os.environ.get("CORVIN_TENANT_ID")
            os.environ["CORVIN_TENANT_ID"] = tenant
            try:
                yield
            finally:
                os.environ["CORVIN_TENANT_ID"] = prev or "_default"
        return _cm()

    def _create(self, client, csrf, **body):
        return client.post(f"{_TT}/items", headers={"X-CSRF-Token": csrf}, json=body)

    def test_two_tenants_two_kbs_and_the_board_creates_into_the_kb(self):
        with _sandbox(self.tmp, tenants=("_default", "acme")) as (_c, _s, home, clients):
            a, a_csrf = clients["_default"]
            b, b_csrf = clients["acme"]
            self.assertEqual(self._enable(a, a_csrf)["state"], "ok")
            with self._as_process("acme"):
                self.assertEqual(self._enable(b, b_csrf)["state"], "ok")
            kb_a = home / "tenants" / "_default" / "knowledge"
            kb_b = home / "tenants" / "acme" / "knowledge"
            self.assertEqual(self.kp.kb_repo("_default"), kb_a)
            self.assertEqual(self.kp.kb_repo("acme"), kb_b)          # its own, never _default's
            self.assertTrue(a.get(f"{_TT}/kb/status").json()["enabled"])
            with self._as_process("acme"):
                self.assertTrue(b.get(f"{_TT}/kb/status").json()["enabled"])

            # create through the board: initiative -> epic -> task, each IN the KB
            r = self._create(a, a_csrf, kind="initiative", title="Onboarding")
            self.assertEqual(r.status_code, 201, r.text)
            ini = r.json()
            self.assertTrue(ini["external_ref"].startswith("kb:"), ini)
            self.assertTrue(ini["title"].startswith("I-01"), ini)
            self.assertTrue(list((kb_a / "kb" / "initiatives").glob("I-01-*.md")))
            r = self._create(a, a_csrf, kind="epic", title="Self-service sign-up", parent_id=ini["id"])
            self.assertEqual(r.status_code, 201, r.text)
            epic = r.json()
            r = self._create(a, a_csrf, kind="task", title="Welcome mail", parent_id=epic["id"],
                             description="Send the welcome mail after sign-up.")
            self.assertEqual(r.status_code, 201, r.text)
            task = r.json()
            self.assertEqual((task["kind"], task["parent_id"], task["status"]), ("task", epic["id"], "open"))
            self.assertTrue(list((kb_a / "kb" / "tasks").glob("T-0001-*.md")))

            # the hierarchy is the KB's: an epic needs an initiative
            r = self._create(a, a_csrf, kind="epic", title="orphan")
            self.assertIn(r.status_code, (400, 409), r.text)            # store rule or KB rule: refused
            r = self._create(a, a_csrf, kind="task", title="wrong parent", parent_id=ini["id"])
            self.assertEqual(r.status_code, 409, r.text)

            # the state machine: open -> in_progress -> done (needs a definition of done)
            h = {"X-CSRF-Token": a_csrf}
            r = a.post(f"{_TT}/items/{task['id']}/kb-transition", headers=h, json={"to": "in_progress"})
            self.assertEqual(r.status_code, 200, r.text)
            r = a.post(f"{_TT}/items/{task['id']}/kb-transition", headers=h, json={"to": "complete"})
            self.assertEqual(r.status_code, 409, r.text)                 # no definition of done
            r = a.post(f"{_TT}/items/{task['id']}/kb-transition", headers=h,
                       json={"to": "complete", "dod": "welcome mail sent in staging"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "complete")
            self.assertIn("status: done", next((kb_a / "kb" / "tasks").glob("T-0001-*.md")).read_text(encoding="utf-8"))
            # a KB card's status is never patched in the store
            cur = a.get(f"{_TT}/items/{task['id']}").json()["item"]
            r = a.patch(f"{_TT}/items/{task['id']}", headers=h, json={"version": cur["version"], "status": "open"})
            self.assertEqual(r.status_code, 409, r.text)

            # tenant isolation: acme (its own host process) sees none of it, cannot move it, and its
            # KB holds nothing of it
            with self._as_process("acme"):
                b_items = b.get(f"{_TT}/items").json()["items"]
                self.assertEqual([i for i in b_items if i["id"] in {ini["id"], epic["id"], task["id"]}], [])
                r = b.post(f"{_TT}/items/{task['id']}/kb-transition", headers={"X-CSRF-Token": b_csrf},
                           json={"to": "in_progress"})
                self.assertEqual(r.status_code, 404, r.text)
                self.assertFalse((kb_b / "kb" / "initiatives").exists()
                                 and list((kb_b / "kb" / "initiatives").iterdir()))
                r = self._create(b, b_csrf, kind="initiative", title="Acme roadmap")
                self.assertEqual(r.status_code, 201, r.text)
                self.assertTrue(r.json()["title"].startswith("I-01"))       # numbered in acme's own KB
                g_b = {e["label"] for e in b.get("/v1/console/plugins/corvin-knowledge/graph").json()["entities"]}
            self.assertNotIn("Acme roadmap", " ".join(i["title"] for i in a.get(f"{_TT}/items").json()["items"]))

            # the Knowledge Graph panel of each tenant reads its own KB
            g_a = {e["label"] for e in a.get("/v1/console/plugins/corvin-knowledge/graph").json()["entities"]}
            self.assertTrue({"I-01", "E-001", "T-0001"} <= g_a, g_a)
            self.assertEqual(g_b, {"I-01"})


if __name__ == "__main__":
    unittest.main()

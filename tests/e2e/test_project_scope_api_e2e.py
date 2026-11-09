"""T-0349 / ADR-2316 Decisions 6, 7 — the Knowledge Graph API takes ``?project=<id>`` and the
Tasks API a ``scope`` filter; both resolve the project from THIS tenant's registry only.

Through the real console routes with real sessions for two tenants. What must hold:

* ``GET /plugins/corvin-knowledge/graph?project=alpha`` returns alpha's entities, not the bound KB's;
* ``GET /plugins/corvin-knowledge/doc/{key}?project=alpha`` serves an alpha document;
* ``GET /task-tracking/items?scope=project:alpha`` lists only alpha's cards, each with
  ``project: "alpha"``; ``scope=bound`` lists only cards without a project; a bad scope is 422;
* another tenant asking for ``alpha`` (graph, doc, items) gets 404 — never a hint it exists.
Note: the graph files are produced here by an explicit ``kb index``, as a user or an agent would (the projector
and the graph API build them too: ``test_project_graph_scope_e2e.py``).
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

_TT = "/v1/console/task-tracking"
_KG = "/v1/console/plugins/corvin-knowledge"


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectScopeApiE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        fx._kb(self.bound, "index")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CORVIN_KB_REPO", None)
        else:
            os.environ["CORVIN_KB_REPO"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_project_scope_on_graph_doc_and_items(self):
        with _sandbox(self.tmp / "s", tenants=("_default", "acme")) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            from unittest import mock
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._kb(alpha / "docs", "index")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            own, _ = clients["_default"]
            other, _ = clients["acme"]
            cfg = self.tmp / "oshome" / ".claude" / "plugins" / "corvin-knowledge.json"
            with mock.patch.object(kg, "CONFIG_FILE", cfg):
                # graph: the project's entities only
                g = own.get(f"{_KG}/graph", params={"project": "alpha"})
                self.assertEqual(g.status_code, 200, g.text)
                titles = {e["title"] for e in g.json()["entities"]}
                self.assertIn("alpha task", titles)
                self.assertNotIn("bound task", titles)
                self.assertEqual(g.json()["project"], "alpha")
                bound_titles = {e["title"] for e in own.get(f"{_KG}/graph").json()["entities"]}
                self.assertIn("bound task", bound_titles)
                self.assertNotIn("alpha task", bound_titles)
                # doc: an alpha document by uid
                uid = next(e["id"] for e in g.json()["entities"] if e["title"] == "alpha task")
                d = own.get(f"{_KG}/doc/{uid}", params={"project": "alpha"})
                self.assertEqual(d.status_code, 200, d.text)
                self.assertEqual(d.json()["entity"]["title"], "alpha task")
                # another tenant: 404 everywhere, no hint
                for url, params in ((f"{_KG}/graph", {"project": "alpha"}),
                                    (f"{_KG}/doc/{uid}", {"project": "alpha"})):
                    r = other.get(url, params=params)
                    self.assertEqual(r.status_code, 404, r.text)
                    self.assertNotIn(str(alpha), r.text)
                self.assertEqual(own.get(f"{_KG}/graph", params={"project": "nope"}).status_code, 404)

            # items: scope filter + project field
            r = own.get(f"{_TT}/items", params={"scope": "project:alpha"})
            self.assertEqual(r.status_code, 200, r.text)
            items = r.json()["items"]
            self.assertTrue(items)
            self.assertTrue(all(it["project"] == "alpha" for it in items), [it["title"] for it in items])
            r = own.get(f"{_TT}/items", params={"scope": "bound"})
            self.assertTrue(all(it["project"] is None for it in r.json()["items"]))
            self.assertTrue(any(it["title"].endswith("bound task") for it in r.json()["items"]))
            every = own.get(f"{_TT}/items").json()["items"]
            self.assertEqual({it["project"] for it in every}, {None, "alpha"})
            self.assertEqual(own.get(f"{_TT}/items", params={"scope": "weird"}).status_code, 422)
            self.assertEqual(other.get(f"{_TT}/items", params={"scope": "project:alpha"}).status_code, 404)


if __name__ == "__main__":
    unittest.main()

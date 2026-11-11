"""The Knowledge Graph and the Tasks counters follow the chosen knowledge base (project scope switcher).

Through the real console routes with a real session; real KB fixtures (a bound KB with the engine, project
KBs without one, registered in ``<tenant_home>/projects/registry.json``). What must hold:

* ``GET /plugins/corvin-knowledge/graph?project=alpha`` builds alpha's ``kb/graph`` when it is missing
  (``graph_state: built``), with the BOUND engine, and names alpha's folder in ``repo_path``;
* a second read is ``ok`` (nothing rebuilt); a KB change after the build makes it stale and it is rebuilt;
* a project KB with no entity answers ``empty`` with its own ``repo_path`` - never the bound graph;
* a project whose git does not ignore ``kb/graph/`` is refused (``error``), and nothing is written there;
* the bound graph names the bound folder;
* the projector builds a project's graph after it exported a changed project KB;
* ``GET /task-tracking/items?scope=project:alpha`` carries a summary counted over alpha's items only.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _sandbox  # noqa: E402

_TT = "/v1/console/task-tracking"
_KG = "/v1/console/plugins/corvin-knowledge"


def _empty_project(root: Path, pid: str, *, ignore_graph: bool = True) -> Path:
    proj = root / pid
    (proj / "docs").mkdir(parents=True)
    (proj / "project.yaml").write_text(f"schema: 1\nid: {pid}\ntenant: _default\nfeatures:\n  kb: true\n", encoding="utf-8")
    fx._kb_init(proj / "docs", engine=False)
    if not ignore_graph:
        (proj / "docs" / ".gitignore").write_text("kb/.lock\n", encoding="utf-8")
        fx._git_commit(proj / "docs", "graph not ignored")
    return proj


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectGraphScopeE2E(unittest.TestCase):
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

    def test_project_graph_is_built_on_demand_and_follows_the_scope(self):
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            alpha = fx._make_project(self.tmp / "p", "alpha")            # seeded, never indexed
            empty = _empty_project(self.tmp / "p", "hollow")
            open_ = _empty_project(self.tmp / "p", "open", ignore_graph=False)
            fx._write_registry(home, "_default", [("alpha", alpha), ("hollow", empty), ("open", open_)])
            own, _ = clients["_default"]
            graph_dir = alpha / "docs" / "kb" / "graph"
            self.assertFalse(graph_dir.exists())
            with mock.patch.object(kg, "CONFIG_FILE", self.tmp / "oshome" / "corvin-knowledge.json"):
                g = own.get(f"{_KG}/graph", params={"project": "alpha"})
                self.assertEqual(g.status_code, 200, g.text)
                body = g.json()
                self.assertEqual(body["graph_state"], "built", body)
                self.assertEqual(Path(body["repo_path"]).resolve(), (alpha / "docs").resolve())
                titles = {e["title"] for e in body["entities"]}
                self.assertIn("alpha task", titles)
                self.assertNotIn("bound task", titles)
                self.assertTrue((graph_dir / "entities.jsonl").is_file())
                self.assertTrue(body["relations"], "the child_of edges of the seeded hierarchy")

                # current: read, not rebuilt
                self.assertEqual(own.get(f"{_KG}/graph", params={"project": "alpha"}).json()["graph_state"], "ok")

                # a change after the build makes the graph stale -> rebuilt on the next read
                time.sleep(0.05)
                fx._kb(alpha / "docs", "new", "initiative", "--title", "later initiative")
                again = own.get(f"{_KG}/graph", params={"project": "alpha"}).json()
                self.assertEqual(again["graph_state"], "built", again)
                self.assertIn("later initiative", {e["title"] for e in again["entities"]})

                # an empty project KB says so, with its own folder - never the bound graph
                e = own.get(f"{_KG}/graph", params={"project": "hollow"}).json()
                self.assertEqual(e["graph_state"], "empty", e)
                self.assertEqual(e["entities"], [])
                self.assertEqual(Path(e["repo_path"]).resolve(), (empty / "docs").resolve())

                # a folder whose git would see the generated files is refused, nothing written
                o = own.get(f"{_KG}/graph", params={"project": "open"}).json()
                self.assertEqual(o["graph_state"], "error", o)
                self.assertIn(".gitignore", o["graph_error"])
                self.assertFalse((open_ / "docs" / "kb" / "graph").exists())

                # the bound graph names the bound folder
                b = own.get(f"{_KG}/graph").json()
                self.assertEqual(Path(b["repo_path"]).resolve(), self.bound.resolve())
                self.assertEqual(b["graph_state"], "ok")
                self.assertIn("bound task", {x["title"] for x in b["entities"]})

    def test_builtin_corvin_graph_is_the_boards_kb_even_when_the_graph_setting_points_elsewhere(self):
        """REVIEW-0067 M4: the graph's Repository setting may name another knowledge base (the operator's choice). The default
        (bound) graph then reads that one; ``?project=corvin`` - the built-in project, the same knowledge base the Tasks board and
        the project's Docs tab read - still reads the board's knowledge base, so "Corvin" never names two knowledge bases."""
        import json as _json
        other = self.tmp / "other"
        fx._kb_init(other, engine=True)
        fx._seed(other, "other")
        fx._kb(other, "index")
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, _home, clients):
            from corvin_console.routes import plugins_corvin_knowledge_api as kg
            cfg = self.tmp / "oshome" / "corvin-knowledge.json"
            cfg.parent.mkdir(parents=True, exist_ok=True)
            cfg.write_text(_json.dumps({**kg.DEFAULT_CONFIG, "repo_path": str(other)}), encoding="utf-8")
            own, _ = clients["_default"]
            with mock.patch.object(kg, "CONFIG_FILE", cfg):
                b = own.get(f"{_KG}/graph").json()
                self.assertEqual(Path(b["repo_path"]).resolve(), other.resolve(), b)
                self.assertIn("other task", {x["title"] for x in b["entities"]})
                c = own.get(f"{_KG}/graph", params={"project": "corvin"})
                self.assertEqual(c.status_code, 200, c.text)
                body = c.json()
                self.assertEqual(Path(body["repo_path"]).resolve(), self.bound.resolve(), body)
                titles = {x["title"] for x in body["entities"]}
                self.assertIn("bound task", titles)
                self.assertNotIn("other task", titles)

    def test_projector_builds_the_project_graph_and_the_counters_follow_the_scope(self):
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            self.assertTrue((alpha / "docs" / "kb" / "graph" / "entities.jsonl").is_file(),
                            "the projector indexes a project KB it exported")
            self.assertFalse(kb_projection.graph_stale(alpha / "docs"))

            own, _ = clients["_default"]
            every = own.get(f"{_TT}/items").json()
            scoped = own.get(f"{_TT}/items", params={"scope": "project:alpha"}).json()
            bound = own.get(f"{_TT}/items", params={"scope": "bound"}).json()
            work = ("task", "subtask", "issue", "proposal")
            for body in (every, scoped, bound):
                n_work = sum(1 for it in body["items"] if it["kind"] in work and not it.get("deleted_at"))
                self.assertEqual(body["summary"]["total"], n_work, body["summary"])
                n_init = sum(1 for it in body["items"] if it["kind"] == "initiative" and it["status"] not in ("complete", "archived"))
                self.assertEqual(body["summary"]["initiatives_active"], n_init)
            self.assertEqual(scoped["summary"]["total"], 1)
            self.assertEqual(bound["summary"]["total"], 1)
            self.assertEqual(every["summary"]["total"], 2)


    def test_a_project_whose_manifest_names_another_id_feeds_neither_board_nor_graph(self):
        # REVIEW-0081 N27: the list called it invalid while the board, the graph and kb-transition still served its knowledge base
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            own, _ = clients["_default"]
            self.assertEqual(own.get(f"{_TT}/items", params={"scope": "project:alpha"}).status_code, 200)
            y = alpha / "project.yaml"
            y.write_text(y.read_text().replace("id: alpha", "id: other-id"))
            self.assertEqual(own.get(f"{_TT}/items", params={"scope": "project:alpha"}).status_code, 404)
            self.assertEqual(own.get(f"{_KG}/graph", params={"project": "alpha"}).status_code, 404)
            self.assertEqual(kb_projection.project_kbs("_default"), [])
            self.assertEqual(kb_projection.project_kb_problem("_default", "alpha"), "project.yaml does not name this project")
            kb_projection.sync_projects("_default")
            every = own.get(f"{_TT}/items").json()["items"]
            self.assertFalse([it for it in every if it.get("project") == "alpha" and not it.get("deleted_at")
                              and it.get("status") != "archived"], "the invalid project's cards are still on the board")

    def test_the_list_names_a_knowledge_base_the_board_cannot_read(self):
        # REVIEW-0081 N28: without sources.yaml the switcher offered the project, the board and the graph answered 404
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            (alpha / "docs" / "kb" / "_meta" / "sources.yaml").unlink()
            own, _ = clients["_default"]
            row = next(p for p in own.get("/v1/console/projects").json()["projects"] if p["id"] == "alpha")
            self.assertEqual(row["kb_problem"], "docs/kb/_meta/sources.yaml is missing")


    def test_a_manual_card_labelled_like_a_project_stays_a_manual_card(self):
        # REVIEW-0082 N30: the project came from any prj-* label; such a card vanished from every view
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            own, csrf = clients["_default"]
            r = own.post(f"{_TT}/items", json={"kind": "task", "title": "my manual card", "labels": ["prj-alpha"]},
                         headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 201, r.text)
            mid = r.json()["id"]
            every = {it["id"]: it for it in own.get(f"{_TT}/items").json()["items"]}
            self.assertIn(mid, every)
            self.assertIsNone(every[mid]["project"])
            self.assertIn(mid, {it["id"] for it in own.get(f"{_TT}/items", params={"scope": "bound"}).json()["items"]})
            self.assertNotIn(mid, {it["id"] for it in own.get(f"{_TT}/items", params={"scope": "project:alpha"}).json()["items"]})

    def test_a_board_move_right_after_a_restart_keeps_the_other_projects(self):
        # REVIEW-0082 N31: sync_project read the state from memory only and wrote back just its own project
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            beta = fx._make_project(self.tmp / "p", "beta")
            fx._write_registry(home, "_default", [("alpha", alpha), ("beta", beta)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            own, csrf = clients["_default"]
            before = own.get(f"{_TT}/items").json()["items"]
            self.assertTrue(any(it["project"] == "beta" for it in before))
            kb_projection._project_state.clear()                              # what a restart leaves: the file only
            task = next(it for it in before if it["project"] == "alpha" and it["kind"] == "task")
            r = own.post(f"{_TT}/items/{task['id']}/kb-transition", json={"to": "in_progress"}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            after = own.get(f"{_TT}/items").json()["items"]
            self.assertTrue(any(it["project"] == "beta" for it in after), "beta's cards vanished after alpha's move")
            self.assertEqual(set(kb_projection.project_status("_default")), {"alpha", "beta"})


    def test_an_item_made_on_a_project_board_goes_into_that_project(self):
        # REVIEW-0083 N32: "New item" on alpha's board wrote the initiative into the tenant's own knowledge base
        import subprocess as _sp
        with _sandbox(self.tmp / "s", tenants=("_default",)) as (_c, _t, home, clients):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            own, csrf = clients["_default"]
            h = {"X-CSRF-Token": csrf}
            commits = lambda repo: _sp.run(["git", "-C", str(repo), "rev-list", "--count", "HEAD"], capture_output=True, text=True).stdout.strip()  # noqa: E731
            bound_before = commits(self.bound)
            r = own.post(f"{_TT}/items", params={"scope": "project:alpha"}, json={"kind": "initiative", "title": "made on alpha board"}, headers=h)
            self.assertEqual(r.status_code, 201, r.text)
            self.assertTrue(r.json()["external_ref"].endswith("@alpha"), r.json())
            self.assertTrue(list((alpha / "docs" / "kb" / "initiatives").glob("*made-on-alpha-board*")))
            self.assertEqual(commits(self.bound), bound_before, "the tenant's own knowledge base was written")
            scoped = {it["id"] for it in own.get(f"{_TT}/items", params={"scope": "project:alpha"}).json()["items"]}
            self.assertIn(r.json()["id"], scoped)
            # a project's board holds only its knowledge base's kinds, and an epic needs its initiative
            self.assertEqual(own.post(f"{_TT}/items", params={"scope": "project:alpha"}, json={"kind": "issue", "title": "x"}, headers=h).status_code, 409)
            self.assertEqual(own.post(f"{_TT}/items", params={"scope": "project:alpha"}, json={"kind": "epic", "title": "x"}, headers=h).status_code, 409)
            epic = own.post(f"{_TT}/items", params={"scope": "project:alpha"}, json={"kind": "epic", "title": "alpha child", "parent_id": r.json()["id"]}, headers=h)
            self.assertEqual(epic.status_code, 201, epic.text)
            self.assertTrue(epic.json()["external_ref"].endswith("@alpha"))
            self.assertEqual(own.post(f"{_TT}/items", params={"scope": "project:ghost"}, json={"kind": "initiative", "title": "x"}, headers=h).status_code, 404)
            # what the dialog asked for besides title and parent is kept on the card (REVIEW-0084 N35)
            t = own.post(f"{_TT}/items", params={"scope": "project:alpha"}, json={"kind": "task", "title": "with fields",
                         "parent_id": epic.json()["id"], "priority": "high", "deadline": "2026-12-01", "assignee": "bob"}, headers=h)
            self.assertEqual(t.status_code, 201, t.text)
            self.assertEqual((t.json()["priority"], (t.json()["deadline"] or "")[:10], t.json()["assignee"]), ("high", "2026-12-01", "bob"))
            # from the All view (no scope) a manual child of a project's KB card is refused like on the project's board (REVIEW-0085 N37)
            for kind, parent in (("subtask", t.json()["id"]), ("story", r.json()["id"])):
                x = own.post(f"{_TT}/items", json={"kind": kind, "title": "dangling", "parent_id": parent}, headers=h)
                self.assertEqual(x.status_code, 409, (kind, x.text))
            manual = own.post(f"{_TT}/items", json={"kind": "initiative", "title": "plain", "priority": "low"}, headers=h)
            self.assertEqual(manual.status_code, 201, manual.text)


if __name__ == "__main__":
    unittest.main()

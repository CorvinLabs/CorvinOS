"""Knowledge Graph "New" feed — GET /v1/console/plugins/corvin-knowledge/recent, end to end.

Driven through the real console router with a real session against a fixture Corvin-Knowledge
checkout: real ``kb new`` commits, a real ``kb.py index`` run, real ``git log`` inside the route.
What must hold:

* an entry added inside the window is ``new``; an entry added BEFORE the window and changed inside
  it is ``updated``; one older than the window and untouched is absent;
* newest first; counts match the list; the human id, uid key, type, title and status come from the graph;
* a file removed in the window is not listed, and a Markdown file the graph does not know is ignored;
* the route is read-only (HEAD and the working tree are unchanged), needs a session, validates its
  query and never takes a path from the request (an unregistered project is 404).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402
from test_corvin_knowledge_graph_panel_e2e import _KB_SCRIPTS, _kb, _sh  # noqa: E402

_URL = "/v1/console/plugins/corvin-knowledge"
_OLD = "2020-01-01T12:00:00+00:00"


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    full = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
            "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t", **(env or {})}
    r = subprocess.run(["git", "-C", str(repo), *args], capture_output=True, text=True, env=full)
    if r.returncode != 0:
        raise AssertionError(f"git {args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


@unittest.skipUnless((_KB_SCRIPTS / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KnowledgeRecentE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        kb = self.kb = self.tmp / "kb-repo"
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            (kb / "scripts" / f).write_text((_KB_SCRIPTS / f).read_text(encoding="utf-8"), encoding="utf-8")
        (kb / "kb/_meta").mkdir(parents=True)
        (kb / "kb/_meta/sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    writable: true\n    dirs:\n"
            "      decisions: decision\n      kb/tasks: task\n      kb/epics: epic\n      kb/initiatives: initiative\n",
            encoding="utf-8")
        (kb / "kb/audit.jsonl").write_text("", encoding="utf-8")
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "add", "-A", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=kb)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _file(self, new: dict) -> Path:
        return next((self.kb / "decisions").glob(f"{new['id']}-*.md"))

    def _build(self):
        kb = self.kb
        # 1. an entry that is old and untouched, 2. old but changed now, 3. brand new, 4. new then removed
        ancient = _kb(kb, "new", "decision", "--title", "Ancient and untouched")
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", _OLD, env={"GIT_COMMITTER_DATE": _OLD})
        changed = _kb(kb, "new", "decision", "--title", "Old but changed")
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", _OLD, env={"GIT_COMMITTER_DATE": _OLD})
        f = self._file(changed)
        f.write_text(f.read_text(encoding="utf-8") + "\nA new paragraph.\n", encoding="utf-8")
        _git(kb, "add", "-A")
        _git(kb, "commit", "-q", "-m", "docs: extend the old decision")
        fresh = _kb(kb, "new", "decision", "--title", "Fresh decision")
        gone = _kb(kb, "new", "decision", "--title", "Added and removed")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        (kb / "NOTES.md").write_text("not a knowledge entry\n", encoding="utf-8")   # a Markdown file the graph never lists
        _git(kb, "add", "-A")
        _git(kb, "commit", "-q", "-m", "docs: notes")
        self._file(gone).unlink()
        _git(kb, "add", "-A")
        _git(kb, "commit", "-q", "-m", "remove one entry")
        return ancient, changed, fresh, gone

    def _bind(self, client, csrf):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        cfg = self.tmp / "home" / ".claude" / "plugins" / "corvin-knowledge.json"
        patcher = mock.patch.object(kg, "CONFIG_FILE", cfg)   # never the operator's live config
        patcher.start()
        self.addCleanup(patcher.stop)
        r = client.post(f"{_URL}/config", json={"repo_path": str(self.kb)}, headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 200, r.text)

    def test_feed_lists_new_and_updated_entries_newest_first_and_is_read_only(self):
        ancient, changed, fresh, gone = self._build()
        head_before = _git(self.kb, "rev-parse", "HEAD").strip()
        status_before = _git(self.kb, "status", "--porcelain")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            r = client.get(f"{_URL}/recent?days=30")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            denied = client.get(f"{_URL}/recent?days=0")
            too_long = client.get(f"{_URL}/recent?limit=9999")
            wrong_project = client.get(f"{_URL}/recent?project=no-such-project")
        self.assertEqual(body["state"], "ok")
        by_title = {i["title"]: i for i in body["items"]}
        self.assertIn("Fresh decision", by_title)
        self.assertEqual(by_title["Fresh decision"]["kind"], "new")
        self.assertEqual(by_title["Fresh decision"]["label"], fresh["id"])
        self.assertEqual(by_title["Fresh decision"]["type"], "decision")
        self.assertEqual(by_title["Fresh decision"]["status"], "proposed")
        self.assertEqual(len(by_title["Fresh decision"]["id"]), 26)          # the uid keys the node, like the graph
        self.assertEqual(by_title["Old but changed"]["kind"], "updated")     # added before the window, changed inside it
        self.assertIn("extend the old decision", by_title["Old but changed"]["summary"])
        self.assertNotIn("Ancient and untouched", by_title)                  # nothing inside the window
        self.assertNotIn("Added and removed", by_title)                      # its newest touch removed it
        self.assertTrue(all(i["label"] != "NOTES" for i in body["items"]))   # not a graph entity
        stamps = [i["changed_at"] for i in body["items"]]
        self.assertEqual(stamps, sorted(stamps, reverse=True))               # newest first
        self.assertEqual(body["total"], len(body["items"]))
        self.assertEqual(body["new_count"] + body["updated_count"], body["total"])
        self.assertEqual((body["window_days"], body["project"]), (30, None))
        self.assertEqual(denied.status_code, 422)
        self.assertEqual(too_long.status_code, 422)
        self.assertEqual(wrong_project.status_code, 404)                     # a path is never taken from the request
        # read-only: the checkout is exactly as it was
        self.assertEqual(_git(self.kb, "rev-parse", "HEAD").strip(), head_before)
        self.assertEqual(_git(self.kb, "status", "--porcelain"), status_before)

    def test_show_more_raises_the_cap_up_to_500(self):
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            ok = client.get(f"{_URL}/recent?days=30&limit=500")
            over = client.get(f"{_URL}/recent?days=30&limit=501")
        self.assertEqual(ok.status_code, 200)
        self.assertEqual(over.status_code, 422)

    def test_limit_caps_the_list_but_not_the_counts(self):
        self._build()
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            body = client.get(f"{_URL}/recent?days=30&limit=1").json()
        self.assertEqual(len(body["items"]), 1)
        self.assertGreaterEqual(body["total"], 2)

    def test_a_window_with_no_change_is_empty(self):
        kb = self.kb
        _kb(kb, "new", "decision", "--title", "Only an old entry")
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", _OLD, env={"GIT_COMMITTER_DATE": _OLD})
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            body = client.get(f"{_URL}/recent?days=30").json()
        self.assertEqual((body["state"], body["items"], body["total"]), ("empty", [], 0))

    def test_an_unindexed_knowledge_base_is_reported_as_missing(self):
        self._build()
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            (self.kb / "kb" / "graph" / "entities.jsonl").rename(self.kb / "kb" / "graph" / "entities.bak")
            missing = client.get(f"{_URL}/recent?days=30").json()
        self.assertEqual(missing["state"], "missing")
        self.assertEqual(missing["items"], [])

    # ---- review findings (REVIEW round on the New tab) ----------------------------------------------------

    def test_a_repository_cannot_start_a_program_through_the_feed(self):
        """A signed commit makes git run ``gpg.program`` when ``log.showSignature`` is on: the feed must not."""
        kb = self.kb
        _kb(kb, "new", "decision", "--title", "Signed one")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        marker = self.tmp / "gpg-ran"
        fake = self.tmp / "fakegpg.sh"
        fake.write_text(f"#!/bin/sh\necho ran >> {marker}\nexit 0\n", encoding="utf-8")
        fake.chmod(0o755)
        _git(kb, "config", "log.showSignature", "true")
        _git(kb, "config", "gpg.program", str(fake))
        tree = _git(kb, "rev-parse", "HEAD^{tree}").strip()
        parent = _git(kb, "rev-parse", "HEAD").strip()
        raw = (f"tree {tree}\nparent {parent}\nauthor t <t@t> 1700000000 +0000\ncommitter t <t@t> 9999999999 +0000\n"
               "gpgsig -----BEGIN PGP SIGNATURE-----\n \n fake\n -----END PGP SIGNATURE-----\n\nsigned\n")
        new = subprocess.run(["git", "-C", str(kb), "hash-object", "-t", "commit", "-w", "--stdin"], input=raw,
                             capture_output=True, text=True, check=True).stdout.strip()
        _git(kb, "update-ref", "refs/heads/main", new)
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            r = client.get(f"{_URL}/recent?days=90")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertFalse(marker.exists(), "the repository's own gpg.program ran")

    def test_order_follows_the_instant_not_the_authors_time_zone(self):
        kb = self.kb
        now = datetime.now(timezone.utc)
        older = _kb(kb, "new", "decision", "--title", "Older instant")
        d1 = (now - timedelta(hours=2)).astimezone(timezone(timedelta(hours=5))).isoformat()
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", d1, env={"GIT_COMMITTER_DATE": d1})
        newer = _kb(kb, "new", "decision", "--title", "Newer instant")
        d2 = (now - timedelta(hours=1)).astimezone(timezone(timedelta(hours=0))).isoformat()
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", d2, env={"GIT_COMMITTER_DATE": d2})
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            items = client.get(f"{_URL}/recent?days=7").json()["items"]
        self.assertEqual([i["label"] for i in items][:2], [newer["id"], older["id"]])
        self.assertTrue(all(i["changed_at"].endswith("+00:00") for i in items))          # normalised to UTC

    def test_the_window_and_the_shown_date_use_the_commit_time(self):
        kb = self.kb
        e = _kb(kb, "new", "decision", "--title", "Authored long ago, committed now")
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", _OLD)             # author date old, committer date now
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            items = client.get(f"{_URL}/recent?days=7").json()["items"]
        hit = next(i for i in items if i["label"] == e["id"])
        self.assertTrue(hit["changed_at"].startswith(str(datetime.now(timezone.utc).year)))

    def test_an_entry_added_and_renamed_in_the_window_is_still_new(self):
        kb = self.kb
        e = _kb(kb, "new", "decision", "--title", "Renamed after being added")
        f = self._file(e)
        _git(kb, "mv", str(f.relative_to(kb)), str(f.relative_to(kb)).replace(f.name, f"{e['id']}-renamed.md"))
        _git(kb, "commit", "-q", "-m", "docs: rename the file")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            items = client.get(f"{_URL}/recent?days=7").json()["items"]
        hit = next(i for i in items if i["label"] == e["id"])
        self.assertEqual(hit["kind"], "new")
        self.assertGreaterEqual(hit["commits"], 2)                                  # the add and the rename

    def test_a_knowledge_base_inside_a_project_repository_has_history(self):
        """Plain mode: a project's ``docs/`` belongs to the project root's repository."""
        root = self.tmp / "proj"
        docs = root / "docs"
        shutil.copytree(self.kb, docs, ignore=shutil.ignore_patterns(".git"))
        shutil.rmtree(self.kb)
        self.kb = docs
        (root / "project.yaml").write_text("id: proj\ntitle: Proj\n", encoding="utf-8")
        _sh("git", "init", "-q", "-b", "main", cwd=root)
        _sh("git", "add", "-A", cwd=root)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "init", cwd=root)
        e = _kb(docs, "new", "decision", "--title", "Inside a project repository")
        _sh(sys.executable, docs / "scripts/kb.py", "--repo", docs, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            body = client.get(f"{_URL}/recent?days=7").json()
        self.assertEqual(body["state"], "ok", body)
        self.assertIn(e["id"], [i["label"] for i in body["items"]])

    # ---- review round 2 ----------------------------------------------------------------------------------

    def test_filters_run_on_the_server_so_a_tile_shows_what_it_counts(self):
        self._build()
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            whole = client.get(f"{_URL}/recent?days=30&limit=1").json()
            upd = client.get(f"{_URL}/recent?days=30&limit=1&kind=updated").json()
            typed = client.get(f"{_URL}/recent?days=30&type=decision").json()
            bad_kind = client.get(f"{_URL}/recent?kind=nonsense")
            bad_type = client.get(f"{_URL}/recent?type=a;b")
        self.assertEqual(len(whole["items"]), 1)                              # capped ...
        self.assertEqual(whole["total"], whole["new_count"] + whole["updated_count"])   # ... but the counts cover the window
        self.assertEqual(whole["type_counts"].get("decision"), whole["total"])
        self.assertEqual(upd["matching"], whole["updated_count"])             # the "Changed" tile shows its own number
        self.assertTrue(upd["items"] and all(i["kind"] == "updated" for i in upd["items"]))
        self.assertEqual(upd["total"], whole["total"])                        # tiles are stable while a filter is on
        self.assertTrue(all(i["type"] == "decision" for i in typed["items"]))
        self.assertEqual((bad_kind.status_code, bad_type.status_code), (422, 422))

    def test_a_repository_without_a_commit_says_so(self):
        empty = self.tmp / "fresh"
        (empty / "kb" / "graph").mkdir(parents=True)
        (empty / "kb" / "graph" / "entities.jsonl").write_text("", encoding="utf-8")
        (empty / "kb" / "_meta").mkdir(parents=True)
        (empty / "kb" / "_meta" / "sources.yaml").write_text("sources: []\n", encoding="utf-8")   # looks like a knowledge base
        _sh("git", "init", "-q", "-b", "main", cwd=empty)
        self.kb = empty
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            body = client.get(f"{_URL}/recent").json()
        self.assertEqual((body["state"], body["items"]), ("no_commits", []))

    def test_a_commit_subject_cannot_hide_its_files(self):
        kb = self.kb
        e = _kb(kb, "new", "decision", "--title", "Hidden by its subject?")
        _git(kb, "commit", "-q", "--amend", "-m", "sneaky \x1e and \x1f separators in a subject")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            items = client.get(f"{_URL}/recent?days=7").json()["items"]
        hit = next((i for i in items if i["label"] == e["id"]), None)
        self.assertIsNotNone(hit, "the subject hid the commit's files")
        self.assertEqual(hit["kind"], "new")

    def test_an_older_file_at_a_reused_path_is_not_the_entrys_history(self):
        """x.md is old; another file lived at b.md and was deleted; then x.md is renamed to b.md: the entry is NOT new."""
        kb = self.kb
        x = _kb(kb, "new", "decision", "--title", "Old entry renamed onto a reused path")
        _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", _OLD, env={"GIT_COMMITTER_DATE": _OLD})
        src = self._file(x)
        target = src.with_name(f"{x['id']}-moved.md")
        target.write_text("temporary file that lived here first\n", encoding="utf-8")
        _git(kb, "add", "-A")
        _git(kb, "commit", "-q", "-m", "docs: a different file at the path")
        target.unlink()
        _git(kb, "add", "-A")
        _git(kb, "commit", "-q", "-m", "docs: remove the different file")
        _git(kb, "mv", str(src.relative_to(kb)), str(target.relative_to(kb)))
        _git(kb, "commit", "-q", "-m", "docs: move the entry onto that path")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            items = client.get(f"{_URL}/recent?days=7").json()["items"]
        hit = next(i for i in items if i["label"] == x["id"])
        self.assertEqual(hit["kind"], "updated")
        self.assertNotIn("first_seen_at", hit)

    # ---- review round 3 ----------------------------------------------------------------------------------

    def test_tiles_count_within_the_active_type_and_chips_within_the_active_kind(self):
        kb = self.kb
        self._build()
        _kb(kb, "new", "initiative", "--title", "An initiative")
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            only_init = client.get(f"{_URL}/recent?days=30&type=initiative").json()
            changed = client.get(f"{_URL}/recent?days=30&kind=updated").json()
        self.assertEqual((only_init["total"], only_init["new_count"], only_init["matching"]), (1, 1, 1))
        self.assertEqual(only_init["type_counts"].get("decision"), 2)        # chips keep every type, counted within the kind (all)
        self.assertEqual(changed["type_counts"].get("initiative"), 0)        # a type with nothing in this kind stays, at zero
        self.assertEqual(changed["type_counts"].get("decision"), changed["matching"])

    def _project_run(self, *, lock, ensure, calls_out=None):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=lock), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=ensure):
            return kg, kg.recent_payload("_default", "some-project", 14, 80)

    def test_a_project_graph_is_indexed_under_its_lock_before_it_is_read(self):
        calls = []
        class _Lock:
            def acquire(self_inner, timeout=None): calls.append("lock"); return True
            def release(self_inner): calls.append("unlock")
        _, body = self._project_run(lock=_Lock(), ensure=lambda *a: calls.append("ensure") or {"state": "error", "error": "no engine"})
        self.assertEqual(calls, ["lock", "ensure", "unlock"])
        self.assertEqual((body["state"], body["error"]), ("error", "no engine"))

    class _Open:
        def acquire(self, timeout=None): return True
        def release(self): pass

    def test_a_failed_index_is_not_run_again_for_every_click_while_the_graph_is_still_stale(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        runs = []
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "graph_stale", return_value=True), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=lambda *a: runs.append(1) or {"state": "error", "error": "timed out"}):
            kg.recent_payload("_default", "p", 14, 80)
            again = kg.recent_payload("_default", "p", 30, 80, "new", None)                 # the next click
        self.assertEqual(len(runs), 1)
        self.assertEqual(again["state"], "error")

    def test_a_remembered_failure_is_dropped_once_the_graph_is_current(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        runs, stale = [], [True]
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "graph_stale", side_effect=lambda r: stale[0]), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=lambda *a: runs.append(1) or ({"state": "error", "error": "x"} if stale[0] else {"state": "ok"})):
            kg.recent_payload("_default", "p", 14, 80)
            stale[0] = False                                                                # the Graph tab (or the operator) fixed it
            kg.recent_payload("_default", "p", 14, 80)
        self.assertEqual(len(runs), 2)                                                      # not served from the memo

    def test_a_success_is_never_remembered_so_a_new_commit_is_seen(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        runs = []
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "graph_stale", return_value=False), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=lambda *a: runs.append(1) or {"state": "ok"}):
            kg.recent_payload("_default", "p", 14, 80)
            kg.recent_payload("_default", "p", 14, 80)
        self.assertEqual(len(runs), 2)                                                      # the staleness check runs every time

    def test_a_remembered_failure_expires_after_the_ttl(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        runs, clock = [], [1000.0]
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.time, "monotonic", side_effect=lambda: clock[0]), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "graph_stale", return_value=True), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=lambda *a: runs.append(1) or {"state": "error", "error": "x"}):
            kg.recent_payload("_default", "p", 14, 80)
            clock[0] += kg._ENSURE_TTL_S - 1
            kg.recent_payload("_default", "p", 14, 80)
            self.assertEqual(len(runs), 1)
            clock[0] += 2
            kg.recent_payload("_default", "p", 14, 80)
        self.assertEqual(len(runs), 2)                                                      # tried again after the TTL

    def test_an_index_that_leaves_the_graph_stale_is_not_rerun_for_every_click(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        runs = []
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "graph_stale", return_value=True), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", side_effect=lambda *a: runs.append(1) or {"state": "built"}):
            for _ in range(4):
                kg.recent_payload("_default", "p", 14, 80)
        self.assertEqual(len(runs), 1)                                                      # a clock-skewed source file cannot loop the index

    def test_an_index_failure_with_an_older_graph_still_serves_the_list_and_says_so(self):
        self._build()
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        kg._ensure_memo.clear()
        with mock.patch.object(kg, "_scoped_config", return_value={"repo_path": str(self.kb)}), \
                mock.patch.object(kg.kb_projection, "graph_lock", return_value=self._Open()), \
                mock.patch.object(kg.kb_projection, "ensure_project_graph", return_value={"state": "error", "error": "kb/graph is not ignored by git"}):
            body = kg.recent_payload("_default", "p", 30, 80)
        self.assertEqual(body["state"], "ok")
        self.assertTrue(body["items"])
        self.assertEqual(body["index_error"], "kb/graph is not ignored by git")

    def test_the_payload_says_when_the_history_names_entries_the_graph_does_not_know(self):
        """Counted from the data: a commit the graph has not seen is lag whatever its commit time; nothing else is."""
        self._build()
        kb = self.kb
        _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
        with _sandbox(self.tmp) as (client, csrf, _home, _):
            self._bind(client, csrf)
            fresh = client.get(f"{_URL}/recent?days=30").json()
            # an uncommitted edit, a commit that only touches a file outside the source folders, an edit of a KNOWN entry: not lag
            (kb / "decisions" / "NEWER.md").write_text("an uncommitted edit\n", encoding="utf-8")
            (kb / "README.md").write_text("readme\n", encoding="utf-8")
            known = next(iter((kb / "decisions").glob("ADR-*.md")))
            known.write_text(known.read_text(encoding="utf-8") + "\nmore\n", encoding="utf-8")
            _git(kb, "add", "README.md", str(known.relative_to(kb)))
            _git(kb, "commit", "-q", "-m", "docs: readme and an edit of a known entry")
            # files that `kb index` would not list (or no longer has) must not count either: a file with no id in a source folder (the
            # index makes it an id-less row nobody sees), readme/index, a nested folder, a symlink, an entry that was added and removed,
            # an entry moved out of every source folder
            (kb / "decisions" / "NOTES.md").write_text("no id anywhere\n", encoding="utf-8")
            (kb / "decisions" / "plain-name.md").write_text("---\ntitle: no id either\n---\nbody\n", encoding="utf-8")
            (kb / "decisions" / "index.md").write_text("---\nid: ADR-0090\n---\nan index\n", encoding="utf-8")
            (kb / "decisions" / "archive").mkdir()
            (kb / "decisions" / "archive" / "ADR-0099-old.md").write_text("nested\n", encoding="utf-8")
            (kb / "decisions" / "ADR-0098-link.md").symlink_to("../README.md")
            (kb / "decisions" / "ADR-0097-gone.md").write_text("gone soon\n", encoding="utf-8")
            (kb / "decisions" / "ADR-0096-moved.md").write_text("moves out\n", encoding="utf-8")
            _git(kb, "add", "-A")
            _git(kb, "commit", "-q", "-m", "docs: files the index never lists")
            (kb / "decisions" / "ADR-0097-gone.md").unlink()
            (kb / "elsewhere").mkdir()
            _git(kb, "mv", "decisions/ADR-0096-moved.md", "elsewhere/ADR-0096-moved.md")
            _git(kb, "add", "-A")
            _git(kb, "commit", "-q", "-m", "docs: remove one, move one out")
            not_lag = client.get(f"{_URL}/recent?days=30").json()
            # a commit the graph has not seen, with a commit time OLDER than the graph (what a pull from another host brings)
            e = _kb(kb, "new", "decision", "--title", "Pulled from elsewhere")
            old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
            _git(kb, "commit", "-q", "--amend", "--no-edit", "--date", old, env={"GIT_COMMITTER_DATE": old})
            # entries the index WOULD list although their names are not "ADR-n-...": a hand-named file with a frontmatter id, and the
            # decision's older "adr_<n>_..." name
            (kb / "decisions" / "hand-named-spike.md").write_text("---\nid: ADR-9998\ntitle: hand named\n---\nbody\n", encoding="utf-8")
            (kb / "decisions" / "adr_9997_system.md").write_text("---\ntitle: legacy name\n---\nbody\n", encoding="utf-8")
            _git(kb, "add", "-A")
            _git(kb, "commit", "-q", "-m", "docs: two more entries nobody indexed")
            lagging = client.get(f"{_URL}/recent?days=30").json()
            _sh(sys.executable, kb / "scripts/kb.py", "--repo", kb, "index")
            caught_up = client.get(f"{_URL}/recent?days=30").json()
        self.assertEqual((fresh["graph_stale"], fresh["unindexed"]), (False, 0))
        self.assertEqual((not_lag["graph_stale"], not_lag["unindexed"]), (False, 0))
        self.assertEqual((lagging["graph_stale"], lagging["unindexed"]), (True, 3))     # the pulled one, the hand-named one, the legacy name
        self.assertNotIn(e["id"], [i["label"] for i in lagging["items"]])                 # the graph does not know it yet
        self.assertEqual((caught_up["graph_stale"], caught_up["unindexed"]), (False, 0))
        self.assertIn(e["id"], [i["label"] for i in caught_up["items"]])
        self.assertIn("ADR-9998", [i["label"] for i in caught_up["items"]])             # the index lists them: the count was right

    def test_a_busy_graph_lock_is_waited_for_a_bounded_time_not_forever(self):
        class _Busy:
            def acquire(self_inner, timeout=None): self_inner.waited = timeout; return False
            def release(self_inner): raise AssertionError("never acquired")
        busy = _Busy()
        _, body = self._project_run(lock=busy, ensure=lambda *a: {"state": "ok"})
        self.assertEqual(body["state"], "busy")
        self.assertIsNotNone(busy.waited)
        self.assertLessEqual(busy.waited, 30)

    def test_a_rename_gives_back_the_older_history_of_the_old_path(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        now = datetime.now(timezone.utc)
        c = lambda minutes, files: {"when": now - timedelta(minutes=minutes), "subject": "s", "files": files}
        commits = [c(1, [("A", "", "P")]),                        # E2 takes the path P
                   c(2, [("D", "", "P")]), c(3, [("A", "", "P")]),   # a different file G lived and died at P
                   c(4, [("R", "P", "Q")]),                       # E1 moved P -> Q
                   c(5, [("A", "", "P")])]                        # E1 was added at P
        rows = {"Q": {"uid": "E1", "id": "ADR-1", "type": "decision", "title": "e1"},
                "P": {"uid": "E2", "id": "ADR-2", "type": "decision", "title": "e2"}}
        by = {i["id"]: i for i in kg._aggregate_recent(commits, rows)}
        self.assertEqual((by["E1"]["kind"], by["E1"]["commits"]), ("new", 2))
        self.assertEqual((by["E2"]["kind"], by["E2"]["commits"]), ("new", 1))

    def test_a_rename_onto_a_dead_path_does_not_hand_its_history_to_the_wrong_entry(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        now = datetime.now(timezone.utc)
        c = lambda hours, files: {"when": now - timedelta(hours=hours), "subject": "s", "files": files}
        # oldest first: add P (Z) -> rename P to Q -> delete Q -> add Q (X) -> add P (W);   live: P = W, Q = X
        commits = [c(0, [("A", "", "P")]), c(1, [("A", "", "Q")]), c(2, [("D", "", "Q")]),
                   c(3, [("R", "P", "Q")]), c(4, [("A", "", "P")])][::-1]
        commits = sorted(commits, key=lambda x: x["when"], reverse=True)
        rows = {"P": {"uid": "W", "id": "ADR-W", "type": "decision", "title": "w"},
                "Q": {"uid": "X", "id": "ADR-X", "type": "decision", "title": "x"}}
        by = {i["id"]: i for i in kg._aggregate_recent(commits, rows)}
        self.assertEqual((by["W"]["kind"], by["W"]["commits"]), ("new", 1))     # Z's old add is not W's history

    def test_the_id_rule_reads_a_file_the_way_the_engine_does(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        d = self.tmp / "idrule"
        d.mkdir()
        def w(name, data):
            (d / name).write_bytes(data)
            return d / name
        fm = b"---\nid: NOTE-0007\ntitle: t\n---\nbody\n"
        self.assertTrue(kg._frontmatter_gives_id(w("plain.md", fm)))
        self.assertTrue(kg._frontmatter_gives_id(w("bom.md", b"\xef\xbb\xbf" + fm)))                       # the engine strips the BOM
        self.assertTrue(kg._frontmatter_gives_id(w("crlf.md", fm.replace(b"\n", b"\r\n"))))
        self.assertTrue(kg._frontmatter_gives_id(w("uid.md", b"---\nuid: 01ABC\n---\n")))
        self.assertFalse(kg._frontmatter_gives_id(w("mixed.md", b"---\r\nid: NOTE-0007\n---\r\nbody\n")))  # mixed endings: no frontmatter
        self.assertFalse(kg._frontmatter_gives_id(w("latin1.md", fm + b"caf\xe9\n")))                        # not UTF-8 anywhere
        self.assertFalse(kg._frontmatter_gives_id(w("noid.md", b"---\ntitle: t\n---\n")))
        self.assertFalse(kg._frontmatter_gives_id(w("empty.md", b"---\nid: \n---\n")))
        self.assertFalse(kg._frontmatter_gives_id(w("late.md", b"text first\n" + fm)))                       # frontmatter must come first
        self.assertFalse(kg._frontmatter_gives_id(w("unterminated.md", b"---\nid: NOTE-0007\n")))
        self.assertTrue(kg._frontmatter_gives_id(w("alias.md", b"---\na: &x 1\nid: *x\n---\n")))          # aliases: the safe side
        self.assertTrue(kg._name_gives_id("ADR-2328-x.md", "decision"))
        self.assertTrue(kg._name_gives_id("0466-legacy.md", "decision"))
        self.assertTrue(kg._name_gives_id("adr_0801_system.md", "decision"))
        self.assertFalse(kg._name_gives_id("0466-legacy.md", "concept"))                                       # the legacy form is a decision's only
        self.assertFalse(kg._name_gives_id("notes.md", "decision"))

    def test_git_quoted_names_and_relative_source_roots_are_read_like_the_engine(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        self.assertEqual(kg._unquote_git_path('"decisions/ADR-0100-say-\\"hi\\".md"'), 'decisions/ADR-0100-say-"hi".md')
        self.assertEqual(kg._unquote_git_path('"tab\\there.md"'), "tab\there.md")
        self.assertEqual(kg._unquote_git_path("plain.md"), "plain.md")
        repo = self.tmp / "roots"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: ./\n    dirs:\n      ./decisions: decision\n  - name: sub\n    root: notes-root\n    dirs:\n      .: note\n", encoding="utf-8")
        dirs = kg._source_dirs(repo)
        self.assertEqual(dirs, {"decisions/": "decision", "notes-root/": "note"})

    def test_unusual_unicode_line_breaks_in_a_name_and_nested_source_folders(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        rows = kg._parse_git_recent("\x00" + "h1\x1f2026-11-16T10:00:00+00:00\x1fs\nA\tdecisions/q\u2028r.md\nA\tdecisions/n\x85m.md")
        self.assertEqual({f[2] for f in rows[0]["files"]}, {"decisions/q\u2028r.md", "decisions/n\x85m.md"})
        repo = self.tmp / "nested-src"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    dirs:\n      docs: note\n      docs/adr: decision\n", encoding="utf-8")
        (repo / "docs" / "adr").mkdir(parents=True)
        (repo / "docs" / "adr" / "ADR-0001-x.md").write_text("x\n", encoding="utf-8")
        commits = [{"when": datetime.now(timezone.utc), "subject": "s", "files": [("A", "", "docs/adr/ADR-0001-x.md")]}]
        self.assertEqual(kg._unindexed_entries(repo, commits, set()), ({"docs/adr/ADR-0001-x.md"}, False))

    def test_a_folder_with_a_climbing_segment_is_matched_as_git_names_it(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        repo = self.tmp / "climb"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    dirs:\n      sub/../decisions: decision\n", encoding="utf-8")
        (repo / "decisions").mkdir()
        (repo / "decisions" / "ADR-0001-x.md").write_text("x\n", encoding="utf-8")
        (repo / "kb" / "graph").mkdir(parents=True)
        (repo / "kb" / "graph" / "entities.jsonl").write_text(json.dumps(
            {"path": str(repo / "sub" / ".." / "decisions" / "ADR-0001-x.md"), "uid": "U1", "id": "ADR-0001", "type": "decision"}) + "\n", encoding="utf-8")
        known: set[str] = set()
        kg._entities_by_relpath(repo, known)
        self.assertIn("decisions/ADR-0001-x.md", known)                          # the graph's row is the file git names

    def test_a_source_with_a_ref_is_not_counted(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        repo = self.tmp / "refsrc"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    ref: origin/main\n    dirs:\n      decisions: decision\n", encoding="utf-8")
        self.assertEqual(kg._source_dirs(repo), {})

    def test_a_long_frontmatter_is_read_only_up_to_its_bound(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        big = self.tmp / "big.md"
        big.write_bytes(b"---\n" + b"a: 1\n" * (kg._FRONTMATTER_MAX_BYTES // 3) + b"---\nid: NOTE-1\n")
        self.assertTrue(kg._frontmatter_gives_id(big))                             # cut before the closer: counted, the safe side

    def test_a_file_touched_by_many_commits_is_read_once_and_a_named_entry_never(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        repo = self.tmp / "cost"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    dirs:\n      decisions: decision\n", encoding="utf-8")
        (repo / "decisions").mkdir()
        (repo / "decisions" / "hand-named.md").write_text("---\nid: ADR-5\n---\n", encoding="utf-8")
        (repo / "decisions" / "ADR-0001-named.md").write_text("x\n", encoding="utf-8")
        now = datetime.now(timezone.utc)
        commits = [{"when": now, "subject": "s", "files": [("M", "", "decisions/hand-named.md"), ("M", "", "decisions/ADR-0001-named.md")]}
                   for _ in range(5)]
        with mock.patch.object(kg, "_frontmatter_gives_id", wraps=kg._frontmatter_gives_id) as read:
            out, capped = kg._unindexed_entries(repo, commits, set())
        self.assertFalse(capped)
        self.assertEqual(out, {"decisions/hand-named.md", "decisions/ADR-0001-named.md"})
        self.assertEqual(read.call_count, 1)                                         # five commits, one read; the named entry: none

    def test_a_source_folder_that_leads_outside_the_repository_is_never_counted(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        repo = self.tmp / "linked"
        outside = self.tmp / "outside"
        (repo / "kb" / "_meta").mkdir(parents=True)
        (repo / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    dirs:\n      decisions: decision\n", encoding="utf-8")
        outside.mkdir()
        (outside / "ADR-0001-elsewhere.md").write_text("---\nid: ADR-0001\n---\nx\n", encoding="utf-8")
        (repo / "decisions").symlink_to(outside, target_is_directory=True)           # the engine skips what resolves outside the repo
        commits = [{"when": datetime.now(timezone.utc), "subject": "s", "files": [("A", "", "decisions/ADR-0001-elsewhere.md")]}]
        self.assertEqual(kg._unindexed_entries(repo, commits, set()), (set(), False))

    def test_a_graph_row_without_an_id_still_counts_as_known(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        repo = self.tmp / "rowless"
        (repo / "kb" / "graph").mkdir(parents=True)
        (repo / "kb" / "graph" / "entities.jsonl").write_text(
            json.dumps({"path": str(repo / "decisions" / "ADR-0001-idless.md"), "type": "decision"}) + "\n"
            + json.dumps({"path": str(repo / "decisions" / "ADR-0002-known.md"), "uid": "U2", "id": "ADR-0002", "type": "decision", "title": "t"}) + "\n",
            encoding="utf-8")
        known: set[str] = set()
        by = kg._entities_by_relpath(repo, known)
        self.assertEqual(set(by), {"decisions/ADR-0002-known.md"})                         # an id-less row is no entity ...
        self.assertEqual(known, {"decisions/ADR-0001-idless.md", "decisions/ADR-0002-known.md"})   # ... but the graph does list the file

    def test_the_folder_name_is_taken_as_it_is_on_disk(self):
        from corvin_console.routes import plugins_corvin_knowledge_api as kg
        root = self.tmp / "case"
        (root / "Docs").mkdir(parents=True)
        self.assertEqual(kg._true_case(root, "docs"), "Docs")                # a case-insensitive file system's spelling
        (root / "docs").mkdir()
        self.assertEqual(kg._true_case(root, "docs"), "docs")                # an exact match wins when both exist

    def test_the_route_needs_a_session(self):
        with _sandbox(self.tmp) as (client, _csrf, _home, _):
            client.cookies.clear()
            self.assertIn(client.get(f"{_URL}/recent").status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()

"""T-0346 / ADR-2316 Decisions 1, 2, 4 — two knowledge bases project side by side into one store.

Before (measured 2026-11-08): ``projection.apply`` archived every ``kb:`` item that its payload
did not contain, so the payload of a second KB would have archived the first KB's whole board.

Real ``kb.py`` subprocesses build two fixture KBs and export them (``kb export``); the payloads
are applied into a sandboxed console store; the board is read back over the real
``GET /v1/console/task-tracking/items`` route. What must hold:

* both KBs' items exist; the bound KB's carry no ``prj-*`` label, the project's carry ``prj-alpha``;
* removing an item from one KB archives only that item, never an item of the other KB;
* re-applying the bound KB's payload never archives the project's items, and vice versa;
* a KB cannot plant ``prj-*`` on its own items; an invalid scope is refused;
* the audit record of a project projection carries ``scope`` (allowlisted, not dropped).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[2] / "core" / "console" / "tests"
sys.path.insert(0, str(_TESTS))
from test_admin_route import _audit_events, _sandbox  # noqa: E402


def _projects_root() -> Path:
    here = Path(__file__).resolve().parent
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return Path(r.stdout.strip()).parent.parent if r.returncode == 0 else Path(__file__).resolve().parents[3]


_KB_SRC = Path(os.environ.get("CORVIN_KB_SCRIPTS") or _projects_root() / "Corvin-Knowledge" / "scripts")


def _sh(*args, cwd=None):
    r = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _kb(repo: Path, *args) -> dict:
    return json.loads(_sh(sys.executable, repo / "scripts" / "kb.py", "--repo", repo, *args))


def _make_kb(root: Path, name: str) -> tuple[Path, dict]:
    kb = root / name
    (kb / "scripts").mkdir(parents=True)
    for f in ("kb.py", "kb_model.py", "kb_lib.py"):
        shutil.copy(_KB_SRC / f, kb / "scripts" / f)
    (kb / "kb" / "_meta").mkdir(parents=True)
    (kb / "kb" / "_meta" / "sources.yaml").write_text(
        "sources:\n  - name: kb\n    root: kb\n    writable: true\n    dirs:\n"
        "      initiatives: initiative\n      epics: epic\n      tasks: task\n", encoding="utf-8")
    (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n", encoding="utf-8")
    _sh("git", "init", "-q", "-b", "main", cwd=kb)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=kb)
    ids = {"i": _kb(kb, "new", "initiative", "--title", f"{name} initiative")["id"]}
    ids["e"] = _kb(kb, "new", "epic", "--title", f"{name} epic", "--initiative", ids["i"])["id"]
    ids["t1"] = _kb(kb, "new", "task", "--title", f"{name} task one", "--epic", ids["e"], "--dod", "done")["id"]
    ids["t2"] = _kb(kb, "new", "task", "--title", f"{name} task two", "--epic", ids["e"], "--dod", "done")["id"]
    return kb, ids


def _export(kb: Path, scope: str | None = None) -> dict:
    payload = _kb(kb, "export")
    if scope:
        payload["scope"] = scope
    return payload


@unittest.skipUnless((_KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectionScopeE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound, self.bound_ids = _make_kb(self.tmp, "bound")
        self.proj, self.proj_ids = _make_kb(self.tmp, "alpha")

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _board(self, client) -> dict[str, dict]:
        # these tests apply a project's export straight into the store, without the registry and the projector; the board shows
        # a project's cards only while the projector carries it (REVIEW-0081 N27), so the state a projector pass writes is set here
        from corvin_console import kb_projection
        kb_projection._save_projects("_default", {"alpha": {"state": "ok"}})
        r = client.get("/v1/console/task-tracking/items")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        items = body["items"] if isinstance(body, dict) else body
        # a projected title is "<kb id> · <title>"; both fixture KBs number from T-0001, which is
        # exactly why display ids must be qualified per project (ADR-2316 Decision 6, T-0349)
        return {it["title"].split(" · ", 1)[-1]: it for it in items}

    def test_two_kbs_project_side_by_side_and_never_archive_each_other(self):
        with _sandbox(self.tmp / "s") as (client, _csrf, home, _):
            from core.task_tracking import projection

            a = projection.apply("_default", _export(self.bound))
            b = projection.apply("_default", _export(self.proj, "alpha"))
            self.assertEqual((a["state"], b["state"]), ("ok", "ok"), (a, b))
            self.assertEqual(b.get("scope"), "alpha")
            board = self._board(client)
            for name in ("bound", "alpha"):
                for what in ("initiative", "epic", "task one", "task two"):
                    self.assertIn(f"{name} {what}", board)
            self.assertFalse(any(x.startswith("prj-") for x in board["bound task one"]["labels"]))
            self.assertIn("prj-alpha", board["alpha task one"]["labels"])

            # re-applying either KB alone archives nothing of the other one
            again = projection.apply("_default", _export(self.bound))
            self.assertEqual(again["archived"], 0, again)
            again = projection.apply("_default", _export(self.proj, "alpha"))
            self.assertEqual(again["archived"], 0, again)
            self.assertEqual(projection.diff("_default", _export(self.bound)), [])
            self.assertEqual(projection.diff("_default", _export(self.proj, "alpha")), [])

            # remove a task from the project KB: only that card is archived
            f = next((self.proj / "kb" / "tasks").glob(f"{self.proj_ids['t2']}-*.md"))
            f.unlink()
            _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "drop t2", cwd=self.proj)
            res = projection.apply("_default", _export(self.proj, "alpha"))
            self.assertEqual(res["archived"], 1, res)
            board = self._board(client)
            self.assertEqual(board["alpha task two"]["status"], "archived")
            for what in ("initiative", "epic", "task one", "task two"):
                self.assertNotEqual(board[f"bound {what}"]["status"], "archived", what)

            # the bound KB's orphan pass does not see the project's cards either
            res = projection.apply("_default", _export(self.bound))
            self.assertEqual(res["archived"], 0, res)
            self.assertEqual(self._board(client)["alpha task one"]["status"], "open")

            # audit: the project projection's record carries its scope (allowlisted, not dropped)
            recs = [r for r in _audit_events(home) if r.get("event_type") == "task_item.projection_applied"]
            scoped = [r for r in recs if (r.get("details") or {}).get("scope") == "alpha"]
            self.assertTrue(scoped, f"no scoped projection record among {len(recs)}")
            self.assertNotIn("scope", (scoped[-1].get("details") or {}).get("_dropped_fields", []))

    def test_the_same_uids_in_two_project_scopes_both_reach_the_board(self):
        """REVIEW-0066 M3: a copied KB carries the original's uids; a pre-change project row is updated in place."""
        with _sandbox(self.tmp / "s") as (client, _csrf, _home, _):
            from core.task_tracking import projection, store

            first = projection.apply("_default", _export(self.proj, "alpha"))
            second = projection.apply("_default", _export(self.proj, "alpha-copy"))
            self.assertEqual((first["state"], second["state"]), ("ok", "ok"), (first, second))
            self.assertEqual(second["created"], 4, second)
            a, b = projection.kb_items("_default", scope="alpha"), projection.kb_items("_default", scope="alpha-copy")
            self.assertEqual(set(a), set(b), "both scopes hold every uid under the payload ref")
            self.assertTrue(all(r["external_ref"].endswith("@alpha-copy") for r in b.values()))
            self.assertEqual(projection.diff("_default", _export(self.proj, "alpha")), [])
            self.assertEqual(projection.diff("_default", _export(self.proj, "alpha-copy")), [])
            ref = next(iter(a))
            self.assertEqual(projection.kb_uid(a[ref]["external_ref"]), ref.split(":", 1)[1])

            # a project row written before the change (kb:<uid> + its prj label) is found and kept, not re-created
            with store.connect("_default") as conn:
                conn.execute("UPDATE items SET external_ref = substr(external_ref, 1, instr(external_ref, '@') - 1) "
                             "WHERE tenant_id='_default' AND external_ref LIKE '%@alpha'")
                conn.commit()
            ids = {k: v["id"] for k, v in projection.kb_items("_default", scope="alpha").items()}
            again = projection.apply("_default", _export(self.proj, "alpha"))
            self.assertEqual((again["created"], again["archived"]), (0, 0), again)
            self.assertEqual({k: v["id"] for k, v in projection.kb_items("_default", scope="alpha").items()}, ids)

    def test_a_kb_cannot_plant_a_scope_label_and_invalid_scopes_are_refused(self):
        t = next((self.bound / "kb" / "tasks").glob(f"{self.bound_ids['t1']}-*.md"))
        t.write_text(t.read_text(encoding="utf-8").replace("title:", "labels: [prj-alpha, urgent]\ntitle:", 1),
                     encoding="utf-8")
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-qam", "plant label", cwd=self.bound)
        with _sandbox(self.tmp / "s") as (client, _csrf, _home, _):
            from core.task_tracking import projection, service

            projection.apply("_default", _export(self.bound))
            labels = self._board(client)["bound task one"]["labels"]
            self.assertIn("urgent", labels)
            self.assertNotIn("prj-alpha", labels)
            for bad in ("Alpha", "../x", "a" * 29, "a_b"):
                with self.assertRaises(service.TaskTrackingError, msg=bad):
                    projection.apply("_default", _export(self.proj, bad))


if __name__ == "__main__":
    unittest.main()

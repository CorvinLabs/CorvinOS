"""T-0348 / ADR-2316 Decision 5 — board writes on a project card go into THAT project's KB.

Through the real console routes (``/v1/console/task-tracking/*``) with a real session:

* moving a project card (``/kb-transition``) runs ``kb task`` in the registered project repo
  (a new commit there), never in the bound KB;
* creating a task under a project epic (``POST /items``) writes the file into the project repo
  and the card appears with ``prj-<id>``;
* PATCHing a KB-owned field of a project card is still 409 ``kb_owned``;
* a card whose project is no longer registered is refused (409), nothing is written;
* the transition audit records carry the scope.
The repo comes only from the registry: the request carries an item id, never a path.
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_project_kb_projector_e2e as fx  # noqa: E402
from test_admin_route import _audit_events, _sandbox  # noqa: E402  (path added by fx)

_URL = "/v1/console/task-tracking"


def _head(repo: Path) -> str:
    return fx._sh("git", "rev-parse", "HEAD", cwd=repo).strip()


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectKbWritesE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound = self.tmp / "bound"
        fx._kb_init(self.bound, engine=True)
        fx._seed(self.bound, "bound")
        self.saved = os.environ.get("CORVIN_KB_REPO")
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        if self.saved is None:
            os.environ.pop("CORVIN_KB_REPO", None)
        else:
            os.environ["CORVIN_KB_REPO"] = self.saved
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _board(self, client) -> dict[str, dict]:
        body = client.get(f"{_URL}/items").json()
        items = body["items"] if isinstance(body, dict) else body
        return {it["title"].split(" · ", 1)[-1]: it for it in items}

    def test_project_card_writes_go_to_the_project_repo(self):
        with _sandbox(self.tmp / "s") as (client, csrf, home, _):
            from corvin_console import kb_projection
            alpha = fx._make_project(self.tmp / "p", "alpha")
            fx._write_registry(home, "_default", [("alpha", alpha)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            board = self._board(client)
            card = board["alpha task"]
            self.assertIn("prj-alpha", card["labels"])
            proj_head, bound_head = _head(alpha / "docs"), _head(self.bound)

            # 1. a board move runs `kb task` in the PROJECT repo
            r = client.post(f"{_URL}/items/{card['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "in_progress")
            self.assertNotEqual(_head(alpha / "docs"), proj_head, "no commit in the project repo")
            self.assertIn("in_progress", fx._sh("git", "log", "-1", "--format=%s", cwd=alpha / "docs"))
            self.assertEqual(_head(self.bound), bound_head, "the bound KB was written")

            # 2. a new task under the project epic lands in the project repo
            epic = self._board(client)["alpha epic"]
            r = client.post(f"{_URL}/items", json={"kind": "task", "title": "made on the board",
                                                   "parent_id": epic["id"]}, headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 201, r.text)
            self.assertIn("prj-alpha", r.json()["labels"])
            self.assertTrue(any("made on the board" in p.read_text(encoding="utf-8")
                                for p in (alpha / "docs" / "kb" / "tasks").glob("*.md")))
            self.assertFalse(any("made on the board" in p.read_text(encoding="utf-8")
                                 for p in (self.bound / "kb" / "tasks").glob("*.md")))
            self.assertEqual(_head(self.bound), bound_head)

            # 3. KB-owned fields of a project card stay KB-owned
            cur = self._board(client)["alpha task"]
            r = client.patch(f"{_URL}/items/{cur['id']}", json={"version": cur["version"], "status": "complete"},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)

            # 4. audit: the transition records carry the scope
            recs = [e for e in _audit_events(home) if e.get("event_type") == "task_item.kb_transition"]
            self.assertTrue(any((e.get("details") or {}).get("scope") == "alpha" for e in recs), recs[-3:])
            creates = [e for e in _audit_events(home) if e.get("event_type") == "task_item.kb_create"]
            self.assertTrue(any((e.get("details") or {}).get("scope") == "alpha" for e in creates), creates[-3:])

            # 5. unregister the project: its card can no longer be moved, nothing is written
            fx._write_registry(home, "_default", [])
            head = _head(alpha / "docs")
            r = client.post(f"{_URL}/items/{cur['id']}/kb-transition", json={"to": "open"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(_head(alpha / "docs"), head)

    def test_bound_kb_writes_are_unchanged(self):
        with _sandbox(self.tmp / "s") as (client, csrf, _home, _):
            from corvin_console import kb_projection
            kb_projection.sync("_default", force=True)
            card = self._board(client)["bound task"]
            head = _head(self.bound)
            r = client.post(f"{_URL}/items/{card['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertNotEqual(_head(self.bound), head)


if __name__ == "__main__":
    unittest.main()

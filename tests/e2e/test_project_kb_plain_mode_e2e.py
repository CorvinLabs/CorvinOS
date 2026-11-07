"""T-0365 (found by spike T-0350, NOTE-0017) — a project KB that is a SUBDIRECTORY of the project's
git root (``docs_mode=plain``) must be held like any KB when a work item is edited but not committed.

``git status --porcelain`` prints paths relative to the git ROOT (``docs/kb/tasks/…``), so the
projector's check (``^kb/(initiatives|epics|tasks)/``) never matched in plain mode: a hand-set,
uncommitted ``status: done`` reached the board past the KB state machine (``kb export`` reads the
working tree). Real fixtures, real projector pass, board read over the real route.
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
from test_admin_route import _sandbox  # noqa: E402

_URL = "/v1/console/task-tracking"


def _make_plain_project(root: Path, pid: str) -> Path:
    """One git root for code and KB: ``<proj>/src`` + ``<proj>/docs`` (no nested repo)."""
    proj = root / pid
    (proj / "src").mkdir(parents=True)
    (proj / "src" / "app.py").write_text("print('hi')\n", encoding="utf-8")
    (proj / "docs" / "kb" / "_meta").mkdir(parents=True)
    (proj / "docs" / "kb" / "_meta" / "sources.yaml").write_text(fx._SOURCES, encoding="utf-8")
    (proj / ".gitignore").write_text("docs/kb/.lock\ndocs/kb/graph/\n", encoding="utf-8")
    (proj / "project.yaml").write_text(
        f"schema: 1\nid: {pid}\ntenant: _default\ndocs_mode: plain\nfeatures:\n  kb: true\n", encoding="utf-8")
    fx._sh("git", "init", "-q", "-b", "main", cwd=proj)
    fx._git_commit(proj, "user: init")
    fx._seed(proj / "docs", pid)
    return proj


@unittest.skipUnless((fx._KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectKbPlainModeE2E(unittest.TestCase):
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

    def _card(self, client, title: str) -> dict:
        body = client.get(f"{_URL}/items").json()
        items = body["items"] if isinstance(body, dict) else body
        return next(it for it in items if it["title"].split(" · ", 1)[-1] == title)

    def test_uncommitted_hand_edit_in_a_plain_mode_project_holds_the_scope(self):
        with _sandbox(self.tmp / "s") as (client, _csrf, home, _):
            from corvin_console import kb_projection
            proj = _make_plain_project(self.tmp / "p", "plainp")
            fx._write_registry(home, "_default", [("plainp", proj)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            self.assertEqual(kb_projection.project_status("_default")["plainp"]["state"], "ok")
            self.assertEqual(self._card(client, "plainp task")["status"], "open")

            # a hand-set, uncommitted status (a legal value: the KB stays green, so only the hold can stop it)
            f = next((proj / "docs" / "kb" / "tasks").glob("T-0001-*.md"))
            f.write_text(f.read_text(encoding="utf-8").replace("status: open", "status: in_progress"), encoding="utf-8")
            kb_projection.sync_projects("_default")
            st = kb_projection.project_status("_default")["plainp"]
            self.assertEqual(st["state"], "held", st)
            self.assertTrue(any("kb/tasks/" in p for p in st.get("pending_uncommitted", [])), st)
            self.assertEqual(self._card(client, "plainp task")["status"], "open",
                             "an uncommitted hand edit reached the board")

            # reverting the edit releases the hold
            fx._sh("git", "checkout", "-q", "--", str(f), cwd=proj)
            kb_projection.sync_projects("_default")
            self.assertEqual(kb_projection.project_status("_default")["plainp"]["state"], "ok")

    def test_nested_and_bound_kbs_still_hold_on_uncommitted_edits(self):
        with _sandbox(self.tmp / "s") as (client, _csrf, home, _):
            from corvin_console import kb_projection
            nested = fx._make_project(self.tmp / "p", "nestedp")
            fx._write_registry(home, "_default", [("nestedp", nested)])
            kb_projection.sync("_default", force=True)
            kb_projection.sync_projects("_default")
            f = next((nested / "docs" / "kb" / "tasks").glob("T-0001-*.md"))
            f.write_text(f.read_text(encoding="utf-8").replace("status: open", "status: in_progress"), encoding="utf-8")
            kb_projection.sync_projects("_default")
            self.assertEqual(kb_projection.project_status("_default")["nestedp"]["state"], "held")
            b = next((self.bound / "kb" / "tasks").glob("T-0001-*.md"))
            b.write_text(b.read_text(encoding="utf-8").replace("status: open", "status: in_progress"), encoding="utf-8")
            self.assertEqual(kb_projection.sync("_default")["state"], "held")


if __name__ == "__main__":
    unittest.main()

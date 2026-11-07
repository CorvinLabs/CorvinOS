"""T-0347 / ADR-2316 Decisions 3, 8, 10 — the projector projects every registered project KB as
its own scope, isolated from the bound KB and from each other.

Real fixtures: a bound KB (``CORVIN_KB_REPO``, with the engine) and project directories with
``project.yaml`` and a ``docs/`` KB WITHOUT an engine copy, indexed by
``<tenant_home>/projects/registry.json`` (the ADR-2315 shape; its writer is T-0351). The real
projector thread (``kb_projection.start``) does the work; the board and ``/kb/status`` are read
over the real console routes. What must hold:

* the bound KB's cards and every project's cards (``prj-<id>``) appear;
* a red project KB is ``blocked`` for its scope only — the bound KB and the other project stay ``ok``;
* a project's own ``scripts/kb.py`` is never executed (the bound engine runs it);
* a manifest of another tenant, or a path into another tenant's home, is skipped;
* a saturated host skips project scopes;
* the cost of one pass over 1, 4 and 16 project KBs is measured (printed, recorded in a NOTE).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

_TESTS = Path(__file__).resolve().parents[2] / "core" / "console" / "tests"
sys.path.insert(0, str(_TESTS))
from test_admin_route import _sandbox  # noqa: E402

_URL = "/v1/console/task-tracking"


def _projects_root() -> Path:
    here = Path(__file__).resolve().parent
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True, encoding="utf-8", errors="replace")
    return Path(r.stdout.strip()).parent.parent if r.returncode == 0 else Path(__file__).resolve().parents[3]


_KB_SRC = Path(os.environ.get("CORVIN_KB_SCRIPTS") or _projects_root() / "Corvin-Knowledge" / "scripts")
_SOURCES = ("sources:\n  - name: kb\n    root: kb\n    writable: true\n    dirs:\n"
            "      initiatives: initiative\n      epics: epic\n      tasks: task\n")


def _sh(*args, cwd=None, env=None):
    r = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, text=True, encoding="utf-8",
                       errors="replace", env=env)
    if r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _git_commit(repo: Path, msg: str):
    _sh("git", "add", "-A", cwd=repo)
    _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg, cwd=repo)


def _kb_init(kb: Path, *, engine: bool) -> None:
    if engine:
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            shutil.copy(_KB_SRC / f, kb / "scripts" / f)
    (kb / "kb" / "_meta").mkdir(parents=True)
    (kb / "kb" / "_meta" / "sources.yaml").write_text(_SOURCES, encoding="utf-8")
    (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n", encoding="utf-8")
    _sh("git", "init", "-q", "-b", "main", cwd=kb)
    _git_commit(kb, "init")


def _kb(repo: Path, *args) -> dict:
    """Always the TEST copy of the engine with --repo (a project KB has none of its own)."""
    return json.loads(_sh(sys.executable, _KB_SRC / "kb.py", "--repo", repo, *args))


def _seed(kb: Path, name: str) -> None:
    i = _kb(kb, "new", "initiative", "--title", f"{name} initiative")["id"]
    e = _kb(kb, "new", "epic", "--title", f"{name} epic", "--initiative", i)["id"]
    _kb(kb, "new", "task", "--title", f"{name} task", "--epic", e, "--dod", "done")


def _make_project(root: Path, pid: str, *, tenant: str = "_default", kb: bool = True, trap: Path | None = None) -> Path:
    proj = root / pid
    (proj / "docs").mkdir(parents=True)
    (proj / "project.yaml").write_text(
        f"schema: 1\nid: {pid}\ntenant: {tenant}\nfeatures:\n  kb: {'true' if kb else 'false'}\n", encoding="utf-8")
    _kb_init(proj / "docs", engine=False)
    if trap is not None:
        # a project-owned engine that would leave a marker if the console ever ran it
        (proj / "docs" / "scripts").mkdir()
        (proj / "docs" / "scripts" / "kb.py").write_text(
            f"from pathlib import Path\nPath({str(trap)!r}).write_text('ran')\n", encoding="utf-8")
        _git_commit(proj / "docs", "trap")
    _seed(proj / "docs", pid)
    return proj


def _write_registry(home: Path, tenant: str, projects: list[tuple[str, Path]]) -> None:
    reg = home / "tenants" / tenant / "projects" / "registry.json"
    reg.parent.mkdir(parents=True, exist_ok=True)
    reg.write_text(json.dumps({"schema": 1, "projects": [
        {"id": pid, "path": str(p), "template": "test@1", "added_at": "2026-11-08T00:00:00Z"} for pid, p in projects]}),
        encoding="utf-8")


@unittest.skipUnless((_KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class ProjectKbProjectorE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.bound = self.tmp / "bound"
        _kb_init(self.bound, engine=True)
        _seed(self.bound, "bound")
        self.saved = {k: os.environ.get(k) for k in ("CORVIN_KB_REPO",)}
        os.environ["CORVIN_KB_REPO"] = str(self.bound)

    def tearDown(self):
        for k, v in self.saved.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _board(self, client) -> dict[str, dict]:
        body = client.get(f"{_URL}/items").json()
        items = body["items"] if isinstance(body, dict) else body
        return {it["title"].split(" · ", 1)[-1]: it for it in items}

    def _wait(self, client, pred, what: str, timeout: float = 45.0):
        deadline = time.time() + timeout
        while time.time() < deadline:
            st = client.get(f"{_URL}/kb/status").json()
            if pred(st, self._board(client)):
                return st
            time.sleep(0.3)
        self.fail(f"timed out waiting for {what}: {client.get(f'{_URL}/kb/status').json()}")

    def test_projector_thread_projects_each_project_as_its_own_scope(self):
        trap = self.tmp / "TRAP_RAN"
        with _sandbox(self.tmp / "s", tenants=("_default", "acme")) as (client, _csrf, home, _):
            from corvin_console import kb_projection
            alpha = _make_project(self.tmp / "p", "alpha", trap=trap)
            beta = _make_project(self.tmp / "p", "beta")
            foreign = _make_project(self.tmp / "p", "foreign", tenant="acme")
            nokb = _make_project(self.tmp / "p", "nokb", kb=False)
            inside_other = _make_project(home / "tenants" / "acme", "sneaky")
            _write_registry(home, "_default", [("alpha", alpha), ("beta", beta), ("foreign", foreign),
                                               ("nokb", nokb), ("sneaky", inside_other)])
            self.assertEqual([p for p, _ in kb_projection.project_kbs("_default")], ["alpha", "beta"])

            kb_projection._started.discard("_default")
            kb_projection._state.pop("_default", None)
            self.assertTrue(kb_projection.start("_default"))
            self.addCleanup(kb_projection.stop, "_default")
            st = self._wait(client, lambda st, b: st.get("state") == "ok"
                            and all((st.get("projects") or {}).get(p, {}).get("state") == "ok" for p in ("alpha", "beta")),
                            "bound + both projects ok")
            board = self._board(client)
            self.assertIn("bound task", board)
            self.assertIn("prj-alpha", board["alpha task"]["labels"])
            self.assertIn("prj-beta", board["beta task"]["labels"])
            self.assertNotIn("foreign task", board)
            self.assertNotIn("nokb task", board)
            self.assertNotIn("sneaky task", board)
            self.assertNotIn("payload", json.dumps(st["projects"]))      # status never ships payloads
            self.assertFalse(trap.exists(), "the console executed code from a project directory")

            # beta turns red: blocked for beta only, nothing archived anywhere
            bad = beta / "docs" / "kb" / "tasks" / "T-0099-orphan.md"
            bad.write_text("---\nid: T-0099\nuid: 01J00000000000000000000000\ntitle: orphan\n"
                           "status: open\nepic: E-404\n---\n", encoding="utf-8")
            _git_commit(beta / "docs", "bad task")
            self._wait(client, lambda st, b: (st.get("projects") or {}).get("beta", {}).get("state") == "blocked",
                       "beta blocked")
            st = client.get(f"{_URL}/kb/status").json()
            self.assertEqual(st["state"], "ok")
            self.assertEqual(st["projects"]["alpha"]["state"], "ok")
            board = self._board(client)
            for t in ("bound task", "alpha task", "beta task"):
                self.assertNotEqual(board[t]["status"], "archived", t)

            # a saturated host skips project scopes
            self.assertEqual(kb_projection.sync_projects("_default", busy=True), {"skipped": "host saturated"})
            kb_projection.stop("_default")
            self.assertFalse(trap.exists())

    def test_tick_cost_over_1_4_16_project_kbs(self):
        with _sandbox(self.tmp / "s") as (client, _csrf, home, _):
            from corvin_console import kb_projection
            projects = [(f"p{n:02d}", _make_project(self.tmp / "p", f"p{n:02d}")) for n in range(16)]
            rows = []
            for n in (1, 4, 16):
                _write_registry(home, "_default", projects[:n])
                kb_projection._project_state.pop("_default", None)
                kb_projection._project_cursor.pop("_default", None)
                try:
                    kb_projection._project_state_path("_default").unlink()
                except OSError:
                    pass
                t0 = time.perf_counter()
                res = kb_projection.sync_projects("_default")
                first = time.perf_counter() - t0
                t0 = time.perf_counter()
                kb_projection.sync_projects("_default")
                unchanged = time.perf_counter() - t0
                self.assertEqual(len(res["visited"]), n)
                rows.append((n, first, unchanged))
            board = self._board(client)
            self.assertEqual(sum(1 for t in board if t.endswith(" task") and t.startswith("p")), 16)
            print("\nproject-KB pass cost (seconds): " + "; ".join(
                f"n={n}: first={a:.2f} unchanged={b:.2f}" for n, a, b in rows))


if __name__ == "__main__":
    unittest.main()

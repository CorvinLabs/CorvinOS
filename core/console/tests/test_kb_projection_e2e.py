"""E2E: knowledge base -> task board, through the real console routes (ADR-2205).

A fixture Corvin-Knowledge repo (real git, the real ``kb.py`` copied from the
sibling checkout) is projected into a sandboxed console. Every step goes through
HTTP (``/v1/console/task-tracking/...``) and the real ``kb`` subprocess:

* the projection creates initiative -> epic -> task with ``external_ref kb:<uid>``;
* the board cannot PATCH a KB-owned field (409 kb_owned) — the KB is the one writer;
* a board move is a KB transition: the KB file changes, the card follows;
* the KB state machine's refusal reaches the board verbatim (409 kb_refused);
* a raw store write behind the KB's back is DRIFT and is repaired on the next tick;
* a derivable KB deviation (an epic carrying a status) is healed and committed;
* a red KB writes nothing but one ``task_item.projection_blocked`` record.
"""
from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/task-tracking"
_KB_SRC = Path(__file__).resolve().parents[3].parent / "Corvin-Knowledge" / "scripts"


def _sh(*args, cwd=None):
    r = subprocess.run(list(map(str, args)), cwd=cwd, capture_output=True, text=True)
    if r.returncode != 0:
        raise AssertionError(f"{args} -> {r.returncode}\n{r.stdout}\n{r.stderr}")
    return r.stdout


def _kb(repo: Path, *args) -> dict:
    out = _sh(sys.executable, repo / "scripts" / "kb.py", "--repo", repo, *args)
    return json.loads(out)


@unittest.skipUnless((_KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KbProjectionE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        kb = self.kb = self.tmp / "kb-repo"
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            shutil.copy(_KB_SRC / f, kb / "scripts" / f)
        (kb / "kb" / "_meta").mkdir(parents=True)
        (kb / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: kb\n    writable: true\n    dirs:\n"
            + "".join(f"      {d}: {t}\n" for d, t in (("decisions", "decision"), ("initiatives", "initiative"),
                                                       ("epics", "epic"), ("tasks", "task"))))
        (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n")
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=kb)
        self.adr = _kb(kb, "new", "decision", "--title", "Board follows the KB")
        self.ini = _kb(kb, "new", "initiative", "--title", "Knowledge base")
        self.epic = _kb(kb, "new", "epic", "--title", "Projection", "--initiative", self.ini["id"])
        self.t1 = _kb(kb, "new", "task", "--title", "Project items", "--epic", self.epic["id"],
                      "--link", f"implements={self.adr['id']}")
        self.t2 = _kb(kb, "new", "task", "--title", "Heal drift", "--epic", self.epic["id"])
        os.environ["CORVIN_KB_REPO"] = str(kb)

    def tearDown(self):
        os.environ.pop("CORVIN_KB_REPO", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _items(self, client) -> dict[str, dict]:
        r = client.get(f"{_URL}/items")
        self.assertEqual(r.status_code, 200, r.text)
        return {i["external_ref"]: i for i in r.json()["items"] if str(i.get("external_ref") or "").startswith("kb:")}

    def _sync(self, client, csrf) -> dict:
        r = client.post(f"{_URL}/kb/sync", headers={"X-CSRF-Token": csrf})
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_projection_ownership_transition_drift_heal_block(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            # 1. projection: hierarchy + refs
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "ok", st)
            self.assertEqual(st["created"], 4)
            items = self._items(client)
            ini, epic = items[f"kb:{self.ini['uid']}"], items[f"kb:{self.epic['uid']}"]
            t1, t2 = items[f"kb:{self.t1['uid']}"], items[f"kb:{self.t2['uid']}"]
            self.assertEqual((ini["kind"], epic["kind"], t1["kind"]), ("initiative", "epic", "task"))
            self.assertEqual((epic["parent_id"], t1["parent_id"]), (ini["id"], epic["id"]))
            self.assertTrue(t1["title"].startswith(self.t1["id"] + " · "))
            self.assertEqual(t1["category"], "kb")
            # idempotent: a second sync writes nothing
            again = self._sync(client, csrf)
            self.assertEqual((again["created"], again["updated"], again["unchanged"]), (0, 0, 4))

            # 2. ownership: the board cannot patch a KB-owned field
            r = client.patch(f"{_URL}/items/{t1['id']}", json={"version": t1["version"], "status": "complete"},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertTrue(r.json()["detail"]["kb_owned"])
            r = client.patch(f"{_URL}/items/{t1['id']}", json={"version": t1["version"], "priority": "high"},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)   # local fields stay editable
            r = client.post(f"{_URL}/items/{t1['id']}/delete", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)

            # 3. a board move is a KB transition
            r = client.post(f"{_URL}/items/{t1['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["status"], "in_progress")
            text = next((self.kb / "kb" / "tasks").glob(f"{self.t1['id']}-*.md")).read_text()
            self.assertIn("status: in_progress", text)
            self.assertIn("in_progress", _sh("git", "log", "-1", "--format=%s", cwd=self.kb))
            self.assertEqual(self._items(client)[f"kb:{self.epic['uid']}"]["status"], "in_progress")  # rollup

            # 4. the state machine's refusal reaches the board
            r = client.post(f"{_URL}/items/{t1['id']}/kb-transition", json={"to": "complete"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertIn("definition_of_done", r.json()["detail"]["message"])
            r = client.post(f"{_URL}/items/{t2['id']}/kb-transition", json={"to": "complete", "dod": "x"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)          # open -> done is not an edge
            r = client.post(f"{_URL}/items/{epic['id']}/kb-transition", json={"to": "complete"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)          # a container's status is derived

            # 5. drift: a raw write behind the KB's back is repaired on the next tick
            db = home / "tenants" / "_default" / "global" / "task_tracking" / "tasks.db"
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE items SET status='complete' WHERE id=?", (t2["id"],))
            st = self._sync_tick()
            self.assertEqual(st["drift_healed"], 1, st)
            self.assertEqual(self._items(client)[f"kb:{self.t2['uid']}"]["status"], "open")

            # 6. heal: an epic carrying a status is a derivable deviation
            ep = next((self.kb / "kb" / "epics").glob(f"{self.epic['id']}-*.md"))
            ep.write_text(ep.read_text().replace("title:", "status: done\ntitle:", 1))
            st = self._sync(client, csrf)
            self.assertEqual(st["healed"], 1, st)
            self.assertNotIn("status:", ep.read_text().split("---")[1])
            self.assertIn("heal", _sh("git", "log", "-1", "--format=%s", cwd=self.kb))
            self.assertEqual(self._items(client)[f"kb:{self.epic['uid']}"]["status"], "in_progress")

            # 7. a red KB blocks the projection and writes nothing else
            bad = self.kb / "kb" / "tasks" / "T-0099-orphan.md"
            bad.write_text("---\nid: T-0099\nuid: 01J00000000000000000000000\ntitle: orphan\n"
                           "status: open\nepic: E-404\n---\n")
            before = len(self._items(client))
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "blocked", st)
            self.assertEqual(len(self._items(client)), before)
            r = client.get(f"{_URL}/kb/status")
            self.assertEqual(r.json()["state"], "blocked")

            evs = [e.get("event_type") for e in _audit_events(home, "_default")]
            for want in ("task_item.projection_applied", "task_item.kb_transition", "task_item.projection_blocked"):
                self.assertIn(want, evs)

    def _sync_tick(self):
        from corvin_console import kb_projection
        return kb_projection.sync("_default")


if __name__ == "__main__":
    unittest.main()

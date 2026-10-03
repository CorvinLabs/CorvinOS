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
def _projects_root() -> Path:
    """Sibling repos live next to the MAIN checkout — also when this runs in a linked worktree."""
    here = Path(__file__).resolve().parent
    r = subprocess.run(["git", "-C", str(here), "rev-parse", "--path-format=absolute", "--git-common-dir"],
                       capture_output=True, text=True)
    return Path(r.stdout.strip()).parent.parent if r.returncode == 0 else Path(__file__).resolve().parents[4]


# CORVIN_KB_SCRIPTS: prove a KB change from its worktree before it is merged
_KB_SRC = Path(os.environ.get("CORVIN_KB_SCRIPTS") or _projects_root() / "Corvin-Knowledge" / "scripts")


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
            # a go/no-go decision would set status (go -> complete) past the KB state machine
            cur = client.get(f"{_URL}/items/{t2['id']}").json()["item"]
            r = client.patch(f"{_URL}/items/{t2['id']}", json={"version": cur["version"], "approval_state": "pending"},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            r = client.post(f"{_URL}/items/{t2['id']}/decision", json={"decision": "approved", "version": r.json()["version"]},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertTrue(r.json()["detail"]["kb_owned"])
            self.assertEqual(self._items(client)[f"kb:{self.t2['uid']}"]["status"], "open")

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
            st = self._sync(client, csrf)                     # uncommitted: never swept into a heal;
            self.assertEqual(st["state"], "blocked", st)      # still red, so red is reported first
            self.assertIn("status: done", ep.read_text())
            self._commit("operator edit")
            st = self._sync(client, csrf)
            self.assertEqual(st["healed"], 1, st)
            self.assertNotIn("status:", ep.read_text().split("---")[1])
            self.assertIn("heal", _sh("git", "log", "-1", "--format=%s", cwd=self.kb))
            self.assertEqual(self._items(client)[f"kb:{self.epic['uid']}"]["status"], "in_progress")

            # 7. a red KB blocks the projection and writes nothing else
            bad = self.kb / "kb" / "tasks" / "T-0099-orphan.md"
            bad.write_text("---\nid: T-0099\nuid: 01J00000000000000000000000\ntitle: orphan\n"
                           "status: open\nepic: E-404\n---\n")
            self._commit("bad task")
            before = len(self._items(client))
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "blocked", st)
            # still red on the next ticks: ONE projection_blocked record, not one per tick
            n_blocked = [e.get("event_type") for e in _audit_events(home, "_default")].count("task_item.projection_blocked")
            self._sync_tick(); self._sync_tick()
            self.assertEqual([e.get("event_type") for e in _audit_events(home, "_default")]
                             .count("task_item.projection_blocked"), n_blocked)
            self.assertEqual(len(self._items(client)), before)
            r = client.get(f"{_URL}/kb/status")
            self.assertEqual(r.json()["state"], "blocked")

            evs = [e.get("event_type") for e in _audit_events(home, "_default")]
            for want in ("task_item.projection_applied", "task_item.kb_transition", "task_item.projection_blocked"):
                self.assertIn(want, evs)

    def _commit(self, msg):
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=self.kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg, cwd=self.kb)

    def test_review_fixes_lock_restore_labels_graph_rollback_audit(self):
        """2026-10-03 adversarial review: each assertion was red before its fix."""
        from core.task_tracking import snapshots, service

        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._sync(client, csrf)
            items = self._items(client)
            t1, t2 = items[f"kb:{self.t1['uid']}"], items[f"kb:{self.t2['uid']}"]
            # (a) every KB-owned field is locked, not just status (test-review finding 4)
            for patch in ({"title": "renamed"}, {"parent_id": None}, {"labels": ["x"]}):
                r = client.patch(f"{_URL}/items/{t1['id']}", json={"version": t1["version"], **patch},
                                 headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 409, (patch, r.text))
            # (b) a raw soft delete (no cascade id) is really restored — and recorded once
            db = home / "tenants" / "_default" / "global" / "task_tracking" / "tasks.db"
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE items SET deleted_at='2026-01-01T00:00:00Z', cascading_delete_id=NULL WHERE id=?",
                             (t2["id"],))
            st = self._sync_tick()
            self.assertEqual((st["state"], st["restored"]), ("ok", 1), st)
            self.assertIsNone(client.get(f"{_URL}/items/{t2['id']}").json()["item"]["deleted_at"])
            # (c) duplicate and >12 labels converge instead of re-writing every tick / crashing
            tf = next((self.kb / "kb" / "tasks").glob(f"{self.t1['id']}-*.md"))
            labels = ", ".join(["dup", "dup"] + [f"l{i}" for i in range(15)])
            tf.write_text(tf.read_text().replace("title:", f"labels: [{labels}]\ntitle:", 1))
            self._commit("labels")
            st = self._sync(client, csrf)
            self.assertEqual(st["state"], "ok", st)
            got = self._items(client)[f"kb:{self.t1['uid']}"]["labels"]
            self.assertEqual(len(got), 12)
            self.assertEqual(got[:2], ["kb", "dup"])
            self.assertEqual(self._sync(client, csrf)["updated"], 0)
            # (d) the graph the Knowledge Graph panel reads is rebuilt by the projector
            ents = (self.kb / "kb" / "graph" / "entities.jsonl").read_text()
            self.assertIn(self.t1["uid"], ents)
            # (e) a board rollback cannot rewrite a KB item
            with self.assertRaises(service.KbOwned):
                import asyncio
                asyncio.run(snapshots.rollback_to_version("_default", t1["id"], 1, actor="operator"))
            # (f) a transition's outcome is audited, the free text is not
            r = client.post(f"{_URL}/items/{t1['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            r = client.post(f"{_URL}/items/{t1['id']}/kb-transition",
                            json={"to": "blocked", "reason": "--dod=sneaky waiting on Jane"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertIn("--dod=sneaky waiting on Jane", tf.read_text())   # one reason, not an option
            outcomes = [e.get("details", {}).get("outcome") for e in _audit_events(home, "_default")
                        if e.get("event_type") == "task_item.kb_transition"]
            self.assertEqual(outcomes.count("applied"), 2)
            self.assertNotIn("Jane", (self.kb / "kb" / "audit.jsonl").read_text())

    def test_held_state_repairs_drift_refuses_moves_and_yields_to_red(self):
        """Refutation round 2026-10-03: held skipped drift repair, stuck on a scratch file,
        answered 200 to a move it never showed, and hid a red KB."""
        from corvin_console import kb_projection
        from core.task_tracking import service

        with _sandbox(self.tmp) as (client, csrf, home, _):
            self.assertEqual(self._sync(client, csrf)["state"], "ok")
            items = self._items(client)
            t1, t2 = items[f"kb:{self.t1['uid']}"], items[f"kb:{self.t2['uid']}"]
            scratch = self.kb / "kb" / "tasks" / "scratch.txt"
            scratch.write_text("not a work item")
            self.assertEqual(self._sync_tick()["state"], "ok")                 # (b) no held for a .txt
            f1 = next((self.kb / "kb" / "tasks").glob(f"{self.t1['id']}-*.md"))
            f1.write_text(f1.read_text() + "\nhalf-written\n")
            st = self._sync_tick()
            self.assertEqual(st["state"], "held", st)
            db = home / "tenants" / "_default" / "global" / "task_tracking" / "tasks.db"
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE items SET status='complete' WHERE id=?", (t2["id"],))
            st = self._sync_tick()
            self.assertEqual((st["state"], st["drift_healed"]), ("held", 1), st)   # (a) drift repaired while held
            self.assertEqual(self._items(client)[f"kb:{self.t2['uid']}"]["status"], "open")
            r = client.post(f"{_URL}/items/{t2['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 409, r.text)                       # (c) refused, not a silent 200
            self.assertIn("paused", r.json()["detail"]["message"])
            bad = self.kb / "kb" / "tasks" / "T-0099-orphan.md"                 # (d) red wins over held
            bad.write_text("---\nid: T-0099\nuid: 01J00000000000000000000099\ntitle: o\nstatus: open\nepic: E-404\n---\n")
            self.assertEqual(self._sync_tick()["state"], "blocked")
            bad.unlink()
            f1.write_text(f1.read_text().replace("\nhalf-written\n", ""))      # revert: back to clean
            self.assertEqual(self._sync_tick()["state"], "ok")
            # tenant check: the projector serves one tenant
            with self.assertRaisesRegex(service.TaskTrackingError, "another tenant"):
                kb_projection.transition("acme", t1["id"], "in_progress")

    def test_kb_subprocess_never_sees_the_console_secrets(self):
        """A KB checkout's code (a commit hook here) runs with a minimal environment."""
        hook = self.kb / ".git" / "hooks" / "pre-commit"
        dump = self.tmp / "hook-env.txt"
        hook.write_text(f"#!/bin/sh\nenv > {dump}\nexit 0\n")
        hook.chmod(0o755)
        os.environ["ANTHROPIC_API_KEY"] = "sk-probe-must-not-leak"
        self.addCleanup(os.environ.pop, "ANTHROPIC_API_KEY", None)
        with _sandbox(self.tmp) as (client, csrf, home, _):
            self._sync(client, csrf)
            t1 = self._items(client)[f"kb:{self.t1['uid']}"]
            r = client.post(f"{_URL}/items/{t1['id']}/kb-transition", json={"to": "in_progress"},
                            headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
        env = dump.read_text()
        self.assertIn("KB_ACTOR=", env)                                         # positive control
        self.assertNotIn("sk-probe-must-not-leak", env)

    def test_projector_thread_starts_and_heals_drift(self):
        """test-review finding 8: start()/_loop had no test; both hosts call it at boot."""
        import time as _t
        from corvin_console import kb_projection

        with _sandbox(self.tmp) as (client, csrf, home, _):
            kb_projection._started.discard("_default")
            kb_projection._state.pop("_default", None)
            self.assertTrue(kb_projection.start("_default"))
            self.addCleanup(kb_projection.stop, "_default")
            self.assertFalse(kb_projection.start("_default"))      # once per process
            deadline = _t.time() + 30
            while _t.time() < deadline and client.get(f"{_URL}/kb/status").json().get("state") != "ok":
                _t.sleep(0.2)
            self.assertEqual(client.get(f"{_URL}/kb/status").json()["state"], "ok")
            t2 = self._items(client)[f"kb:{self.t2['uid']}"]
            db = home / "tenants" / "_default" / "global" / "task_tracking" / "tasks.db"
            with sqlite3.connect(db) as conn:
                conn.execute("UPDATE items SET status='complete' WHERE id=?", (t2["id"],))
            while _t.time() < deadline and client.get(f"{_URL}/kb/status").json().get("drift_total", 0) < 1:
                _t.sleep(0.2)
            self.assertGreaterEqual(client.get(f"{_URL}/kb/status").json()["drift_total"], 1)
            self.assertEqual(self._items(client)[f"kb:{self.t2['uid']}"]["status"], "open")

    def _sync_tick(self):
        from corvin_console import kb_projection
        return kb_projection.sync("_default")


if __name__ == "__main__":
    unittest.main()

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


def _kb_lib_of(kb_repo: Path):
    import importlib
    import sys as _sys
    _sys.path.insert(0, str(kb_repo / "scripts"))
    mod = importlib.import_module("kb_lib")
    _sys.path.remove(str(kb_repo / "scripts"))
    return mod


def _kb_file(d: Path, id_: str) -> Path:
    return next(d.glob(f"{id_}-*.md"))


def _kb_append_link(kb_repo: Path, d: Path, id_: str, rel: str, to: str):
    """fm_set, not raw string concatenation: a second top-level `links:`/`paths:` key
    (both already written by `kb new decision`) is a YAML collision, which fm_set
    (CONCEPT.md's own frontmatter editor) is built to avoid."""
    L = _kb_lib_of(kb_repo)
    p = _kb_file(d, id_)
    doc = L.parse(p)
    existing = doc.fm.get("links") or []
    new_links = list(existing) + [{"rel": rel, "to": to}]
    value_yaml = "\n" + "\n".join(f"- rel: {x['rel']}\n  to: {x['to']}" for x in new_links)
    text = L.fm_set(doc.text, "links", value_yaml)
    L.write_doc(p, text, like=doc)


def _kb_append_paths(kb_repo: Path, d: Path, id_: str, paths: list[str]):
    L = _kb_lib_of(kb_repo)
    p = _kb_file(d, id_)
    doc = L.parse(p)
    text = L.fm_set(doc.text, "paths", L.yaml_list(paths))
    L.write_doc(p, text, like=doc)


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
            + "".join(f"      {d}: {t}\n" for d, t in (("decisions", "decision"), ("concepts", "concept"),
                                                       ("initiatives", "initiative"),
                                                       ("epics", "epic"), ("tasks", "task"))))
        (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n")
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=kb)
        self.adr = _kb(kb, "new", "decision", "--title", "Board follows the KB")
        # G1-G3 (T-0037/T-0038): satisfied via a SEPARATE epic+task, deliberately not the
        # ones below -- t1 must stay without a definition_of_done for the state-machine
        # refusal this test asserts (section 4), so G3's "every task has acceptance
        # criteria" cannot be satisfied through self.epic without breaking that assertion.
        # G3 only inspects epics carrying `implements:` (never tasks), so self.epic staying
        # implements-free, exactly as before this gate existed, is sufficient and correct.
        self.concept = _kb(kb, "new", "concept", "--title", "Board follows the KB (concept)",
                           "--link", f"search={self.adr['id']}", "--link", f"formalized_as={self.adr['id']}")
        _kb_append_link(kb, kb / "kb" / "decisions", self.adr["id"], "inspired_by", self.concept["id"])
        self.ini = _kb(kb, "new", "initiative", "--title", "Knowledge base")
        self.epic = _kb(kb, "new", "epic", "--title", "Projection", "--initiative", self.ini["id"])
        self.t1 = _kb(kb, "new", "task", "--title", "Project items", "--epic", self.epic["id"],
                      "--link", f"implements={self.adr['id']}")
        self.t2 = _kb(kb, "new", "task", "--title", "Heal drift", "--epic", self.epic["id"])
        compliance_epic = _kb(kb, "new", "epic", "--title", "G3 compliance (fixture-only)",
                              "--initiative", self.ini["id"], "--link", f"implements={self.adr['id']}")
        _kb_append_paths(kb, kb / "kb" / "decisions", self.adr["id"], ["x.py"])
        _kb_append_paths(kb, kb / "kb" / "epics", compliance_epic["id"], ["x.py"])
        _kb(kb, "new", "task", "--title", "G3 compliance task", "--epic", compliance_epic["id"],
           "--dod", "G3 acceptance criteria")
        _sh("git", "add", "-A", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture: G1-G3 links", cwd=kb)
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
            self.assertEqual(st["created"], 6)
            items = self._items(client)
            ini, epic = items[f"kb:{self.ini['uid']}"], items[f"kb:{self.epic['uid']}"]
            t1, t2 = items[f"kb:{self.t1['uid']}"], items[f"kb:{self.t2['uid']}"]
            self.assertEqual((ini["kind"], epic["kind"], t1["kind"]), ("initiative", "epic", "task"))
            self.assertEqual((epic["parent_id"], t1["parent_id"]), (ini["id"], epic["id"]))
            self.assertTrue(t1["title"].startswith(self.t1["id"] + " · "))
            self.assertEqual(t1["category"], "kb")
            # idempotent: a second sync writes nothing
            again = self._sync(client, csrf)
            self.assertEqual((again["created"], again["updated"], again["unchanged"]), (0, 0, 6))

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

            # 5. drift: a raw write behind the KB's back is repaired on the next tick. The refusals
            # above are committed audit records (Corvin-Knowledge 912e36c), so HEAD moved: sync once
            # so the next tick takes the unchanged-KB drift path this step pins.
            self._sync(client, csrf)
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


@unittest.skipUnless((_KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KbReadyBadgeE2E(unittest.TestCase):
    """T-0040: the board's read-only 'ready' label, through the real sync route.

    The fixture brings a decision to implementation_ready the real way — concept back-edge
    (G1/G2), an implementing epic with overlapping paths and acceptance criteria (G3), and a
    review closed three-consecutive-zero through `kb review calibrate` with an oracle reviewer
    (G4) — and the label must reach the real tasks.db row. (Until 2026-10-04 this test
    patched kb_model.py to fake G4, which was not built.)
    """
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        kb = self.kb = self.tmp / "kb-repo"
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            shutil.copy(_KB_SRC / f, kb / "scripts" / f)
        (kb / "kb" / "_meta").mkdir(parents=True)
        (kb / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: kb\n    writable: true\n    dirs:\n"
            + "".join(f"      {d}: {t}\n" for d, t in (
                ("decisions", "decision"), ("concepts", "concept"), ("reviews", "review"),
                ("initiatives", "initiative"), ("epics", "epic"), ("tasks", "task"))))
        (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n")
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--allow-empty", "-m", "init", cwd=kb)
        self.adr = _kb(kb, "new", "decision", "--title", "Ready decision")
        self.concept = _kb(kb, "new", "concept", "--title", "Ready concept",
                           "--link", f"search={self.adr['id']}", "--link", f"formalized_as={self.adr['id']}")
        # G1/G2: the decision links back to the concept
        _kb_append_link(kb, kb / "kb" / "decisions", self.adr["id"], "inspired_by", self.concept["id"])
        self.ini = _kb(kb, "new", "initiative", "--title", "Ready initiative")
        self.epic = _kb(kb, "new", "epic", "--title", "Ready epic", "--initiative", self.ini["id"],
                        "--link", f"implements={self.adr['id']}")
        self.t1 = _kb(kb, "new", "task", "--title", "Ready task", "--epic", self.epic["id"],
                     "--dod", "G3 acceptance criteria")
        # G3: matching paths: on the decision and its implementing epic
        _kb_append_paths(kb, kb / "kb" / "decisions", self.adr["id"], ["x.py"])
        _kb_append_paths(kb, kb / "kb" / "epics", self.epic["id"], ["x.py"])
        _sh("git", "add", "-A", cwd=kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", "fixture: G1-G3 links", cwd=kb)
        # G4: three calibrated zero rounds by an oracle reviewer (diffs the seeded copy)
        oracle = self.tmp / "oracle.py"
        oracle.write_text(
            "import difflib, json, os, pathlib, sys\n"
            "inp = json.load(sys.stdin); d = pathlib.Path(os.environ['KB_REVIEW_DIR'])\n"
            f"repo = pathlib.Path({str(kb)!r}); out = []\n"
            "for rel in inp['files']:\n"
            "    orig = next(x for x in repo.rglob(pathlib.Path(rel).name) if '.kb-cache' not in x.parts)\n"
            "    a, b = orig.read_text().split(chr(10)), (d / rel).read_text().split(chr(10))\n"
            "    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():\n"
            "        if tag != 'equal': out.append(dict(file=rel, line=j1 + 1, repro='diff', observed=tag))\n"
            "print(json.dumps(dict(findings=out, claims_checked=['decision consistent'])))\n")
        rev = _kb(kb, "review", "open", "--title", "Review", "--reviews", self.adr["id"],
                  "--reviews", self.epic["id"])                       # G4: the decision AND its plan
        for q in ("correctness", "failure paths", "docs versus code"):
            rnd = _kb(kb, "review", "calibrate", rev["id"], "--lead-question", q, "--reviewer", "agent:rev",
                      "--reviewer-cmd", f"{sys.executable} {oracle}")
            self.assertEqual(rnd["calibration"], "passed", rnd)
        self.assertEqual(_kb(kb, "review", "close", rev["id"])["terminated_reason"], "three-consecutive-zero")
        os.environ["CORVIN_KB_REPO"] = str(kb)

    def tearDown(self):
        os.environ.pop("CORVIN_KB_REPO", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_ready_label_reaches_the_real_task_store_row(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            r = client.post(f"{_URL}/kb/sync", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            st = r.json()
            self.assertEqual(st["state"], "ok", st)
            r = client.get(f"{_URL}/items")
            self.assertEqual(r.status_code, 200, r.text)
            items = {i["external_ref"]: i for i in r.json()["items"]}
            epic = items[f"kb:{self.epic['uid']}"]
            self.assertIn("ready", epic["labels"], epic)


_ORACLE = (
    "import difflib, json, os, pathlib, sys\n"
    "inp = json.load(sys.stdin); d = pathlib.Path(os.environ['KB_REVIEW_DIR'])\n"
    "repo = pathlib.Path(sys.argv[1]); out = []\n"
    "for rel in inp['files']:\n"
    "    orig = repo / rel\n"
    "    if not orig.exists():\n"
    "        orig = next(x for x in repo.rglob(pathlib.Path(rel).name) if '.kb-cache' not in x.parts)\n"
    "    a, b = orig.read_text().split(chr(10)), (d / rel).read_text().split(chr(10))\n"
    "    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(None, a, b).get_opcodes():\n"
    "        if tag != 'equal': out.append(dict(file=rel, line=j1 + 1, repro='diff', observed=tag))\n"
    "print(json.dumps(dict(findings=out, claims_checked=['artifact consistent'])))\n")


@unittest.skipUnless((_KB_SRC / "kb.py").is_file(), "needs the Corvin-Knowledge checkout next to CorvinOS")
class KbPeriodicLoopE2E(unittest.TestCase):
    """ADR-2208 T-0044/T-0049 in the host: the projector's periodic loop runs the real
    `kb sweep --create-tasks` (G6 drift -> a regression task that reaches the board) and the
    SkillForge bridge (KB guidance -> a bootstrap-graded learned-experience skill in the
    tenant's real registry, acknowledged back through `kb guidance ack`)."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        kb = self.kb = self.tmp / "Corvin-Knowledge"
        (kb / "scripts").mkdir(parents=True)
        for f in ("kb.py", "kb_model.py", "kb_lib.py"):
            shutil.copy(_KB_SRC / f, kb / "scripts" / f)
        (kb / "kb" / "_meta").mkdir(parents=True)
        shutil.copy(_KB_SRC.parent / "kb" / "_meta" / "autonomy.yaml", kb / "kb" / "_meta" / "autonomy.yaml")
        (kb / "kb" / "_meta" / "sources.yaml").write_text(
            "sources:\n  - name: kb\n    root: .\n    writable: true\n    dirs:\n"
            "      decisions: decision\n      concepts: concept\n      ideas: idea\n      reviews: review\n"
            "      kb/initiatives: initiative\n      kb/epics: epic\n      kb/tasks: task\n")
        (kb / ".gitignore").write_text("kb/.lock\nkb/graph/\n.kb-cache/\n")
        (kb / "src").mkdir()
        (kb / "src" / "feature.py").write_text("def feature(x):\n    if x > 1:\n        return x\n    return 0\n")
        (kb / "tests").mkdir()
        (kb / "tests" / "test_feature.py").write_text("def test_feature():\n    assert True\n")
        origin = self.tmp / "origin.git"
        _sh("git", "init", "-q", "--bare", "-b", "main", origin)
        _sh("git", "init", "-q", "-b", "main", cwd=kb)
        _sh("git", "remote", "add", "origin", str(origin), cwd=kb)
        self._commit("init")
        os.environ["CORVIN_KB_REPO"] = str(kb)

    def tearDown(self):
        os.environ.pop("CORVIN_KB_REPO", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _commit(self, msg):
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "add", "-A", cwd=self.kb)
        _sh("git", "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "-m", msg, cwd=self.kb)

    def _push(self):
        _sh("git", "push", "-q", "origin", "HEAD:main", cwd=self.kb)
        _sh("git", "fetch", "-q", "origin", cwd=self.kb)

    def _built_decision(self):
        kb = self.kb
        c = _kb(kb, "new", "concept", "--title", "Feature concept")
        d = _kb(kb, "new", "decision", "--title", "Build the feature", "--link", f"inspired_by={c['id']}")
        _kb_append_link(kb, kb / "concepts", c["id"], "formalized_as", d["id"])
        _kb_append_paths(kb, kb / "decisions", d["id"], ["src/feature.py"])
        self._commit("G1/G2 + paths")
        i = _kb(kb, "new", "initiative", "--title", "Ship")
        e = _kb(kb, "new", "epic", "--title", "Feature epic", "--initiative", i["id"], "--link", f"implements={d['id']}")
        _kb_append_paths(kb, kb / "kb" / "epics", e["id"], ["src/feature.py"])
        self._commit("G3 paths")
        t = _kb(kb, "new", "task", "--title", "Implement it", "--epic", e["id"], "--dod", "feature() works")
        oracle = self.tmp / "oracle.py"
        oracle.write_text(_ORACLE)
        rv = _kb(kb, "review", "open", "--title", "Review", "--reviews", d["id"], "--reviews", e["id"])
        for q in ("correctness", "failure paths", "docs versus code"):
            _kb(kb, "review", "calibrate", rv["id"], "--lead-question", q, "--reviewer", "agent:rev",
                "--reviewer-cmd", f"{sys.executable} {oracle} {kb}")
        _kb(kb, "review", "close", rv["id"])
        self._push()
        sha = _sh("git", "rev-parse", "HEAD", cwd=kb).strip()
        _kb(kb, "task", t["id"], "in_progress")
        _kb(kb, "task", t["id"], "done", "--evidence", "call_site=src/feature.py", "--evidence",
            "e2e_test=tests/test_feature.py", "--evidence", "exit_code=0", "--evidence", f"commit={sha}")
        return d, e, t

    def test_sweep_turns_vanished_work_into_a_regression_task_on_the_board(self):
        d, e, t = self._built_decision()
        with _sandbox(self.tmp) as (client, csrf, home, _):
            from corvin_console import kb_projection as kp
            r = client.post(f"{_URL}/kb/sync", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.json()["state"], "ok", r.text)
            items = {i["external_ref"]: i for i in client.get(f"{_URL}/items").json()["items"]}
            self.assertEqual(items[f"kb:{t['uid']}"]["status"], "complete")
            self.assertEqual(kp.periodic("_default", force=True)["sweep"]["drift"], [])
            _sh("git", "rm", "-q", "src/feature.py", cwd=self.kb)
            self._commit("the feature vanished")
            out = kp.periodic("_default", force=True)
            self.assertEqual(len(out["sweep"]["created"]), 1, out)
            self.assertEqual(kp.periodic("_default", force=True)["sweep"]["created"], [])   # never duplicated
            r = client.post(f"{_URL}/kb/sync", headers={"X-CSRF-Token": csrf})
            items = {i["external_ref"]: i for i in client.get(f"{_URL}/items").json()["items"]}
            self.assertNotEqual(items[f"kb:{t['uid']}"]["status"], "complete")   # regressed on the board
            self.assertTrue(any(i["title"].startswith(out["sweep"]["created"][0]) for i in items.values()))
            self.assertIn("periodic", client.get(f"{_URL}/kb/status").json())

    def test_periodic_skips_a_kb_checkout_that_lags_behind_origin(self):
        kb = self.kb
        self._push()
        (kb / "src" / "late.py").write_text("x = 1\n")
        self._commit("a commit the checkout will not have")
        self._push()
        _sh("git", "reset", "-q", "--hard", "HEAD~1", cwd=kb)          # the checkout lags behind origin/main
        with _sandbox(self.tmp) as (client, csrf, home, _):
            from corvin_console import kb_projection as kp
            out = kp.periodic("_default", force=True)
            self.assertIn("skipped", out, out)
            self.assertIn("pull", out["skipped"])
            self.assertEqual(client.get(f"{_URL}/kb/status").json()["periodic"]["skipped"], out["skipped"])

    def test_guidance_is_minted_as_a_bootstrap_graded_skill_and_acknowledged(self):
        kb = self.kb
        _kb(kb, "new", "idea", "--title", "Alpha beta gamma delta")
        for title in ("Alpha beta gamma delta epsilon", "Alpha beta gamma delta zeta"):
            r = subprocess.run([sys.executable, str(kb / "scripts" / "kb.py"), "--repo", str(kb), "new", "idea",
                                "--title", title], capture_output=True, text=True)
            self.assertEqual(r.returncode, 2, r.stdout)
        with _sandbox(self.tmp) as (client, csrf, home, _):
            from corvin_console import kb_projection as kp
            out = kp.periodic("_default", force=True)
            name = kp.guidance_skill_name("G0_no_search_evidence")
            self.assertEqual(out["guidance"]["created"], ["G0_no_search_evidence"], out)
            self.assertEqual(out["skills"]["minted"], [name], out)
            spec = kp._skill_registry("_default").get(name)
            self.assertEqual(spec.type, "learned-experience")
            self.assertEqual([g["score"] if isinstance(g, dict) else g.score for g in spec.grades], [0.3])
            gfile = (kb / "kb" / "_meta" / "guidance" / "G0_no_search_evidence.md").read_text()
            self.assertIn("skillforge_injection: minted", gfile)
            self.assertIn(f"skill: {name}", gfile)
            self.assertEqual(kp.periodic("_default", force=True)["skills"]["minted"], [])   # once


if __name__ == "__main__":
    unittest.main()

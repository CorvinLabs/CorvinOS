"""Host activity → Task-Tracking SSOT (ADR-2060): agent sessions, commits, ADR-derived items.

Driven through the real console router with a real session (``_sandbox`` from
test_admin_route.py), a real temporary git repository, a temporary Corvin-Knowledge
checkout and a temporary Claude Code home (``CLAUDE_CONFIG_DIR``). What must hold:

* only interactive ``cli`` sessions appear; ``sdk-cli`` workers and sessions
  under CORVIN_HOME never do; a session is titled by its ai-title, never by
  prompt text; a live session whose pid is alive is running / paused, a
  registry entry with a dead pid is not live;
* commits appear as finished runs; host data is the default tenant's only;
* the sync creates one item per referenced ADR with the ADR's status, links the
  commits and the session that made them, and a second run writes nothing;
* an operator edit is never overwritten; a deleted item is never re-created;
* every sync write is an audit-first ``task_item.*`` record with actor_kind sync
  and no title in the chain.
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import time
import unittest
from contextlib import contextmanager
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

PROMPT_SECRET = "private prompt text that must never be shown"
TASKS = "/v1/console/initiatives/tasks"
ITEMS = "/v1/console/task-tracking"


def _git(repo: Path, *args: str, env: dict | None = None) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
                          env={**os.environ, **(env or {})}).stdout


def _commit(repo: Path, subject: str, when: float) -> str:
    (repo / "f.txt").write_text(subject)
    _git(repo, "add", "f.txt")
    stamp = f"@{int(when)} +0000"
    _git(repo, "commit", "-q", "-m", subject,
         env={"GIT_AUTHOR_DATE": stamp, "GIT_COMMITTER_DATE": stamp})
    return _git(repo, "rev-parse", "HEAD").strip()


def _proc_start(pid: int) -> str:
    stat = Path(f"/proc/{pid}/stat").read_text()
    return stat[stat.rfind(")") + 2:].split()[19]


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def _cc(cwd: str) -> str:
    import re
    return re.sub(r"[^A-Za-z0-9]", "-", cwd)


class HostSyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.now = time.time()
        self.repo = self.tmp / "work" / "Proj"
        self.repo.mkdir(parents=True)
        _git(self.repo, "init", "-q")
        _git(self.repo, "config", "user.email", "t@example.invalid")
        _git(self.repo, "config", "user.name", "t")
        self.sha_a = _commit(self.repo, "feat(x): first part of the thing [ADR-9001]", self.now - 3600)
        self.sha_b = _commit(self.repo, "feat(y): the other thing [ADR-9002]", self.now - 1800)
        _commit(self.repo, "chore: no record referenced here", self.now - 900)
        self.adr = self.tmp / "Corvin-Knowledge"
        (self.adr / "decisions").mkdir(parents=True)
        self._adr(9001, "ACCEPTED", "The first thing")
        self._adr(9002, "PROPOSED", "The other thing")
        self.claude = self.tmp / "claude"

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _adr(self, n: int, status: str, title: str) -> None:
        (self.adr / "decisions" / f"ADR-{n}-x.md").write_text(
            f"---\nid: ADR-{n}\nstatus: {status}\n---\n\n# ADR-{n}: {title}\n\nbody\n")

    def _seed_sessions(self, corvin_home: Path) -> None:
        proj = self.claude / "projects" / _cc(str(self.repo))
        started = self.now - 4000
        # Operator session that made commit A; titled by ai-title only.
        _jsonl(proj / "s-op.jsonl", [
            {"type": "user", "entrypoint": "cli", "cwd": str(self.repo), "timestamp": started,
             "message": {"role": "user", "content": PROMPT_SECRET}},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {
                "command": "git commit -m \"$(cat <<'EOF'\nfeat(x): first part of the thing [ADR-9001]\nEOF\n)\""}}]}},
            {"type": "ai-title", "aiTitle": "Build the first thing", "sessionId": "s-op"},
        ])
        # Worker session (sdk-cli) in the same project: never shown.
        _jsonl(proj / "s-worker.jsonl", [
            {"type": "user", "entrypoint": "sdk-cli", "cwd": str(self.repo), "timestamp": started},
            {"type": "ai-title", "aiTitle": "worker", "sessionId": "s-worker"},
        ])
        # A cli session whose cwd is under CORVIN_HOME: never shown.
        inner = corvin_home / "tenants" / "_default" / "sessions" / "web:x"
        _jsonl(self.claude / "projects" / _cc(str(inner)) / "s-inner.jsonl", [
            {"type": "user", "entrypoint": "cli", "cwd": str(inner), "timestamp": started},
        ])
        # Live registry: this test process stands in for a live busy session;
        # pid 999999 with a bogus start time is a dead entry.
        me = os.getpid()
        reg = self.claude / "sessions"
        reg.mkdir(parents=True, exist_ok=True)
        (reg / f"{me}.json").write_text(json.dumps({
            "pid": me, "procStart": _proc_start(me), "sessionId": "s-live", "cwd": str(self.repo),
            "kind": "interactive", "entrypoint": "cli", "status": "busy", "name": "proj-01",
            "startedAt": int((self.now - 600) * 1000), "updatedAt": int(self.now * 1000)}))
        (reg / "999999.json").write_text(json.dumps({
            "pid": 999999, "procStart": "1", "sessionId": "s-dead", "cwd": str(self.repo),
            "kind": "interactive", "entrypoint": "cli", "status": "busy"}))
        _jsonl(proj / "s-live.jsonl", [
            {"type": "user", "entrypoint": "cli", "cwd": str(self.repo), "timestamp": self.now - 2000},
            {"type": "assistant", "message": {"content": [{"type": "tool_use", "name": "Bash", "input": {
                "command": "git commit -m 'feat(y): the other thing [ADR-9002]'"}}]}},
            {"type": "ai-title", "aiTitle": "Live work", "sessionId": "s-live"}])

    @contextmanager
    def _env(self):
        prev = os.environ.get("CLAUDE_CONFIG_DIR")
        os.environ["CLAUDE_CONFIG_DIR"] = str(self.claude)
        try:
            with _sandbox(self.tmp, tenants=("_default", "acme")) as (client, csrf, home, clients):
                self._seed_sessions(home)
                from corvin_console import host_activity
                host_activity.repo_root = lambda: self.repo
                yield client, csrf, home, clients
        finally:
            if prev is None:
                os.environ.pop("CLAUDE_CONFIG_DIR", None)
            else:
                os.environ["CLAUDE_CONFIG_DIR"] = prev

    def _sync(self):
        from corvin_console import task_tracking_git_sync as g
        return g.run("_default", adr_root=self.adr)

    def test_sources_over_http(self):
        with self._env() as (client, _csrf, _home, clients):
            body = client.get(TASKS, params={"types": "agent,commit"}).json()
            active = {r["id"]: r for r in body["active"]}
            finished = {r["id"]: r for r in body["finished"]}
            self.assertEqual(active["agent:s-live"]["status"], "running")
            self.assertEqual(active["agent:s-live"]["title"], "Live work")
            self.assertEqual(finished["agent:s-op"]["title"], "Build the first thing")
            ids = set(active) | set(finished)
            self.assertNotIn("agent:s-worker", ids)
            self.assertNotIn("agent:s-inner", ids)
            self.assertNotIn("agent:s-dead", ids)
            self.assertNotIn(PROMPT_SECRET, json.dumps(body))
            self.assertIn(f"commit:Proj:{self.sha_a[:12]}", finished)
            self.assertEqual(sum(1 for i in finished if i.startswith("commit:")), 3)
            # Host data is the default tenant's only.
            other = clients["acme"][0].get(TASKS, params={"types": "agent,commit"}).json()
            self.assertEqual(other["active"] + other["finished"], [])
            self.assertTrue(next(t for t in other["types"] if t["type"] == "agent")["note"])

    def test_sync_creates_links_and_is_idempotent(self):
        with self._env() as (client, _csrf, home, _):
            res = self._sync()
            self.assertEqual(res["inserted"], 3, res)  # container + 2 ADR items
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            done, prop = items["git:Proj#ADR-9001"], items["git:Proj#ADR-9002"]
            self.assertEqual(done["status"], "complete")
            self.assertEqual(done["title"], "ADR-9001 · The first thing")
            self.assertEqual(prop["status"], "in_progress")
            self.assertEqual(done["parent_id"], items["git:Proj"]["id"])
            detail = client.get(f"{ITEMS}/items/{done['id']}").json()
            runs = {(r["run_type"], r["run_ref"]): r for r in detail["runs"]}
            self.assertIn(("commit", f"commit:Proj:{self.sha_a[:12]}"), runs)
            self.assertTrue(runs[("commit", f"commit:Proj:{self.sha_a[:12]}")]["found"])
            self.assertIn(("agent", "agent:s-op"), runs)  # the session that made the commit
            self.assertEqual(len(runs), 2)
            # Live overlay on the list (graph view): the running session linked to ADR-9002.
            self.assertEqual((prop["live_runs"], prop["running_runs"]), (1, 1), prop)
            self.assertEqual(prop["live_run_titles"], ["Agent session: Live work"])
            self.assertEqual((done["live_runs"], done["running_runs"]), (0, 0))  # s-op has ended
            n_events = len(_audit_events(home))
            again = self._sync()
            self.assertEqual((again["inserted"], again["updated"], again["linked"]), (0, 0, 0), again)
            self.assertEqual(len(_audit_events(home)), n_events, "a no-op sync must write nothing")
            evs = [e for e in _audit_events(home) if str(e.get("event_type", "")).startswith("task_item.")]
            self.assertTrue(evs)
            for e in evs:
                blob = json.dumps(e)
                self.assertNotIn("The first thing", blob)
                self.assertNotIn("s-op", blob)  # run_ref stays local
            self.assertTrue(any((e.get("details") or e).get("actor_kind") == "sync" for e in evs))

    def test_adr_status_follows_until_operator_edits(self):
        with self._env() as (client, csrf, _home, _):
            self._sync()
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            prop = items["git:Proj#ADR-9002"]
            self._adr(9002, "ACCEPTED", "The other thing")
            self.assertEqual(self._sync()["updated"], 1)
            now_item = client.get(f"{ITEMS}/items/{prop['id']}").json()["item"]
            self.assertEqual(now_item["status"], "complete")
            # Operator takes it over; the record changes again; the operator's value stays.
            r = client.patch(f"{ITEMS}/items/{now_item['id']}", headers={"X-CSRF-Token": csrf},
                             json={"version": now_item["version"], "status": "blocked"})
            self.assertEqual(r.status_code, 200, r.text)
            self._adr(9002, "SUPERSEDED", "The other thing")
            res = self._sync()
            self.assertEqual((res["updated"], res["kept_operator_edits"]), (0, 1), res)
            self.assertEqual(client.get(f"{ITEMS}/items/{prop['id']}").json()["item"]["status"], "blocked")
            # A new commit is still linked to the operator's item.
            sha_c = _commit(self.repo, "fix(y): follow-up [ADR-9002]", time.time())
            self.assertEqual(self._sync()["linked"], 1)
            refs = {r["run_ref"] for r in client.get(f"{ITEMS}/items/{prop['id']}").json()["runs"]}
            self.assertIn(f"commit:Proj:{sha_c[:12]}", refs)

    def test_duplicate_numbered_records_resolve_deterministically(self):
        """Two files carrying one ADR number: a superseded sibling never decides the
        status, and two live siblings that disagree read as open (never done)."""
        with self._env() as (client, _csrf, _home, _):
            # ADR-9001: the ADR- prefixed file is a superseded stub, the bare file the real record.
            (self.adr / "decisions" / "ADR-9001-x.md").write_text(
                "---\nid: ADR-9001\nstatus: SUPERSEDED\n---\n\n# ADR-9001: Stub\n")
            (self.adr / "decisions" / "9001-real.md").write_text(
                "---\nid: ADR-9001\nstatus: ACCEPTED\n---\n\n# ADR-9001: The first thing\n")
            # ADR-9002: two live records disagree.
            (self.adr / "decisions" / "9002-other.md").write_text(
                "---\nid: ADR-9002\nstatus: ACCEPTED\n---\n\n# ADR-9002: Another take\n")
            self._sync()
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            first, second = items["git:Proj#ADR-9001"], items["git:Proj#ADR-9002"]
            self.assertEqual((first["status"], first["title"]), ("complete", "ADR-9001 · The first thing"))
            self.assertEqual(second["status"], "in_progress")
            detail = client.get(f"{ITEMS}/items/{second['id']}").json()["item"]["description"]
            self.assertIn("9002-other.md", detail)

    def test_items_outside_the_commit_window_still_follow_their_record(self):
        """An item whose commits aged out of the window keeps following the record's
        status and title instead of freezing at its last in-window value."""
        with self._env() as (client, _csrf, _home, _):
            from corvin_console import task_tracking_git_sync as g
            self._sync()
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            prop = items["git:Proj#ADR-9002"]
            self.assertEqual(prop["status"], "in_progress")
            self._adr(9002, "ACCEPTED", "The other thing, renamed")
            later = time.time() + 30 * 86400  # every commit is now outside the window
            res = g.run("_default", adr_root=self.adr, now=later)
            self.assertEqual(res["refreshed"], 1, res)
            item = client.get(f"{ITEMS}/items/{prop['id']}").json()["item"]
            self.assertEqual((item["status"], item["title"]), ("complete", "ADR-9002 · The other thing, renamed"))
            # Idempotent: a second run writes nothing.
            self.assertEqual(g.run("_default", adr_root=self.adr, now=later)["refreshed"], 0)

    def test_deleted_item_is_not_recreated(self):
        with self._env() as (client, csrf, _home, _):
            self._sync()
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            gone = items["git:Proj#ADR-9001"]
            r = client.post(f"{ITEMS}/items/{gone['id']}/delete", headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            _commit(self.repo, "fix(x): more [ADR-9001]", time.time())
            res = self._sync()
            self.assertEqual((res["inserted"], res["linked"]), (0, 0), res)
            refs = [i["external_ref"] for i in client.get(f"{ITEMS}/items").json()["items"]]
            self.assertNotIn("git:Proj#ADR-9001", refs)

    def test_kb_mode_retires_adr_cards_and_links_task_commits(self):
        """ADR-2205: once the KB projects into the tenant, ADR numbers in commits stop
        creating cards, existing ADR cards are archived (never deleted) and a commit naming
        a KB task id is linked to it (test-review finding 3: `_kb_mode` had no test)."""
        from core.task_tracking import projection
        from corvin_console import task_tracking_git_sync as g

        with self._env() as (client, _csrf, home, _):
            self.assertEqual(self._sync()["inserted"], 3)
            payload = {"namespace": "kb", "ok": True, "sha": "abc", "items": [
                {"ref": "kb:U1", "id": "I-01", "kind": "initiative", "title": "I-01 · KB", "status": "in_progress"},
                {"ref": "kb:U2", "id": "E-001", "kind": "epic", "title": "E-001 · E", "status": "in_progress",
                 "parent_ref": "kb:U1"},
                {"ref": "kb:U3", "id": "T-0001", "kind": "task", "title": "T-0001 · Do it", "status": "in_progress",
                 "parent_ref": "kb:U2"}]}
            self.assertEqual(projection.apply("_default", payload)["state"], "ok")
            sha = _commit(self.repo, "feat(z): the KB task [T-0001] [ADR-9003]", self.now - 60)
            res = self._sync()
            self.assertEqual((res["mode"], res["retired"], res["linked"]), ("kb", 3, 1), res)
            items = {i["external_ref"]: i for i in client.get(f"{ITEMS}/items").json()["items"]}
            self.assertNotIn("git:Proj#ADR-9003", items)                      # no new ADR card
            for ref in ("git:Proj#ADR-9001", "git:Proj#ADR-9002"):
                self.assertEqual(items[ref]["status"], "archived")
                self.assertEqual(items[ref]["status_reason"], g.RETIRED_REASON)
            runs = client.get(f"{ITEMS}/items/{items['kb:U3']['id']}").json()["runs"]
            self.assertIn(("commit", f"commit:Proj:{sha[:12]}"), {(r["run_type"], r["run_ref"]) for r in runs})
            again = self._sync()
            self.assertEqual((again["retired"], again["linked"]), (0, 0), again)   # idempotent

    def test_cli_entry_point(self):
        """The timer's command line: a real subprocess against the sandbox store."""
        with self._env() as (_client, _csrf, home, _):
            env = {**os.environ, "CORVIN_HOME": str(home), "CLAUDE_CONFIG_DIR": str(self.claude),
                   "CORVIN_ADR_ROOT": str(self.adr)}
            cp = subprocess.run([sys.executable, "-m", "corvin_console.task_tracking_git_sync", "--dry-run"],
                                capture_output=True, text=True, env=env, timeout=120,
                                cwd=str(Path(__file__).resolve().parents[3]))
            self.assertEqual(cp.returncode, 0, cp.stderr[-2000:])
            out = json.loads(cp.stdout.strip().splitlines()[-1])
            self.assertTrue(out.get("dry_run"))
            other = subprocess.run([sys.executable, "-m", "corvin_console.task_tracking_git_sync",
                                    "--tenant", "acme"], capture_output=True, text=True, env=env, timeout=120,
                                   cwd=str(Path(__file__).resolve().parents[3]))
            self.assertIn("skipped", json.loads(other.stdout.strip().splitlines()[-1]))


if __name__ == "__main__":
    unittest.main()

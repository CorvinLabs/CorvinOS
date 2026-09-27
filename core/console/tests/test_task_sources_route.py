"""GET /v1/console/initiatives/tasks — every task type, one list (task_sources.py).

Through the real console router with a real session (``_sandbox`` from
test_admin_route.py). Each source gets one fixture in the store where that
subsystem really writes it. What must hold:

* every source is read and typed; active / finished / stale are separated;
* a record claiming to run with no sign of life is ``stale``, not ``running``;
* a bridge chat task never exposes the message text (another person's words);
  a web-chat task shows a preview (the operator's own turn);
* the tenant comes from the session;
* one broken source does not blank the list.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import tempfile
import time
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

URL = "/v1/console/initiatives/tasks"
SECRET = "please transfer my salary to IBAN DE00"


def _w(path: Path, data) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data) if not isinstance(data, str) else data)
    return path


def _seed(home: Path, now: float) -> None:
    t = home / "tenants" / "_default"
    old = now - 10 * 3600
    _w(t / "sessions/web:abc/tasks/w1.json", {"task_id": "w1", "chat_key": "web:abc", "status": "running",
       "created_at": now - 5, "started_at": now - 4, "input": {"instruction": "summarise the report", "persona": "assistant"}})
    _w(t / "sessions/voice/discord/123/tasks/d1.json", {"task_id": "d1", "chat_key": "123", "status": "completed",
       "created_at": now - 60, "started_at": now - 59, "ended_at": now - 30, "result_summary": SECRET,
       "input": {"instruction": SECRET, "persona": "assistant"}})
    _w(t / "sessions/voice/discord/bgtask__x/tasks/b1.json", {"task_id": "b1", "chat_key": "bgtask", "status": "failed",
       "created_at": now - 100, "ended_at": now - 90, "input": {"instruction": SECRET}})
    _w(t / "global/acs/runs/acs-1/manifest.json", {"run_id": "acs-1", "workflow_id": "insights", "status": "success",
       "started_at": now - 300, "completed_at": now - 200})
    _w(t / "global/gateway/runs/run_g1.json", {"run_id": "g1", "status": "running", "created_at": now - 20,
       "updated_at": now - 2, "request": {"spec": {"persona": "coder", "input": SECRET}}})
    fr = home / "global/forge/runs/2026_old"
    _w(fr / "run_manifest.json", {"run_id": "f-old", "tool": "svg_to_png", "started_at": old})
    fr2 = home / "global/forge/runs/2026_ok"
    _w(fr2 / "run_manifest.json", {"run_id": "f-ok", "tool": "calc", "started_at": now - 50})
    _w(fr2 / "run_completion.json", {"status": "ok", "completed_at": now - 49})
    _w(t / "global/compute/jobs/j1.json", {"job_id": "j1", "name": "grid", "status": "queued",
       "created_at": old, "updated_at": old})
    _w(t / "workflows/wf1/runs/r1.meta.json", {"id": "r1", "workflow_id": "nightly-report", "status": "running",
       "started_at": now - 10})
    _w(t / "global/flows/runs/fl1.manifest.jsonl",
       json.dumps({"type": "mesh_flow.run_started", "flow_id": "ingest", "ts": now - 40}) + "\n"
       + json.dumps({"type": "mesh_flow.run_completed", "ts": now - 35}) + "\n")
    _w(t / "voice/schedule.json", [{"id": "abc123", "text": SECRET, "cron": "0 9 * * 1", "next_run": now + 3600}])
    _w(t / "global/gateway/runs/run_broken.json", "{not json")   # unreadable record: skipped, not fatal


# Types this route test deliberately does not seed, each with the reason.
COVERAGE_EXEMPT = {
    "initiative": "work items — covered by the Task-Tracking store tests",
    "skill_creator": "held in the console process's memory, no store to seed",
    "agent": "host-level Claude Code sessions — test_host_activity_sources",
    "commit": "host-level git history — test_host_activity_sources",
}

OWNER = "owner-uid-1"


def _iso(ts: float) -> str:
    from datetime import datetime, timezone
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


@contextlib.contextmanager
def _claude_home(tmp: Path):
    prev = os.environ.get("CLAUDE_CONFIG_DIR")
    os.environ["CLAUDE_CONFIG_DIR"] = str(tmp / "claude")
    try:
        yield tmp / "claude"
    finally:
        if prev is None:
            os.environ.pop("CLAUDE_CONFIG_DIR", None)
        else:
            os.environ["CLAUDE_CONFIG_DIR"] = prev


def _subagent(tmp: Path, workdir: Path, sid: str, agent: str, *, start: float, end: float, desc: str) -> None:
    proj = tmp / "claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", str(workdir))
    f = _w(proj / sid / "subagents" / f"agent-{agent}.jsonl",
           json.dumps({"type": "user", "timestamp": _iso(start), "message": {"content": SECRET}}) + "\n"
           + json.dumps({"type": "assistant", "timestamp": _iso(end), "message": {"content": SECRET}}) + "\n")
    _w(proj / sid / "subagents" / f"agent-{agent}.meta.json", {"agentType": "Explore", "description": desc})
    os.utime(f, (end, end))


def _seed_extended(home: Path, now: float, tmp: Path) -> None:
    """The sources added for complete coverage: A2A, the background registry,
    subagent steps inside a turn, and the operator-owned bridge turn."""
    t = home / "tenants" / "_default"
    _w(home / "bridges/discord/settings.json", {"whitelist": [OWNER]})
    # an operator turn on a bridge (the adapter stamps from_operator at create time)
    d2 = t / "sessions/voice/discord/123"
    _w(d2 / "tasks/d2.json", {"task_id": "d2", "chat_key": "123", "status": "running",
       "created_at": now - 20, "started_at": now - 19,
       "input": {"instruction": "deploy the new console", "persona": "assistant", "from_operator": True}})
    _subagent(tmp, d2, "s1", "a1", start=now - 55, end=now - 35, desc=SECRET)          # inside d1 (third party)
    _subagent(tmp, d2, "s1", "a2", start=now - 15, end=now - 1, desc="Review the deploy plan")  # inside d2
    # A2A: inbound done, inbound in flight, outbound failed, inbound refused
    feed = t / "global/a2a_feed/messages.jsonl"
    rows = [
        {"direction": "in", "kind": "task", "task_id": "a1", "peer_id": "origin-tw", "peer_label": "Twin",
         "status": "received", "text": SECRET, "ts": now - 70},
        {"direction": "out", "kind": "response", "task_id": "a1", "peer_id": "origin-tw", "status": "ok",
         "text": SECRET, "ts": now - 65, "duration_ms": 5000},
        {"direction": "in", "kind": "task", "task_id": "a2", "peer_id": "origin-tw", "status": "received",
         "text": SECRET, "ts": now - 8},
        {"direction": "out", "kind": "task", "task_id": "a3", "peer_id": "ep-lab", "peer_label": "Lab",
         "status": "sent", "text": SECRET, "ts": now - 90},
        {"direction": "in", "kind": "response", "task_id": "a3", "peer_id": "ep-lab", "status": "error",
         "error": "timeout", "ts": now - 80},
        {"direction": "in", "kind": "task", "task_id": "a4", "peer_id": "origin-x", "status": "received",
         "text": SECRET, "ts": now - 40},
        {"direction": "out", "kind": "response", "task_id": "a4", "peer_id": "origin-x", "status": "rejected",
         "ts": now - 39},
    ]
    _w(feed, "".join(json.dumps(r) + "\n" for r in rows) + "{torn line\n")
    # background registry (completion_notify, <corvin_home>/pending_notifications)
    q = home / "pending_notifications"
    _w(q / "bgt_aaa.json", {"id": "bgt_aaa", "channel": "discord", "sender": OWNER, "tenant_id": "_default",
       "label": "rebuild the ADR index", "state": "pending", "ok": None, "created_at": now - 30})
    _w(q / "bgt_bbb.json", {"id": "bgt_bbb", "channel": "discord", "sender": "stranger", "tenant_id": "_default",
       "label": SECRET, "state": "delivered", "ok": False, "created_at": now - 500,
       "ready_at": now - 400, "delivered_at": now - 399})
    _w(q / "bgt_ccc.json", {"id": "bgt_ccc", "channel": "discord", "sender": OWNER, "tenant_id": "acme",
       "label": "other tenant", "state": "pending", "created_at": now - 30})
    # the detached worker's own engine turn for bgt_aaa — folded into the registry record
    _w(t / "sessions/voice/discord/bgtask__123__bgt_aaa/tasks/wk.json", {"task_id": "wk", "chat_key": "x",
       "status": "running", "created_at": now - 25, "started_at": now - 24, "input": {"instruction": SECRET}})


class TaskSourcesRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())
        from corvin_console import task_sources
        task_sources._AGG_CACHE.clear()

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_every_source_typed_and_split(self):
        now = time.time()
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            _seed(home, now)
            r = client.get(URL)
            self.assertEqual(r.status_code, 200, r.text)
            b = r.json()
            by_id = {x["id"]: x for x in b["active"] + b["finished"]}
            expect = {
                "chat:w1": ("chat", "running"), "chat:d1": ("chat", "done"),
                "chat:b1": ("background", "failed"), "acs:acs-1": ("acs", "done"),
                "gateway:g1": ("gateway", "running"), "forge:f-old": ("forge", "stale"),
                "forge:f-ok": ("forge", "done"), "compute:job:j1": ("compute", "stale"),
                "workflow:r1": ("workflow", "running"), "flow:fl1": ("flow", "done"),
                "scheduled:abc123": ("scheduled", "scheduled"),
            }
            for rid, (typ, status) in expect.items():
                self.assertIn(rid, by_id, rid)
                self.assertEqual((by_id[rid]["type"], by_id[rid]["status"]), (typ, status), rid)
            self.assertIn("no worker", by_id["compute:job:j1"]["stale_reason"])
            self.assertIn("no sign of life", by_id["forge:f-old"]["stale_reason"])
            # finished never contains an active/stale record and vice versa
            self.assertTrue(all(x["status"] in ("done", "failed", "cancelled") for x in b["finished"]))
            # newest end first — among the seeded sources; host-level `commit` and
            # `agent` read this checkout's real git log and Claude Code sessions,
            # so a commit made seconds ago legitimately sorts above the fixtures
            seeded = [x for x in b["finished"] if x["type"] not in ("commit", "agent")]
            self.assertEqual(seeded[0]["id"], "chat:d1")
            types = {t["type"]: t for t in b["types"]}
            self.assertEqual(types["forge"]["stale"], 1)
            self.assertEqual(types["chat"]["active"], 1)

    def test_bridge_message_text_never_exposed(self):
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            _seed(home, time.time())
            body = client.get(URL).text
            self.assertNotIn(SECRET, body)
            self.assertIn("summarise the report", body)  # operator's own web turn

    def test_type_filter_and_paging(self):
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            _seed(home, time.time())
            b = client.get(URL + "?types=forge,acs&finished_limit=1").json()
            self.assertTrue(all(x["type"] in ("forge", "acs") for x in b["active"] + b["finished"]))
            self.assertEqual(len(b["finished"]), 1)
            self.assertEqual(b["finished_total"], 2)
            self.assertEqual(client.get(URL + "?types=bogus").status_code, 400)

    def test_every_type_label_has_a_seeded_record(self):
        """Positive control: a type that exists in TYPE_LABELS but that no fixture
        here produces is a type nobody proved the console can show."""
        from corvin_console import task_sources
        now = time.time()
        with _sandbox(self._tmp) as (client, _csrf, home, _), _claude_home(self._tmp):
            _seed(home, now)
            _seed_extended(home, now, self._tmp)
            b = client.get(URL).json()
            seen = {x["type"] for x in b["active"] + b["finished"]}
            missing = set(task_sources.TYPE_LABELS) - set(COVERAGE_EXEMPT) - seen
            self.assertEqual(missing, set(), f"types with no record in the fixture: {sorted(missing)}")

    def test_extended_sources(self):
        now = time.time()
        with _sandbox(self._tmp) as (client, _csrf, home, _), _claude_home(self._tmp):
            _seed(home, now)
            _seed_extended(home, now, self._tmp)
            r = client.get(URL)
            self.assertEqual(r.status_code, 200, r.text)
            b = r.json()
            by_id = {x["id"]: x for x in b["active"] + b["finished"]}
            expect = {
                "a2a:in:a1": ("a2a", "done"), "a2a:in:a2": ("a2a", "running"),
                "a2a:out:a3": ("a2a", "failed"), "a2a:in:a4": ("a2a", "cancelled"),
                "background:bgt_aaa": ("background", "running"),
                "background:bgt_bbb": ("background", "failed"),
                "chat:d2": ("chat", "running"),
            }
            for rid, (typ, status) in expect.items():
                self.assertIn(rid, by_id, rid)
                self.assertEqual((by_id[rid]["type"], by_id[rid]["status"]), (typ, status), rid)
            self.assertEqual(by_id["a2a:in:a1"]["subtype"], "inbound")
            self.assertIn("Twin", by_id["a2a:in:a1"]["title"])
            self.assertIn("timeout", by_id["a2a:out:a3"]["detail"] or "")
            # the worker's own engine turn is folded into its registry record, not listed twice
            self.assertNotIn("chat:wk", by_id)
            self.assertEqual(by_id["background:bgt_aaa"]["steps"]["total"], 1)
            self.assertNotIn("background:bgt_ccc", by_id)          # another tenant's task
            # operator titles: own bridge turn and own /task show a preview; strangers stay anonymous
            self.assertEqual(by_id["chat:d2"]["title"], "deploy the new console")
            self.assertEqual(by_id["background:bgt_aaa"]["title"], "rebuild the ADR index")
            self.assertNotIn("deploy", by_id["chat:d1"]["title"])
            # subagents inside a turn are steps of that turn, matched by the turn's window
            d1, d2 = by_id["chat:d1"], by_id["chat:d2"]
            self.assertEqual((d1["steps"]["total"], d2["steps"]["total"]), (1, 1))
            self.assertEqual(d2["steps"]["items"][0]["title"], "Review the deploy plan")
            self.assertEqual(d1["steps"]["items"][0]["title"], "Explore subagent")  # third-party turn
            self.assertIn("1 subagent", d2["detail"])
            self.assertNotIn(SECRET, r.text)
            types = {t["type"]: t for t in b["types"]}
            self.assertEqual(types["a2a"]["label"], "A2A")

    def test_review_round2_rules(self):
        """JID device suffix and console-started background tasks count as the
        operator's own; worker turns fold without losing their subagents; the
        A2A feed is read incrementally and re-read whole after compaction."""
        from corvin_console import task_sources as ts
        now = time.time()
        with _sandbox(self._tmp) as (client, _csrf, home, _), _claude_home(self._tmp):
            _w(home / "bridges/whatsapp/settings.json", {"whitelist": ["4917000@s.whatsapp.net"]})
            q = home / "pending_notifications"
            _w(q / "bgt_111.json", {"id": "bgt_111", "channel": "whatsapp", "sender": "4917000:11@s.whatsapp.net",
               "tenant_id": "_default", "label": "compile the report", "state": "pending", "created_at": now - 5})
            _w(q / "bgt_222.json", {"id": "bgt_222", "channel": "web", "sender": "",
               "tenant_id": "_default", "label": "reindex the docs", "state": "pending", "created_at": now - 5})
            by_id = {x["id"]: x for x in (lambda b: b["active"] + b["finished"])(client.get(URL).json())}
            self.assertEqual(by_id["background:bgt_111"]["title"], "compile the report")
            self.assertEqual(by_id["background:bgt_222"]["title"], "reindex the docs")

        def rec(i, **kw):
            base = {"id": i, "type": "background", "status": "done", "started_at": None, "created_at": "2026-09-27T10:00:00Z",
                    "ended_at": None, "duration_s": 1.0, "detail": "waiting for a worker", "stale_reason": None}
            return {**base, **kw}
        parent = rec("background:bgt_x", status="queued")
        turns = [rec("chat:a", _bg_ref="bgt_x", status="done", steps={"total": 2, "running": 0, "items": []}),
                 rec("chat:b", _bg_ref="bgt_x", status="running", steps={"total": 1, "running": 1, "items": []})]
        out = ts._fold_background([parent, *turns])
        self.assertEqual([r["id"] for r in out], ["background:bgt_x"])
        self.assertEqual(parent["status"], "running")
        self.assertEqual(parent["detail"], "worker running · 2 worker turns · 3 subagents")
        self.assertEqual([i["title"] for i in parent["steps"]["items"]],
                         ["Worker turn · 2 subagents", "Worker turn · 1 subagent"])

        feed = self._tmp / "feed.jsonl"
        row = lambda tid, st: json.dumps({"direction": "in", "kind": "task", "task_id": tid, "status": st, "text": SECRET}) + "\n"
        feed.write_text(row("t1", "received"))
        self.assertEqual([r["task_id"] for r in ts._a2a_rows(feed)], ["t1"])
        with feed.open("a") as fh:
            fh.write(row("t2", "received") + '{"direction": "in", "kind": "ta')   # torn tail
        self.assertEqual([r["task_id"] for r in ts._a2a_rows(feed)], ["t1", "t2"])
        with feed.open("a") as fh:
            fh.write('sk", "task_id": "t3", "status": "received"}\n')
        self.assertEqual([r["task_id"] for r in ts._a2a_rows(feed)], ["t1", "t2", "t3"])
        feed.write_text(row("t9", "received"))                                   # compacted: smaller file
        self.assertEqual([r["task_id"] for r in ts._a2a_rows(feed)], ["t9"])
        self.assertTrue(all("text" not in r for r in ts._a2a_rows(feed)))

    def test_tenant_from_session(self):
        with _sandbox(self._tmp, tenants=("_default", "acme")) as (_c, _s, home, clients):
            (home / "tenants/acme/global/console/sessions").mkdir(parents=True, exist_ok=True)
            _seed(home, time.time())
            b = clients["acme"][0].get(URL).json()
            # nothing — including the host-level forge runs, which carry no
            # tenant and are therefore shown to the host tenant (_default) only
            self.assertEqual(b["active"] + b["finished"], [])


if __name__ == "__main__":
    unittest.main()

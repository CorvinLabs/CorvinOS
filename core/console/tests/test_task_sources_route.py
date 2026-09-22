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

import json
import os
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
       "created_at": now - 60, "started_at": now - 59, "ended_at": now - 30, "input": {"instruction": SECRET, "persona": "assistant"}})
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
            self.assertEqual(b["finished"][0]["id"], "chat:d1")  # newest end first
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

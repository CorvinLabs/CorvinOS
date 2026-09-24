"""HTTP tests for the Task-Tracking SSOT console surface (routes/task_tracking.py, ADR-2056).

Driven through the real console router with a real session cookie (the
``_sandbox`` pattern of test_admin_route.py) against a temporary CORVIN_HOME,
so every assertion covers route → service → SQLite → core audit chain.

What must hold:
* an empty store is an empty list, never sample data;
* writes need CSRF; the tenant comes from the session only;
* a stale ``version`` is a 409 carrying the current record;
* hierarchy rules and cycles are refused;
* every mutation lands in the core chain FIRST — a failing chain write is a
  503 and leaves no row; the chain record never carries a title;
* the initiatives.json import is idempotent and leaves the file untouched;
* soft delete cascades and restore brings the whole cascade back.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/task-tracking"
_SECRET_TITLE = "Quarterly plan for ACME-internal-codename"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _task_events(home: Path, tenant: str = "_default") -> list[dict]:
    return [e for e in _audit_events(home, tenant) if str(e.get("event_type", "")).startswith("task_item.")]


class TaskTrackingRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)
        from core.task_tracking import service
        service.chain_writer = service._default_chain_writer

    def _post(self, client, csrf, path, body=None, expect=None):
        r = client.post(f"{_URL}{path}", json=body, headers={"X-CSRF-Token": csrf})
        if expect is not None:
            self.assertEqual(r.status_code, expect, r.text)
        return r

    def _patch(self, client, csrf, iid, body):
        return client.patch(f"{_URL}/items/{iid}", json=body, headers={"X-CSRF-Token": csrf})

    def test_empty_store_is_empty_not_sample_data(self):
        with _sandbox(self._tmp) as (client, _csrf, _home, _):
            r = client.get(f"{_URL}/items")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["items"], [])
            self.assertFalse(body["import_available"])
            self.assertEqual(body["summary"]["total"], 0)

    def test_create_patch_rollup_conflict_and_audit_first(self):
        with _sandbox(self._tmp) as (client, csrf, home, _):
            self.assertIn(client.post(f"{_URL}/items", json={"kind": "initiative", "title": "x"}).status_code,
                          (401, 403))
            ini = self._post(client, csrf, "/items", {"kind": "initiative", "title": _SECRET_TITLE}, 201).json()
            self.assertEqual(ini["version"], 1)
            # hierarchy rules
            self.assertEqual(self._post(client, csrf, "/items", {"kind": "epic", "title": "E"}).status_code, 400)
            self.assertEqual(self._post(client, csrf, "/items", {"kind": "subtask", "title": "S"}).status_code, 400)
            self.assertEqual(self._post(client, csrf, "/items", {
                "kind": "task", "title": "T", "assignee": "someone@example.com"}).status_code, 422)
            epic = self._post(client, csrf, "/items", {"kind": "epic", "title": "E", "parent_id": ini["id"]}, 201).json()
            a = self._post(client, csrf, "/items", {"kind": "task", "title": "A", "parent_id": epic["id"],
                                                    "priority": "high", "assignee": "claude"}, 201).json()
            b = self._post(client, csrf, "/items", {"kind": "task", "title": "B", "parent_id": epic["id"],
                                                    "progress": 50}, 201).json()
            # an initiative under a task is refused; so is a parent cycle
            self.assertEqual(self._patch(client, csrf, ini["id"], {"version": 1, "parent_id": a["id"]}).status_code, 400)
            self.assertEqual(self._patch(client, csrf, epic["id"], {"version": 1, "kind": "task",
                                                                   "parent_id": a["id"]}).status_code, 400)

            r = self._patch(client, csrf, a["id"], {"version": 1, "status": "complete"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["version"], 2)
            self.assertIsNotNone(r.json()["completed_at"])
            stale = self._patch(client, csrf, a["id"], {"version": 1, "priority": "low"})
            self.assertEqual(stale.status_code, 409)
            self.assertEqual(stale.json()["detail"]["current"]["version"], 2)
            # clearing a nullable field works; clearing a required one does not
            self.assertEqual(self._patch(client, csrf, b["id"], {"version": 1, "progress": None}).json()["progress"], None)
            self.assertEqual(self._patch(client, csrf, b["id"], {"version": 2, "title": None}).status_code, 400)
            self._patch(client, csrf, b["id"], {"version": 2, "progress": 50})

            items = {i["id"]: i for i in client.get(f"{_URL}/items").json()["items"]}
            self.assertEqual(items[epic["id"]]["rollup"]["progress"], 75)       # (100 + 50) / 2
            self.assertEqual(items[ini["id"]]["rollup"]["descendants"], 3)
            self.assertEqual(items[ini["id"]]["rollup"]["counts"], {"open": 2, "complete": 1})

            evs = _task_events(home)
            types = [e["event_type"] for e in evs]
            self.assertEqual(types.count("task_item.created"), 4)
            self.assertIn("task_item.updated", types)
            upd = next(e for e in evs if e["event_type"] == "task_item.updated"
                       and e["details"].get("new_status") == "complete")
            self.assertEqual((upd["details"]["old_status"], upd["details"]["fields"]), ("open", "status"))
            self.assertEqual(upd["details"]["tenant_id"], "_default")
            chain_text = (home / "tenants" / "_default" / "global" / "forge" / "audit.jsonl").read_text()
            self.assertNotIn(_SECRET_TITLE, chain_text)
            self.assertNotIn("claude", chain_text.split("task_item.", 1)[1])  # assignee never in the chain

            detail = client.get(f"{_URL}/items/{a['id']}").json()
            self.assertEqual([h["event_type"] for h in detail["history"]], ["task_item.updated", "task_item.created"])
            chain_hashes = {e.get("hash") for e in evs}
            self.assertTrue(all(h["chain_hash"] in chain_hashes for h in detail["history"]))
            self.assertEqual([x["id"] for x in detail["ancestors"]], [ini["id"], epic["id"]])

    def test_failed_chain_write_is_503_and_writes_nothing(self):
        from core.task_tracking import service

        with _sandbox(self._tmp) as (client, csrf, home, _):
            ok = self._post(client, csrf, "/items", {"kind": "task", "title": "kept"}, 201).json()

            def broken(*_a, **_k):
                raise OSError("disk full")

            service.chain_writer = broken
            r = self._post(client, csrf, "/items", {"kind": "task", "title": "lost"})
            self.assertEqual(r.status_code, 503, r.text)
            r = self._patch(client, csrf, ok["id"], {"version": 1, "status": "blocked"})
            self.assertEqual(r.status_code, 503)
            service.chain_writer = service._default_chain_writer
            items = client.get(f"{_URL}/items").json()["items"]
            self.assertEqual([(i["title"], i["status"], i["version"]) for i in items], [("kept", "open", 1)])

    def test_tenant_comes_from_session(self):
        with _sandbox(self._tmp, tenants=("_default", "acme")) as (client, csrf, _home, clients):
            item = self._post(client, csrf, "/items", {"kind": "task", "title": "mine"}, 201).json()
            acme, acme_csrf = clients["acme"]
            self.assertEqual(acme.get(f"{_URL}/items").json()["items"], [])
            self.assertEqual(acme.get(f"{_URL}/items/{item['id']}").status_code, 404)
            r = acme.patch(f"{_URL}/items/{item['id']}", json={"version": 1, "title": "stolen"},
                           headers={"X-CSRF-Token": acme_csrf})
            self.assertEqual(r.status_code, 404)
            # a tenant id in the query string is ignored
            self.assertEqual(acme.get(f"{_URL}/items?tenant_id=_default").json()["items"], [])

    def test_delete_cascades_and_restore_brings_the_cascade_back(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _):
            ini = self._post(client, csrf, "/items", {"kind": "initiative", "title": "I"}, 201).json()
            t = self._post(client, csrf, "/items", {"kind": "task", "title": "T", "parent_id": ini["id"]}, 201).json()
            s = self._post(client, csrf, "/items", {"kind": "subtask", "title": "S", "parent_id": t["id"]}, 201).json()
            self._post(client, csrf, f"/items/{ini['id']}/delete", None, 200)
            self.assertEqual(client.get(f"{_URL}/items").json()["items"], [])
            all_rows = client.get(f"{_URL}/items?include_deleted=true").json()["items"]
            self.assertEqual(len([r for r in all_rows if r["deleted_at"]]), 3)
            # the child cannot come back on its own while its parent is deleted
            self.assertEqual(self._post(client, csrf, f"/items/{s['id']}/restore").status_code, 400)
            self._post(client, csrf, f"/items/{ini['id']}/restore", None, 200)
            self.assertEqual(len(client.get(f"{_URL}/items").json()["items"]), 3)

    def test_dependencies_decisions_and_run_links(self):
        with _sandbox(self._tmp) as (client, csrf, _home, _):
            a = self._post(client, csrf, "/items", {"kind": "task", "title": "A"}, 201).json()
            b = self._post(client, csrf, "/items", {"kind": "task", "title": "B"}, 201).json()
            self._post(client, csrf, f"/items/{a['id']}/dependencies", {"depends_on_id": b["id"]}, 201)
            self.assertEqual(self._post(client, csrf, f"/items/{b['id']}/dependencies",
                                        {"depends_on_id": a["id"]}).status_code, 400)  # cycle
            items = {i["id"]: i for i in client.get(f"{_URL}/items").json()["items"]}
            self.assertEqual(items[a["id"]]["waiting_on"], [b["id"]])

            self.assertEqual(self._post(client, csrf, f"/items/{a['id']}/decision",
                                        {"decision": "approved", "version": 1}).status_code, 400)  # no approval
            gate = self._post(client, csrf, "/items", {"kind": "task", "title": "Gate", "category": "gate",
                                                       "approval_state": "pending"}, 201).json()
            r = self._post(client, csrf, f"/items/{gate['id']}/decision", {"decision": "approved", "version": 1}, 200)
            self.assertEqual(r.json()["approval_state"], "approved")
            self.assertEqual(client.get(f"{_URL}/summary").json()["summary"]["approvals_pending"], 0)

            self.assertEqual(self._post(client, csrf, f"/items/{a['id']}/runs",
                                        {"run_type": "initiative", "run_ref": "x"}).status_code, 400)
            self._post(client, csrf, f"/items/{a['id']}/runs", {"run_type": "forge", "run_ref": "run-123"}, 201)
            runs = client.get(f"{_URL}/items/{a['id']}").json()["runs"]
            self.assertEqual((runs[0]["run_type"], runs[0]["run_ref"], runs[0]["found"]), ("forge", "run-123", False))
            r = client.delete(f"{_URL}/items/{a['id']}/runs?run_type=forge&run_ref=run-123",
                              headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)

    def test_initiatives_import_is_idempotent_and_leaves_the_file_alone(self):
        now = datetime.now(timezone.utc)
        board = {"version": 1, "initiatives": [
            {"id": "loop-b", "label": "Loop B", "title": "Fixes",
             "start": _iso(now - timedelta(days=2)), "deadline": _iso(now + timedelta(days=2)),
             "tasks": [
                 {"id": "p0-a", "title": "Audit wiring", "group": "P0", "status": "running", "progress": 40,
                  "evidence": {"paths": ["README.md"]},
                  "verification": {"at": _iso(now), "passed": 0, "failed": 0, "errors": 0,
                                   "paths_present": 1, "paths_total": 1}},
                 {"id": "p0-b", "title": "Late", "group": "P0", "status": "pending",
                  "due": _iso(now - timedelta(hours=1))},
             ],
             "gates": [{"id": "blocker", "title": "Blocker Gate", "at": _iso(now + timedelta(days=4)),
                        "decision": "pending", "criteria": [{"label": "All green", "state": "pending"}]}]},
            {"id": "loop-c", "label": "Loop C", "title": "Production",
             "blocked_by": {"initiative": "loop-b", "gate": "blocker"},
             "tasks": [{"id": "s1", "title": "Stream 1", "status": "pending"}]},
        ]}
        with _sandbox(self._tmp) as (client, csrf, home, _):
            path = home / "tenants" / "_default" / "global" / "initiatives.json"
            path.write_text(json.dumps(board))
            before = path.read_bytes()
            self.assertTrue(client.get(f"{_URL}/items").json()["import_available"])
            r = self._post(client, csrf, "/import", None, 200).json()
            self.assertEqual((r["inserted"], r["skipped"]), (r["planned"], 0))
            again = self._post(client, csrf, "/import", None, 200).json()
            self.assertEqual((again["inserted"], again["skipped"]), (0, r["planned"]))
            self.assertEqual(path.read_bytes(), before)

            body = client.get(f"{_URL}/items").json()
            by_title = {i["title"]: i for i in body["items"]}
            self.assertEqual(by_title["P0"]["kind"], "epic")
            wiring = by_title["Audit wiring"]
            self.assertEqual((wiring["status"], wiring["progress"]), ("complete", 100))  # evidence-derived
            self.assertEqual(wiring["evidence"]["state"], "ok")
            self.assertTrue(by_title["Late"]["overdue"])
            gate = by_title["Blocker Gate"]
            self.assertEqual((gate["category"], gate["approval_state"]), ("gate", "pending"))
            self.assertEqual(by_title["Loop C — Production"]["waiting_on"], [gate["id"]])
            self.assertEqual(body["summary"]["approvals_pending"], 1)
            self.assertFalse(body["import_available"])
            types = [e["event_type"] for e in _task_events(home)]
            self.assertEqual(types.count("task_item.imported"), 1)
            self.assertEqual(types.count("task_item.created"), r["planned"])


if __name__ == "__main__":
    unittest.main()

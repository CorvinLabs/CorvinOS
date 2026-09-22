"""HTTP tests for the initiatives board (routes/initiatives.py + initiatives.py).

Driven through the real console router with a real session cookie — the same
``_sandbox`` pattern as test_admin_route.py. What must hold:

* no file → an empty board, never sample data;
* time-dependent numbers are derived on read, not stored;
* a gate that is not "go" blocks the initiative that depends on it;
* mutations need CSRF, are written to the tenant file and audited;
* the tenant comes from the session — tenant B never sees tenant A's board.
"""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_URL = "/v1/console/initiatives"


def _iso(dt: datetime) -> str:
    return dt.isoformat().replace("+00:00", "Z")


def _fixture(now: datetime) -> dict:
    return {
        "version": 1,
        "initiatives": [
            {
                "id": "loop-b", "label": "Loop B", "title": "Fixes",
                "start": _iso(now - timedelta(days=2)),
                "deadline": _iso(now + timedelta(days=2)),
                "tasks": [
                    {"id": "p0-a", "title": "Audit wiring", "group": "P0", "status": "running", "progress": 40},
                    {"id": "p0-b", "title": "Late", "group": "P0", "status": "running", "progress": 20,
                     "due": _iso(now - timedelta(hours=1))},
                ],
                "gates": [{"id": "blocker", "title": "Blocker Gate",
                           "at": _iso(now + timedelta(days=4)), "decision": "pending"}],
            },
            {
                "id": "loop-c", "label": "Loop C", "title": "Production",
                "start": _iso(now - timedelta(days=1)),
                "deadline": _iso(now + timedelta(days=80)),
                "blocked_by": {"initiative": "loop-b", "gate": "blocker"},
                "tasks": [{"id": "s1", "title": "Stream 1", "status": "pending"}],
            },
        ],
    }


class InitiativesRouteTest(unittest.TestCase):
    def setUp(self):
        self._tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        import shutil
        shutil.rmtree(self._tmp, ignore_errors=True)

    def _write(self, home: Path, data: dict, tenant: str = "_default") -> Path:
        p = home / "tenants" / tenant / "global" / "initiatives.json"
        p.write_text(json.dumps(data))
        return p

    def test_missing_file_is_empty_board_not_sample_data(self):
        with _sandbox(self._tmp) as (client, _csrf, _home, _):
            r = client.get(_URL)
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["initiatives"], [])
            self.assertIsNone(body["revision"])
            self.assertEqual(body["totals"]["total"], 0)

    def test_derived_numbers_and_gate_blocking(self):
        now = datetime.now(timezone.utc)
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            self._write(home, _fixture(now))
            body = client.get(_URL).json()
            b, c = body["initiatives"]
            self.assertIn(b["time_progress_pct"], (49, 50, 51))  # 2 of 4 days, derived
            self.assertEqual(b["task_progress_pct"], 30)
            self.assertEqual(b["task_counts"]["overdue"], 1)
            self.assertEqual(b["status"], "at_risk")
            self.assertEqual(b["next_checkpoint"]["label"], "Gate: Blocker Gate")
            self.assertEqual(c["status"], "blocked")
            self.assertEqual(c["blocked_by"]["decision"], "pending")
            self.assertEqual(body["totals"]["running"], 2)
            self.assertEqual(body["totals"]["initiatives_blocked"], 1)

    def test_mutations_need_csrf_persist_and_audit(self):
        now = datetime.now(timezone.utc)
        with _sandbox(self._tmp) as (client, csrf, home, _):
            path = self._write(home, _fixture(now))
            r = client.patch(f"{_URL}/loop-b/tasks/p0-a", json={"status": "done"})
            self.assertIn(r.status_code, (401, 403))

            r = client.patch(f"{_URL}/loop-b/tasks/p0-a", json={"status": "done"},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            task = r.json()["initiatives"][0]["tasks"][0]
            self.assertEqual((task["status"], task["progress"]), ("done", 100))
            self.assertIsNotNone(task["completed_at"])
            self.assertEqual(json.loads(path.read_text())["initiatives"][0]["tasks"][0]["status"], "done")
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)

            r = client.put(f"{_URL}/loop-b/gates/blocker", json={"decision": "go"},
                           headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["initiatives"][1]["status"], "running")  # unblocked

            r = client.patch(f"{_URL}/nope/tasks/x", json={"progress": 5},
                             headers={"X-CSRF-Token": csrf})
            self.assertEqual(r.status_code, 404)

            actions = [e.get("details", {}).get("action") for e in _audit_events(home)]
            self.assertIn("initiative.task.update", actions)
            self.assertIn("initiative.gate.go", actions)

    def test_tenant_comes_from_session(self):
        now = datetime.now(timezone.utc)
        with _sandbox(self._tmp, tenants=("_default", "acme")) as (_c, _s, home, clients):
            self._write(home, _fixture(now), tenant="_default")
            acme_client, _ = clients["acme"]
            self.assertEqual(acme_client.get(_URL).json()["initiatives"], [])


if __name__ == "__main__":
    unittest.main()

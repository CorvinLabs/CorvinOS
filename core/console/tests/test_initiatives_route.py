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

    def test_runs_split_into_active_and_finished_history(self):
        now = datetime.now(timezone.utc)
        data = _fixture(now)
        data["initiatives"].append({
            "id": "old", "label": "Loop 0", "title": "Past run",
            "start": _iso(now - timedelta(days=30)), "deadline": _iso(now - timedelta(days=20)),
            "tasks": [{"id": "t", "title": "T", "status": "done",
                       "completed_at": _iso(now - timedelta(days=21))}],
        })
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            self._write(home, data)
            body = client.get(_URL).json()
            old = body["initiatives"][2]
            self.assertEqual((old["phase"], old["outcome"], old["status"]), ("finished", "completed", "done"))
            self.assertAlmostEqual(old["schedule_delta_s"], 86400, delta=2)  # one day early
            self.assertAlmostEqual(old["duration_s"], 9 * 86400, delta=2)
            self.assertEqual(old["time_progress_pct"], 90)  # frozen at finished_at, not now
            self.assertIsNone(old["next_checkpoint"])
            self.assertEqual((body["totals"]["runs_active"], body["totals"]["runs_finished"]), (2, 1))

    def test_close_and_reopen_run(self):
        now = datetime.now(timezone.utc)
        with _sandbox(self._tmp) as (client, csrf, home, _):
            path = self._write(home, _fixture(now))
            h = {"X-CSRF-Token": csrf}
            self.assertIn(client.put(f"{_URL}/loop-b/close", json={"outcome": "cancelled"}).status_code, (401, 403))
            r = client.put(f"{_URL}/loop-b/close", json={"outcome": "cancelled"}, headers=h)
            self.assertEqual(r.status_code, 200, r.text)
            b = r.json()["initiatives"][0]
            self.assertEqual((b["phase"], b["status"], b["task_counts"]["overdue"]), ("finished", "cancelled", 0))
            self.assertIsNotNone(b["finished_at"])
            self.assertEqual(json.loads(path.read_text())["initiatives"][0]["closed"]["outcome"], "cancelled")

            r = client.put(f"{_URL}/loop-b/close", json={"outcome": None}, headers=h)
            self.assertEqual(r.json()["initiatives"][0]["phase"], "active")
            self.assertNotIn("closed", json.loads(path.read_text())["initiatives"][0])
            self.assertEqual(client.put(f"{_URL}/loop-b/close", json={"outcome": "bogus"}, headers=h).status_code, 422)

            actions = [e.get("details", {}).get("action") for e in _audit_events(home)]
            self.assertIn("initiative.close.cancelled", actions)
            self.assertIn("initiative.reopen", actions)

    def test_evidence_drives_status_progress_and_gate(self):
        now = datetime.now(timezone.utc)
        at = _iso(now - timedelta(minutes=5))
        data = _fixture(now)
        b = data["initiatives"][0]
        b["tasks"][0].update(evidence={"tests": ["x"]},
                             verification={"at": at, "passed": 10, "failed": 0, "errors": 0,
                                           "paths_total": 0, "paths_present": 0, "first_ok_at": at})
        b["tasks"][1].update(status="done", evidence={"tests": ["y"], "paths": ["a", "b"]},
                             verification={"at": _iso(now - timedelta(hours=3)), "passed": 6, "failed": 1,
                                           "errors": 1, "paths_total": 2, "paths_present": 1})
        b["gates"][0]["criteria"] = [{"label": "P0", "requires_tasks": ["p0-a", "p0-b"]}]
        with _sandbox(self._tmp) as (client, _csrf, home, _):
            self._write(home, data)
            body = client.get(_URL).json()
            t0, t1 = body["initiatives"][0]["tasks"]
            self.assertEqual((t0["status"], t0["progress"], t0["progress_source"]), ("done", 100, "evidence"))
            self.assertEqual(t0["completed_at"], at)
            # 6 passed + 1 path of 6+1+1 tests + 2 paths = 7/10
            self.assertEqual((t1["status"], t1["progress"], t1["claim_conflict"]), ("running", 70, True))
            self.assertTrue(t1["verification"]["stale"])
            self.assertEqual(body["initiatives"][0]["gates"][0]["criteria"][0]["state"], "pending")
            self.assertEqual(body["verification"]["claim_conflicts"], 1)
            self.assertEqual(body["verification"]["stale_tasks"], 1)

    def test_verifier_runs_repo_tests_in_sandbox_and_records_results(self):
        from unittest import mock
        repo = self._tmp / "repo"
        (repo / "t").mkdir(parents=True)
        (repo / "t" / "test_ok.py").write_text("def test_a():\n    assert True\n")
        (repo / "t" / "test_bad.py").write_text(
            "import os\ndef test_b():\n    assert False\n"
            "def test_home_is_sandboxed():\n    assert 'corvin-initiatives-verify-' in os.environ['CORVIN_HOME']\n")
        (repo / "present.txt").write_text("x")
        now = datetime.now(timezone.utc)
        data = _fixture(now)
        tasks = data["initiatives"][0]["tasks"]
        tasks[0]["evidence"] = {"tests": ["t/test_ok.py"], "paths": ["present.txt"]}
        tasks[1]["evidence"] = {"tests": ["t/test_bad.py", "../escape.py"], "paths": ["missing.txt"]}
        with _sandbox(self._tmp) as (client, csrf, home, _):
            path = self._write(home, data)
            from corvin_console import initiatives_verify as iv
            with mock.patch.object(iv, "REPO", repo):
                summary = iv.verify("_default")
            self.assertEqual((summary["verified"], summary["green"], summary["red"]), (2, 1, 1))
            stored = json.loads(path.read_text())["initiatives"][0]["tasks"]
            ok, bad = stored[0]["verification"], stored[1]["verification"]
            self.assertEqual((ok["passed"], ok["paths_present"]), (1, 1))
            self.assertIsNotNone(ok["first_ok_at"])
            self.assertEqual((bad["passed"], bad["failed"]), (1, 1))  # sandbox check passed
            self.assertEqual(bad["errors"], 1)                         # ../escape.py refused
            self.assertEqual(bad["missing_paths"], ["missing.txt"])
            self.assertIsNone(bad["first_ok_at"])
            events = [e.get("event_type") for e in _audit_events(home)]
            self.assertIn("initiatives.verified", events)

            body = client.get(_URL).json()
            self.assertEqual(body["initiatives"][0]["tasks"][0]["status"], "done")

            with mock.patch.object(iv, "needs_run", return_value=(False, "unchanged")), \
                 mock.patch.object(iv, "start_background", return_value=True) as sb:
                r = client.post(f"{_URL}/verify?if_changed=true", headers={"X-CSRF-Token": csrf})
                self.assertEqual((r.status_code, r.json()["started"], r.json()["reason"]), (202, False, "unchanged"))
                sb.assert_not_called()   # nothing changed → no run, no audit record
            with mock.patch.object(iv, "needs_run", return_value=(True, "repo or evidence changed")), \
                 mock.patch.object(iv, "start_background", return_value=True) as sb:
                r = client.post(f"{_URL}/verify?if_changed=true", headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.json()["started"], True)
                sb.assert_called_once_with("_default")
            with mock.patch.object(iv, "start_background", return_value=True) as sb:
                self.assertIn(client.post(f"{_URL}/verify").status_code, (401, 403))
                r = client.post(f"{_URL}/verify", headers={"X-CSRF-Token": csrf})
                self.assertEqual(r.status_code, 202, r.text)
                sb.assert_called_once_with("_default")

    def test_if_changed_skips_until_repo_or_evidence_changes(self):
        from unittest import mock
        repo = self._tmp / "repo2"
        repo.mkdir()
        now = datetime.now(timezone.utc)
        data = _fixture(now)
        data["initiatives"][0]["tasks"][0]["evidence"] = {"paths": ["gen.py"]}
        with _sandbox(self._tmp) as (_client, _csrf, home, _):
            self._write(home, data)
            from corvin_console import initiatives_verify as iv
            with mock.patch.object(iv, "REPO", repo):
                self.assertEqual(iv.needs_run("_default"), (True, "last run older than max age"))
                iv.verify("_default")
                self.assertEqual(iv.needs_run("_default"), (False, "unchanged"))
                (repo / "gen.py").write_text("x")           # the evidence path appears
                self.assertEqual(iv.needs_run("_default"), (True, "repo or evidence changed"))
                iv.verify("_default")
                self.assertEqual(iv.needs_run("_default")[0], False)
                t = time.time() + iv.MAX_AGE_S + 1          # nothing changed, but too old
                self.assertEqual(iv.needs_run("_default", now=t), (True, "last run older than max age"))

    def test_tenant_comes_from_session(self):
        now = datetime.now(timezone.utc)
        with _sandbox(self._tmp, tenants=("_default", "acme")) as (_c, _s, home, clients):
            self._write(home, _fixture(now), tenant="_default")
            acme_client, _ = clients["acme"]
            self.assertEqual(acme_client.get(_URL).json()["initiatives"], [])


if __name__ == "__main__":
    unittest.main()

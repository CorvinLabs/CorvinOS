"""POST /v1/console/api/dod/verify through the real console router.

Measured 2026-10-01 on this repo: the verifier could never pass. Reachability ran
``grep -r`` over the whole checkout (.venv, node_modules, worktrees) and hit its
5 s timeout every time; the audit check looked for a top-level ``task_id``, which
none of the 114 635 chain records carries (it lives under ``details``). Together
45 % of the weight, so the ceiling was 55 % against an 80 % threshold.

The request body also reached ``git diff`` and ``grep`` as arguments and named
files to read, so it is validated here too.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

_VERIFY = "/v1/console/api/dod/verify"


def _seed_task_event(home: Path, task_id: str) -> None:
    from forge import security_events
    from forge.paths import tenant_audit_chain
    security_events.write_event(tenant_audit_chain("_default"), "task.started",
                                details={"task_id": task_id})


class DodVerifyRouteTest(unittest.TestCase):

    def test_reachability_and_audit_checks_can_pass_on_the_real_repo(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            _seed_task_event(home, "dod-e2e-1")
            before = len(_audit_events(home))
            r = client.post(_VERIFY, headers={"X-CSRF-Token": csrf}, json={
                "task_id": "dod-e2e-1",
                "symbol_name": "DoD_VerifierSkillWrapper",
                "commit_msg": "fix(dod): reproduce with pytest",
            })
            self.assertEqual(r.status_code, 200, r.text)
            checks = r.json()["checks"]
            self.assertTrue(checks["reachability"]["passed"], checks["reachability"])
            self.assertTrue(checks["audit_trail"]["passed"], checks["audit_trail"])
            self.assertTrue(checks["reproducibility"]["passed"])

            new = _audit_events(home)[before:]
            executed = [e for e in new if e.get("event_type") == "skill.executed"
                        or e.get("event_type") == "skill_executed"]
            self.assertEqual(len(executed), 1, [e.get("event_type") for e in new])

            hist = client.get("/v1/console/api/dod/history/dod-e2e-1").json()
            self.assertEqual(len(hist["verifications"]), 1)

    def test_definition_alone_is_not_a_call_site(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            r = client.post(_VERIFY, headers={"X-CSRF-Token": csrf}, json={
                "task_id": "dod-e2e-2",
                # defined in hardening.py, used only by tests; a substring of
                # LiveMonitoringMetrics, which a plain substring search matched
                "symbol_name": "MonitoringMetrics",
            })
            self.assertEqual(r.status_code, 200, r.text)
            self.assertFalse(r.json()["checks"]["reachability"]["passed"])

    def test_commit_range_cannot_inject_git_options(self):
        tmp = Path(tempfile.mkdtemp())
        target = tmp / "pwned"
        with _sandbox(tmp) as (client, csrf, _home, _):
            r = client.post(_VERIFY, headers={"X-CSRF-Token": csrf}, json={
                "task_id": "dod-e2e-3", "commit_range": f"--output={target}",
            })
            self.assertEqual(r.status_code, 400, r.text)
        self.assertFalse(target.exists())

    def test_symbol_name_is_a_literal_not_a_grep_option(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            r = client.post(_VERIFY, headers={"X-CSRF-Token": csrf}, json={
                "task_id": "dod-e2e-4", "symbol_name": "--version",
            })
            self.assertEqual(r.status_code, 400, r.text)

    def test_files_outside_the_project_are_not_read(self):
        outside = Path(tempfile.mkdtemp()) / "out.txt"
        outside.write_text("1 passed in 0.1s SECRET-4711\n" * 3)
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            r = client.post(_VERIFY, headers={"X-CSRF-Token": csrf}, json={
                "task_id": "dod-e2e-5",
                "test_path": str(outside), "test_output_file": str(outside),
            })
            self.assertEqual(r.status_code, 400, r.text)
            self.assertNotIn("4711", r.text)

    def test_session_and_csrf_required(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            self.assertEqual(client.post(_VERIFY, json={"task_id": "x"}).status_code, 403)
            client.cookies.clear()
            self.assertIn(client.post(_VERIFY, json={"task_id": "x"}).status_code, (401, 403))


if __name__ == "__main__":
    unittest.main()

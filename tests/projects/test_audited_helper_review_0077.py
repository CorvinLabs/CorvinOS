"""``projects_service._audited``: requested before the body, applied or error after it, and a chain that fails between two opening
records closes the ones already open (REVIEW-0076 N15-N17, REVIEW-0077 note)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[0] / "e2e"))
from _projects_fixtures import Sandbox  # noqa: E402


class Audited(unittest.TestCase):
    def setUp(self):
        self.sb = Sandbox()
        from corvin_console import projects_service as svc
        self.svc = svc
        self.calls: list[tuple[str, str]] = []

    def tearDown(self):
        self.sb.close()

    def _rec(self, fail_on: int | None = None):
        def fake(tenant_id, event, **d):
            self.calls.append((d.get("feature"), d.get("outcome")))
            if fail_on is not None and len(self.calls) == fail_on:
                raise self.svc._tt.AuditUnavailable("chain down")
        return mock.patch.object(self.svc, "_audit", fake)

    def test_applied_and_error(self):
        with self._rec():
            with self.svc._audited("t", "project.feature_toggled", {"feature": "kb"}):
                pass
        self.assertEqual(self.calls, [("kb", "requested"), ("kb", "applied")])
        self.calls.clear()
        with self._rec(), self.assertRaises(ValueError):
            with self.svc._audited("t", "project.feature_toggled", {"feature": "kb"}):
                raise ValueError("refused")
        self.assertEqual(self.calls, [("kb", "requested"), ("kb", "error")])

    def test_a_chain_failing_between_two_opening_records_closes_the_first_and_runs_nothing(self):
        ran = []
        with self._rec(fail_on=2), self.assertRaises(self.svc._tt.AuditUnavailable):
            with self.svc._audited("t", "project.feature_toggled", {"feature": "kb"}, {"feature": "tasks"}):
                ran.append(1)
        self.assertEqual(ran, [])
        self.assertEqual(self.calls, [("kb", "requested"), ("tasks", "requested"), ("kb", "error")])


if __name__ == "__main__":
    unittest.main()

"""Remediation approve/reject (ADR-0411) through the real console router.

What must hold:

* every remediation route answers 401 without a console session;
* approve/reject answer 403 without the CSRF token;
* the decider is the session's operator — a body-supplied ``approved_by`` is
  ignored, so no caller can approve under another name;
* the decision goes through ``ApprovalGate.approve_request`` (state really
  changes, a second decision on the same request is a 409, not a silent rewrite).
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

BASE = "/v1/console/remediation"


def _seed_request(request_id: str) -> None:
    from core.remediation.approval_workflow import ApprovalRequest, ApprovalState, get_approval_gate

    get_approval_gate().pending_requests[request_id] = ApprovalRequest(
        request_id=request_id, drift_id="d-1", drift_type="config", instance_id="i-1",
        risk_assessment={}, state=ApprovalState.PENDING, requested_at=datetime.utcnow().isoformat())


class RemediationAuthTest(unittest.TestCase):
    def test_routes_require_a_session(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            client.cookies.clear()
            for path in ("/pending", "/history", "/status/x"):
                self.assertEqual(client.get(BASE + path).status_code, 401, path)
            for verb in ("approve", "reject"):
                r = client.post(f"{BASE}/{verb}/x", json={"approved_by": "attacker"})
                self.assertEqual(r.status_code, 401, verb)

    def test_mutation_requires_csrf(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            _seed_request("r-csrf")
            r = client.post(f"{BASE}/approve/r-csrf", json={})
            self.assertEqual(r.status_code, 403, r.text)

    def test_decider_comes_from_the_session_not_the_body(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, _home, _):
            _seed_request("r-1")
            r = client.post(f"{BASE}/approve/r-1", headers={"X-CSRF-Token": csrf},
                            json={"approved_by": "attacker", "reason": "looks fine"})
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertEqual(body["state"], "approved")
            self.assertTrue(body["decided_by"].startswith("console:"), body)
            from core.remediation.approval_workflow import get_approval_gate
            req = get_approval_gate().pending_requests["r-1"]
            self.assertEqual(req.approved_by, body["decided_by"])
            self.assertNotEqual(req.approved_by, "attacker")
            # A decided request cannot be decided again.
            r2 = client.post(f"{BASE}/reject/r-1", headers={"X-CSRF-Token": csrf}, json={})
            self.assertEqual(r2.status_code, 409, r2.text)
            self.assertEqual(client.get(f"{BASE}/status/r-1").json()["state"], "approved")


if __name__ == "__main__":
    unittest.main()

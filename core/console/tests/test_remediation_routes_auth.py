"""Remediation approve/reject (ADR-0411) through the real console router.

What must hold:

* every remediation route answers 401 without a console session;
* approve/reject answer 403 without the CSRF token;
* the decider is the session's operator — a body-supplied ``approved_by`` is
  ignored, so no caller can approve under another name;
* the decision goes through ``ApprovalGate.approve_request`` (state really
  changes, a second decision on the same request is a 409, not a silent rewrite);
* requests are tenant-bound: another tenant's request is a 404 on every route;
* an expired request cannot be decided (409);
* the decision is on the tenant's core chain, and when that record cannot be
  written the decision is refused (503) and nothing changes.
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import timedelta
from unittest.mock import patch
import unittest
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402

BASE = "/v1/console/remediation"


def _seed_request(request_id: str, *, tenant_id: str = "_default", expired: bool = False) -> None:
    from core.remediation.approval_workflow import ApprovalRequest, ApprovalState, get_approval_gate

    now = datetime.utcnow()
    expires = now - timedelta(minutes=1) if expired else now + timedelta(hours=24)
    get_approval_gate().pending_requests[request_id] = ApprovalRequest(
        request_id=request_id, drift_id="d-1", drift_type="config", instance_id="i-1",
        risk_assessment={}, state=ApprovalState.PENDING, requested_at=now.isoformat(),
        expires_at=expires.isoformat(), tenant_id=tenant_id)


def _decisions(home: Path, tenant_id: str) -> list:
    chain = home / "tenants" / tenant_id / "global" / "forge" / "audit.jsonl"
    if not chain.exists():
        return []
    recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
    return [r for r in recs if r.get("event_type") == "remediation.approval_decided"]


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

            self.assertEqual(len(_decisions(_home, "_default")), 1)
            self.assertEqual(_decisions(_home, "_default")[0]["details"]["decided_by"], body["decided_by"])
            self.assertNotIn("looks fine", json.dumps(_decisions(_home, "_default")))

    def test_other_tenants_request_is_invisible_and_undecidable(self):
        with _sandbox(Path(tempfile.mkdtemp()), tenants=("_default", "acme")) as (
            client, csrf, home, clients,
        ):
            _seed_request("r-acme", tenant_id="acme")
            for path in ("/status/r-acme",):
                self.assertEqual(client.get(BASE + path).status_code, 404)
            self.assertNotIn("r-acme", client.get(BASE + "/pending").text)
            self.assertNotIn("r-acme", client.get(BASE + "/history").text)
            r = client.post(f"{BASE}/approve/r-acme", headers={"X-CSRF-Token": csrf}, json={})
            self.assertEqual(r.status_code, 404, r.text)
            # The owning tenant sees it. Deciding it needs a record on ACME's
            # chain; the forge writer refuses a tenant other than the process
            # tenant (this console runs as _default), so the decision is
            # refused with 503 and the request stays open — never decided
            # unrecorded, never recorded on another tenant's chain.
            acme, acme_csrf = clients["acme"]
            self.assertIn("r-acme", acme.get(BASE + "/pending").text)
            r = acme.post(f"{BASE}/reject/r-acme", headers={"X-CSRF-Token": acme_csrf}, json={})
            self.assertEqual(r.status_code, 503, r.text)
            self.assertEqual(acme.get(BASE + "/status/r-acme").json()["state"], "pending")
            self.assertEqual(_decisions(home, "_default"), [])

    def test_expired_request_cannot_be_approved(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            _seed_request("r-old", expired=True)
            r = client.post(f"{BASE}/approve/r-old", headers={"X-CSRF-Token": csrf}, json={})
            self.assertEqual(r.status_code, 409, r.text)
            self.assertEqual(client.get(f"{BASE}/status/r-old").json()["state"], "expired")
            self.assertEqual(_decisions(home, "_default"), [])

    def test_decision_refused_when_audit_cannot_be_written(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            _seed_request("r-na")
            with patch("core.remediation.approval_workflow.remediation_audit",
                       side_effect=RuntimeError("chain unwritable")):
                r = client.post(f"{BASE}/approve/r-na", headers={"X-CSRF-Token": csrf}, json={})
            self.assertEqual(r.status_code, 503, r.text)
            self.assertEqual(client.get(f"{BASE}/status/r-na").json()["state"], "pending")


if __name__ == "__main__":
    unittest.main()

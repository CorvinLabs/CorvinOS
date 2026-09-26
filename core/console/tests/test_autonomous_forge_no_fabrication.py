"""Autonomous Forge routes (ADR-0904) never fabricate state (ADR-0763).

Through the real console router with a real session (``_sandbox``):

* ``GET /autonomous-forge/status`` answers 404 "No active canary deployment" —
  it used to return a hard-coded ``os.delegation_router 2.1.0`` canary;
* approve / defer / rollback against no active canary are a 409 and write no
  audit decision — approve used to answer "rolled_out_at" for a rollout that
  never happened;
* ``GET /manifest/...`` answers 404 when no manifest file exists and returns the
  real file's content when one does — it used to return a generated placeholder.
"""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402

BASE = "/v1/console/autonomous-forge"


class NoFabricationTest(unittest.TestCase):
    def test_status_has_no_invented_canary(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            r = client.get(f"{BASE}/status")
            self.assertEqual(r.status_code, 404, r.text)
            self.assertIn("No active canary", r.text)
            self.assertNotIn("delegation_router", r.text)

    def test_decisions_without_a_canary_are_refused_and_not_audited(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            before = len(_audit_events(home))
            auth = {"operator_id": "x", "session_token": "0" * 64, "client_nonce": "0" * 32}
            for verb, body in (("approve", {"skill_id": "os.delegation_router", "version": "2.1.0"}),
                               ("defer", {"skill_id": "os.delegation_router", "version": "2.1.0",
                                          "reason": "not yet"}),
                               ("rollback", {"skill_id": "os.delegation_router", "reason": "bad"})):
                r = client.post(f"{BASE}/{verb}", headers={"X-CSRF-Token": csrf}, json={**body, **auth})
                self.assertEqual(r.status_code, 409, f"{verb}: {r.status_code} {r.text}")
                self.assertNotIn("rolled_out_at", r.text)
            decisions = [e for e in _audit_events(home)[before:]
                         if str(e.get("event_type", "")).startswith("autonomous_forge.operator_")]
            self.assertEqual(decisions, [])

    def test_manifest_is_real_or_absent(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, home, _):
            r = client.get(f"{BASE}/manifest/os.example_skill/1.0.0")
            self.assertEqual(r.status_code, 404, r.text)
            d = home / "tenants" / "_default" / "skill-forge" / "os.example_skill" / "1.0.0"
            d.mkdir(parents=True)
            (d / "skill.json").write_text(json.dumps({"id": "os.example_skill", "version": "1.0.0",
                                                      "description": "on disk"}))
            r = client.get(f"{BASE}/manifest/os.example_skill/1.0.0")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["skill_json"]["description"], "on disk")


if __name__ == "__main__":
    unittest.main()

"""Console backend regressions found in the 2026-09-27 adversarial review.

Every case goes through the real console router with a real session (``_sandbox``).

* ``GET /discovery/peers`` answered 500 on EVERY request: it called
  ``audit.audit_info`` / ``audit_error``, which do not exist. A listed peer also
  reported ``status: "online"`` with no liveness probe behind it.
* ``/skills/{id}/learning|feedback/history|optimization/proposals`` were
  unauthenticated and served the same hard-coded numbers for every skill id.
* ``/api/dod/feedback`` and ``/api/dod/history`` were 500 on every call (a dict
  passed where the store takes a LearningEvent; a ``filters=`` keyword the store
  does not have), and the 500 carried the exception text to the client.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _audit_events, _sandbox  # noqa: E402


class DiscoveryPeersTest(unittest.TestCase):
    def test_lists_peers_without_500_and_without_invented_liveness(self):
        tmp = Path(tempfile.mkdtemp())
        origins = tmp / "origins"
        origins.mkdir()
        (origins / "peer-a.json").write_text(json.dumps({"peer_id": "peer-a", "name": "A"}))
        prev = os.environ.get("REMOTE_ORIGINS_DIR")
        os.environ["REMOTE_ORIGINS_DIR"] = str(origins)
        try:
            with _sandbox(tmp) as (client, _csrf, _home, _):
                r = client.get("/v1/console/discovery/peers")
                self.assertEqual(r.status_code, 200, r.text)
                body = r.json()
                self.assertEqual(body["total"], 1)
                self.assertEqual(body["peers"][0]["status"], "unknown")
                self.assertEqual(client.get("/v1/console/discovery/peers/peer-a").status_code, 200)
                self.assertEqual(client.get("/v1/console/discovery/peers/nope").status_code, 404)
                client.cookies.clear()
                self.assertEqual(client.get("/v1/console/discovery/peers").status_code, 401)
        finally:
            if prev is None:
                os.environ.pop("REMOTE_ORIGINS_DIR", None)
            else:
                os.environ["REMOTE_ORIGINS_DIR"] = prev


class SkillLearningNoFabricationTest(unittest.TestCase):
    def test_no_sample_data_and_session_required(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, _csrf, _home, _):
            base = "/v1/console/skills/os.delegation_router"
            r = client.get(f"{base}/learning")
            self.assertEqual(r.status_code, 404, r.text)
            self.assertIn("not available", r.json()["detail"])
            hist = client.get(f"{base}/feedback/history").json()
            self.assertEqual((hist["recent"], hist["available"]), ([], False))
            props = client.get(f"{base}/optimization/proposals").json()
            self.assertEqual((props["proposals"], props["available"]), ([], False))
            client.cookies.clear()
            for path in ("learning", "feedback/history", "optimization/proposals"):
                self.assertEqual(client.get(f"{base}/{path}").status_code, 401, path)


class DodVerifierRoutesTest(unittest.TestCase):
    def test_feedback_and_history_work_and_do_not_leak(self):
        with _sandbox(Path(tempfile.mkdtemp())) as (client, csrf, home, _):
            h = {"X-CSRF-Token": csrf}
            r = client.post("/v1/console/api/dod/feedback", headers=h,
                            json={"task_id": "t1", "check_name": "tests", "feedback": "accurate",
                                  "note": "private remark 4711"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["accepted"])
            self.assertNotIn("weight_adjustment", r.json())
            events_dir = home / "tenants" / "_default" / "learning" / "events"
            disk = "".join(p.read_text() for p in events_dir.glob("*.jsonl"))
            self.assertIn("dod_feedback", disk)
            self.assertNotIn("4711", disk + repr(_audit_events(home)))

            r = client.post("/v1/console/api/dod/feedback", headers=h,
                            json={"task_id": "t1", "feedback": "whatever"})
            self.assertEqual(r.status_code, 400)

            r = client.get("/v1/console/api/dod/history/t1")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["verifications"], [])


if __name__ == "__main__":
    unittest.main()

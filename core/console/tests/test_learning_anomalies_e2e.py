"""ADR-2044 Phase 3 through the real path: refused feedback over HTTP -> ADR-0534 decision ledger ->
learning host tick -> ``skill.high_rejection_rate`` in the tenant chain -> the console daemon route."""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_admin_route import _sandbox  # noqa: E402


def _run(coro):
    return asyncio.new_event_loop().run_until_complete(coro)


def _chain_events(home: str, name: str) -> list[dict]:
    chain = Path(home) / "tenants" / "_default" / "global" / "forge" / "audit.jsonl"
    out = []
    for line in chain.read_text().splitlines():
        try:
            rec = json.loads(line)
        except ValueError:
            continue
        if name in (rec.get("event"), rec.get("event_type")):
            out.append(rec)
    return out


class LearningAnomaliesE2E(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def test_refused_feedback_becomes_one_audited_anomaly_shown_by_the_daemon_route(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            # flow_guard never ran in this sandbox: the reality check refuses every rating, and the
            # throttle refuses the burst; every attempt lands in the persisted decision ledger.
            codes = []
            for i in range(12):
                r = client.post("/v1/console/feedback/skill", headers={"X-CSRF-Token": csrf}, json={
                    "skill_id": "os.flow_guard", "subject_id": f"flow_{i}", "rating": -1,
                    "category": "accuracy"})
                codes.append(r.status_code)
            self.assertEqual(set(codes), {422}, codes)

            from core.background.learning_host import LearningHost
            host = LearningHost("_default")
            _run(host.tick())
            recs = _chain_events(home, "skill.high_rejection_rate")
            self.assertEqual(len(recs), 1, recs)
            self.assertIn("os.flow_guard", json.dumps(recs[0]))

            # edge-triggered: a restarted host does not record the same ongoing anomaly again
            again = LearningHost("_default")
            again._last_anomaly_check = 0.0
            _run(again.tick())
            self.assertEqual(len(_chain_events(home, "skill.high_rejection_rate")), 1)

            r = client.get("/v1/console/api/learning/daemon")
            self.assertEqual(r.status_code, 200, r.text)
            anomalies = r.json()["data"]["anomalies"]
            self.assertEqual([(a["event"], a["skill_id"]) for a in anomalies],
                             [("skill.high_rejection_rate", "os.flow_guard")])
            self.assertEqual(anomalies[0]["sample_count"], 12)
            self.assertEqual(anomalies[0]["rejection_count"], 12)

    def test_an_operator_rates_a_recorded_decision_and_the_router_config_moves(self):
        with _sandbox(self.tmp) as (client, csrf, home, _):
            from core.learning.event_store import EventStore
            from core.learning.learning_events import EventType, LearningEvent
            from core.paths.tenant import tenant_home

            store = EventStore(tenant_home("_default"), tenant_id="_default")
            ev = LearningEvent.create(
                event_type=EventType.SKILL_EXECUTED, skill_id="os.delegation_router", tenant_id="_default",
                signal={"status": "success", "output": {"decision": "native", "confidence": 0.95,
                                                         "reasoning": "free text never shown"}},
                lom="tests:seed")
            store.write_event(ev)

            r = client.get("/v1/console/feedback/skill/recent?skill_id=os.delegation_router")
            self.assertEqual(r.status_code, 200, r.text)
            body = r.json()
            self.assertTrue(body["config_consumer"])
            self.assertEqual([(i["event_id"], i["decision"]) for i in body["items"]], [(ev.event_id, "native")])
            self.assertNotIn("free text", r.text)
            self.assertEqual(client.get("/v1/console/feedback/skill/recent?skill_id=os.nope").status_code, 400)

            r = client.post("/v1/console/feedback/skill", headers={"X-CSRF-Token": csrf}, json={
                "skill_id": "os.delegation_router", "subject_id": ev.event_id, "rating": -1,
                "category": "accuracy"})
            self.assertEqual(r.status_code, 200, r.text)
            self.assertTrue(r.json()["config_applied"], r.json())
            self.assertEqual(len(_chain_events(home, "skill_feedback")), 1)


if __name__ == "__main__":
    unittest.main()

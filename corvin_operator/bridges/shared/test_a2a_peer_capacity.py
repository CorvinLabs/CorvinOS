"""ADR-2242 §8 — the peer's task capacity from the pong to the presence rule.

``_ping_peer`` keeps its (reachable, via) contract (other tests mock it); the capacity travels
beside it. Only a ping that ANSWERED may change what is stored: "available"/"limit_reached" set
it, an answer without the field (an older build) clears a stale value, a failed ping leaves it
alone — and presence() reports it only while the peer is online."""
from __future__ import annotations

import sys
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent))

import a2a_connectivity as ac  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402

NOW = time.time()


def _online(**extra):
    return {"enabled": True, "state": "ACTIVE", "_last_check_at": NOW, "_last_ok_at": NOW, **extra}


class TestPingResultCapacity(unittest.TestCase):
    def _ping(self, response_extra, ok=True):
        def probe(self_, ep, timeout_s=5, audit=True, **kw):
            kw["out"]["response"] = {"ok": True, **response_extra}
            return ok, None, None, "relay"
        with mock.patch.object(rts.RemoteTriggerSender, "_http_ping_probe", probe):
            return rts.RemoteTriggerSender(instance_id="x").ping("ep", audit=False)

    def test_reported_values_pass_through(self):
        self.assertEqual(self._ping({"task_capacity": "limit_reached"}).task_capacity, "limit_reached")
        self.assertEqual(self._ping({"task_capacity": "available"}).task_capacity, "available")

    def test_an_older_peer_or_an_unknown_value_is_none(self):
        self.assertIsNone(self._ping({}).task_capacity)
        self.assertIsNone(self._ping({"task_capacity": "<script>"}).task_capacity)
        self.assertIsNone(self._ping({"task_capacity": 7}).task_capacity)

    def test_a_failed_ping_reports_nothing(self):
        self.assertIsNone(self._ping({"task_capacity": "limit_reached"}, ok=False).task_capacity)


class TestConnectivityStampsCapacity(unittest.TestCase):
    def setUp(self):
        ac._CAPACITY_SEEN.clear()

    def _ping_peer(self, *, reachable=True, capacity=None):
        # _ping_peer imports remote_trigger_sender at call time: patch the module object it
        # will get (a sibling suite may have replaced sys.modules' entry since this file imported).
        import importlib
        mod = importlib.import_module("remote_trigger_sender")
        res = mod.PingResult(reachable=reachable, source="network_probe", via="relay", task_capacity=capacity)
        with mock.patch.object(mod.RemoteTriggerSender, "ping", return_value=res):
            return ac._ping_peer("kid-1", Path("/nonexistent"), audit=False)

    def test_ping_peer_keeps_its_two_value_contract(self):
        self.assertEqual(self._ping_peer(capacity="limit_reached"), (True, "relay"))
        self.assertEqual(ac._CAPACITY_SEEN["kid-1"], "limit_reached")

    def test_answer_without_the_field_is_recorded_as_unknown_not_as_available(self):
        self._ping_peer(capacity=None)
        self.assertEqual(ac._CAPACITY_SEEN["kid-1"], "")

    def test_an_unreachable_peer_records_nothing(self):
        self._ping_peer(reachable=False, capacity="limit_reached")
        self.assertNotIn("kid-1", ac._CAPACITY_SEEN)

    def test_stamping_sets_clears_and_leaves_alone(self):
        cfg = {}
        ac._CAPACITY_SEEN["kid-1"] = "limit_reached"
        ac._stamp_capacity(cfg, "kid-1")
        self.assertEqual(cfg["_peer_task_capacity"], "limit_reached")
        ac._CAPACITY_SEEN["kid-1"] = "available"        # recovers after the daily reset
        ac._stamp_capacity(cfg, "kid-1")
        self.assertEqual(cfg["_peer_task_capacity"], "available")
        ac._CAPACITY_SEEN["kid-1"] = ""                  # an older build: a stale value must not linger
        ac._stamp_capacity(cfg, "kid-1")
        self.assertNotIn("_peer_task_capacity", cfg)
        cfg["_peer_task_capacity"] = "limit_reached"
        ac._CAPACITY_SEEN.pop("kid-1")                   # no ping happened: untouched
        ac._stamp_capacity(cfg, "kid-1")
        self.assertEqual(cfg["_peer_task_capacity"], "limit_reached")

    def test_both_connection_files_get_the_same_value(self):
        ac._CAPACITY_SEEN["kid-1"] = "limit_reached"
        a, b = {}, {}
        ac._stamp_capacity(a, "kid-1")
        ac._stamp_capacity(b, "kid-1")
        self.assertEqual(a, b)


class TestPresenceReportsCapacityOnlyWhileOnline(unittest.TestCase):
    def test_online_peer_with_a_spent_pool(self):
        p = ac.presence([_online(_peer_task_capacity="limit_reached")], NOW)
        self.assertEqual((p["presence"], p["task_capacity"]), ("online", "limit_reached"))

    def test_a_stale_reading_from_a_peer_that_is_gone_is_not_shown(self):
        gone = {"enabled": True, "state": "UNREACHABLE", "_last_check_at": NOW, "_last_ok_at": NOW - 10_000,
                "_peer_task_capacity": "limit_reached"}
        p = ac.presence([gone], NOW)
        self.assertEqual(p["presence"], "offline")
        self.assertIsNone(p["task_capacity"])

    def test_junk_and_absence_are_none(self):
        self.assertIsNone(ac.presence([_online()], NOW)["task_capacity"])
        self.assertIsNone(ac.presence([_online(_peer_task_capacity="<script>")], NOW)["task_capacity"])


if __name__ == "__main__":
    unittest.main()

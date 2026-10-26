"""T-0252 / ADR-2300 step 1 — a task queued at the relay is not run after its sender stopped waiting.

Before: a sender that gave up (``response_timeout``) left its envelope in the relay queue for 300 s;
the peer reconnected, received it inside the +-300 s freshness window and EXECUTED it, with nobody
left to read the answer. The sender now stamps ``_relay_deliver_by`` (its own wait budget) into the
AEAD-protected relay payload and the receiving ``RelayListener`` drops a task whose deadline passed
by more than ``_DELIVER_BY_GRACE_S``. No relay change, no wire change to the signed envelope.

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_relay_deliver_by.py
"""
from __future__ import annotations

import asyncio
import math
import os
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_relay
from test_a2a_relay_hardening import (  # noqa: F401  (helpers of the sibling suite)
    _LOG, _FakeWS, _Instance, _RelayServer, _deliver_frame, _free_port, _pair,
)


class _Recorder:
    """Receiver stand-in: records what reached ``receive`` and every audit call."""

    def __init__(self) -> None:
        self.received: list[dict] = []
        self.audits: list[tuple[str, str, dict]] = []

    def receive(self, payload):  # noqa: ANN001
        self.received.append(dict(payload))

        class R:
            def to_dict(_s):
                return {"task_id": payload["task_id"], "status": "ok", "signature": "x"}
        return R()

    def _audit_best_effort(self, event_type: str, severity: str, details: dict) -> None:
        self.audits.append((event_type, severity, details))


class TestListenerDeliverBy(unittest.TestCase):
    """The deadline rule at the listener, including every malformed value."""

    def _run(self, extra: dict):
        key = secrets.token_hex(32)
        body = {"task_id": "t1", "instruction": "x", "sender_instance_id": "other", **extra}
        rec = _Recorder()
        ws = _FakeWS([_deliver_frame("peer", key, "t1", body)])
        lst = a2a_relay.RelayListener(relay_url="ws://unused", receiver=rec,
                                      origins_dir="/nonexistent", instance_id="me-instance")
        asyncio.run(lst._serve(ws, {"peer": key}, set(), _LOG))
        return rec, ws

    def test_no_field_runs_as_before(self):
        rec, ws = self._run({})
        self.assertEqual(len(rec.received), 1)
        self.assertEqual(len(ws.sent), 1)
        self.assertEqual(rec.audits, [])

    def test_future_and_recently_expired_run_and_the_field_never_reaches_the_receiver(self):
        for label, by in (("future", time.time() + 60), ("inside grace", time.time() - 5)):
            with self.subTest(label):
                rec, _ws = self._run({"_relay_deliver_by": by})
                self.assertEqual(len(rec.received), 1)
                self.assertNotIn("_relay_deliver_by", rec.received[0])
                self.assertEqual(rec.audits, [])

    def test_expired_beyond_the_grace_is_dropped_unrun_unanswered_and_audited(self):
        rec, ws = self._run({"_relay_deliver_by": time.time() - 100})
        self.assertEqual(rec.received, [], "an expired task must never reach the receiver")
        self.assertEqual(ws.sent, [], "nobody is waiting: no answer is sent")
        self.assertEqual(len(rec.audits), 1)
        event, severity, details = rec.audits[0]
        self.assertEqual((event, severity), ("A2A.relay_expired_dropped", "INFO"))
        self.assertEqual(details["task_id"], "t1")
        self.assertGreaterEqual(details["late_s"], 70)
        self.assertNotIn("instruction", details)

    def test_malformed_values_mean_no_deadline_and_are_stripped(self):
        for bad in ("soon", True, None, [1], {"a": 1}, math.inf, -math.inf, math.nan, -1.0e18 + 0, 0):
            with self.subTest(repr(bad)):
                rec, _ws = self._run({"_relay_deliver_by": bad})
                if bad in (-1.0e18, 0):
                    # finite numbers ARE a deadline (long gone): dropped, not ignored
                    self.assertEqual(rec.received, [])
                else:
                    self.assertEqual(len(rec.received), 1)
                    self.assertNotIn("_relay_deliver_by", rec.received[0])


class TestSenderStampsTheDeadline(unittest.TestCase):
    def _post(self, timeout_s: int):
        import remote_trigger_sender as rts
        seen: dict = {}

        def fake_round_trip(**kw):
            seen.update(kw)
            return {"ok": True}
        envelope = {"task_id": "t-1", "sender_instance_id": "abc", "signature": "s" * 64,
                    "instruction": "hello"}
        original = dict(envelope)
        cfg = {"hmac_key": "k" * 64, "endpoint_id": "kid1"}
        t0 = time.time()
        with mock.patch.object(rts, "_relay_fallback_enabled", return_value=True), \
                mock.patch("a2a_friendship.get_my_relay_url", return_value="ws://relay"), \
                mock.patch.object(rts, "_relay_round_trip", fake_round_trip):
            rts.RemoteTriggerSender._relay_post(cfg, "kid1", envelope, timeout_s)
        return seen, envelope, original, t0

    def test_task_payload_carries_the_senders_wait_budget(self):
        seen, _env, _orig, t0 = self._post(75)
        by = seen["payload"]["_relay_deliver_by"]
        self.assertGreaterEqual(by, t0 + 75 - 1)
        self.assertLessEqual(by, time.time() + 75 + 1)

    def test_the_signed_envelope_itself_is_not_touched(self):
        seen, envelope, original, _t0 = self._post(30)
        self.assertEqual(envelope, original, "the caller's envelope must not be mutated")
        stripped = {k: v for k, v in seen["payload"].items() if k != "_relay_deliver_by"}
        self.assertEqual(stripped, original)


class TestOfflinePeerOverARealRelay(unittest.TestCase):
    """Real relay (uvicorn), real listeners, real receivers and senders."""

    def setUp(self) -> None:
        import remote_trigger_sender as rts
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.relay = _RelayServer()
        self.dead_url = f"http://127.0.0.1:{_free_port()}"  # connection refused: relay is the only path
        self._patches = [
            mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.root / "home"),
                                         "CORVIN_A2A_ATTESTATION_DISABLED": "1"}),
            mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
            mock.patch("a2a_friendship.get_my_relay_url", return_value=self.relay.url),
            mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None),
            mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None),
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)),
            mock.patch.object(a2a_relay, "_DELIVER_BY_GRACE_S", 0.3),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop(rts._REMOTE_ENDPOINTS_ENV, None)
        self.loop = asyncio.new_event_loop()
        self.loop_thread = threading.Thread(target=self.loop.run_forever, daemon=True)
        self.loop_thread.start()
        self.listeners: list[a2a_relay.RelayListener] = []

    def tearDown(self) -> None:
        for lst in self.listeners:
            self.loop.call_soon_threadsafe(lst.stop)
        time.sleep(0.3)
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.loop_thread.join(5)
        self.relay.stop()
        for p in reversed(self._patches):
            p.stop()
        self._tmp.cleanup()

    def _listen(self, inst: _Instance) -> a2a_relay.RelayListener:
        lst = a2a_relay.RelayListener(relay_url=self.relay.url, receiver=inst.receiver,
                                      origins_dir=inst.origins, instance_id=inst.iid)
        self.listeners.append(lst)
        asyncio.run_coroutine_threadsafe(lst.run_forever(reconnect_backoff_s=0.5), self.loop)
        deadline = time.time() + 15
        while lst.status.get("registered", 0) < 1 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(lst.status.get("registered"), 1, lst.status)
        return lst

    def _take_offline(self, lst: a2a_relay.RelayListener, kid: str) -> None:
        """Peer goes away AFTER registering its kid, so the relay queues instead of dropping."""
        self.loop.call_soon_threadsafe(lst.stop)
        slot = self.relay.state._slots[kid]
        deadline = time.time() + 10
        while slot.connection_ids and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(slot.connection_ids, "peer must be offline at the relay")

    def _audit_events(self, inst: _Instance) -> list[str]:
        return [c.args[1] for c in inst.receiver._inst_forge_se.write_event.call_args_list]

    def test_a_task_queued_past_its_senders_patience_is_not_run(self):
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        self._take_offline(self._listen(b), kid)

        res = a.sender().send(kid, "run this while I am away", timeout_s=2)
        self.assertFalse(res.ok, "no peer answered: the sender must report a failure")
        time.sleep(0.6)                      # deadline + grace are now behind us
        self.assertEqual(b.got, [])
        queued = [q.payload.get("task_id", "") for q in self.relay.state._slots[kid].queue]
        self.assertGreaterEqual(len(queued), 1, "the relay still holds it")

        self._listen(b)                      # peer is back: the relay flushes its queue
        deadline = time.time() + 8
        while "A2A.relay_expired_dropped" not in self._audit_events(b) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(b.got, [], "the expired task must never reach the receiver")
        self.assertIn("A2A.relay_expired_dropped", self._audit_events(b))

    def test_a_listener_that_does_not_know_the_stamp_still_runs_the_task(self):
        """Compatibility: a peer on an older build never strips ``_relay_deliver_by``. The stamp then
        reaches ``receive`` inside the envelope dict, which must neither fail validation nor the
        signature check (both read known keys only)."""
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        with mock.patch.object(a2a_relay, "_pop_deliver_by", lambda payload: None):
            self._listen(b)
            res = a.sender().send(kid, "hello old peer", timeout_s=20)
        self.assertTrue(res.ok, res)
        self.assertEqual(len(b.got), 1)
        self.assertIn("_relay_deliver_by", b.got[0], "the stamp really reached the receiver")

    def test_positive_control_a_task_queued_inside_its_patience_still_runs(self):
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        self._take_offline(self._listen(b), kid)

        threading.Timer(1.0, lambda: self._listen(b)).start()   # peer returns after ~1 s
        res = a.sender().send(kid, "run this when you are back", timeout_s=20)
        self.assertTrue(res.ok, res)
        self.assertEqual(len(b.got), 1)
        self.assertNotIn("A2A.relay_expired_dropped", self._audit_events(b))


if __name__ == "__main__":
    unittest.main()

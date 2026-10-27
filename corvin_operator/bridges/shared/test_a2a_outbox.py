"""T-0252 / ADR-2300 step 2 — the sender outbox: a message to an offline peer is kept and delivered later.

Layers, from cheap to real:

1. ``TestOutboxStore``      — the store: bounds, one-shot closing, text deleted on close, erasure.
2. ``TestDrainDecisions``   — ``a2a_outbox.drain`` with a scripted sender: WHEN it resends, and above all
                              when it must NOT (the peer already has the task, or cannot say).
3. ``TestManagerWiring``    — ``ConnectivityManager.start()`` really starts the outbox task and the task
                              really calls ``drain`` (a mechanism nothing calls does not exist).
4. ``TestOfflinePeerEndToEnd`` — real relay, real listeners, real senders: the exact T-0252 story. The peer
                              is down, the sender times out, the relay still queues the copy; the peer
                              returns, DROPS the stale copy (step 1), is asked for the task id, has never
                              heard of it, and gets exactly one fresh delivery.

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_outbox.py
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import a2a_feed
import a2a_outbox
import a2a_relay
from test_a2a_relay_hardening import _Instance, _RelayServer, _free_port, _pair

PEER = "kid-peer-1"


def _result(*, ok=False, category="", maybe=False, status="error"):
    return SimpleNamespace(ok=ok, error_category=category, maybe_delivered=maybe, status=status,
                           task_id="x", data={}, attachments=[], duration_ms=1, error_detail=None)


class _ScriptedSender:
    """Records every call; answers from scripted values."""

    def __init__(self, *, stage=None, send=None):
        self.stage = stage or {"reachable": True, "supported": True, "stage": "unknown",
                               "stage_seq": 0, "reason": "", "via": "direct"}
        self.send_result = send or _result(ok=True, status="ok")
        self.status_calls: list[tuple] = []
        self.send_calls: list[dict] = []

    def task_status(self, endpoint_id, task_id, timeout_s=8):
        self.status_calls.append((endpoint_id, task_id))
        return dict(self.stage)

    def send(self, endpoint_id, text, **kw):
        self.send_calls.append({"endpoint_id": endpoint_id, "text": text, **kw})
        return self.send_result


class _HomeCase(unittest.TestCase):
    """A real, sandboxed CORVIN_HOME with the real feed and outbox files."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        p = mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.root / "home"),
                                         "CORVIN_TENANT_ID": "_default"})
        p.start()
        self.addCleanup(p.stop)
        self.addCleanup(self._tmp.cleanup)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        a2a_outbox._reset_for_tests()
        self.audits: list[tuple[str, str, dict]] = []

    def audit(self, event, severity, details):
        self.audits.append((event, severity, details))

    def keep(self, task="t-1", *, maybe=False, deadline=3600, now=None, text="hello later"):
        e = a2a_outbox.enqueue(PEER, task, text, deadline_s=deadline, maybe_delivered=maybe, now=now)
        self.assertIsNotNone(e)
        return e

    def feed_rows(self):
        return [r for r in a2a_feed.read(limit=100, tenant_id="_default")]


class TestOutboxStore(_HomeCase):
    def test_enqueue_validates_and_bounds(self):
        for bad in (dict(text="  "), dict(text="x" * (a2a_outbox.MAX_TEXT_BYTES + 1)),
                    dict(deadline=0), dict(deadline=-5), dict(deadline=True), dict(deadline="soon")):
            with self.subTest(bad):
                kw = {"text": "hi", "deadline": 60, **bad}
                self.assertIsNone(a2a_outbox.enqueue(PEER, "t-bad", kw["text"], deadline_s=kw["deadline"],
                                                     maybe_delivered=False))
        self.assertIsNone(a2a_outbox.enqueue("", "t", "hi", deadline_s=60, maybe_delivered=False))
        self.assertIsNone(a2a_outbox.enqueue(PEER, "", "hi", deadline_s=60, maybe_delivered=False))
        first = self.keep("t-dup")
        self.assertIsNone(a2a_outbox.enqueue(PEER, "t-dup", "again", deadline_s=60, maybe_delivered=False),
                          "a task id is kept once")
        self.assertEqual(first["state"], "waiting")

    def test_deadline_is_capped_and_the_outbox_is_bounded(self):
        e = a2a_outbox.enqueue(PEER, "t-cap", "hi", deadline_s=10 * 365 * 86400, maybe_delivered=False, now=1000.0)
        self.assertEqual(e["deadline_at"], 1000.0 + a2a_outbox.MAX_DEADLINE_S)
        with mock.patch.object(a2a_outbox, "MAX_WAITING", 2):
            self.keep("t-1")
            self.assertIsNone(a2a_outbox.enqueue(PEER, "t-3", "hi", deadline_s=60, maybe_delivered=False))

    def test_closing_is_one_shot_and_deletes_the_text(self):
        self.keep("t-1")
        closed = a2a_outbox.close("t-1", "sent")
        self.assertEqual((closed["state"], closed["text"]), ("sent", ""))
        self.assertIsNone(a2a_outbox.close("t-1", "cancelled"), "a closed entry is immutable")
        self.assertIsNone(a2a_outbox.update("t-1", attempts=9))
        self.assertEqual(a2a_outbox.waiting(), [])
        on_disk = (a2a_feed.feed_dir("_default") / a2a_outbox.OUTBOX_FILE).read_text("utf-8")
        self.assertNotIn("hello later", on_disk.splitlines()[-1], "no text at rest after closing")

    def test_waiting_survives_a_restart_and_old_closed_entries_are_forgotten(self):
        self.keep("t-live")
        self.keep("t-old")
        a2a_outbox.close("t-old", "sent", now=time.time() - 30 * 86400)
        a2a_outbox._reset_for_tests()                      # a new process
        self.assertEqual([e["task_id"] for e in a2a_outbox.waiting()], ["t-live"])
        self.assertIsNone(a2a_outbox.get("t-old"))

    def test_waiting_view_has_ids_and_deadline_never_text(self):
        self.keep("t-1", text="a private sentence")
        view = a2a_outbox.waiting_view(PEER)
        self.assertEqual(set(view["t-1"]), {"deadline_at", "attempts"})
        self.assertNotIn("private", json.dumps(view))
        self.assertEqual(a2a_outbox.waiting_view("some-other-peer"), {})

    def test_erasure_removes_one_peer_and_clear_removes_all(self):
        self.keep("t-1")
        a2a_outbox.enqueue("kid-other", "t-2", "bye", deadline_s=60, maybe_delivered=False)
        self.assertEqual(a2a_outbox.erase_peer(PEER), 1)
        self.assertEqual([e["task_id"] for e in a2a_outbox.waiting()], ["t-2"])
        a2a_outbox._reset_for_tests()                      # the file really lost it, not just memory
        self.assertEqual([e["task_id"] for e in a2a_outbox.waiting()], ["t-2"])
        self.assertEqual(a2a_outbox.clear(), 1)
        self.assertEqual(a2a_outbox.waiting(), [])

    def test_feed_erasure_reaches_the_outbox(self):
        """GDPR Art. 17 through the feed's own door: ``a2a_feed.erase_peer`` / ``clear``."""
        self.keep("t-1")
        removed, _blobs = a2a_feed.erase_peer(PEER, "_default")
        self.assertEqual(removed, 1)
        self.assertEqual(a2a_outbox.waiting(), [])
        self.keep("t-2")
        a2a_feed.clear("_default")
        self.assertEqual(a2a_outbox.waiting(), [])

    def test_backoff_doubles_to_a_ceiling(self):
        self.assertEqual([a2a_outbox.backoff_s(n) for n in range(7)], [30, 60, 120, 240, 300, 300, 300])


class TestDrainDecisions(_HomeCase):
    def drain(self, sender, *, active=False, now=None):
        return a2a_outbox.drain(sender, audit=self.audit, is_active=lambda ep: active, now=now)

    def test_a_provably_undelivered_message_is_resent_with_the_same_task_id_and_closed_when_sent(self):
        self.keep("t-1", maybe=False)
        s = _ScriptedSender()
        counts = self.drain(s, active=True)
        self.assertEqual(s.status_calls, [], "provable non-delivery needs no stage question")
        self.assertEqual(len(s.send_calls), 1)
        call = s.send_calls[0]
        self.assertEqual((call["endpoint_id"], call["text"], call["task_id"]), (PEER, "hello later", "t-1"))
        self.assertTrue(call["outbox_retry"] and call["feed_task_recorded"])
        self.assertEqual(counts["sent"], 1)
        self.assertEqual(a2a_outbox.get("t-1")["state"], "sent")
        self.assertEqual(a2a_outbox.get("t-1")["text"], "")
        self.assertEqual([a[0] for a in self.audits], ["A2A.outbox_retried", "A2A.outbox_closed"])

    def test_still_unreachable_keeps_the_entry_with_a_longer_backoff_and_writes_no_feed_row(self):
        self.keep("t-1", maybe=False)
        s = _ScriptedSender(send=_result(category="unreachable", maybe=False))
        t = time.time()
        self.drain(s, active=True, now=t)
        e = a2a_outbox.get("t-1")
        self.assertEqual((e["state"], e["attempts"], e["maybe_delivered"]), ("waiting", 1, False))
        self.assertAlmostEqual(e["next_at"], t + a2a_outbox.backoff_s(1), delta=1)
        self.assertEqual(self.feed_rows(), [], "the failed attempt is not a failure in the feed")

    def test_two_attempts_are_never_closer_than_the_minimum_gap_even_for_an_active_peer(self):
        self.keep("t-1")
        s = _ScriptedSender(send=_result(category="unreachable", maybe=False))
        t = time.time()
        self.drain(s, active=True, now=t)
        self.drain(s, active=True, now=t + 1)
        self.assertEqual(len(s.send_calls), 1)
        self.drain(s, active=True, now=t + a2a_outbox.MIN_ATTEMPT_GAP_S + 1)
        self.assertEqual(len(s.send_calls), 2)

    def test_an_inactive_peer_is_left_alone_until_its_backoff_elapsed(self):
        self.keep("t-1", now=1000.0)
        s = _ScriptedSender()
        self.drain(s, active=False, now=1000.0 + 5)
        self.assertEqual(s.send_calls, [])
        self.drain(s, active=False, now=1000.0 + a2a_outbox.BACKOFF_MIN_S + 1)
        self.assertEqual(len(s.send_calls), 1)

    def test_an_answer_timeout_asks_the_peer_first_and_resends_only_if_it_never_heard_of_the_task(self):
        self.keep("t-1", maybe=True, deadline=20000, now=time.time() - 5000)  # relay hold long over
        s = _ScriptedSender()                                  # reachable, supported, stage unknown
        self.drain(s, active=True)
        self.assertEqual(s.status_calls, [(PEER, "t-1")])
        self.assertEqual(len(s.send_calls), 1)

    def test_a_peer_that_already_has_the_task_is_never_sent_it_again(self):
        self.keep("t-1", maybe=True, deadline=20000, now=time.time() - 5000)  # relay hold long over
        s = _ScriptedSender(stage={"reachable": True, "supported": True, "stage": "processing",
                                   "stage_seq": 4, "reason": "", "via": "direct"})
        counts = self.drain(s, active=True)
        self.assertEqual(s.send_calls, [], "resending would run the task twice")
        self.assertEqual(counts["delivered"], 1)
        self.assertEqual(a2a_outbox.get("t-1")["state"], "delivered")
        latest = a2a_feed.latest_stages(task_ids=["t-1"], tenant_id="_default")
        self.assertEqual(latest["t-1"]["stage"], "processing", "what the peer reported is shown")
        self.assertEqual(self.feed_rows()[-1]["status"], "unconfirmed")

    def test_a_peer_that_cannot_answer_a_stage_query_is_never_retried(self):
        self.keep("t-1", maybe=True, deadline=20000, now=time.time() - 5000)  # relay hold long over
        s = _ScriptedSender(stage={"reachable": True, "supported": False, "stage": "unknown",
                                   "stage_seq": 0, "reason": "", "via": "direct"})
        counts = self.drain(s, active=True)
        self.assertEqual(s.send_calls, [])
        self.assertEqual((counts["unconfirmed"], a2a_outbox.get("t-1")["state"]), (1, "unconfirmed"))

    def test_an_offline_peer_cannot_be_asked_so_nothing_is_sent_and_the_entry_waits(self):
        self.keep("t-1", maybe=True, deadline=20000, now=time.time() - 5000)  # relay hold long over
        s = _ScriptedSender(stage={"reachable": False, "supported": False, "stage": "unknown",
                                   "stage_seq": 0, "reason": "", "via": "direct"})
        self.drain(s, active=True)
        self.assertEqual(s.send_calls, [])
        self.assertEqual(a2a_outbox.get("t-1")["state"], "waiting")

    def test_an_expired_entry_is_closed_unsent_and_the_feed_says_so(self):
        self.keep("t-1", now=1000.0, deadline=60)
        s = _ScriptedSender()
        counts = self.drain(s, active=True, now=1000.0 + 61)
        self.assertEqual((s.send_calls, s.status_calls), ([], []))
        self.assertEqual((counts["expired"], a2a_outbox.get("t-1")["state"]), (1, "expired"))
        row = self.feed_rows()[-1]
        self.assertEqual((row["status"], row["task_id"]), ("error", "t-1"))
        self.assertIn("deadline", row["error"])

    def test_a_peer_that_answers_and_refuses_closes_the_entry_as_failed(self):
        self.keep("t-1")
        s = _ScriptedSender(send=_result(category="rejected", maybe=False, status="rejected"))
        counts = self.drain(s, active=True)
        self.assertEqual((counts["failed"], a2a_outbox.get("t-1")["state"]), (1, "failed"))

    def test_a_crash_during_a_send_leaves_the_entry_marked_maybe_delivered(self):
        self.keep("t-1", maybe=False)

        class Boom(_ScriptedSender):
            def send(self, *a, **k):
                raise RuntimeError("process died mid-send")
        self.drain(Boom(), active=True)
        e = a2a_outbox.get("t-1")
        self.assertEqual(e["state"], "waiting")
        self.assertTrue(e["maybe_delivered"], "the next pass must ask the peer first")

    def test_cancel_withdraws_a_waiting_entry_and_the_next_drain_ignores_it(self):
        self.keep("t-1")
        self.assertEqual(a2a_outbox.cancel("t-1")["state"], "cancelled")
        s = _ScriptedSender()
        self.drain(s, active=True)
        self.assertEqual(s.send_calls, [])
        self.assertIsNone(a2a_outbox.cancel("t-1"), "cancelling twice answers None")

    def test_an_entry_being_sent_cannot_be_cancelled(self):
        self.keep("t-1")
        seen = {}

        class Probe(_ScriptedSender):
            def send(self_inner, *a, **k):
                seen["cancel_during_send"] = a2a_outbox.cancel("t-1")
                return super().send(*a, **k)
        self.drain(Probe(), active=True)
        self.assertIsNone(seen["cancel_during_send"])
        self.assertEqual(a2a_outbox.get("t-1")["state"], "sent")


class TestAuditFieldsSurviveTheRealWriter(_HomeCase):
    """An event whose fields are not registered is still written, but with its details cut to the
    generic floor (names listed under ``_dropped_fields``). Registration is only worth something if
    the real writer keeps what the emitters send — so ask it."""

    def _write(self, event, details):
        import sys
        forge_pkg = str(Path(__file__).resolve().parents[2] / "forge")
        if forge_pkg not in sys.path:
            sys.path.insert(0, forge_pkg)
        from forge import security_events as se
        chain = self.root / "chain" / "audit.jsonl"
        se.write_event(chain, event, severity="INFO", tool="", run_id="", details=details, hash_chain=True)
        return json.loads(chain.read_text("utf-8").splitlines()[-1])

    def test_every_new_event_keeps_the_fields_its_emitter_sends(self):
        cases = {
            "A2A.outbox_enqueued": {"endpoint_id": PEER, "task_id": "t-1", "reason": "timeout_transport",
                                    "deadline_s": 86400},
            "A2A.outbox_retried": {"endpoint_id": PEER, "task_id": "t-1", "attempts": 2},
            "A2A.outbox_closed": {"endpoint_id": PEER, "task_id": "t-1", "state": "sent", "attempts": 2},
            "A2A.relay_expired_dropped": {"task_id": "t-1", "late_s": 95},
        }
        for event, details in cases.items():
            with self.subTest(event):
                rec = self._write(event, details)
                kept = rec.get("details", rec)
                self.assertNotIn("_dropped_fields", kept, kept)
                for k, v in details.items():
                    self.assertEqual(kept.get(k), v, (event, k, kept))


class TestManagerWiring(unittest.TestCase):
    def test_start_launches_the_outbox_task_and_it_calls_drain_with_the_managers_view(self):
        from core.a2a import a2a_connectivity as conn

        calls: list[dict] = []
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for d in ("origins", "endpoints", "pending"):
            (root / d).mkdir()

        async def idle_main_loop(self):                    # the presence loop is not under test
            while not self._stop:
                await asyncio.sleep(0.05)

        def fake_drain(sender, **kw):
            calls.append(kw)
            return {}

        async def scenario():
            mgr = conn.ConnectivityManager(receiver=None, origins_dir=root / "origins",
                                           endpoints_dir=root / "endpoints", pending_dir=root / "pending",
                                           tenant_id="_default")
            mgr._known_states["kid-live"] = "active:direct"
            with mock.patch.object(conn.ConnectivityManager, "_run", idle_main_loop), \
                    mock.patch.object(conn, "OUTBOX_TICK_S", 0.05), \
                    mock.patch.object(a2a_outbox, "waiting", lambda tenant_id=None, endpoint_id=None: [{"task_id": "t"}]), \
                    mock.patch.object(a2a_outbox, "drain", fake_drain):
                mgr.start()
                deadline = time.time() + 5
                while not calls and time.time() < deadline:
                    await asyncio.sleep(0.05)
                await mgr.stop()
            return mgr

        mgr = asyncio.run(scenario())
        self.assertTrue(calls, "ConnectivityManager.start() must drive a2a_outbox.drain")
        self.assertEqual(calls[0]["tenant_id"], "_default")
        self.assertTrue(calls[0]["is_active"]("kid-live"))
        self.assertFalse(calls[0]["is_active"]("kid-unknown"))
        self.assertIsNotNone(mgr._outbox_task)


class TestOfflinePeerEndToEnd(unittest.TestCase):
    """Real relay (uvicorn), real listeners, real receivers and senders, real feed and outbox files."""

    def setUp(self) -> None:
        import remote_trigger_sender as rts
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.relay = _RelayServer()
        self.dead_url = f"http://127.0.0.1:{_free_port()}"
        self._patches = [
            mock.patch.dict(os.environ, {"CORVIN_HOME": str(self.root / "home"),
                                         "CORVIN_TENANT_ID": "_default",
                                         "CORVIN_A2A_ATTESTATION_DISABLED": "1"}),
            mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
            mock.patch("a2a_friendship.get_my_relay_url", return_value=self.relay.url),
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)),
            mock.patch.object(a2a_relay, "_DELIVER_BY_GRACE_S", 0.3),
        ]
        for p in self._patches:
            p.start()
        os.environ.pop(rts._REMOTE_ENDPOINTS_ENV, None)
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        a2a_outbox._reset_for_tests()
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

    def _listen(self, inst):
        lst = a2a_relay.RelayListener(relay_url=self.relay.url, receiver=inst.receiver,
                                      origins_dir=inst.origins, instance_id=inst.iid)
        self.listeners.append(lst)
        asyncio.run_coroutine_threadsafe(lst.run_forever(reconnect_backoff_s=0.5), self.loop)
        deadline = time.time() + 15
        while lst.status.get("registered", 0) < 1 and time.time() < deadline:
            time.sleep(0.05)
        self.assertEqual(lst.status.get("registered"), 1, lst.status)
        return lst

    def _take_offline(self, lst, kid):
        self.loop.call_soon_threadsafe(lst.stop)
        slot = self.relay.state._slots[kid]
        deadline = time.time() + 10
        while slot.connection_ids and time.time() < deadline:
            time.sleep(0.05)
        self.assertFalse(slot.connection_ids)

    def _events(self, inst):
        return [c.args[1] for c in inst.receiver._inst_forge_se.write_event.call_args_list]

    def test_a_second_attempt_for_the_same_task_id_is_not_refused_by_the_relay(self):
        """The relay pins a reply slot to the credential of its first registrant. One slot per task id
        therefore refused every second attempt (``auth_key_mismatch``) for 60 s; one per attempt does not."""
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        self._listen(b)
        sender = a.sender()
        first = sender.send(kid, "first", task_id="same-task-id", timeout_s=20)
        second = sender.send(kid, "second", task_id="same-task-id", timeout_s=20)
        self.assertTrue(first.ok, first)
        self.assertTrue(second.ok, (second.error_category, second.error_detail))
        self.assertEqual(len(b.got), 2)

    def test_the_message_to_a_peer_that_was_down_is_delivered_exactly_once_when_it_returns(self):
        a, b = _Instance(self.root, "a"), _Instance(self.root, "b")
        kid = _pair(a, b, self.dead_url)
        sender = a.sender()
        self._take_offline(self._listen(b), kid)

        # 1. the peer is down: the sender waits, gives up, and KEEPS the message
        res = sender.send(kid, "please read this when you are back", timeout_s=2, outbox_deadline_s=3600)
        self.assertFalse(res.ok)
        waiting = a2a_outbox.waiting()
        self.assertEqual(len(waiting), 1)
        task_id = waiting[0]["task_id"]
        self.assertTrue(waiting[0]["maybe_delivered"], "an answer timeout may have delivered")
        self.assertNotIn("error", [r["status"] for r in a2a_feed.read(limit=50, tenant_id="_default")
                                   if r["kind"] == "response"],
                         "no failure row while the message waits")
        sender_events = [c.args[1] for c in sender._inst_forge_se.write_event.call_args_list]
        self.assertEqual(sender_events.count("A2A.outbox_enqueued"), 1)
        time.sleep(0.6)

        # 2. the peer returns: the relay flushes the OLD copy, which the peer DROPS (step 1)
        self._listen(b)
        deadline = time.time() + 8
        while "A2A.relay_expired_dropped" not in self._events(b) and time.time() < deadline:
            time.sleep(0.1)
        self.assertEqual(b.got, [], "the stale queued copy must not run")

        # 3. the drain asks the peer for the task id, hears "never seen", and delivers ONE fresh copy
        drain_audits: list[str] = []
        attempts: list = []
        real_send = sender.send

        def spy(*args, **kw):
            r = real_send(*args, **kw)
            attempts.append(r)
            return r
        sender.send = spy
        # the first attempt queued a relay copy: the retry waits out its deliver-by + grace
        later = time.time() + a2a_outbox._send_budget_s() + a2a_outbox.RELAY_GRACE_S + 1
        counts = a2a_outbox.drain(sender, audit=lambda e, s, d: drain_audits.append(e),
                                  is_active=lambda ep: True, now=later)
        self.assertEqual(counts["resent"], 1, counts)
        self.assertEqual(counts["sent"], 1, (counts, [(r.ok, r.status, r.error_category, r.error_detail,
                                                       r.maybe_delivered) for r in attempts]))
        self.assertEqual(len(b.got), 1, "exactly one delivery")
        self.assertEqual(b.got[0]["task_id"], task_id, "same task id")
        self.assertEqual(a2a_outbox.get(task_id)["state"], "sent")
        self.assertEqual(drain_audits, ["A2A.outbox_retried", "A2A.outbox_closed"])
        replies = [r for r in a2a_feed.read(limit=50, tenant_id="_default")
                   if r["kind"] == "response" and r["task_id"] == task_id]
        self.assertTrue(replies, "the peer's answer reached the feed")
        self.assertNotIn(replies[-1]["status"], ("error", "unconfirmed", "rejected", "timeout"), replies[-1])
        self.assertFalse(replies[-1].get("error"), replies[-1])

        # 4. nothing is left to do: another pass sends nothing
        again = a2a_outbox.drain(sender, is_active=lambda ep: True)
        self.assertEqual(again["checked"], 0)
        self.assertEqual(len(b.got), 1)


if __name__ == "__main__":
    unittest.main()

"""Review findings of the A2A outbox (ADR-2300): a drain pass must not send what was cancelled or
erased meanwhile, a closed entry's text must leave the disk, the drain must read the tenant the
senders write to, and a retry must wait for the previous copy's relay deadline.

Docstring note: the cancel/erase race is a unit-level race between two threads on the module's own
lock (the public functions the console and the L36 handler call), driven with a real blocking sender.
"""
from __future__ import annotations

import asyncio
import os
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_feed
import a2a_outbox
from test_a2a_outbox import PEER, _HomeCase, _result, _ScriptedSender


class _BlockingSender(_ScriptedSender):
    """send() of task A blocks until released; every send is recorded."""

    def __init__(self):
        super().__init__()
        self.in_a = threading.Event()
        self.release = threading.Event()

    def send(self, endpoint_id, text, **kw):
        self.send_calls.append({"endpoint_id": endpoint_id, "text": text, **kw})
        if kw.get("task_id") == "A":
            self.in_a.set()
            self.release.wait(10)
        return self.send_result


class TestCancelAndEraseDuringAPass(_HomeCase):
    def _run_pass_with(self, mutate):
        t0 = time.time() - 100
        a2a_outbox.enqueue(PEER, "A", "first", deadline_s=3600, maybe_delivered=False, now=t0)
        a2a_outbox.enqueue(PEER, "B", "SECRET-B", deadline_s=3600, maybe_delivered=False, now=t0 + 1)
        a2a_outbox.enqueue("peer-3", "C", "ERASED-C", deadline_s=3600, maybe_delivered=False, now=t0 + 2)
        s = _BlockingSender()
        th = threading.Thread(target=lambda: a2a_outbox.drain(s, tenant_id="_default"))
        th.start()
        self.assertTrue(s.in_a.wait(5))
        mutate()
        s.release.set()
        th.join(10)
        return [c["task_id"] for c in s.send_calls]

    def test_an_entry_cancelled_while_an_earlier_send_blocks_is_not_sent(self):
        sent = self._run_pass_with(lambda: self.assertIsNotNone(a2a_outbox.cancel("B", tenant_id="_default")))
        self.assertNotIn("B", sent)

    def test_an_entry_erased_while_an_earlier_send_blocks_is_not_sent(self):
        sent = self._run_pass_with(lambda: a2a_outbox.erase_peer("peer-3", tenant_id="_default"))
        self.assertNotIn("C", sent)
        self.assertEqual(sent, ["A", "B"])


class TestClosedTextLeavesTheDisk(_HomeCase):
    def test_cancel_removes_the_text_from_the_file(self):
        self.keep("T9", text="PRIVATE text the operator withdrew")
        a2a_outbox.cancel("T9", tenant_id="_default")
        raw = (a2a_feed.feed_dir("_default") / "outbox.jsonl").read_text()
        self.assertNotIn("PRIVATE text", raw)
        self.assertEqual(a2a_outbox.get("T9")["state"], "cancelled")

    def test_a_sent_entry_and_a_restart_keep_no_text_either(self):
        self.keep("T8", text="DELIVERED-TEXT")
        a2a_outbox.update("T8", attempts=1)
        a2a_outbox.close("T8", "sent")
        raw = (a2a_feed.feed_dir("_default") / "outbox.jsonl").read_text()
        self.assertNotIn("DELIVERED-TEXT", raw)
        a2a_outbox._reset_for_tests()
        self.assertEqual(a2a_outbox.get("T8")["state"], "sent")


class TestHostTenantOutbox(unittest.TestCase):
    def test_the_manager_drains_the_outbox_senders_fill_on_a_non_default_host_tenant(self):
        """Real enqueue (tenant None, as RemoteTriggerSender does) -> the ConnectivityManager's own
        task drains it -> the entry expires and its text is gone, on CORVIN_TENANT_ID=acme."""
        import tempfile
        from core.a2a import a2a_connectivity as conn

        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        for d in ("origins", "endpoints", "pending"):
            (root / d).mkdir()
        env = {"CORVIN_HOME": str(root / "home"), "CORVIN_TENANT_ID": "acme"}
        with mock.patch.dict(os.environ, env):
            os.environ.pop("CORVIN_A2A_FEED_DIR", None)
            a2a_outbox._reset_for_tests()
            self.addCleanup(a2a_outbox._reset_for_tests)
            a2a_outbox.enqueue(PEER, "TX", "hello acme peer", deadline_s=1, maybe_delivered=False,
                               now=time.time() - 50)

            async def idle_main_loop(self):
                while not self._stop:
                    await asyncio.sleep(0.05)

            async def scenario():
                mgr = conn.ConnectivityManager(receiver=None, origins_dir=root / "origins",
                                               endpoints_dir=root / "endpoints",
                                               pending_dir=root / "pending")
                with mock.patch.object(conn.ConnectivityManager, "_run", idle_main_loop), \
                        mock.patch.object(conn, "OUTBOX_TICK_S", 0.05):
                    mgr.start()
                    end = time.time() + 5
                    while a2a_outbox.get("TX")["state"] == "waiting" and time.time() < end:
                        await asyncio.sleep(0.05)
                    await mgr.stop()
                return mgr

            mgr = asyncio.run(scenario())
            self.assertEqual(mgr._tenant_id, "acme")
            self.assertEqual(a2a_outbox.get("TX")["state"], "expired")
            raw = (a2a_feed.feed_dir(None) / "outbox.jsonl").read_text()
            self.assertNotIn("hello acme peer", raw)
            self.assertIn("acme", str(a2a_feed.feed_dir(None)))


class _SlowTimeoutSender(_ScriptedSender):
    def send(self, endpoint_id, text, **kw):
        self.send_calls.append({"endpoint_id": endpoint_id, "text": text, **kw})
        time.sleep(0.2)
        return _result(category="timeout_transport", maybe=True)


class TestRetryWaitsForTheStaleCopy(_HomeCase):
    def test_a_retry_never_starts_before_the_previous_copys_relay_deadline_plus_grace(self):
        """The previous copy may sit at the relay until send start + send budget (its deliver-by)
        + 30 s grace; a retry (peer looks active) before that could run the task twice."""
        now = time.time() + 1000
        self.keep("R1", now=now - 100)
        s = _SlowTimeoutSender(stage={"reachable": True, "supported": True, "stage": "unknown"})
        a2a_outbox.drain(s, tenant_id="_default", now=now, is_active=lambda ep: True)
        self.assertEqual(len(s.send_calls), 1)
        import remote_trigger_sender as rts
        budget = max(rts._DEFAULT_TIMEOUT_S, rts._DEFAULT_TTL_S + rts._TIMEOUT_MARGIN_S)
        horizon = now + budget + 30.0     # deliver-by + the relay's _DELIVER_BY_GRACE_S
        e = a2a_outbox.get("R1")
        self.assertGreaterEqual(e["next_at"], horizon)
        # even an active peer, long after the 15 s minimum gap, is left alone until the horizon
        a2a_outbox.drain(s, tenant_id="_default", now=now + horizon - now - 1, is_active=lambda ep: True)
        self.assertEqual(len(s.send_calls), 1)
        a2a_outbox.drain(s, tenant_id="_default", now=horizon + 1, is_active=lambda ep: True)
        self.assertEqual(len(s.send_calls), 2)


if __name__ == "__main__":
    unittest.main()

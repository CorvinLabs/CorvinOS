"""Round-two review of the A2A outbox (ADR-2300): timestamps taken at the real send start, the
first failed attempt's relay-copy window, a loud text-removal failure, and route-dir isolation."""
from __future__ import annotations

import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_outbox
import session_state
from test_a2a_outbox import PEER, _HomeCase, _result


class _Clock:
    def __init__(self, t0):
        self.t = t0

    def time(self):
        return self.t

    def sleep(self, s):
        self.t += s


class _SlowSender:
    """Every send blocks 75 s of fake wall clock and ends in a transport timeout."""

    def __init__(self, clock):
        self.clock, self.sends = clock, []

    def task_status(self, ep, tid, timeout_s=8):
        return {"reachable": True, "supported": True, "stage": "unknown", "stage_seq": 0,
                "reason": "", "via": "direct"}

    def send(self, ep, text, **kw):
        self.sends.append((kw["task_id"], self.clock.t))
        self.clock.t += 75
        return _result(category="timeout_transport", maybe=True)


class TestStampsUseRealSendStart(_HomeCase):
    def test_later_entry_in_a_slow_pass_holds_until_its_own_relay_copy_dies(self):
        clock = _Clock(1_000_000.0)
        fake_time = mock.Mock(time=clock.time, sleep=clock.sleep)
        t0 = clock.t
        with mock.patch.object(a2a_outbox, "time", fake_time):
            for i in "ABCD":
                a2a_outbox.enqueue(PEER, i, "hi " + i, deadline_s=7200, maybe_delivered=False,
                                   now=t0 - 100)
            s = _SlowSender(clock)
            a2a_outbox.drain(s)                      # pass start t0; D is sent at t0 + 225
            d_sent = dict(s.sends)["D"]
            self.assertEqual(d_sent, t0 + 225)
            budget = a2a_outbox._send_budget_s()
            copy_dies = d_sent + budget + a2a_outbox.RELAY_GRACE_S
            d = a2a_outbox.get("D")
            self.assertGreaterEqual(d["stale_until"], copy_dies)
            self.assertGreaterEqual(d["last_attempt_at"], d_sent)
            # next pass 6 s later: D's copy is still queued, so D must not be resent
            clock.t += 6
            for x in "ABC":
                a2a_outbox.cancel(x)
            before = len(s.sends)
            a2a_outbox.drain(s)
            self.assertEqual(len(s.sends), before)


class TestFirstFailedAttemptHoldsTheRelayCopy(_HomeCase):
    def test_enqueue_after_a_timeout_waits_out_the_queued_copy(self):
        now = time.time()
        e = a2a_outbox.enqueue(PEER, "F1", "x", deadline_s=3600, maybe_delivered=True, now=now)
        self.assertGreaterEqual(e["stale_until"], now + a2a_outbox.RELAY_GRACE_S)
        # an ACTIVE peer ignores next_at, but must still not be asked/resent inside the grace
        s = mock.Mock()
        a2a_outbox.drain(s, is_active=lambda ep: True, now=now + 20)
        s.send.assert_not_called()
        s.task_status.assert_not_called()

    def test_provable_miss_has_no_hold(self):
        e = a2a_outbox.enqueue(PEER, "F2", "x", deadline_s=3600, maybe_delivered=False)
        self.assertFalse(e.get("stale_until"))


class TestCloseFailureIsLoud(_HomeCase):
    def test_rewrite_failure_is_retried_once_and_logged(self):
        self.keep("C1", text="SECRET")
        calls = {"n": 0}
        real = a2a_outbox._rewrite

        def flaky(root, table):
            calls["n"] += 1
            if calls["n"] == 1:
                raise OSError("disk")
            return real(root, table)

        with mock.patch.object(a2a_outbox, "_rewrite", flaky):
            a2a_outbox.close("C1", "sent")
        self.assertEqual(calls["n"], 2)
        raw = (a2a_outbox._root() / a2a_outbox.OUTBOX_FILE).read_text()
        self.assertNotIn("SECRET", raw)

    def test_persistent_failure_logs_an_error(self):
        self.keep("C2", text="SECRET")
        with mock.patch.object(a2a_outbox, "_rewrite", side_effect=OSError("disk")):
            with self.assertLogs(a2a_outbox.__name__, level="ERROR"):
                a2a_outbox.close("C2", "sent")


class TestRouteDirsDoNotCollide(unittest.TestCase):
    def test_similar_provider_ids_get_distinct_dirs(self):
        w = Path("/tmp/chat")
        a, b = session_state.route_session_dir(w, "a-b"), session_state.route_session_dir(w, "a_b")
        self.assertNotEqual(a, b)
        self.assertTrue(a.name.startswith(session_state.ROUTE_SESSION_PREFIX))
        self.assertEqual(session_state.route_session_dir(a, "a-b"), a)   # idempotent from a route dir


if __name__ == "__main__":
    unittest.main()

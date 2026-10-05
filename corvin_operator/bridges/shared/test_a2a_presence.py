"""Peer presence — one rule (a2a_connectivity.presence), fed by one writer
(a2a_friendship.stamp_probe), probed on a fixed cadence.

Regression (2026-10-05): the chat sidebar painted a peer green from
can_send/can_receive (permissions), so a peer last reachable 7.5 days ago
showed as "two-way" online. Presence is now measured, aged, and shared.
"""
from __future__ import annotations

import json
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_connectivity as ac
import a2a_friendship as ft

NOW = 2_000_000_000.0


def _cfg(**kw):
    base = {"enabled": True, "state": "ACTIVE"}
    base.update(kw)
    return base


class PresenceRuleTests(unittest.TestCase):
    def p(self, *cfgs, now=NOW):
        return ac.presence(list(cfgs), now)["presence"]

    def test_fresh_successful_probe_is_online(self):
        self.assertEqual(self.p(_cfg(_last_check_at=NOW - 30, _last_ok_at=NOW - 30)), "online")

    def test_fresh_failed_probe_is_offline_even_if_state_says_active(self):
        c = _cfg(state="ACTIVE", _last_check_at=NOW - 30, _last_ok_at=NOW - 3600)
        self.assertEqual(self.p(c), "offline")

    def test_stale_ok_is_not_online(self):
        t = NOW - ac.PRESENCE_FRESH_S - 1
        self.assertEqual(self.p(_cfg(_last_check_at=t, _last_ok_at=t)), "unknown")

    def test_never_checked_is_unknown_not_online(self):
        self.assertEqual(self.p(_cfg(state="ACTIVE")), "unknown")

    def test_permissions_alone_never_make_a_peer_online(self):
        # The exact shape of the live bug: enabled both ways, no fresh ok.
        c = _cfg(state="UNREACHABLE", _last_check_at=NOW - 10, _last_ok_at=NOW - 649_000)
        self.assertEqual(self.p(c, c), "offline")

    def test_operator_disabled_wins(self):
        c = _cfg(_operator_disabled=True, _last_check_at=NOW, _last_ok_at=NOW)
        self.assertEqual(self.p(c), "disabled")

    def test_pending_without_ok(self):
        self.assertEqual(self.p(_cfg(enabled=False, state="PENDING")), "pending")

    def test_newest_probe_across_origin_and_endpoint_decides(self):
        old_ok = _cfg(_last_check_at=NOW - 100, _last_ok_at=NOW - 100)
        newer_fail = _cfg(_last_check_at=NOW - 5)
        self.assertEqual(self.p(old_ok, newer_fail), "offline")

    def test_timestamps_are_returned(self):
        out = ac.presence([_cfg(_last_check_at=NOW - 3, _last_ok_at=NOW - 3)], NOW)
        self.assertEqual(out["last_check_at"], NOW - 3)
        self.assertEqual(out["last_ok_at"], NOW - 3)

    def test_stamp_probe_writes_exactly_what_presence_reads(self):
        c = _cfg()
        ft.stamp_probe(c, True, NOW)
        self.assertEqual(ac.presence([c], NOW)["presence"], "online")
        ft.stamp_probe(c, False, NOW + 10)
        self.assertEqual(ac.presence([c], NOW + 10)["presence"], "offline")


class PresenceCadenceTests(unittest.TestCase):
    """The manager pings every connection each PRESENCE_INTERVAL_S without a
    hello; hellos keep their own (slower / backoff) schedule."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.O, self.E = self.tmp / "o", self.tmp / "e"
        self.O.mkdir(); self.E.mkdir()
        tok = ft.FriendshipToken(kid="kid-presence", key=secrets.token_hex(32),
                                 url="http://peer.invalid/v1/a2a/receive", label="p", expires=None)
        o, e = ft.to_origin_dict(tok), ft.to_endpoint_dict(tok)
        for c in (o, e):
            c["enabled"] = True
        ft._atomic_write(self.O / "kid-presence.json", o)
        ft._atomic_write(self.E / "kid-presence.json", e)
        self.mgr = ac.ConnectivityManager.__new__(ac.ConnectivityManager)
        self.mgr._origins_dir, self.mgr._endpoints_dir = self.O, self.E
        import threading
        self.mgr._schedules, self.mgr._sched_lock, self.mgr._known_states = {}, threading.Lock(), {}

    def test_ping_without_hello_between_hellos_and_presence_updates(self):
        calls = []
        reachable = {"v": True}

        def fake_ack(kid, endpoints_dir):
            calls.append("hello")
            return {"ok": True, "reachable": True, "via": "direct"}

        def fake_ping(kid, endpoints_dir, audit=True):
            calls.append("ping")
            return reachable["v"], ("direct" if reachable["v"] else None)

        clock = {"t": NOW}
        with mock.patch.object(ft, "retry_friendship_ack", fake_ack), \
             mock.patch.object(ac, "_ping_peer", fake_ping), \
             mock.patch.object(ac, "_audit", lambda *a, **k: None), \
             mock.patch.object(ac.time, "time", lambda: clock["t"]):
            self.mgr._maintain_friendships()                 # first pass: hello + ping
            self.assertEqual(calls, ["hello", "ping"])
            cfg = json.loads((self.O / "kid-presence.json").read_text())
            self.assertEqual(ac.presence([cfg], clock["t"])["presence"], "online")

            clock["t"] += ac.PRESENCE_INTERVAL_S + 1          # ping due, hello not
            reachable["v"] = False
            calls.clear()
            self.mgr._maintain_friendships()
            self.assertEqual(calls, ["ping"])
            cfg = json.loads((self.E / "kid-presence.json").read_text())
            self.assertEqual(ac.presence([cfg], clock["t"])["presence"], "offline")

            calls.clear()                                      # nothing due
            self.mgr._maintain_friendships()
            self.assertEqual(calls, [])


if __name__ == "__main__":
    unittest.main()

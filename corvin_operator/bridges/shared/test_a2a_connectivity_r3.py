"""Connectivity manager — round-3 adversarial review regressions.

1. A connection imported from a token WITHOUT an address (disabled + PENDING)
   must survive the manager's upkeep pass so the bound issuer's hello can
   activate it (the pass used to rewrite its state to ACTIVE/UNREACHABLE).
2. An operator-disabled connection gets no upkeep at all.
3. With the ingress down, the manager never announces the ingress port.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_connectivity as ac
import a2a_friendship as ft


class _Pair(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.O, self.E = self.tmp / "o", self.tmp / "e"
        self.O.mkdir(); self.E.mkdir()
        self.tok = ft.FriendshipToken(kid="kid-r3-activation", key=secrets.token_hex(32), url=None,
                                      label="issuer", expires=None)
        o, e = ft.to_origin_dict(self.tok), ft.to_endpoint_dict(self.tok)
        for c in (o, e):
            c["_peer_instance_id"] = "issuer-iid-0001"  # bound at import (verified ack)
            c["state"] = "PENDING"
        ft._atomic_write(self.O / f"{self.tok.kid}.json", o)
        ft._atomic_write(self.E / f"{self.tok.kid}.json", e)
        for target, value in (("_audit_pairing_event", lambda *a, **k: None),
                              ("_ack_ping_back_and_respond", lambda **k: (200, {"ok": True})),
                              ("_local_instance_id", lambda: "redeemer-iid-0002")):
            p = mock.patch.object(ft, target, value)
            p.start()
            self.addCleanup(p.stop)

    def origin(self):
        return json.loads((self.O / f"{self.tok.kid}.json").read_text())

    def hello(self, sender="issuer-iid-0001", url="http://192.168.1.10:8775"):
        hk = self.origin()["hmac_key"]
        now = int(time.time())
        req = {"kid": self.tok.kid, "issued_at": now, "peer_url": url, "sender_instance_id": sender}
        req["signature"] = hmac.new(bytes.fromhex(hk), ft._ack_canonical(self.tok.kid, now, url, None),
                                    "sha256").hexdigest()
        req["signature_v2"] = hmac.new(bytes.fromhex(hk),
                                       ft._ack_canonical(self.tok.kid, now, url, None, sender),
                                       "sha256").hexdigest()
        return ft.process_friendship_ack_request(req, pending_dir=self.tmp / "p",
                                                 origins_dir=self.O, endpoints_dir=self.E)


class TestUrlLessActivationSurvivesUpkeep(_Pair):
    def _refresh(self, reachable: bool):
        with mock.patch.object(ft, "retry_friendship_ack",
                               return_value=({"ok": True, "reachable": True, "via": "relay"}
                                             if reachable else {"ok": False, "error": "unreachable"})), \
             mock.patch.object(ac, "_ping_peer", return_value=(reachable, "relay" if reachable else None)):
            return ac.refresh_friendship(self.tok.kid, origins_dir=self.O, endpoints_dir=self.E)

    def test_upkeep_keeps_pending_then_the_issuer_hello_activates(self):
        for reachable in (True, False):
            self._refresh(reachable)
            o = self.origin()
            self.assertEqual((o["state"], o["enabled"]), ("PENDING", False))
            self.assertTrue(ft._awaiting_activation(o))
        self.assertEqual(self.hello()[0], 200)
        o = self.origin()
        self.assertTrue(o["enabled"])
        self.assertEqual(o["state"], "ACTIVE")

    def test_operator_disabled_connection_gets_no_upkeep_and_no_activation(self):
        o = self.origin()
        o["_operator_disabled"] = True
        ft._atomic_write(self.O / f"{self.tok.kid}.json", o)
        called = []
        with mock.patch.object(ft, "retry_friendship_ack", side_effect=lambda *a, **k: called.append(1)):
            res = ac.refresh_friendship(self.tok.kid, origins_dir=self.O, endpoints_dir=self.E)
        self.assertEqual(res.get("error"), "disabled")
        self.assertEqual(called, [], "no hello to an operator-disabled peer")
        self.assertEqual(self.hello()[0], 403)
        self.assertFalse(self.origin()["enabled"])


class TestInboundAckNeverBinds(_Pair):
    def test_two_step_bind_then_repoint_is_refused_on_an_unbound_pairing(self):
        # Unbound (e.g. CLI import whose ack failed): an attacker with the token
        # repeats the stored URL, then tries to move it.
        for path in (self.O / f"{self.tok.kid}.json", self.E / f"{self.tok.kid}.json"):
            c = json.loads(path.read_text())
            c.pop("_peer_instance_id", None)
            c.update(enabled=True, state="ACTIVE")
            if "url" in c or path.parent == self.E:
                c["url"] = "http://192.168.1.10:8775/v1/a2a/receive"
            ft._atomic_write(path, c)
        self.assertEqual(self.hello(sender="attacker-iid-99")[0], 200)  # keep-alive only
        self.assertNotIn("_peer_instance_id", self.origin(), "an inbound ack must never bind")
        self.assertEqual(self.hello(sender="attacker-iid-99", url="http://192.168.1.66:8775")[0], 403)
        ep = json.loads((self.E / f"{self.tok.kid}.json").read_text())
        self.assertEqual(ep["url"], "http://192.168.1.10:8775/v1/a2a/receive")


class TestNoIngressPortAnnouncementWhileDown(unittest.TestCase):
    def test_ip_change_with_ingress_down_does_not_announce_the_ingress_port(self):
        stored = {"u": "http://192.168.1.20:8775"}
        announced = []
        mgr = ac.ConnectivityManager.__new__(ac.ConnectivityManager)

        class _Ing:
            running = False
            config = None

        mgr.ingress = _Ing()
        mgr._wake = threading.Event()
        mgr._sched_lock = threading.Lock()
        mgr._schedules = {}
        mgr._detect_host = lambda: ("192.168.1.33", "auto_lan")
        with mock.patch.dict(os.environ, {}, clear=False), \
             mock.patch.object(ft, "get_my_url", side_effect=lambda: stored["u"]), \
             mock.patch.object(ft, "set_my_url", side_effect=lambda u: announced.append(u)), \
             mock.patch.object(ac, "_audit", lambda *a, **k: None):
            os.environ.pop("CORVIN_A2A_URL", None)
            mgr._ensure_my_url()
        self.assertEqual(announced, [])


if __name__ == "__main__":
    unittest.main(verbosity=2)

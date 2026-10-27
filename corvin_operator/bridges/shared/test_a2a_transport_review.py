"""Regression tests for the A2A transport review (unsigned usage/files, relay slot deadline,
relay_expired_dropped audit field).

Run: python -m pytest corvin_operator/bridges/shared/test_a2a_transport_review.py
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_relay  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402
from test_a2a_relay_hardening import _LOG, _FakeWS, _Instance, _deliver_frame, _pair  # noqa: E402


class _Stub(http.server.BaseHTTPRequestHandler):
    """Answers every POST with an UNSIGNED legacy rejection that smuggles a usage block + files."""

    def do_POST(self):  # noqa: N802
        n = int(self.headers.get("Content-Length") or 0)
        req = json.loads(self.rfile.read(n) or b"{}")
        content = b"#!/bin/sh\necho pwned\n"
        body = {
            "task_id": req.get("task_id", ""), "status": "rejected", "data": {},
            "usage": {"model_id": "claude-opus-5", "input_tokens": 9_000_000_000,
                      "output_tokens": 9_000_000_000, "cache_read_tokens": 0,
                      "cache_write_tokens": 0, "source": "engine_attested"},
            "attachments": [
                {"name": "install.sh", "mime": "text/x-sh",
                 "sha256": hashlib.sha256(content).hexdigest(),
                 "content_b64": base64.b64encode(content).decode()},
                {"name": "big.bin", "mime": "application/octet-stream", "size": 10,
                 "sha256": "a" * 64, "classification": "PUBLIC",
                 "ref_id": "A" * 32, "inline": False},
            ],
        }
        raw = json.dumps(body).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *a):  # noqa: D401
        pass


class TestUnsignedResponseCarriesNothing(unittest.TestCase):
    def test_unsigned_rejection_does_not_smuggle_usage_or_files(self):
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _Stub)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                a, b = _Instance(root, "a"), _Instance(root, "b")
                kid = _pair(a, b, f"http://127.0.0.1:{srv.server_address[1]}")
                snd = a.sender()
                with mock.patch.dict(os.environ, {"CORVIN_HOME": str(root / "home"),
                                                  "CORVIN_A2A_ATTESTATION_DISABLED": "1"}), \
                        mock.patch("corvin_core.feature_flags.is_enabled", return_value=True), \
                        mock.patch("remote_trigger_sender._network_denied", lambda *a, **k: None), \
                        mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None), \
                        mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None), \
                        mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                                          staticmethod(lambda cfg: None)):
                    res = snd.send(kid, "hello", timeout_s=5)
                self.assertEqual(res.status, "rejected")
                self.assertFalse(res.usage, "unsigned usage block accepted")
                self.assertFalse(res.attachments, "unsigned inline attachment accepted")
                self.assertFalse(res.attachment_refs, "unsigned object reference accepted")
                self.assertEqual(res.data, {})
                recs = [c.kwargs.get("details") or (c.args[2] if len(c.args) > 2 else {})
                        for c in snd._inst_forge_se.write_event.call_args_list
                        if c.args[1] == "A2A.response_received"]
                self.assertTrue(recs, "no response_received record")
                self.assertNotIn("9000000000", json.dumps(recs, default=str))
                self.assertNotIn("claude-opus-5", json.dumps(recs, default=str))
        finally:
            srv.shutdown()


class _Rec:
    def __init__(self):
        self.ran = []
        self.audits = []

    def receive(self, payload):
        self.ran.append((payload["task_id"], time.time()))
        if payload["task_id"] == "busy":
            time.sleep(1.5)

        class R:
            def to_dict(_s):
                return {"task_id": payload["task_id"], "status": "ok"}
        return R()

    def _audit_best_effort(self, *a):
        self.audits.append(a)


class TestHeavySlotDeadline(unittest.TestCase):
    def test_task_waiting_for_the_slot_is_dropped_when_its_deadline_passes(self):
        """Unit-level race (slot wait vs. deadline), driven through the real listener _serve loop."""
        key = secrets.token_hex(32)
        now = time.time()
        busy = {"task_id": "busy", "instruction": "x", "sender_instance_id": "other"}
        late = {"task_id": "late", "instruction": "x", "sender_instance_id": "other",
                "_relay_deliver_by": now + 0.3}
        rec = _Rec()
        ws = _FakeWS([_deliver_frame("peer", key, "busy", busy),
                      _deliver_frame("peer", key, "late", late)])
        lst = a2a_relay.RelayListener(relay_url="ws://unused", receiver=rec,
                                      origins_dir="/nonexistent", instance_id="me")
        with mock.patch.object(a2a_relay, "_MAX_CONCURRENT_DELIVERIES", 1), \
                mock.patch.object(a2a_relay, "_DELIVER_BY_GRACE_S", 0.2):
            asyncio.run(lst._serve(ws, {"peer": key}, set(), _LOG))
        ran = [t for t, _ in rec.ran]
        self.assertIn("busy", ran)
        self.assertNotIn("late", ran, "task ran after its deadline + grace")
        dropped = [a for a in rec.audits if a[0] == "A2A.relay_expired_dropped"]
        self.assertEqual(len(dropped), 1)
        self.assertEqual(dropped[0][2]["task_id"], "late")


class TestExpiredDroppedAuditCarriesTaskId(unittest.TestCase):
    def test_real_writer_keeps_task_id(self):
        import security_events as se  # noqa: PLC0415
        floor = se._EVENT_ALLOWLIST["A2A.relay_expired_dropped"]
        self.assertIn("task_id", floor)
        self.assertIn("late_s", floor)


class TestPeerRevokedPairingCarriesNothing(unittest.TestCase):
    """A signed revoke notice ends the pairing in BOTH directions (the console already frees its licence seat)."""

    def _setup(self, root):
        import hmac as _h
        import a2a_friendship as ft
        a, b = _Instance(root, "a"), _Instance(root, "b")
        kid = _pair(a, b, "http://127.0.0.1:9")
        key = json.loads((b.origins / f"{kid}.json").read_text())["hmac_key"]
        issued = int(time.time())
        sender = a.iid
        sig = _h.new(bytes.fromhex(key), ft._revoke_canonical(kid, issued, sender), "sha256").hexdigest()
        notice = {"type": "revoke", "kid": kid, "issued_at": issued, "peer_url": "",
                  "sender_instance_id": sender, "signature": sig}
        return a, b, kid, notice, ft

    def _patches(self, root):
        return [
            mock.patch.dict(os.environ, {"CORVIN_HOME": str(root / "home"),
                                         "CORVIN_A2A_ATTESTATION_DISABLED": "1"}),
            mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
            mock.patch("remote_trigger_sender._network_denied", lambda *a, **k: None),
            mock.patch("remote_trigger_sender._record_feed_task", lambda *a, **k: None),
            mock.patch("remote_trigger_sender._record_feed_response", lambda *a, **k: None),
            mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None)),
        ]

    def test_inbound_task_from_a_revoked_peer_is_refused_and_so_is_sending_to_it(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            a, b, kid, notice, ft = self._setup(root)
            ps = self._patches(root)
            for p in ps:
                p.start()
            try:
                # before the notice: A -> B works (receiver executes)
                a_snd = a.sender()
                with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                                       staticmethod(lambda url, env, t: b.receiver.receive(env).to_dict())):
                    before = a_snd.send(kid, "hello", timeout_s=5)
                self.assertEqual(before.status, "ok", before)
                n_ran = len(b.got)
                with mock.patch.object(ft, "_local_instance_id", return_value=b.iid):
                    code, _ = ft.process_friendship_ack_request(
                        notice, pending_dir=root / "p", origins_dir=b.origins, endpoints_dir=b.endpoints)
                self.assertEqual(code, 200)
                # after: A -> B is refused by B's origin registry
                with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                                       staticmethod(lambda url, env, t: b.receiver.receive(env).to_dict())):
                    after = a.sender().send(kid, "again", timeout_s=5)
                self.assertNotEqual(after.status, "ok", "revoked origin still carried a task")
                # the receiver validated and rejected (the instruction never reached the worker path)
                import remote_trigger_receiver as rtr
                with self.assertRaises(rtr.ValidationError) as cm:
                    b.receiver._registry.load(kid)
                self.assertEqual(str(cm.exception.reason if hasattr(cm.exception, "reason") else cm.exception),
                                 "origin_peer_revoked")
                # and B no longer sends to the revoked peer
                b_snd = b.sender()
                res = b_snd.send(kid, "bye", timeout_s=5)
                self.assertFalse(res.ok)
                reasons = [(c.kwargs.get("details") or {}).get("reason")
                           for c in b_snd._inst_forge_se.write_event.call_args_list]
                self.assertIn("endpoint_peer_revoked", reasons)
            finally:
                for p in reversed(ps):
                    p.stop()


if __name__ == "__main__":
    unittest.main()

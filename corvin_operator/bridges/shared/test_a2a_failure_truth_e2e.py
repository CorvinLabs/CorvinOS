"""What the operator sees when A2A fails must be the truth (review R3,
2026-10-05). Over real HTTP: RemoteTriggerSender → a2a_http_server →
RemoteTriggerReceiver.

1. Two hosts 60 s apart: tasks are accepted (±300 s window), so the ping
   must say reachable too — it used to allow only ±30 s and showed the peer
   "offline" while every message went through.
2. A signed rejection names its CLOSED reason (here: the peer requires a
   verified CorvinOS identity) instead of a bare "rejected".
3. A response that may have been delivered (read timeout after the peer got
   the request) is recorded "unconfirmed", not "error" — a resend would run
   it twice.
"""
from __future__ import annotations

import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_http_server
import remote_trigger_receiver
import remote_trigger_sender
from remote_trigger_sender import RemoteTriggerSender
from test_a2a_crypto_e2e import (
    _SimpleFakeEngine, _WithAuditMock, _hex, _write_endpoint, _write_origin,
)


class FailureTruthE2E(_WithAuditMock):
    def setUp(self):
        super().setUp()
        self._env = mock.patch.dict(os.environ, {"CORVIN_A2A_ATTESTATION_DISABLED": "1"})
        self._env.start()
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        os.environ["CORVIN_A2A_FEED_DIR"] = str(self.tmp / "feed")
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        self._tmp.cleanup()
        os.environ.pop("CORVIN_A2A_FEED_DIR", None)
        self._env.stop()
        super().tearDown()

    def _pair(self, origin_extra: dict | None = None):
        tag = secrets.token_hex(3)
        origins_b = self.tmp / tag / "o"
        endpoints_a = self.tmp / tag / "e"
        origins_b.mkdir(parents=True)
        endpoints_a.mkdir(parents=True)
        hk, rk = _hex(), _hex()
        iid_b = "iid-B-" + tag
        _write_origin(origins_b, origin_id="peer-a", hmac_key=hk, recv_key=rk)
        if origin_extra:
            p = origins_b / "peer-a.json"
            cfg = json.loads(p.read_text())
            cfg.update(origin_extra)
            p.write_text(json.dumps(cfg))
        srv = a2a_http_server.build_server(host="127.0.0.1", port=0, origins_dir=origins_b,
                                           engine_factory=lambda: _SimpleFakeEngine(),
                                           instance_id=iid_b)
        a2a_http_server.serve_in_thread(srv)
        self.servers.append(srv)
        _write_endpoint(endpoints_a, endpoint_id="peer-b",
                        url=f"http://127.0.0.1:{srv.server_address[1]}/v1/a2a/receive",
                        hmac_key=hk, recv_key=rk, instance_id=iid_b, our_origin_id="peer-a")
        return RemoteTriggerSender(endpoints_dir=endpoints_a, instance_id="iid-A-" + tag)

    def test_ping_window_matches_the_task_window(self):
        self.assertEqual(a2a_http_server._PING_WINDOW_S, remote_trigger_receiver._TIME_WINDOW_S)

    def test_clock_skew_of_a_minute_is_reachable_and_deliverable(self):
        sender = self._pair()

        class _SkewedTime:
            """Only the SENDER's clock runs 60 s ahead — patching time.time
            itself would move the receiver's clock too (no skew at all)."""
            def __getattr__(self, name):
                return getattr(time, name)

            @staticmethod
            def time():
                return time.time() + 60

        with mock.patch.object(remote_trigger_sender, "time", _SkewedTime()):
            ping = sender.ping("peer-b", timeout_s=5)
            res = sender.send("peer-b", "hello")
        self.assertTrue(res.ok, res.status)
        self.assertTrue(ping.reachable, "presence must agree with what is deliverable")

    def test_rejection_names_its_reason(self):
        sender = self._pair({"require_ibc": True})
        res = sender.send("peer-b", "hello")
        self.assertEqual(res.status, "rejected")
        self.assertEqual(res.data.get("reason"), "identity_required")
        self.assertIn("verified CorvinOS", res.error_detail or "")

    def test_maybe_delivered_is_recorded_unconfirmed(self):
        sender = self._pair()

        def timeout_after_send(*a, **k):
            raise remote_trigger_sender.TransportError("timeout", maybe_delivered=True)

        with mock.patch.object(sender, "_http_post", timeout_after_send):
            res = sender.send("peer-b", "run the backup")
        self.assertFalse(res.ok)
        self.assertTrue(res.maybe_delivered)
        recs = [json.loads(l) for l in (self.tmp / "feed" / "messages.jsonl").read_text().splitlines()]
        resp = [r for r in recs if r["kind"] == "response"][-1]
        self.assertEqual(resp["status"], "unconfirmed")
        self.assertIn("may have", resp["error"])


if __name__ == "__main__":
    unittest.main()

"""A group message the receiving instance does NOT store must not be reported
to the sender as delivered (review R2, 2026-10-05).

Before the fix the receiver's group branch fell through into the M1 path,
which overwrote the handler's status with "ok": a message refused for a
non-participant, a revoked friendship, a failing handler or a host without
group support came back as a signed ``ok`` and the sender audited
``chat.group.message_sent_to_peer``. Driven over real HTTP: RemoteTriggerSender
→ a2a_http_server → RemoteTriggerReceiver.receive → group handler.
"""
from __future__ import annotations

import os
import secrets
import tempfile
import unittest
from pathlib import Path

import a2a_http_server
from remote_trigger_sender import RemoteTriggerSender
from test_a2a_crypto_e2e import (  # the suite's pairing helpers
    _SimpleFakeEngine, _WithAuditMock, _hex, _write_endpoint, _write_origin,
)


class GroupReceiveStatusE2E(_WithAuditMock):
    def setUp(self):
        super().setUp()
        self._att = os.environ.get("CORVIN_A2A_ATTESTATION_DISABLED")
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.servers = []

    def tearDown(self):
        for srv in self.servers:
            srv.shutdown()
            srv.server_close()
        self._tmp.cleanup()
        if self._att is None:
            os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        else:
            os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = self._att
        super().tearDown()

    def _send_group(self, handler):
        tag = secrets.token_hex(3)
        origins_b = self.tmp / tag / "b" / "origins"
        endpoints_a = self.tmp / tag / "a" / "endpoints"
        origins_b.mkdir(parents=True)
        endpoints_a.mkdir(parents=True)
        hmac_key, recv_key = _hex(), _hex()
        iid_b = "iid-B-" + tag
        _write_origin(origins_b, origin_id="peer-a", hmac_key=hmac_key, recv_key=recv_key,
                      spawn_worker=False)
        srv = a2a_http_server.build_server(
            host="127.0.0.1", port=0, origins_dir=origins_b,
            engine_factory=lambda: _SimpleFakeEngine(), instance_id=iid_b,
            group_message_handler=handler,
        )
        a2a_http_server.serve_in_thread(srv)
        self.servers.append(srv)
        _write_endpoint(endpoints_a, endpoint_id="peer-b",
                        url=f"http://127.0.0.1:{srv.server_address[1]}/v1/a2a/receive",
                        hmac_key=hmac_key, recv_key=recv_key, instance_id=iid_b,
                        our_origin_id="peer-a")
        sender = RemoteTriggerSender(endpoints_dir=endpoints_a, instance_id="iid-A-" + tag)
        return sender.send("peer-b", "hello group", purpose_id="group_message", group_id="g-1")

    def test_stored_message_is_ok(self):
        res = self._send_group(lambda **kw: {"status": "accepted", "message_id": "m-1"})
        self.assertTrue(res.ok, res.status)
        self.assertEqual(res.status, "ok")

    def test_refused_message_is_rejected(self):
        res = self._send_group(lambda **kw: {"status": "error", "reason": "sender_not_a_group_participant"})
        self.assertFalse(res.ok, "a refused group message was reported as delivered")
        self.assertEqual(res.status, "rejected")

    def test_failing_handler_is_rejected(self):
        def boom(**kw):
            raise RuntimeError("disk full")
        res = self._send_group(boom)
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "rejected")

    def test_host_without_group_support_is_rejected(self):
        res = self._send_group(None)
        self.assertFalse(res.ok)
        self.assertEqual(res.status, "rejected")


if __name__ == "__main__":
    unittest.main()

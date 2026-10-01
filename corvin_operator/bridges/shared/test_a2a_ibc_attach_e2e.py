"""Real sender -> real receiver over HTTP with a test-mode IBC (ADR-2099 P0 gate).

The sender reads its Instance Binding Certificate from disk, attaches
``instance_attestation`` and the receiver's ``require_ibc`` gate verifies it.
Until 2026-10-01 the sender decoded the ``CORVIN-`` token with PyJWT, which
cannot parse it, so no envelope ever carried an attestation and
``require_ibc: true`` rejected every peer. The bound-sender case below goes
red when that decode is restored.

Run: ``pytest corvin_operator/bridges/shared/test_a2a_ibc_attach_e2e.py -v``
"""
from __future__ import annotations

import base64
import json
import os
import secrets
import sys
import tempfile
import time
import unittest
import unittest.mock as mock
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

import a2a_http_server
import instance_identity
import remote_trigger_receiver as rtr
import remote_trigger_sender as rts
from remote_trigger_sender import RemoteTriggerSender
from test_a2a_crypto_e2e import _SimpleFakeEngine, _hex, _write_endpoint, _write_origin


def _b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()


def _make_ibc(signer: Ed25519PrivateKey, *, sub: str, instance_pubkey_b64: str,
              exp_offset_s: int = 3600) -> str:
    header = {"alg": "EdDSA", "kid": "ibc-test"}
    claims = {
        "type": "instance_binding",
        "iss": "corvinlabs.io",
        "sub": sub,
        "jti": "ibc-" + secrets.token_hex(8),
        "instance_pubkey": instance_pubkey_b64,
        "exp": int(time.time()) + exp_offset_s,
    }
    h = _b64url(json.dumps(header).encode())
    p = _b64url(json.dumps(claims).encode())
    sig = _b64url(signer.sign(f"{h}.{p}".encode()))
    return f"CORVIN-{h}.{p}.{sig}"


class TestIbcAttachOverHttp(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.tmp = Path(self._tmp.name)
        self.emitted: list[dict] = []

        def _capture(_path, event_type, **kwargs):
            self.emitted.append({"event_type": event_type, **kwargs})
            return {"hash": "abc"}

        se = mock.MagicMock()
        se.write_event = mock.MagicMock(side_effect=_capture)

        # Test-mode IBC trust anchor: a throwaway Ed25519 key standing in for
        # Corvin-Features (kid "ibc-test" is not in the production ring).
        self.trust_key = Ed25519PrivateKey.generate()
        trust_der = self.trust_key.public_key().public_bytes(
            serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)

        self.cert_path = self.tmp / "sender" / "instance_cert.jwt"
        key_path = self.tmp / "sender" / "instance_key.pem"
        self.cert_path.parent.mkdir(parents=True)

        for p in (
            mock.patch.object(rtr, "_forge_se", se),
            mock.patch.object(rts, "_forge_se", se),
            mock.patch.object(instance_identity, "_TEST_MODE_SNAPSHOT", "1"),
            # CRL is an external Corvin Labs service; the receive path must not
            # reach it from a test. Revocation itself is covered elsewhere.
            mock.patch.object(rtr, "_peer_ibc_revoked", lambda _jti: False),
            mock.patch.dict(os.environ, {
                "CORVIN_IBC_PUBKEY_DER_B64": base64.b64encode(trust_der).decode(),
                "CORVIN_INSTANCE_CERT_PATH": str(self.cert_path),
                "CORVIN_INSTANCE_KEY_PATH": str(key_path),
                "CORVIN_A2A_ATTESTATION_DISABLED": "1",
            }),
        ):
            p.start()
            self.addCleanup(p.stop)

        self.iid_a = "iid-A-" + secrets.token_hex(4)
        self.iid_b = "iid-B-" + secrets.token_hex(4)
        self.instance_pubkey = instance_identity.get_instance_pubkey_b64()

        self.origins_b = self.tmp / "b" / "origins"
        endpoints_a = self.tmp / "a" / "endpoints"
        self.origins_b.mkdir(parents=True)
        endpoints_a.mkdir(parents=True)
        hmac_key, recv_key = _hex(), _hex()
        _write_origin(self.origins_b, origin_id="peer-a",
                      hmac_key=hmac_key, recv_key=recv_key)
        self.srv_b = a2a_http_server.build_server(
            host="127.0.0.1", port=0, origins_dir=self.origins_b,
            engine_factory=lambda: _SimpleFakeEngine(), instance_id=self.iid_b,
        )
        a2a_http_server.serve_in_thread(self.srv_b)
        self.addCleanup(self.srv_b.server_close)
        self.addCleanup(self.srv_b.shutdown)
        url_b = f"http://127.0.0.1:{self.srv_b.server_address[1]}/v1/a2a/receive"
        _write_endpoint(endpoints_a, endpoint_id="peer-b", url=url_b,
                        hmac_key=hmac_key, recv_key=recv_key,
                        instance_id=self.iid_b, our_origin_id="peer-a")
        self.sender = RemoteTriggerSender(endpoints_dir=endpoints_a, instance_id=self.iid_a)
        self.addCleanup(self._tmp.cleanup)

    def _set_require_ibc(self, value: bool) -> None:
        p = self.origins_b / "peer-a.json"
        cfg = json.loads(p.read_text())
        cfg["require_ibc"] = value
        p.write_text(json.dumps(cfg))
        p.chmod(0o600)

    def _bind(self, ibc: str) -> None:
        self.cert_path.write_text(ibc)
        self.cert_path.chmod(0o600)

    def _send(self):
        return self.sender.send("peer-b", "Do something.",
                                result_schema={"properties": {"done": {"type": "boolean"}}})

    def _events(self, name: str) -> list[dict]:
        return [e for e in self.emitted if e["event_type"] == name]

    def test_bound_sender_admitted_when_ibc_required(self):
        self._set_require_ibc(True)
        self._bind(_make_ibc(self.trust_key, sub=self.iid_a,
                             instance_pubkey_b64=self.instance_pubkey))
        res = self._send()
        self.assertTrue(res.ok, f"bound sender rejected: status={res.status}")
        self.assertTrue(self._events("instance.ibc_verified"),
                        "receiver never verified an attestation")
        self.assertFalse(self._events("instance.ibc_sig_failed"),
                         "a verified IBC must not also be audited as a failure")

    def test_unbound_sender_rejected_when_ibc_required(self):
        self._set_require_ibc(True)
        res = self._send()
        self.assertFalse(res.ok)
        self.assertFalse(self._events("instance.ibc_verified"))

    def test_ibc_from_foreign_signer_rejected_when_required(self):
        self._set_require_ibc(True)
        self._bind(_make_ibc(Ed25519PrivateKey.generate(), sub=self.iid_a,
                             instance_pubkey_b64=self.instance_pubkey))
        res = self._send()
        self.assertFalse(res.ok)
        self.assertFalse(self._events("instance.ibc_verified"))

    def test_ibc_for_another_instance_rejected_even_when_not_required(self):
        self._set_require_ibc(False)
        self._bind(_make_ibc(self.trust_key, sub="iid-someone-else",
                             instance_pubkey_b64=self.instance_pubkey))
        res = self._send()
        self.assertFalse(res.ok)

    def test_unbound_sender_still_admitted_when_ibc_not_required(self):
        self._set_require_ibc(False)
        res = self._send()
        self.assertTrue(res.ok, f"status={res.status}")


if __name__ == "__main__":
    unittest.main()

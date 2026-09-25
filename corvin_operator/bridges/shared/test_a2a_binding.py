"""Pairing binding keys (a2a_binding, ADR-2064 round 4).

A leaked friendship token yields the pairing HMAC keys, and the bound peer's
instance id is public (every signed ping response carries it). With the
binding public keys exchanged during pairing, a CHANGE to the pairing (repeat
ack that moves the URL, revoke notice, reconnect push) additionally needs a
MAC under an ECDH secret that the token does not contain.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import a2a_binding as bind
import a2a_friendship as ft

A_IID = "aaaaaaaa-1111-4111-8111-aaaaaaaaaaaa"
B_IID = "bbbbbbbb-2222-4222-8222-bbbbbbbbbbbb"
C_IID = "cccccccc-3333-4333-8333-cccccccccccc"


class _Keys:
    """Switch the process between instance identities' binding key files."""

    def __init__(self, root: Path):
        self.root = root

    def use(self, who: str):
        bind._cached.clear()
        return mock.patch.dict(os.environ, {"CORVIN_A2A_BIND_KEY_PATH": str(self.root / f"{who}.key")})

    def pub(self, who: str) -> str:
        with self.use(who):
            return bind.local_bind_pub()


class TestBindingPrimitives(unittest.TestCase):
    def test_ecdh_secret_is_symmetric_and_pairing_scoped(self):
        keys = _Keys(Path(tempfile.mkdtemp()))
        a, b = keys.pub("A"), keys.pub("B")
        with keys.use("A"):
            mac_a = bind.bind_mac(b, "kid1", b"msg")
            other_kid = bind.bind_mac(b, "kid2", b"msg")
        with keys.use("B"):
            self.assertTrue(bind.verify_bind_mac(a, "kid1", b"msg", mac_a))
            self.assertFalse(bind.verify_bind_mac(a, "kid1", b"other", mac_a))
        self.assertNotEqual(mac_a, other_kid)
        with keys.use("C"):
            self.assertFalse(bind.verify_bind_mac(a, "kid1", b"msg", mac_a))

    def test_key_file_is_private_and_stable(self):
        root = Path(tempfile.mkdtemp())
        keys = _Keys(root)
        p1 = keys.pub("A")
        self.assertEqual(p1, keys.pub("A"))
        self.assertEqual(oct((root / "A.key").stat().st_mode & 0o777), "0o600")


class TestBoundPairingNeedsTheBindingSecret(unittest.TestCase):
    """B's side of a pairing with A, after a v3 handshake stored A's key."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.keys = _Keys(self.tmp / "keys")
        (self.tmp / "keys").mkdir()
        self.O, self.E, self.P = self.tmp / "o", self.tmp / "e", self.tmp / "p"
        for d in (self.O, self.E, self.P):
            d.mkdir()
        tok, _ = ft.create_friendship_token(url="http://192.168.1.10:8775")
        self.tok = tok
        self.hk, _rk = ft._derive_channel_keys(tok.key)
        a_pub = self.keys.pub("A")
        for d, cfg in ((self.O, ft.to_origin_dict(tok)), (self.E, ft.to_endpoint_dict(tok))):
            cfg.update(_peer_instance_id=A_IID, _peer_bind_pub=a_pub)
            (d / f"{tok.kid}.json").write_text(json.dumps(cfg))
        for target, value in (("_audit_pairing_event", lambda *a, **k: None),
                              ("_ack_ping_back_and_respond", lambda **k: (200, {"ok": True}))):
            p = mock.patch.object(ft, target, value)
            p.start()
            self.addCleanup(p.stop)

    def _ack(self, url: str, *, sender: str, signer: str | None):
        """A repeat ack claiming ``sender``; signed with the binding key of
        ``signer`` (None = a token holder without any binding key)."""
        ia = int(time.time())
        body = {"kid": self.tok.kid, "issued_at": ia, "peer_url": url, "sender_instance_id": sender}
        body["signature"] = hmac.new(bytes.fromhex(self.hk), ft._ack_canonical(self.tok.kid, ia, url, None),
                                     "sha256").hexdigest()
        body["signature_v2"] = hmac.new(bytes.fromhex(self.hk),
                                        ft._ack_canonical(self.tok.kid, ia, url, None, sender),
                                        "sha256").hexdigest()
        if signer is not None:
            with self.keys.use(signer):
                pub = bind.local_bind_pub()
                canon = ft._ack_canonical(self.tok.kid, ia, url, None, sender, pub)
                body["bind_pub"] = pub
                body["signature_v3"] = hmac.new(bytes.fromhex(self.hk), canon, "sha256").hexdigest()
                body["bind_mac"] = bind.bind_mac(self.keys.pub("B"), self.tok.kid, canon)
        with self.keys.use("B"), mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            return ft.process_friendship_ack_request(body, pending_dir=self.P, origins_dir=self.O,
                                                     endpoints_dir=self.E)

    def url(self):
        return json.loads((self.E / f"{self.tok.kid}.json").read_text())["url"]

    def test_token_holder_claiming_the_public_instance_id_cannot_repoint(self):
        # The round-4 repro, against a pairing that exchanged binding keys.
        self.assertEqual(self._ack("http://192.168.1.66:8775", sender=A_IID, signer=None),
                         (403, {"reason": "ack_peer_unproven"}))
        self.assertEqual(self._ack("http://192.168.1.66:8775", sender=A_IID, signer="C")[0], 403)
        self.assertEqual(self.url(), "http://192.168.1.10:8775/v1/a2a/receive")

    def test_the_real_peer_can_move_and_keepalives_need_no_proof(self):
        self.assertEqual(self._ack("http://192.168.1.10:8775", sender=A_IID, signer=None)[0], 200)
        self.assertEqual(self._ack("http://192.168.1.11:8775", sender=A_IID, signer="A")[0], 200)
        self.assertEqual(self.url(), "http://192.168.1.11:8775/v1/a2a/receive")

    def test_forged_revoke_notice_is_refused(self):
        ia = int(time.time())
        body = {"type": "revoke", "kid": self.tok.kid, "issued_at": ia, "peer_url": "",
                "sender_instance_id": A_IID,
                "signature": hmac.new(bytes.fromhex(self.hk),
                                      ft._revoke_canonical(self.tok.kid, ia, A_IID), "sha256").hexdigest()}
        with self.keys.use("B"), mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            code, resp = ft.process_friendship_ack_request(body, pending_dir=self.P, origins_dir=self.O,
                                                           endpoints_dir=self.E)
        self.assertEqual((code, resp), (403, {"reason": "ack_peer_unproven"}))
        with self.keys.use("A"):
            body["bind_mac"] = bind.bind_mac(self.keys.pub("B"), self.tok.kid,
                                             ft._revoke_canonical(self.tok.kid, ia, A_IID))
        with self.keys.use("B"), mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            code, _ = ft.process_friendship_ack_request(body, pending_dir=self.P, origins_dir=self.O,
                                                        endpoints_dir=self.E)
        self.assertEqual(code, 200)


if __name__ == "__main__":
    unittest.main(verbosity=2)


class TestReconnectNeedsBinding(unittest.TestCase):
    """Round 4, finding 1: the ADR-0198 reconnect push re-pointed an endpoint
    on key possession alone — a second route around the ack binding."""

    def setUp(self):
        import remote_trigger_receiver as rtr
        from remote_trigger_sender import reconnect_bind_canonical
        self.canon = reconnect_bind_canonical
        self.tmp = Path(tempfile.mkdtemp())
        self.keys = _Keys(self.tmp)
        self.O, self.E = self.tmp / "o", self.tmp / "e"
        self.O.mkdir(); self.E.mkdir()
        self.kid = "kid-reconnect-1"
        (self.O / f"{self.kid}.json").write_text(json.dumps({"origin_id": self.kid}))
        (self.E / f"{self.kid}.json").write_text(json.dumps({"endpoint_id": self.kid}))
        self.rec = rtr.RemoteTriggerReceiver(origins_dir=self.O, nonce_store=rtr.NonceStore(),
                                             instance_id=B_IID, forge_se=mock.MagicMock())
        self.rec._endpoints_dir = lambda: self.E

    def _env(self, sender, mac=None, url="http://192.168.1.66:8775", bts=None):
        env = mock.MagicMock()
        env.origin_id, env.sender_instance_id, env.nonce = self.kid, sender, "n" * 64
        env.reconnect = {"new_url": url, **({"bind_mac": mac, "bind_ts": bts} if mac else {})}
        return env

    def _bind(self, **fields):
        for d in (self.O, self.E):
            p = d / f"{self.kid}.json"
            p.write_text(json.dumps({**json.loads(p.read_text()), **fields}))

    def test_unbound_mismatched_reflected_and_unproven_pushes_are_refused(self):
        url = "http://192.168.1.66:8775"
        self.assertEqual(self.rec._reconnect_binding_rejection(self._env(A_IID), url), "reconnect_peer_unbound")
        self._bind(_peer_instance_id=A_IID)
        self.assertEqual(self.rec._reconnect_binding_rejection(self._env(C_IID), url), "reconnect_peer_mismatch")
        self.assertIsNone(self.rec._reconnect_binding_rejection(self._env(A_IID), url))  # legacy-bound
        self._bind(_peer_bind_pub=self.keys.pub("A"))
        with self.keys.use("B"):
            self.assertEqual(self.rec._reconnect_binding_rejection(self._env(A_IID), url),
                             "reconnect_peer_unproven")
        now = int(time.time())
        with self.keys.use("A"):
            a_pub = self.keys.pub("A")
            mac = bind.bind_mac(self.keys.pub("B"), self.kid, self.canon(self.kid, url, "n" * 64, now, a_pub))
            old = bind.bind_mac(self.keys.pub("B"), self.kid,
                                self.canon(self.kid, url, "n" * 64, now - 900, a_pub))
        with self.keys.use("B"):
            self.assertIsNone(self.rec._reconnect_binding_rejection(self._env(A_IID, mac, bts=now), url))
            # Round 7: a recorded push replayed after its nonce aged out.
            self.assertEqual(self.rec._reconnect_binding_rejection(self._env(A_IID, old, bts=now - 900), url),
                             "reconnect_peer_unproven")

    def test_a_push_reflected_back_to_its_sender_does_not_verify(self):
        # Round 9: both ends derive the same binding secret. A push B sent to A
        # (MAC'd by B, direction = B's key) replayed back at B must fail there,
        # even with sender_instance_id forged to A's id.
        url = "http://192.168.1.20:8775"
        self._bind(_peer_instance_id=A_IID, _peer_bind_pub=self.keys.pub("A"))
        now = int(time.time())
        with self.keys.use("B"):
            b_pub = self.keys.pub("B")
            reflected = bind.bind_mac(self.keys.pub("A"), self.kid,
                                      self.canon(self.kid, url, "n" * 64, now, b_pub))
            self.assertEqual(self.rec._reconnect_binding_rejection(self._env(A_IID, reflected, bts=now), url),
                             "reconnect_peer_unproven")


class TestKeyFileRobustness(unittest.TestCase):
    def test_truncated_key_is_replaced_and_never_raises(self):
        root = Path(tempfile.mkdtemp())
        (root / "A.key").write_bytes(b"")  # power loss right after creation
        keys = _Keys(root)
        pub = keys.pub("A")
        self.assertIsNotNone(pub)
        self.assertEqual(len((root / "A.key").read_bytes()), 32)
        self.assertEqual(pub, keys.pub("A"), "stable after the repair")

    def test_unwritable_location_means_no_key_not_an_exception(self):
        keys = _Keys(Path("/proc/nonexistent-dir"))
        self.assertIsNone(keys.pub("A"))


class TestTokenAnchorsTheIssuerKey(unittest.TestCase):
    """Round 5: learning the peer key from a hello response was TOFU (anyone
    with the token could answer first over the relay). The issuer's key now
    travels in the signed token; responses may not replace or add it."""

    def test_token_carries_the_issuer_key_into_the_redeemers_records(self):
        keys = _Keys(Path(tempfile.mkdtemp()))
        with keys.use("A"):
            tok, s = ft.create_friendship_token(url="http://192.168.1.10:8775")
            a_pub = bind.local_bind_pub()
        red = ft.parse_and_verify(s)
        self.assertEqual(red.bind_pub, a_pub)
        self.assertEqual(ft.to_origin_dict(red)["_peer_bind_pub"], a_pub)
        self.assertEqual(ft.to_endpoint_dict(red)["_peer_bind_pub"], a_pub)

    def test_no_response_can_ever_install_a_key(self):
        # Round 6: after pairing every response path is spoofable (relay
        # fan-out, or a URL moved on a legacy pairing) — keys come only from
        # the token and the first ack.
        tmp = Path(tempfile.mkdtemp())
        e = tmp / "endpoints"; e.mkdir()
        (e / "k1.json").write_text(json.dumps({"_friendship": True, "_peer_instance_id": A_IID}))
        with mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            for via in ("relay", "direct"):
                ft.remember_peer_instance_id("k1", A_IID, endpoints_dir=e,
                                             peer_bind_pub="ab" * 32, via=via)
        self.assertNotIn("_peer_bind_pub", json.loads((e / "k1.json").read_text()))

    def test_pin_on_the_endpoint_protects_the_origin_too(self):
        tmp = Path(tempfile.mkdtemp())
        o, e = tmp / "origins", tmp / "endpoints"
        o.mkdir(); e.mkdir()
        (e / "k3.json").write_text(json.dumps({"_friendship": True, "instance_id": A_IID}))
        (o / "k3.json").write_text(json.dumps({"_friendship": True}))
        with mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            ft.remember_peer_instance_id("k3", C_IID, endpoints_dir=e, origins_dir=o)
        self.assertNotIn("_peer_instance_id", json.loads((o / "k3.json").read_text()))

    def test_instance_pin_blocks_binding_a_different_instance(self):
        tmp = Path(tempfile.mkdtemp())
        e = tmp / "endpoints"; e.mkdir()
        (e / "k2.json").write_text(json.dumps({"_friendship": True, "instance_id": A_IID}))
        with mock.patch.object(ft, "_local_instance_id", return_value=B_IID):
            ft.remember_peer_instance_id("k2", C_IID, endpoints_dir=e, peer_bind_pub="cd" * 32, via="direct")
        cfg = json.loads((e / "k2.json").read_text())
        self.assertNotIn("_peer_instance_id", cfg)
        self.assertNotIn("_peer_bind_pub", cfg)

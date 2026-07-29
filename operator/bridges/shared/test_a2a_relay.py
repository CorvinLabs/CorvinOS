"""ADR-0258 Stage 3 — A2A encrypted relay: crypto trust + routing state.

The implementation shipped without tests (the Session-1 work stopped before the
ADR's own Verification section was satisfied). This file proves the two
load-bearing properties the ADR calls non-negotiable:

  1. CONTENT CONFIDENTIALITY (a2a_friendship): a relay — or anyone holding only
     the routing credential — can NEVER read a payload it forwards. Only a peer
     holding the pairing's shared ``hmac_key`` can decrypt; a wrong key, a
     tampered ciphertext, or the relay-auth credential all fail closed
     (RelayDecryptError), never returning partial/unverified plaintext.
  2. ROUTING CORRECTNESS (a2a_relay.RelayState): trust-on-first-use slot
     pinning, bounded store-and-forward queue, and reconnect-resume — all
     without the relay ever needing durable state.

Run: python3 operator/bridges/shared/test_a2a_relay.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import unittest
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship as ft  # noqa: E402
import a2a_relay as relay  # noqa: E402


def _key() -> str:
    """A plausible pairing hmac_key (hex), the input every relay key derives from."""
    return os.urandom(32).hex()


# ── 1. Content confidentiality ───────────────────────────────────────────────

class TestRelayCryptoTrust(unittest.TestCase):
    def test_roundtrip(self):
        k = _key()
        pt = b'{"cmd":"ping","nonce":"abc"}'
        nonce_hex, ct_hex = ft.encrypt_for_relay(k, pt)
        self.assertEqual(ft.decrypt_from_relay(k, nonce_hex, ct_hex), pt)

    def test_ciphertext_is_not_plaintext(self):
        k = _key()
        pt = b"secret payload"
        _n, ct_hex = ft.encrypt_for_relay(k, pt)
        self.assertNotIn(b"secret", bytes.fromhex(ct_hex))

    def test_wrong_key_cannot_decrypt(self):
        # THE trust property: the relay never holds hmac_key, so it can never
        # read routed content. A different key fails closed.
        k1, k2 = _key(), _key()
        nonce_hex, ct_hex = ft.encrypt_for_relay(k1, b"for peer only")
        with self.assertRaises(ft.RelayDecryptError):
            ft.decrypt_from_relay(k2, nonce_hex, ct_hex)

    def test_relay_auth_credential_cannot_decrypt(self):
        # The relay DOES learn the routing credential (derive_relay_auth_key).
        # It must grant routing only — never content. Feeding it as the decrypt
        # key must fail (distinct label → distinct key).
        k = _key()
        auth = ft.derive_relay_auth_key(k)
        nonce_hex, ct_hex = ft.encrypt_for_relay(k, b"still secret")
        with self.assertRaises(ft.RelayDecryptError):
            ft.decrypt_from_relay(auth, nonce_hex, ct_hex)

    def test_enc_and_auth_keys_are_independent(self):
        k = _key()
        enc = ft._derive_enc_key(k)              # 32 raw bytes
        auth = ft.derive_relay_auth_key(k)        # hex
        self.assertNotEqual(enc.hex(), auth)
        self.assertEqual(len(enc), 32)

    def test_nonce_is_fresh_each_call(self):
        k = _key()
        n1, _ = ft.encrypt_for_relay(k, b"x")
        n2, _ = ft.encrypt_for_relay(k, b"x")
        self.assertNotEqual(n1, n2)  # no nonce reuse under a fixed key

    def test_tampered_ciphertext_fails_closed(self):
        k = _key()
        nonce_hex, ct_hex = ft.encrypt_for_relay(k, b"authentic")
        raw = bytearray(bytes.fromhex(ct_hex))
        raw[0] ^= 0x01  # flip one bit → GCM tag must reject
        with self.assertRaises(ft.RelayDecryptError):
            ft.decrypt_from_relay(k, nonce_hex, raw.hex())


# ── 2. Routing correctness ───────────────────────────────────────────────────

class _FakeWS:
    """Minimal WebSocket stand-in — records what the relay tried to forward."""
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("socket dead")
        self.sent.append(text)


class TestRelayRoutingState(unittest.TestCase):
    def setUp(self):
        self.state = relay.RelayState()

    def test_tofu_pin_first_registration_wins(self):
        self.assertIsNone(self.state.register("c1", "kidA", "authA"))
        # same credential reclaims (reconnect) — fine
        self.assertIsNone(self.state.register("c2", "kidA", "authA"))
        # a DIFFERENT credential for the same kid is rejected (TOFU conflict)
        self.assertEqual(self.state.register("c3", "kidA", "EVIL"), "auth_key_mismatch")

    def test_deliver_to_unknown_kid_is_dropped(self):
        # Nobody ever claimed this kid → nothing to queue against.
        self.assertEqual(asyncio.run(self.state.deliver("ghost", {"m": 1})), "dropped")

    def test_deliver_to_registered_but_offline_kid_queues(self):
        self.state.register("c1", "kidB", "authB")
        self.state.close_connection("c1")  # goes offline, slot un-claimed
        self.assertEqual(asyncio.run(self.state.deliver("kidB", {"m": 1})), "queued")
        # …and the queued message survives to the next registration
        flushed = self.state.flush_queue("kidB")
        self.assertEqual(flushed, [{"m": 1}])

    def test_deliver_to_live_connection_forwards(self):
        ws = _FakeWS()
        cid = self.state.open_connection(ws)
        self.state.register(cid, "kidC", "authC")
        self.assertEqual(asyncio.run(self.state.deliver("kidC", {"hello": "world"})), "delivered")
        self.assertEqual(len(ws.sent), 1)
        self.assertIn("hello", ws.sent[0])

    def test_send_failure_falls_back_to_queue(self):
        ws = _FakeWS(fail=True)
        cid = self.state.open_connection(ws)
        self.state.register(cid, "kidD", "authD")
        # live connection errors on send → must queue, not lose the message
        self.assertEqual(asyncio.run(self.state.deliver("kidD", {"m": 2})), "queued")

    def test_reconnect_resumes_with_same_credential_and_queue(self):
        # register, go offline, queue a message, reconnect with SAME auth →
        # the pin held and the queue is intact.
        self.state.register("c1", "kidE", "authE")
        self.state.close_connection("c1")
        asyncio.run(self.state.deliver("kidE", {"m": 3}))
        self.assertIsNone(self.state.register("c2", "kidE", "authE"))  # pin survived
        self.assertEqual(self.state.flush_queue("kidE"), [{"m": 3}])

    def test_expired_queue_entries_are_dropped_on_flush(self):
        self.state.register("c1", "kidF", "authF")
        self.state.close_connection("c1")
        asyncio.run(self.state.deliver("kidF", {"m": 4}))
        # force-expire the queued entry
        slot = self.state._slots["kidF"]
        slot.queue[0].expires_at = 0.0
        self.assertEqual(self.state.flush_queue("kidF"), [])

    def test_queue_is_bounded(self):
        self.state.register("c1", "kidG", "authG")
        self.state.close_connection("c1")
        # fill to capacity
        for i in range(relay._MAX_QUEUE_PER_KID):
            self.assertEqual(asyncio.run(self.state.deliver("kidG", {"i": i})), "queued")
        # one more must be dropped, not grow unbounded
        self.assertEqual(asyncio.run(self.state.deliver("kidG", {"i": 999})), "dropped")


if __name__ == "__main__":
    unittest.main()

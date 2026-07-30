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
import json
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
    """Minimal WebSocket stand-in — records what was forwarded. Provides BOTH
    `send_text` (FastAPI/Starlette server side, used by RelayState.deliver) and
    `send` (websockets client side, used by RelayListener)."""
    def __init__(self, fail: bool = False):
        self.sent: list[str] = []
        self.fail = fail

    async def send_text(self, text: str) -> None:
        if self.fail:
            raise RuntimeError("socket dead")
        self.sent.append(text)

    async def send(self, text: str) -> None:  # websockets client API
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

    def test_total_slot_table_is_bounded(self):
        """2026-07-30 DoS fix: the queue per-kid was always bounded, but the
        routing TABLE itself (self._slots) had no ceiling — an attacker
        opening connections, registering many fake kids, disconnecting, and
        repeating could grow it forever. A never-before-seen kid must be
        rejected once the process-wide cap is hit."""
        original = relay._MAX_TOTAL_SLOTS
        try:
            relay._MAX_TOTAL_SLOTS = 3
            self.assertIsNone(self.state.register("c1", "kidX1", "auth1"))
            self.assertIsNone(self.state.register("c1", "kidX2", "auth2"))
            self.assertIsNone(self.state.register("c1", "kidX3", "auth3"))
            # table is now at the (patched) cap of 3 distinct kids
            self.assertEqual(
                self.state.register("c1", "kidX4", "auth4"), "relay_at_capacity"
            )
        finally:
            relay._MAX_TOTAL_SLOTS = original

    def test_reregistering_an_existing_kid_is_never_blocked_by_capacity(self):
        """The cap must only ever refuse a BRAND NEW kid — an already-
        tracked kid reconnecting (even with the SAME credential, the normal
        case) must always succeed regardless of table size, or a legitimate
        peer would be locked out by an unrelated flood."""
        original = relay._MAX_TOTAL_SLOTS
        try:
            relay._MAX_TOTAL_SLOTS = 1
            self.assertIsNone(self.state.register("c1", "kidY1", "authY1"))
            # table is at cap (1), but kidY1 already exists — must still work
            self.assertIsNone(self.state.register("c2", "kidY1", "authY1"))
        finally:
            relay._MAX_TOTAL_SLOTS = original

    def test_queue_is_bounded(self):
        self.state.register("c1", "kidG", "authG")
        self.state.close_connection("c1")
        # fill to capacity
        for i in range(relay._MAX_QUEUE_PER_KID):
            self.assertEqual(asyncio.run(self.state.deliver("kidG", {"i": i})), "queued")
        # one more must be dropped, not grow unbounded
        self.assertEqual(asyncio.run(self.state.deliver("kidG", {"i": 999})), "dropped")

    def test_offline_reply_kid_is_reaped(self):
        """A3 (2026-07-30): each fallback send mints an ephemeral `*:reply:*`
        slot; before the reaper they were never evicted, so a busy relay wedged
        itself after _MAX_TOTAL_SLOTS legitimate sends. An offline reply slot
        must be reclaimed once idle past its short TTL."""
        cid = self.state.open_connection(_FakeWS())
        self.state.register(cid, "K:reply:t1", "auth1")
        self.state.note_registered_kid(cid, "K:reply:t1")
        self.state.close_connection(cid)  # offline
        self.state._slots["K:reply:t1"].last_active -= relay._REPLY_KID_IDLE_TTL_S + 10
        self.state._prune()
        self.assertNotIn("K:reply:t1", self.state._slots)

    def test_offline_slot_with_queue_is_not_reaped(self):
        """The reaper must never drop a slot still holding non-expired messages
        for a peer that may reconnect."""
        self.state.register("c1", "kidQ", "authQ")
        self.state.close_connection("c1")
        asyncio.run(self.state.deliver("kidQ", {"m": 1}))
        self.state._slots["kidQ"].last_active -= relay._SLOT_IDLE_TTL_S + 10
        self.state._prune()
        self.assertIn("kidQ", self.state._slots)  # queue still pending → kept

    def test_global_byte_budget_refuses_overflow(self):
        """A2 (2026-07-30): bounded slot COUNT alone still allowed slots × 512 KB
        of queued payloads. Once the global byte ceiling is hit, further queuing
        is dropped rather than growing RAM without limit."""
        original = relay._MAX_TOTAL_QUEUE_BYTES
        try:
            relay._MAX_TOTAL_QUEUE_BYTES = 50  # tiny ceiling
            self.state.register("c1", "kidB1", "auth1")
            self.state.close_connection("c1")
            asyncio.run(self.state.deliver("kidB1", {"m": "x" * 20}))
            self.assertEqual(
                asyncio.run(self.state.deliver("kidB1", {"m": "y" * 60})), "dropped")
        finally:
            relay._MAX_TOTAL_QUEUE_BYTES = original


# ── 3. Stage 2 — mesh-VPN address detection (ADR-0258 Verification) ──────────

class TestStage2MeshVpnDetection(unittest.TestCase):
    """ADR-0258 Stage 2: detect_mesh_vpn_address shells out to `tailscale ip -4`
    and degrades to "" on ANY failure — never raises, never blocks the ladder."""

    def test_absent_cli_returns_empty(self):
        import unittest.mock as mock
        with mock.patch("shutil.which", return_value=None):
            self.assertEqual(ft.detect_mesh_vpn_address(), "")

    def test_present_cli_returns_validated_ipv4(self):
        import subprocess
        import unittest.mock as mock
        fake = mock.Mock(returncode=0, stdout="100.64.1.2\n")
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(ft.detect_mesh_vpn_address(), "100.64.1.2")

    def test_garbage_output_is_rejected_not_returned(self):
        import unittest.mock as mock
        fake = mock.Mock(returncode=0, stdout="not-an-ip\n")
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), \
             mock.patch("subprocess.run", return_value=fake):
            self.assertEqual(ft.detect_mesh_vpn_address(), "")

    def test_cli_error_degrades_to_empty(self):
        import subprocess
        import unittest.mock as mock
        with mock.patch("shutil.which", return_value="/usr/bin/tailscale"), \
             mock.patch("subprocess.run", side_effect=subprocess.TimeoutExpired("tailscale", 2)):
            self.assertEqual(ft.detect_mesh_vpn_address(), "")


# ── 4. RelayListener robustness (adversarial review round 2 fixes) ───────────

class TestRelayListenerRobustness(unittest.TestCase):
    """A single malformed/hostile delivery must be dropped, NOT allowed to raise
    out of the receive loop (which would tear down the socket and cause a
    reconnect storm). And a good delivery must produce an encrypted response."""

    def _listener(self, receiver):
        return relay.RelayListener(relay_url="ws://x", receiver=receiver, origins_dir="/tmp/nope")

    def _good_delivery(self, hmac_key):
        nonce, ct = ft.encrypt_for_relay(hmac_key, b'{"task":"x"}')
        return {"to_kid": "meKid", "from_kid": "peerKid", "nonce": nonce,
                "ciphertext": ct, "task_id": "t1"}

    def test_receiver_exception_does_not_raise(self):
        import unittest.mock as mock
        k = _key()
        bad_receiver = mock.Mock()
        bad_receiver.receive.side_effect = RuntimeError("boom in receive()")
        lst = self._listener(bad_receiver)
        ws = _FakeWS()
        # Must NOT raise — the fix wraps receive/encrypt/send.
        asyncio.run(lst._handle_deliver(ws, self._good_delivery(k), {"meKid": k}))
        self.assertEqual(ws.sent, [])  # nothing sent, but no crash

    def test_good_delivery_sends_encrypted_response(self):
        import unittest.mock as mock
        k = _key()
        resp = mock.Mock()
        resp.to_dict.return_value = {"ok": True}
        receiver = mock.Mock()
        receiver.receive.return_value = resp
        lst = self._listener(receiver)
        ws = _FakeWS()
        asyncio.run(lst._handle_deliver(ws, self._good_delivery(k), {"meKid": k}))
        self.assertEqual(len(ws.sent), 1)
        sent = json.loads(ws.sent[0])
        # response is addressed back to the sender, and is encrypted (decrypts
        # back to the receiver's response dict with the shared key)
        self.assertEqual(sent["to_kid"], "peerKid")
        pt = ft.decrypt_from_relay(k, sent["nonce"], sent["ciphertext"])
        self.assertEqual(json.loads(pt), {"ok": True})

    def test_undecryptable_delivery_is_dropped(self):
        import unittest.mock as mock
        k = _key()
        receiver = mock.Mock()
        lst = self._listener(receiver)
        ws = _FakeWS()
        # ciphertext encrypted with a DIFFERENT key → decrypt fails → drop,
        # receiver never even called.
        bad = self._good_delivery(_key())
        asyncio.run(lst._handle_deliver(ws, bad, {"meKid": k}))
        receiver.receive.assert_not_called()
        self.assertEqual(ws.sent, [])

    def test_self_delivery_is_refused(self):
        """A1 (2026-07-30): the pairing kid + relay keys are identical on both
        peers, so a relay can route our OWN outbound task back to us. An envelope
        whose HMAC-covered sender_instance_id is our own local UUID must never be
        executed — otherwise we run the task we meant for the peer and hand back
        a self-signed 'reply'."""
        import unittest.mock as mock
        k = _key()
        receiver = mock.Mock()
        lst = self._listener(receiver)
        ws = _FakeWS()
        # envelope that claims to come from OUR instance id
        nonce, ct = ft.encrypt_for_relay(k, b'{"task":"x","sender_instance_id":"me-uuid"}')
        delivery = {"to_kid": "meKid", "from_kid": "peerKid", "nonce": nonce,
                    "ciphertext": ct, "task_id": "t1"}
        with mock.patch("instance_identity.get_instance_id", return_value="me-uuid"):
            asyncio.run(lst._handle_deliver(ws, delivery, {"meKid": k}))
        receiver.receive.assert_not_called()  # our own task — never executed
        self.assertEqual(ws.sent, [])


if __name__ == "__main__":
    unittest.main()

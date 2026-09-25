"""Reciprocal friendship handshake — bidirectional pairing in ONE round trip.

Regression coverage for the 2026-07-29 finding: the friendship-token flow as
originally shipped was NOT bidirectional. ``create_friendship_token()`` wrote
nothing to disk, so the issuer (A) never learned a redeemer (B) had imported
the token — a SECOND, entirely independent token exchange in reverse was the
only way to make A aware of B, producing two unlinked kid/keypairs instead of
one shared connection. Nor did EITHER side ever check real reachability
before reporting a connection as "ACTIVE" — a URL string being present was
sufficient (empirically reproduced in output/friendship_e2e_run.log: two
dead URLs with no listening server were both accepted).

This file proves, with REAL HTTP over real 127.0.0.1 sockets (same pattern as
test_a2a_bidirectional.py):

  * ONE token exchange (create -> import) leaves BOTH sides knowing about
    each other, under the SAME kid.
  * Both sides' ``state`` reflects a check EACH SIDE performed itself (a
    real signed ping), never a peer's self-report.
  * A redeemer that declares an unreachable URL is marked UNREACHABLE, not
    ACTIVE — url-presence alone must never be enough.
  * The ack endpoint is single-use (a2a_friendship-level: the pending record
    is consumed) and rejects a forged signature.

Run: ``python3 operator/bridges/shared/test_a2a_friendship_handshake.py``
"""
from __future__ import annotations

import hmac
import json
import time
import os
import sys
import tempfile
import unittest
import unittest.mock as mock
from dataclasses import dataclass
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship as ft  # noqa: E402
import a2a_http_server  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402

import spawn_gates  # noqa: E402
mock.patch.object(spawn_gates, "check_l44", lambda *a, **kw: None).start()

_SAVED_LICENSE_MODULES: dict[str, object | None] = {}


def setUpModule() -> None:
    # Same rationale as test_a2a_bidirectional.py's setUpModule — the free-tier
    # a2a_peers_max / compute-quota gates are irrelevant to wire-protocol tests.
    for name in ("license.compute_quota", "license.limits"):
        _SAVED_LICENSE_MODULES[name] = sys.modules.get(name)
        sys.modules[name] = None  # type: ignore[assignment]


def tearDownModule() -> None:
    for name, mod in _SAVED_LICENSE_MODULES.items():
        if mod is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = mod


# ── two instances, one process: distinct instance ids ─────────────────
#
# Acks carry an HMAC-covered sender_instance_id and a receiver refuses an ack
# whose sender is ITSELF (reflection, 2026-09-25). Both in-process "instances"
# would otherwise resolve the same process-wide instance id. Acks are only
# ever PROCESSED by A (in its server threads, or on the main thread inside an
# explicit ``as_side(IID_A)`` block) and SENT by B (main thread), so the id is
# chosen per thread, with an explicit override for direct calls.
import contextlib as _contextlib  # noqa: E402
import threading as _threading  # noqa: E402

IID_A = "11111111-1111-4111-8111-111111111111"
IID_B = "22222222-2222-4222-8222-222222222222"
_side_tls = _threading.local()


def _fake_local_instance_id() -> str:
    forced = getattr(_side_tls, "iid", None)
    if forced is not None:
        return forced
    return IID_B if _threading.current_thread() is _threading.main_thread() else IID_A


@_contextlib.contextmanager
def as_side(iid: str):
    prev = getattr(_side_tls, "iid", None)
    _side_tls.iid = iid
    try:
        yield
    finally:
        _side_tls.iid = prev


def patch_instance_ids(testcase: unittest.TestCase) -> None:
    patcher = mock.patch.object(ft, "_local_instance_id", _fake_local_instance_id)
    patcher.start()
    testcase.addCleanup(patcher.stop)


@dataclass
class _Instance:
    label: str
    origins_dir: Path
    endpoints_dir: Path
    pending_dir: Path
    server: object  # ThreadingHTTPServer
    base_url: str  # e.g. http://127.0.0.1:PORT (no /v1/a2a/... suffix)


def _build_instance(label: str, tmpdir: Path) -> _Instance:
    origins = tmpdir / label / "origins"
    endpoints = tmpdir / label / "endpoints"
    pending = tmpdir / label / "pending"
    for d in (origins, endpoints, pending):
        d.mkdir(parents=True)

    server = a2a_http_server.build_server(
        host="127.0.0.1", port=0,
        origins_dir=origins,
        endpoints_dir=endpoints,
        pending_dir=pending,
        nonce_store=rtr.NonceStore(),
        # Distinct receiver identities for the two in-process instances: the
        # sender refuses a signed response carrying its OWN instance id
        # (reflection guard), which one shared process id would trip.
        instance_id={"a": IID_A, "b": IID_B}.get(label),
    )
    a2a_http_server.serve_in_thread(server)
    host, port = server.server_address[:2]
    return _Instance(
        label=label, origins_dir=origins, endpoints_dir=endpoints,
        pending_dir=pending, server=server, base_url=f"http://{host}:{port}",
    )


class TestFriendshipHandshake(unittest.TestCase):
    """Two instances, one token exchange, real HTTP, both directions."""

    def setUp(self):
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.A = _build_instance("a", self.tmpdir)
        self.B = _build_instance("b", self.tmpdir)
        # The test sandbox only has a loopback interface, and 127.0.0.1 is
        # unconditionally "forbidden" by _ack_url_rejection_reason (as it must
        # be in production — a peer declaring loopback is a misconfiguration
        # or an SSRF attempt). The gate itself is covered by
        # TestAckUrlRejectionReason below with real address literals; here we
        # bypass it so the round-trip tests exercise the handshake/ping/state
        # logic instead of re-proving the host classifier.
        self._gate_patch = mock.patch.object(ft, "_ack_url_rejection_reason", lambda url: None)
        self._gate_patch.start()
        patch_instance_ids(self)

    def tearDown(self):
        self._gate_patch.stop()
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        self.A.server.shutdown()
        self.A.server.server_close()
        self.B.server.shutdown()
        self.B.server.server_close()
        self._tmp.cleanup()

    def _issue_token(self, issuer: _Instance) -> tuple[ft.FriendshipToken, str]:
        token, token_str = ft.create_friendship_token(url=issuer.base_url, label="issuer")
        ft.save_pending_friendship(token, pending_dir=issuer.pending_dir)
        return token, token_str

    def _import_token(self, redeemer: _Instance, token_str: str) -> ft.FriendshipToken:
        token = ft.parse_and_verify(token_str)
        origin_path = redeemer.origins_dir / f"{token.kid}.json"
        endpoint_path = redeemer.endpoints_dir / f"{token.kid}.json"
        origin_path.write_text(json.dumps(ft.to_origin_dict(token)), encoding="utf-8")
        origin_path.chmod(0o600)
        endpoint_path.write_text(json.dumps(ft.to_endpoint_dict(token)), encoding="utf-8")
        endpoint_path.chmod(0o600)
        return token

    # ── Core: one exchange, both sides linked ───────────────────────

    def test_one_token_exchange_links_both_sides(self):
        token, token_str = self._issue_token(self.A)
        redeemed = self._import_token(self.B, token_str)

        ack = ft.send_friendship_ack(redeemed, my_url=self.B.base_url)
        self.assertTrue(ack.get("ok"), msg=ack)
        self.assertTrue(ack.get("reachable"), msg=ack)

        # A now has ITS OWN record for B, under the SAME kid.
        a_origin = json.loads((self.A.origins_dir / f"{token.kid}.json").read_text("utf-8"))
        a_endpoint = json.loads((self.A.endpoints_dir / f"{token.kid}.json").read_text("utf-8"))
        self.assertEqual(a_origin["state"], "ACTIVE")
        self.assertEqual(a_endpoint["state"], "ACTIVE")
        self.assertTrue(a_endpoint["url"].startswith(self.B.base_url))

        # The pending record was consumed (single-use).
        self.assertIsNone(ft.load_pending_friendship(token.kid, pending_dir=self.A.pending_dir))

    def test_ordering_ack_before_ping_back(self):
        """A has NO record of B until a successful ack completes — a ping
        FROM B TO A before that ack would always fail (A's origin registry
        has nothing to look up yet). Assert that ordering explicitly: before
        the ack, B pinging A fails; after the ack, it succeeds — this is
        exactly why friendship_import must send the ack BEFORE attempting
        any direct ping of its own."""
        token, token_str = self._issue_token(self.A)
        redeemed = self._import_token(self.B, token_str)

        import remote_trigger_sender as rts
        sender = rts.RemoteTriggerSender(self.B.endpoints_dir)

        pre_ack = sender.ping(token.kid, timeout_s=5)
        self.assertFalse(pre_ack.reachable, msg="A must not know B before the ack")

        ack = ft.send_friendship_ack(redeemed, my_url=self.B.base_url)
        self.assertTrue(ack.get("ok"), msg=ack)

        post_ack = sender.ping(token.kid, timeout_s=5)
        self.assertTrue(post_ack.reachable, msg="A must recognize B's ping after a successful ack")

    # ── Negative: unreachable redeemer must NOT become ACTIVE ───────

    def test_unreachable_redeemer_url_marked_unreachable_not_active(self):
        token, token_str = self._issue_token(self.A)
        redeemed = ft.parse_and_verify(token_str)

        # Declare a URL nothing listens on (closed port on loopback).
        dead_url = "http://127.0.0.1:1"
        # Generous timeout: the issuer pings the dead URL back (and may try a
        # relay if an earlier test in a full run left the flag on) before it
        # answers; this test is about the resulting state, not latency.
        ack = ft.send_friendship_ack(redeemed, my_url=dead_url, timeout_s=30)
        self.assertTrue(ack.get("ok"), msg=ack)
        self.assertFalse(ack.get("reachable"), msg=ack)

        a_origin = json.loads((self.A.origins_dir / f"{token.kid}.json").read_text("utf-8"))
        a_endpoint = json.loads((self.A.endpoints_dir / f"{token.kid}.json").read_text("utf-8"))
        self.assertEqual(a_origin["state"], "UNREACHABLE")
        self.assertEqual(a_endpoint["state"], "UNREACHABLE")

    # ── Negative: forged / replayed ack must be rejected ────────────

    def test_forged_signature_rejected(self):
        token, token_str = self._issue_token(self.A)
        redeemed = ft.parse_and_verify(token_str)
        tampered = mock.Mock(wraps=redeemed)
        # Sign with a WRONG key — send_friendship_ack derives from token.key,
        # so corrupt the key it will sign with.
        from dataclasses import replace
        wrong_key_token = replace(redeemed, key="0" * 64)
        # Point at the real issuer URL but with the wrong shared key: the
        # issuer's pending record still has the ORIGINAL key, so the HMAC
        # will not match.
        wrong_key_token = replace(wrong_key_token, url=token.url)
        ack = ft.send_friendship_ack(wrong_key_token, my_url=self.B.base_url)
        self.assertFalse(ack.get("ok", True) and ack.get("reachable", True))
        # The issuer must NOT have created a record for this kid via a forged ack.
        self.assertFalse((self.A.origins_dir / f"{token.kid}.json").exists())

    def test_ack_for_unknown_kid_rejected(self):
        # No create_friendship_token()/save_pending_friendship() call for this
        # kid — the issuer has no pending record, so ANY ack must be an
        # opaque rejection (anti-enumeration), never a 500 or a silent write.
        token, _ = ft.create_friendship_token(url=self.A.base_url, kid="never-issued")
        ack = ft.send_friendship_ack(token, my_url=self.B.base_url)
        self.assertFalse(ack.get("ok"))
        self.assertFalse((self.A.origins_dir / "never-issued.json").exists())


class TestRetryFriendshipAck(unittest.TestCase):
    """2026-08-02: recheck can re-attempt the ack round trip using the
    ALREADY-DERIVED keys persisted on disk (the raw token key is discarded
    after import, so a genuine re-derivation is not possible) — closing the
    gap where `_peer_knows_us` stayed stuck false forever once the first
    ack attempt failed, even after the issuer became reachable again."""

    def setUp(self):
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        self._tmp = tempfile.TemporaryDirectory()
        self.tmpdir = Path(self._tmp.name)
        self.A = _build_instance("a", self.tmpdir)
        self.B = _build_instance("b", self.tmpdir)
        self._gate_patch = mock.patch.object(ft, "_ack_url_rejection_reason", lambda url: None)
        self._gate_patch.start()
        patch_instance_ids(self)

    def tearDown(self):
        self._gate_patch.stop()
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        self.A.server.shutdown()
        self.A.server.server_close()
        self.B.server.shutdown()
        self.B.server.server_close()
        self._tmp.cleanup()

    def _redeem_without_ack(self, issuer: _Instance, redeemer: _Instance) -> ft.FriendshipToken:
        """Same as TestFriendshipHandshake._import_token, but never calls
        send_friendship_ack — simulates B having imported at a time its own
        URL wasn't configured yet (the realistic trigger for a stuck
        'peer can't reach you back')."""
        token, token_str = ft.create_friendship_token(url=issuer.base_url, label="issuer")
        ft.save_pending_friendship(token, pending_dir=issuer.pending_dir)
        redeemed = ft.parse_and_verify(token_str)
        origin_path = redeemer.origins_dir / f"{redeemed.kid}.json"
        endpoint_path = redeemer.endpoints_dir / f"{redeemed.kid}.json"
        origin_path.write_text(json.dumps(ft.to_origin_dict(redeemed)), encoding="utf-8")
        origin_path.chmod(0o600)
        endpoint_path.write_text(json.dumps(ft.to_endpoint_dict(redeemed)), encoding="utf-8")
        endpoint_path.chmod(0o600)
        return redeemed

    def test_retry_succeeds_once_own_url_is_configured(self):
        redeemed = self._redeem_without_ack(self.A, self.B)
        # A has NOT recorded B yet — the ack was never attempted.
        self.assertFalse((self.A.endpoints_dir / f"{redeemed.kid}.json").exists())

        with mock.patch.object(ft, "get_my_url", return_value=self.B.base_url):
            result = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(result.get("reachable"), msg=result)
        # A now has its own record for B, exactly like a normal import-time ack.
        a_endpoint = json.loads((self.A.endpoints_dir / f"{redeemed.kid}.json").read_text("utf-8"))
        self.assertEqual(a_endpoint["state"], "ACTIVE")

    def test_retry_fails_without_own_url_configured(self):
        redeemed = self._redeem_without_ack(self.A, self.B)
        with mock.patch.object(ft, "get_my_url", return_value=None):
            result = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "no_own_url")

    def test_retry_on_unknown_kid_fails_closed(self):
        result = ft.retry_friendship_ack("never-imported", endpoints_dir=self.B.endpoints_dir)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "endpoint_unreadable")

    def test_ack_falls_back_to_relay_when_issuer_direct_url_is_dead(self):
        """2026-08-02: the B->A ack POST itself now has a relay fallback —
        without it, an issuer only reachable via relay could never complete
        the reciprocal handshake even with a relay configured on both sides."""
        redeemed = self._redeem_without_ack(self.A, self.B)
        # Point the issuer's stored URL at a dead port so the direct POST
        # genuinely fails with a connection error (not mocked away) —
        # exercises the real urllib except branch in _ack_round_trip.
        endpoint_path = self.B.endpoints_dir / f"{redeemed.kid}.json"
        cfg = json.loads(endpoint_path.read_text("utf-8"))
        dead_url = "http://127.0.0.1:1/v1/a2a/receive"
        cfg["url"] = dead_url
        endpoint_path.write_text(json.dumps(cfg), encoding="utf-8")

        def _fake_relay_deliver_and_wait(*, relay_url, my_kid, my_relay_auth_key,
                                          to_kid, nonce_hex, ciphertext_hex,
                                          task_id, timeout_s):
            # Decrypt what B encrypted, hand it to A's REAL ack-processing
            # core (the same function the direct HTTP route would call),
            # re-encrypt A's real signed response — exercises the actual
            # process_friendship_ack_request logic, not a stub.
            hmac_key, _recv_key = ft._derive_channel_keys(redeemed.key)
            plain = ft.decrypt_from_relay(hmac_key, nonce_hex, ciphertext_hex)
            req = json.loads(plain)
            with as_side(IID_A):  # A processes the ack (on this thread)
                status, resp = ft.process_friendship_ack_request(
                    req, pending_dir=self.A.pending_dir,
                    origins_dir=self.A.origins_dir, endpoints_dir=self.A.endpoints_dir,
                )
            self.assertEqual(status, 200, msg=resp)
            n, c = ft.encrypt_for_relay(hmac_key, json.dumps(resp).encode("utf-8"))
            return {"nonce": n, "ciphertext": c}

        with mock.patch.object(ft, "get_my_url", return_value=self.B.base_url), \
             mock.patch("corvin_console.feature_flags.is_enabled", return_value=True), \
             mock.patch.object(ft, "get_my_relay_url", return_value="ws://relay.example"), \
             mock.patch("a2a_relay.relay_deliver_and_wait",
                        side_effect=_fake_relay_deliver_and_wait):
            result = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)

        self.assertTrue(result.get("ok"), msg=result)
        self.assertTrue(result.get("reachable"), msg=result)
        a_endpoint = json.loads((self.A.endpoints_dir / f"{redeemed.kid}.json").read_text("utf-8"))
        self.assertEqual(a_endpoint["state"], "ACTIVE")

    def test_ack_relay_fallback_not_attempted_when_flag_off(self):
        redeemed = self._redeem_without_ack(self.A, self.B)
        endpoint_path = self.B.endpoints_dir / f"{redeemed.kid}.json"
        cfg = json.loads(endpoint_path.read_text("utf-8"))
        cfg["url"] = "http://127.0.0.1:1/v1/a2a/receive"
        endpoint_path.write_text(json.dumps(cfg), encoding="utf-8")

        with mock.patch.object(ft, "get_my_url", return_value=self.B.base_url), \
             mock.patch("corvin_console.feature_flags.is_enabled", return_value=False):
            result = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)
        self.assertFalse(result.get("ok"))
        self.assertEqual(result.get("error"), "unreachable")


class TestRepeatAck(unittest.TestCase):
    """2026-09-24: the issuer consumed its pending record on the FIRST ack and
    answered every later ack with the opaque 403 — so a redeemer whose first
    ack response was lost (or whose recheck retried while `_peer_knows_us` was
    still false) stayed "peer can't reach you back" forever, and a redeemer
    whose IP changed could never re-announce its URL this way. A repeat ack is
    now verified against the established origin's channel key."""

    # Same two-instance fixture, without re-running the parent's tests.
    setUp = TestRetryFriendshipAck.setUp
    tearDown = TestRetryFriendshipAck.tearDown
    _redeem_without_ack = TestRetryFriendshipAck._redeem_without_ack

    def _pair(self) -> ft.FriendshipToken:
        redeemed = self._redeem_without_ack(self.A, self.B)
        with mock.patch.object(ft, "get_my_url", return_value=self.B.base_url):
            first = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)
        self.assertTrue(first.get("ok"), msg=first)
        self.assertIsNone(ft.load_pending_friendship(redeemed.kid, pending_dir=self.A.pending_dir))
        return redeemed

    def _signed_ack(self, kid: str, peer_url: str, *, issued_at: int | None = None,
                    key: str | None = None, sender: str | None = IID_B) -> dict:
        """An ack as B builds it: legacy ``signature`` always, plus the v2
        signature over ``sender_instance_id`` unless ``sender`` is None (an
        ack from a previous-version peer)."""
        b_endpoint = json.loads((self.B.endpoints_dir / f"{kid}.json").read_text("utf-8"))
        k = bytes.fromhex(key or b_endpoint["hmac_key"])
        body = {"kid": kid, "issued_at": issued_at or int(time.time()), "peer_url": peer_url}
        canon = json.dumps(body, separators=(",", ":"), sort_keys=True)
        body["signature"] = hmac.new(k, canon.encode(), "sha256").hexdigest()
        if sender is not None:
            v2 = dict(body, sender_instance_id=sender)
            v2.pop("signature")
            canon2 = json.dumps(v2, separators=(",", ":"), sort_keys=True)
            body["sender_instance_id"] = sender
            body["signature_v2"] = hmac.new(k, canon2.encode(), "sha256").hexdigest()
            # v3 (ADR-2064): B's binding key + a MAC under the ECDH binding
            # secret, which A requires for any change once it knows B's key.
            # Both sides of this in-process sandbox share one key file, so
            # the peer's public key is our own.
            import a2a_binding as _bind
            pub = _bind.local_bind_pub()
            canon3 = ft._ack_canonical(kid, body["issued_at"], peer_url, None, sender, pub)
            body["bind_pub"] = pub
            body["signature_v3"] = hmac.new(k, canon3, "sha256").hexdigest()
            body["bind_mac"] = _bind.bind_mac(pub, kid, canon3)
        return body

    def _process(self, req: dict):
        with as_side(IID_A):  # A is the receiving side
            return ft.process_friendship_ack_request(
                req, pending_dir=self.A.pending_dir,
                origins_dir=self.A.origins_dir, endpoints_dir=self.A.endpoints_dir,
            )

    def test_repeat_ack_after_pending_consumed_succeeds(self):
        redeemed = self._pair()
        with mock.patch.object(ft, "get_my_url", return_value=self.B.base_url):
            again = ft.retry_friendship_ack(redeemed.kid, endpoints_dir=self.B.endpoints_dir)
        self.assertTrue(again.get("ok"), msg=again)
        self.assertTrue(again.get("reachable"), msg=again)
        self.assertEqual(again.get("via"), "direct")

    def test_repeat_ack_refreshes_stale_peer_url(self):
        redeemed = self._pair()
        ep_path = self.A.endpoints_dir / f"{redeemed.kid}.json"
        stale = json.loads(ep_path.read_text("utf-8"))
        stale["url"] = "http://192.0.2.1:8765/v1/a2a/receive"  # TEST-NET: dead
        stale["state"] = "UNREACHABLE"
        ep_path.write_text(json.dumps(stale), encoding="utf-8")

        # The loopback-only sandbox cannot satisfy the reconnect host gate
        # (loopback is forbidden, as it must be); that gate is exercised with
        # real address literals in test_a2a_friendship_security.py.
        with mock.patch.object(ft, "_reconnect_url_rejection_reason", lambda new, prev: None):
            status, resp = self._process(self._signed_ack(redeemed.kid, self.B.base_url))
        self.assertEqual(status, 200, resp)
        self.assertTrue(resp["reachable"])
        healed = json.loads(ep_path.read_text("utf-8"))
        self.assertEqual(healed["url"], self.B.base_url + "/v1/a2a/receive")
        self.assertEqual(healed["state"], "ACTIVE")
        # everything else on the record is untouched
        self.assertEqual(healed["hmac_key"], stale["hmac_key"])

    def test_repeat_ack_with_wrong_key_is_opaque_403_and_changes_nothing(self):
        redeemed = self._pair()
        ep_path = self.A.endpoints_dir / f"{redeemed.kid}.json"
        before = ep_path.read_text("utf-8")
        status, resp = self._process(
            self._signed_ack(redeemed.kid, "http://192.0.2.9:8765", key="c" * 64))
        self.assertEqual((status, resp), (403, {"reason": "ack_rejected"}))
        self.assertEqual(ep_path.read_text("utf-8"), before)

    def test_repeat_ack_never_resurrects_a_disabled_friendship(self):
        redeemed = self._pair()
        o_path = self.A.origins_dir / f"{redeemed.kid}.json"
        origin = json.loads(o_path.read_text("utf-8"))
        origin["enabled"] = False
        o_path.write_text(json.dumps(origin), encoding="utf-8")
        status, resp = self._process(self._signed_ack(redeemed.kid, self.B.base_url))
        self.assertEqual((status, resp), (403, {"reason": "ack_rejected"}))

    def test_stale_repeat_ack_rejected(self):
        redeemed = self._pair()
        status, resp = self._process(self._signed_ack(
            redeemed.kid, self.B.base_url, issued_at=int(time.time()) - 600))
        self.assertEqual((status, resp), (400, {"reason": "stale_ack"}))

    def test_repeat_ack_rebuilds_a_missing_endpoint_record(self):
        redeemed = self._pair()
        ep_path = self.A.endpoints_dir / f"{redeemed.kid}.json"
        ep_path.unlink()
        status, resp = self._process(self._signed_ack(redeemed.kid, self.B.base_url))
        self.assertEqual(status, 200, resp)
        rebuilt = json.loads(ep_path.read_text("utf-8"))
        self.assertEqual(rebuilt["url"], self.B.base_url + "/v1/a2a/receive")
        self.assertEqual(rebuilt["origin_id_for_send"], redeemed.kid)
        self.assertTrue(resp["reachable"])


class TestAckUrlRejectionReason(unittest.TestCase):
    """Pure unit coverage for the first-pairing host gate — the ONE piece
    the E2E tests above deliberately bypass (loopback-only sandbox)."""

    def test_loopback_forbidden(self):
        self.assertEqual(ft._ack_url_rejection_reason("http://127.0.0.1:8080"),
                         "ack_url_forbidden_host")

    def test_link_local_metadata_forbidden(self):
        self.assertEqual(ft._ack_url_rejection_reason("http://169.254.169.254/"),
                         "ack_url_forbidden_host")

    def test_lan_allowed_on_first_pairing(self):
        # Unlike _reconnect_url_rejection_reason, a brand-new pairing between
        # two LAN machines (the common home/office case) must be allowed.
        self.assertIsNone(ft._ack_url_rejection_reason("http://192.168.1.50:8765"))
        self.assertIsNone(ft._ack_url_rejection_reason("http://10.0.0.5:8765"))

    def test_global_allowed(self):
        self.assertIsNone(ft._ack_url_rejection_reason("https://example.com"))

    def test_bad_scheme_rejected(self):
        self.assertEqual(ft._ack_url_rejection_reason("ftp://example.com"),
                         "ack_url_bad_scheme")

    def test_onion_rejected(self):
        self.assertEqual(
            ft._ack_url_rejection_reason("http://abc123.onion"),
            "ack_url_forbidden_host",
        )


if __name__ == "__main__":
    unittest.main()

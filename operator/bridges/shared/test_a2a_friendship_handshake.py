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

import json
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
        ack = ft.send_friendship_ack(redeemed, my_url=dead_url)
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

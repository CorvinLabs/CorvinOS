"""Regression tests for the 2026-09-25 A2A friendship/pairing review.

Each class names the finding it pins. Everything here is local: temp dirs,
real ``a2a_friendship`` / ``a2a_invite`` / ``a2a_invite_registry`` code, and
at most a loopback HTTP listener that must NOT be hit. The issuer's ping-back
(ADR-0199) is replaced by a stub sender so no test ever dials a LAN address.

Run: ``pytest corvin_operator/bridges/shared/test_a2a_friendship_security.py``
"""
from __future__ import annotations

import base64
import hmac
import json
import os
import secrets
import sys
import tempfile
import threading
import time
import types
import unittest
import unittest.mock as mock
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship as ft  # noqa: E402
import a2a_invite as inv  # noqa: E402
import a2a_invite_registry as reg  # noqa: E402

IID_A = "11111111-1111-4111-8111-111111111111"   # the issuer
IID_B = "22222222-2222-4222-8222-222222222222"   # the legitimate redeemer
IID_C = "33333333-3333-4333-8333-333333333333"   # a second holder of a leaked token


# ── helpers ─────────────────────────────────────────────────────────────

def _forge_token(payload: dict) -> str:
    """A friendship token whose (self-)signature is VALID for ``payload`` —
    exactly what a malicious issuer can produce."""
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(ft._derive_sig_key(payload["key"]), pb, "sha256").digest()
    return ft.TOKEN_PREFIX + ft._b64_enc(pb) + "." + ft._b64_enc(sig)


def _ack_body(key_hex: str, kid: str, peer_url: str, *, sender: str | None,
              label: str | None = None, issued_at: int | None = None) -> dict:
    """An ack exactly as ``_ack_round_trip`` builds it (legacy signature,
    plus the v2 signature when ``sender`` is given)."""
    hk, _ = ft._derive_channel_keys(key_hex)
    k = bytes.fromhex(hk)
    ts = issued_at or int(time.time())
    body: dict = {"kid": kid, "issued_at": ts, "peer_url": peer_url}
    if label:
        body["peer_label"] = label
    body["signature"] = hmac.new(k, ft._ack_canonical(kid, ts, peer_url, label), "sha256").hexdigest()
    if sender is not None:
        body["sender_instance_id"] = sender
        body["signature_v2"] = hmac.new(
            k, ft._ack_canonical(kid, ts, peer_url, label, sender), "sha256").hexdigest()
    return body


class _StubPing:
    reachable = False
    via = None


class _StubSender:
    def __init__(self, *a, **kw):
        pass

    def ping(self, *a, **kw):
        return _StubPing()


_STUB_RTS = types.ModuleType("remote_trigger_sender")
_STUB_RTS.RemoteTriggerSender = _StubSender        # type: ignore[attr-defined]
_STUB_RTS.RemoteEndpointRegistry = lambda *a, **kw: None  # type: ignore[attr-defined]


class _Sandbox(unittest.TestCase):
    """Temp origins/endpoints/pending dirs, a private audit chain, no network
    for the ping-back, and a settable "own instance id"."""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-sec-")
        self.root = Path(self._tmp.name)
        self.od, self.ed, self.pd = self.root / "o", self.root / "e", self.root / "p"
        for d in (self.od, self.ed, self.pd):
            d.mkdir()
        self.audit_file = self.root / "audit" / "audit.jsonl"
        env = mock.patch.dict(os.environ, {
            "VOICE_AUDIT_PATH": str(self.audit_file),
            "CORVIN_HOME": str(self.root / "home"),
        })
        env.start()
        self.addCleanup(env.stop)
        # Unlimited a2a_peers_max unless a test sets a limit — the real
        # license.validator resolves to the free tier (1 peer) when importable.
        lic = types.ModuleType("license")
        val = types.ModuleType("license.validator")
        val.get_limit = lambda feature: None  # type: ignore[attr-defined]
        mods = mock.patch.dict(sys.modules, {"remote_trigger_sender": _STUB_RTS,
                                             "license": lic, "license.validator": val})
        mods.start()
        self.addCleanup(mods.stop)
        self.own_iid = IID_A
        iid = mock.patch.object(ft, "_local_instance_id", lambda: self.own_iid)
        iid.start()
        self.addCleanup(iid.stop)
        self.addCleanup(self._tmp.cleanup)

    def issue(self, url: str | None = "http://10.0.0.1:8765") -> ft.FriendshipToken:
        tok, _ = ft.create_friendship_token(url=url, label="issuer")
        ft.save_pending_friendship(tok, pending_dir=self.pd)
        return tok

    def ack(self, tok: ft.FriendshipToken, peer_url: str, *, sender: str | None = IID_B,
            **kw) -> tuple[int, dict]:
        return ft.process_friendship_ack_request(
            _ack_body(tok.key, tok.kid, peer_url, sender=sender, **kw),
            pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed,
        )

    def endpoint_url(self, kid: str) -> str:
        return json.loads((self.ed / f"{kid}.json").read_text("utf-8"))["url"]


# ── Finding 1 — kid / oid path traversal ────────────────────────────────

class TestKidIsAFilenameSafeIdentifier(_Sandbox):
    def test_parse_rejects_validly_signed_traversal_kid(self):
        tok = _forge_token({"kid": "../../escape/pwned", "key": secrets.token_hex(32), "v": 1})
        with self.assertRaises(ft.FriendshipError):
            ft.parse_and_verify(tok)

    def test_parse_accepts_uuid_kid(self):
        _t, s = ft.create_friendship_token(url="http://10.0.0.1:8765")
        self.assertTrue(ft.is_valid_kid(ft.parse_and_verify(s).kid))

    def test_create_rejects_bad_kid(self):
        for bad in ("../x", "a/b", "x" * 65, "a.b", "..", "a\\b"):  # "" = mint a uuid
            with self.assertRaises(ft.FriendshipError, msg=bad):
                ft.create_friendship_token(url=None, kid=bad)

    def test_kid_path_refuses_traversal(self):
        with self.assertRaises(ft.FriendshipError):
            ft.kid_path(self.od, "../../etc/x")

    def test_ack_with_traversal_kid_touches_no_file(self):
        outside = self.root / "escape"
        body = _ack_body(secrets.token_hex(32), "../escape/x", "http://10.0.0.2:8765", sender=IID_B)
        status, resp = ft.process_friendship_ack_request(
            body, pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed)
        self.assertEqual((status, resp), (400, {"reason": "invalid_kid"}))
        self.assertFalse(outside.exists())

    def test_pending_helpers_refuse_traversal(self):
        self.assertIsNone(ft.load_pending_friendship("../p/x", pending_dir=self.pd))
        self.assertFalse(ft.delete_pending_friendship("../p/x", pending_dir=self.pd))
        bad = ft.FriendshipToken(kid="../x", key=secrets.token_hex(32), url=None,
                                 label=None, expires=None)
        with self.assertRaises(ft.FriendshipError):
            ft.save_pending_friendship(bad, pending_dir=self.pd)


class TestInviteOidIsAFilenameSafeIdentifier(unittest.TestCase):
    def _token(self, oid: str) -> str:
        p = {"v": 1, "iid": "someone-else", "oid": oid, "url": "http://10.1.1.1:8765",
             "rp": "/v1/a2a/receive", "hk": secrets.token_hex(32), "rk": secrets.token_hex(32),
             "pa": ["assistant"], "mt": 300, "iat": 1.0}
        pb = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        return inv.TOKEN_PREFIX + inv._b64_enc(pb) + "." + inv._b64_enc(b"\x00" * 32)

    def test_parse_invite_rejects_traversal_oid(self):
        for bad in ("../../escape2/x", "a/b", "..", ".hidden", "a..b", "x" * 65, ""):
            with self.assertRaises(inv.InviteError, msg=bad):
                inv.parse_invite(self._token(bad))

    def test_parse_invite_keeps_accepting_console_minted_oids(self):
        # CLIInviteRequest.origin_id allows dots after a leading alnum.
        tok, _payload, _sig = inv.parse_invite(self._token("peer.lan-1_A"))
        self.assertEqual(tok.oid, "peer.lan-1_A")

    def test_generate_rejects_bad_oid(self):
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {"CORVIN_A2A_INVITE_MASTER_KEY_PATH": f"{d}/mk"}):
            with self.assertRaises(inv.InviteError):
                inv.generate_invite(iid="i", origin_id="../x", url="http://10.0.0.1:8765")


# ── Findings 2 + 3 — repeat-ack takeover and ack reflection ─────────────

class TestAckSenderBinding(_Sandbox):
    def test_first_ack_binds_the_authenticated_sender(self):
        tok = self.issue()
        status, _ = self.ack(tok, "http://10.0.0.2:8765")
        self.assertEqual(status, 200)
        for d in (self.od, self.ed):
            self.assertEqual(json.loads((d / f"{tok.kid}.json").read_text())["_peer_instance_id"], IID_B)

    def test_second_token_holder_cannot_rewrite_the_url(self):
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)
        status, resp = self.ack(tok, "http://10.0.0.66:8765", sender=IID_C)
        self.assertEqual((status, resp), (403, {"reason": "ack_peer_mismatch"}))
        self.assertEqual(self.endpoint_url(tok.kid), "http://10.0.0.2:8765/v1/a2a/receive")

    def test_legacy_repeat_ack_cannot_rewrite_the_url(self):
        # The original review repro (t2): first ack https/global, then a
        # token holder repoints it at plain-http LAN through the repeat path.
        tok = self.issue()
        self.assertEqual(self.ack(tok, "https://8.8.4.4", sender=None)[0], 200)
        status, resp = self.ack(tok, "http://192.168.1.1:80", sender=None, label="C")
        self.assertEqual((status, resp), (403, {"reason": "ack_peer_unbound"}))
        self.assertEqual(self.endpoint_url(tok.kid), "https://8.8.4.4/v1/a2a/receive")

    def test_legacy_keepalive_with_unchanged_url_still_accepted(self):
        # Backward compatibility: a previous-version peer re-acks (recheck)
        # with the URL it already registered.
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765", sender=None)[0], 200)
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765", sender=None)[0], 200)
        # ... also once the pairing is bound to a v2 peer.
        tok2 = self.issue()
        self.assertEqual(self.ack(tok2, "http://10.0.0.3:8765")[0], 200)
        self.assertEqual(self.ack(tok2, "http://10.0.0.3:8765", sender=None)[0], 200)

    def test_bound_peer_url_change_runs_the_reconnect_gate(self):
        tok = self.issue()
        self.assertEqual(self.ack(tok, "https://8.8.4.4")[0], 200)
        status, resp = self.ack(tok, "http://192.168.1.1:80")
        self.assertEqual((status, resp), (400, {"reason": "reconnect_url_scheme_downgrade"}))
        self.assertEqual(self.endpoint_url(tok.kid), "https://8.8.4.4/v1/a2a/receive")

    def test_bound_peer_lan_renumbering_is_accepted(self):
        # The product case (zero-config E2E test_3): same peer, new LAN port/IP.
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)
        self.assertEqual(self.ack(tok, "http://10.0.0.9:9000")[0], 200)
        self.assertEqual(self.endpoint_url(tok.kid), "http://10.0.0.9:9000/v1/a2a/receive")

    def test_pre_binding_pairing_is_bound_only_by_our_verified_outbound_hello(self):
        # Round 3: an inbound ack never binds (a URL-repeating ack from a
        # leaked-token holder used to bind, the next one re-pointed).
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765", sender=None)[0], 200)
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)   # keep-alive, no bind
        self.assertNotIn("_peer_instance_id", json.loads((self.od / f"{tok.kid}.json").read_text()))
        self.assertEqual(self.ack(tok, "http://10.0.0.4:8765")[0], 403)   # unbound: no move
        # Our own hello's recv_key-verified response binds the peer ...
        self.assertTrue(ft.remember_peer_instance_id(tok.kid, IID_B, endpoints_dir=self.ed,
                                                     origins_dir=self.od))
        self.assertEqual(self.ack(tok, "http://10.0.0.4:8765")[0], 200)   # ... which may then move
        self.assertEqual(self.ack(tok, "http://10.0.0.66:8765", sender=IID_C)[0], 403)
        self.assertEqual(self.endpoint_url(tok.kid), "http://10.0.0.4:8765/v1/a2a/receive")

    def test_leaked_token_cannot_bind_and_redirect_an_unbound_pairing(self):
        # Round 2: an unbound (pre-binding / old-peer) pairing — an attacker's
        # v2 ack must not bind itself AND move the endpoint in one message.
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765", sender=None)[0], 200)
        status, resp = self.ack(tok, "http://10.0.0.66:8765", sender=IID_C)
        self.assertEqual((status, resp), (403, {"reason": "ack_peer_unbound"}))
        self.assertEqual(self.endpoint_url(tok.kid), "http://10.0.0.2:8765/v1/a2a/receive")
        self.assertNotIn("_peer_instance_id", json.loads((self.od / f"{tok.kid}.json").read_text()))

    def test_tampered_sender_id_fails_the_v2_signature(self):
        tok = self.issue()
        body = _ack_body(tok.key, tok.kid, "http://10.0.0.2:8765", sender=IID_B)
        body["sender_instance_id"] = IID_C
        status, _ = ft.process_friendship_ack_request(
            body, pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed)
        self.assertEqual(status, 403)
        self.assertFalse((self.od / f"{tok.kid}.json").exists())

    def test_new_ack_still_verifies_under_the_previous_version_canonical(self):
        # A peer running the previous version verifies ONLY `signature` over
        # {kid, issued_at, peer_url[, peer_label]} and ignores other fields.
        tok = self.issue()
        hk, _ = ft._derive_channel_keys(tok.key)
        body = _ack_body(tok.key, tok.kid, "http://10.0.0.2:8765", sender=IID_B, label="B")
        old_canonical = json.dumps(
            {k: body[k] for k in ("kid", "issued_at", "peer_url", "peer_label")},
            separators=(",", ":"), sort_keys=True).encode()
        self.assertEqual(body["signature"],
                         hmac.new(bytes.fromhex(hk), old_canonical, "sha256").hexdigest())


class TestAckReflection(_Sandbox):
    def _redeemer_side(self) -> ft.FriendshipToken:
        """B's files after importing A's token (A is at 192.168.50.10)."""
        self.own_iid = IID_B
        _tok, s = ft.create_friendship_token(url="http://192.168.50.10:8765")
        t = ft.parse_and_verify(s)
        ft._atomic_write(self.od / f"{t.kid}.json", ft.to_origin_dict(t))
        ft._atomic_write(self.ed / f"{t.kid}.json", ft.to_endpoint_dict(t))
        return t

    def test_own_v2_ack_reflected_back_is_refused(self):
        t = self._redeemer_side()
        status, resp = self.ack(t, "http://192.168.50.20:8765", sender=IID_B)
        self.assertEqual((status, resp), (400, {"reason": "ack_reflected"}))
        self.assertEqual(self.endpoint_url(t.kid), "http://192.168.50.10:8765/v1/a2a/receive")

    def test_own_legacy_ack_reflected_back_cannot_rewrite(self):
        # The original review repro (t7).
        t = self._redeemer_side()
        status, _ = self.ack(t, "http://192.168.50.20:8765", sender=None)
        self.assertEqual(status, 403)
        self.assertEqual(self.endpoint_url(t.kid), "http://192.168.50.10:8765/v1/a2a/receive")

    def test_reflection_refused_on_the_first_ack_path_too(self):
        tok = self.issue()
        status, resp = self.ack(tok, "http://10.0.0.2:8765", sender=IID_A)
        self.assertEqual((status, resp), (400, {"reason": "ack_reflected"}))
        self.assertFalse((self.od / f"{tok.kid}.json").exists())
        self.assertIsNotNone(ft.load_pending_friendship(tok.kid, pending_dir=self.pd))

    def test_redeemer_binds_issuer_from_a_verified_ack_response(self):
        t = self._redeemer_side()
        self.assertTrue(ft.remember_peer_instance_id(
            t.kid, IID_A, endpoints_dir=self.ed, origins_dir=self.od))
        # the issuer's hello (a repeat ack on B) from IID_A is accepted; from C not
        self.assertEqual(self.ack(t, "http://192.168.50.10:8765", sender=IID_A)[0], 200)
        self.assertEqual(self.ack(t, "http://192.168.50.66:8765", sender=IID_C)[0], 403)
        # never binds our own id, never overwrites an existing binding
        self.assertFalse(ft.remember_peer_instance_id(t.kid, IID_B, endpoints_dir=self.ed))
        self.assertFalse(ft.remember_peer_instance_id(t.kid, IID_C, endpoints_dir=self.ed))


# ── Finding 4 — redeemer SSRF (library layer) ───────────────────────────

class TestAckNeverPostsToForbiddenHost(_Sandbox):
    def test_ack_round_trip_skips_a_loopback_issuer_url(self):
        hits: list[str] = []

        class H(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                hits.append(self.path)
                self.send_response(200)
                self.end_headers()
                self.wfile.write(b"{}")

            def log_message(self, *a):
                pass

        srv = HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        self.addCleanup(srv.server_close)
        self.addCleanup(srv.shutdown)
        _tok, s = ft.create_friendship_token(url=f"http://127.0.0.1:{srv.server_port}/internal-admin")
        with mock.patch.dict(os.environ, {"CORVIN_A2A_RELAY_URL": "off"}):
            res = ft.send_friendship_ack(ft.parse_and_verify(s), my_url="http://10.9.9.9:8765",
                                         timeout_s=2)
        self.assertFalse(res["ok"])
        self.assertEqual(hits, [])


# ── Finding 5 — pending records: revocable, expire on load ──────────────

class TestPendingLifecycle(_Sandbox):
    def test_expired_pending_record_is_deleted_on_load(self):
        tok, _ = ft.create_friendship_token(url=None, ttl_seconds=-3600)
        ft.save_pending_friendship(tok, pending_dir=self.pd)
        self.assertIsNone(ft.load_pending_friendship(tok.kid, pending_dir=self.pd))
        self.assertFalse((self.pd / f"{tok.kid}.json").exists())

    def test_revoked_unredeemed_token_can_no_longer_pair(self):
        tok = self.issue()
        self.assertTrue(ft.delete_pending_friendship(tok.kid, pending_dir=self.pd))
        self.assertFalse(ft.delete_pending_friendship(tok.kid, pending_dir=self.pd))
        status, _ = self.ack(tok, "http://10.0.0.2:8765")
        self.assertEqual(status, 403)
        self.assertFalse((self.od / f"{tok.kid}.json").exists())


# ── Finding 6 — a2a_peers_max: after the signature, under the lock ──────

class TestPeersMaxAtomic(_Sandbox):
    def _limit(self, n):
        lic = types.ModuleType("license")
        val = types.ModuleType("license.validator")
        val.get_limit = lambda feature: n  # type: ignore[attr-defined]
        p = mock.patch.dict(sys.modules, {"license": lic, "license.validator": val})
        p.start()
        self.addCleanup(p.stop)

    def test_ten_concurrent_acks_admit_exactly_the_limit(self):
        # The original review repro (t4): a slow URL gate widened the window.
        self._limit(1)
        real_gate = ft._ack_url_rejection_reason

        def slow_gate(u):
            time.sleep(0.2)
            return real_gate(u)

        toks = [self.issue() for _ in range(10)]
        results: list[int] = []
        with mock.patch.object(ft, "_ack_url_rejection_reason", slow_gate):
            threads = [threading.Thread(target=lambda t=t, i=i: results.append(
                self.ack(t, f"http://10.0.1.{i + 1}:8765")[0])) for i, t in enumerate(toks)]
            for th in threads:
                th.start()
            for th in threads:
                th.join()
        self.assertEqual(sorted(results), [200] + [402] * 9)
        self.assertEqual(len(list(self.od.glob("*.json"))), 1)

    def test_limit_is_not_an_oracle_for_forged_acks(self):
        self._limit(0)
        tok = self.issue()
        forged = _ack_body(secrets.token_hex(32), tok.kid, "http://10.0.0.2:8765", sender=IID_B)
        status, _ = ft.process_friendship_ack_request(
            forged, pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed)
        self.assertEqual(status, 403)                     # not 402
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 402)
        self.assertIsNotNone(ft.load_pending_friendship(tok.kid, pending_dir=self.pd))

    def test_rewriting_an_existing_kid_is_not_counted(self):
        self._limit(1)
        tok = self.issue()
        ft._atomic_write(self.od / f"{tok.kid}.json", {"origin_id": tok.kid})
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)


# ── Finding 8 — audit-first pairing events ──────────────────────────────

class TestPairingAudit(_Sandbox):
    def test_first_ack_audits_before_any_file_is_written(self):
        tok = self.issue()
        seen: list[tuple[str, dict, bool]] = []

        def spy(event, severity, **details):
            seen.append((event, details, (self.od / f"{tok.kid}.json").exists()))

        with mock.patch.object(ft, "_audit_pairing_event", spy):
            self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)
        self.assertEqual(len(seen), 1)
        event, details, file_existed = seen[0]
        self.assertEqual(event, "A2A.friendship_paired")
        self.assertFalse(file_existed, "audit must precede the write")
        self.assertEqual(details, {"endpoint_id": tok.kid, "pairing": "first",
                                   "url_changed": True, "reason": "ack_verified",
                                   "peer_bound": True})

    def test_repeat_url_change_audits_before_the_rewrite(self):
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)
        seen: list[tuple[str, dict, str]] = []

        def spy(event, severity, **details):
            seen.append((event, details, self.endpoint_url(tok.kid)))

        with mock.patch.object(ft, "_audit_pairing_event", spy):
            self.assertEqual(self.ack(tok, "http://10.0.0.7:8765")[0], 200)
        self.assertEqual(seen, [("A2A.friendship_url_updated",
                                 {"endpoint_id": tok.kid, "pairing": "repeat",
                                  "url_changed": True, "reason": "peer_reannounced",
                                  "peer_bound": True},
                                 "http://10.0.0.2:8765/v1/a2a/receive")])
        # an unchanged keep-alive writes nothing and audits nothing
        seen.clear()
        with mock.patch.object(ft, "_audit_pairing_event", spy):
            self.assertEqual(self.ack(tok, "http://10.0.0.7:8765")[0], 200)
        self.assertEqual(seen, [])

    def test_failed_audit_blocks_the_pairing(self):
        tok = self.issue()

        def boom(*a, **kw):
            raise ft.FriendshipAuditError("chain down")

        with mock.patch.object(ft, "_audit_pairing_event", boom):
            status, resp = self.ack(tok, "http://10.0.0.2:8765")
        self.assertEqual((status, resp), (503, {"reason": "audit_unavailable"}))
        self.assertFalse((self.od / f"{tok.kid}.json").exists())
        self.assertIsNotNone(ft.load_pending_friendship(tok.kid, pending_dir=self.pd))

    def test_record_lands_in_the_hash_chain(self):
        if ft._audit_writer() is None:
            self.skipTest("no hash-chained writer (forge) in this environment")
        tok = self.issue()
        self.assertEqual(self.ack(tok, "http://10.0.0.2:8765")[0], 200)
        recs = [json.loads(line) for line in self.audit_file.read_text().splitlines() if line.strip()]
        paired = [r for r in recs if r.get("event") == "A2A.friendship_paired"
                  or r.get("event_type") == "A2A.friendship_paired"]
        self.assertEqual(len(paired), 1, recs)
        self.assertTrue(paired[0].get("hash"))


# ── Finding 7 — invite registry: locked load-modify-save, single use ────

class TestInviteRegistryAtomic(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-reg-")
        self.addCleanup(self._tmp.cleanup)
        self.r = reg.InviteRegistry(Path(self._tmp.name) / "invites.json")

    def _entry(self, ikey, su=False):
        return reg.InviteEntry(ikey=ikey, oid="o", lbl="", iat=1, exp=None, su=su)

    def test_concurrent_writers_lose_nothing(self):
        # The original review repro (t8), with a load delay that keeps the
        # serialised total under the 2 s lock deadline.
        self.r.create(self._entry("victim"))
        orig = reg.InviteRegistry._load

        def slow(self_):
            d = orig(self_)
            time.sleep(0.05)
            return d

        with mock.patch.object(reg.InviteRegistry, "_load", slow):
            ths = [threading.Thread(target=self.r.revoke, args=("victim",))] + [
                threading.Thread(target=self.r.create, args=(self._entry(f"k{i}"),))
                for i in range(10)]
            for t in ths:
                t.start()
            for t in ths:
                t.join()
        d = self.r._load()
        self.assertEqual(len(d), 11)
        self.assertTrue(d["victim"]["revoked"])

    def test_single_use_invite_is_claimed_exactly_once(self):
        self.r.create(self._entry("su", su=True))
        out: list[str] = []
        ths = [threading.Thread(target=lambda: out.append(self.r.claim("su"))) for _ in range(8)]
        for t in ths:
            t.start()
        for t in ths:
            t.join()
        self.assertEqual(sorted(out), ["already_accepted"] * 7 + ["ok"])
        self.assertFalse(self.r.mark_accepted("su"))

    def test_mark_accepted_refuses_revoked_and_unknown(self):
        self.r.create(self._entry("x"))
        self.r.revoke("x")
        self.assertFalse(self.r.mark_accepted("x"))
        self.assertEqual(self.r.claim("nope"), "not_found")
        self.r.create(self._entry("multi"))
        self.assertTrue(self.r.mark_accepted("multi"))
        self.assertTrue(self.r.mark_accepted("multi"))   # multi-use stays acceptable


if __name__ == "__main__":
    unittest.main(verbosity=2)


# ── revoke notice (2026-09-25) ─────────────────────────────────────────────

class TestRevokeNotice(unittest.TestCase):
    """Revoking tells the peer (signed, with the old keys) — otherwise it
    shows the connection as bidirectional forever, since it can no longer get
    an authoritative answer once our keys and relay slot are gone."""

    def setUp(self):
        import tempfile
        self.tmp = Path(tempfile.mkdtemp())
        self.origins, self.endpoints = self.tmp / "o", self.tmp / "e"
        self.origins.mkdir(); self.endpoints.mkdir()
        self.kid = "kid-revoke-1"
        self.key = "ab" * 32
        for d, extra in ((self.origins, {"origin_id": self.kid}),
                         (self.endpoints, {"endpoint_id": self.kid, "url": ""})):
            (d / f"{self.kid}.json").write_text(json.dumps({
                "hmac_key": self.key, "recv_key": "cd" * 32, "_friendship": True,
                "_peer_knows_us": True, "_peer_reports_reachable": True, **extra}))

    def _notice(self, sender="peer-instance-0001", issued_at=None, key=None):
        import hmac as _h
        issued_at = int(time.time()) if issued_at is None else issued_at
        sig = _h.new(bytes.fromhex(key or self.key),
                     ft._revoke_canonical(self.kid, issued_at, sender), "sha256").hexdigest()
        return {"type": "revoke", "kid": self.kid, "issued_at": issued_at, "peer_url": "",
                "sender_instance_id": sender, "signature": sig}

    def _process(self, req):
        return ft.process_friendship_ack_request(
            req, pending_dir=self.tmp / "p", origins_dir=self.origins, endpoints_dir=self.endpoints)

    def _flags(self):
        return [json.loads((d / f"{self.kid}.json").read_text())["_peer_knows_us"]
                for d in (self.origins, self.endpoints)]

    def test_valid_notice_marks_the_peer_as_gone(self):
        with mock.patch.object(ft, "_local_instance_id", return_value="me-instance-0002"):
            code, body = self._process(self._notice())
        self.assertEqual((code, body), (200, {"ok": True}))
        self.assertEqual(self._flags(), [False, False])

    def test_forged_stale_reflected_and_unbound_notices_change_nothing(self):
        with mock.patch.object(ft, "_local_instance_id", return_value="me-instance-0002"):
            self.assertEqual(self._process(self._notice(key="ef" * 32))[0], 403)
            self.assertEqual(self._process(self._notice(issued_at=int(time.time()) - 3600))[0], 403)
            self.assertEqual(self._process(self._notice(sender="me-instance-0002"))[0], 400)
            cfg = json.loads((self.origins / f"{self.kid}.json").read_text())
            cfg["_peer_instance_id"] = "bound-peer-0003"
            (self.origins / f"{self.kid}.json").write_text(json.dumps(cfg))
            self.assertEqual(self._process(self._notice(sender="someone-else-04"))[0], 403)
        self.assertEqual(self._flags(), [True, True])

    def test_unknown_kid_is_opaque(self):
        (self.origins / f"{self.kid}.json").unlink()
        self.assertEqual(self._process(self._notice()), (403, {"reason": "ack_rejected"}))

    def test_sender_builds_a_notice_the_receiver_accepts(self):
        sent = {}

        def fake_relay(kid, hmac_key, body, timeout_s):
            sent["body"] = body
            return {"ok": True}

        with mock.patch.object(ft, "_local_instance_id", return_value="peer-instance-0001"), \
             mock.patch.object(ft, "_relay_send_ack", side_effect=fake_relay):
            res = ft.send_revoke_notice(self.kid, endpoints_dir=self.endpoints)
        self.assertEqual(res, {"ok": True, "via": "relay"})
        self.assertIn("peer_url", sent["body"])  # relay listener routes by this key
        with mock.patch.object(ft, "_local_instance_id", return_value="me-instance-0002"):
            self.assertEqual(self._process(sent["body"])[0], 200)


class TestNonAsciiSignatureNeverRaises(TestRevokeNotice):
    """A non-ASCII signature used to raise TypeError in compare_digest → 500."""

    def test_ack_and_revoke_paths_reject_instead_of_raising(self):
        bad = self._notice()
        bad["signature"] = "é" * 64
        self.assertEqual(self._process(bad)[0], 403)
        ack = {"kid": self.kid, "issued_at": int(time.time()), "peer_url": "http://10.0.0.5:8775",
               "signature": "é" * 64, "signature_v2": "ü" * 64, "sender_instance_id": "peer-instance-0001"}
        code, _ = self._process(ack)
        self.assertIn(code, (400, 403))
        self.assertFalse(ft._hex_sig_eq("ab" * 32, "é" * 64))
        self.assertFalse(ft._hex_sig_eq("ab" * 32, None))


class TestUrlLessImportActivation(_Sandbox):
    """A token without an address leaves the redeemer's connection PENDING
    (disabled). The issuer's verified, bound hello must activate it — before,
    only a manual set-url did, so a relay-only issuer could never send."""

    def _redeemer_side(self, *, operator_disabled=False):
        tok = ft.FriendshipToken(kid="kid-urlless-1", key=secrets.token_hex(32), url=None,
                                 label="issuer", expires=None)
        origin = ft.to_origin_dict(tok)
        endpoint = ft.to_endpoint_dict(tok)
        self.assertFalse(origin["enabled"])
        self.assertEqual(origin["state"], "PENDING")
        origin["_peer_instance_id"] = endpoint["_peer_instance_id"] = IID_B  # bound at import
        if operator_disabled:
            origin["_operator_disabled"] = True
        (self.od / f"{tok.kid}.json").write_text(json.dumps(origin))
        (self.ed / f"{tok.kid}.json").write_text(json.dumps(endpoint))
        return tok

    def _hello(self, tok, sender=IID_B):
        body = _ack_body(tok.key, tok.kid, "http://10.0.0.7:8765", sender=sender)
        with mock.patch.object(ft, "_ack_ping_back_and_respond", return_value=(200, {"ok": True})):
            return ft.process_friendship_ack_request(
                body, pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed)

    def test_bound_issuer_hello_activates_the_pending_connection(self):
        tok = self._redeemer_side()
        self.assertEqual(self._hello(tok)[0], 200)
        o = json.loads((self.od / f"{tok.kid}.json").read_text())
        e = json.loads((self.ed / f"{tok.kid}.json").read_text())
        self.assertTrue(o["enabled"] and e["enabled"])
        self.assertEqual((o["state"], e["url"]), ("ACTIVE", "http://10.0.0.7:8765/v1/a2a/receive"))

    def test_operator_disabled_connection_is_never_activated_by_a_peer(self):
        tok = self._redeemer_side(operator_disabled=True)
        self.assertEqual(self._hello(tok)[0], 403)
        self.assertFalse(json.loads((self.od / f"{tok.kid}.json").read_text())["enabled"])

    def test_other_sender_cannot_activate(self):
        tok = self._redeemer_side()
        self.assertEqual(self._hello(tok, sender=IID_C)[0], 403)
        self.assertFalse(json.loads((self.od / f"{tok.kid}.json").read_text())["enabled"])


class TestTokenRelayAdoption(_Sandbox):
    """Round 3: an issuer-chosen relay URL was adopted unchecked — the
    listener then connected there and registered every pairing's credential."""

    def test_forbidden_relay_hosts_are_rejected(self):
        for bad in ("ws://127.0.0.1:6379/x", "ws://169.254.169.254/x", "wss://[::1]/x", "http://x/y"):
            self.assertIsNotNone(ft.relay_url_rejection_reason(bad), bad)
        self.assertIsNone(ft.relay_url_rejection_reason("wss://8.8.8.8/v1/a2a/relay/connect"))

    def test_existing_pairings_keep_their_relay(self):
        with mock.patch.object(ft, "my_relay_url_is_explicit", return_value=False), \
             mock.patch.object(ft, "get_my_relay_url", return_value="wss://8.8.4.4/relay"), \
             mock.patch.object(ft, "set_my_relay_url") as setter:
            (self.ed / "other.json").write_text(json.dumps({"_friendship": True}))
            self.assertEqual(ft.adopt_token_relay("wss://8.8.8.8/relay", endpoints_dir=self.ed,
                                                  exclude_kid="new"), "kept_existing")
            (self.ed / "other.json").unlink()
            self.assertEqual(ft.adopt_token_relay("wss://8.8.8.8/relay", endpoints_dir=self.ed,
                                                  exclude_kid="new"), "adopted")
            setter.assert_called_once_with("wss://8.8.8.8/relay")
            self.assertEqual(ft.adopt_token_relay("ws://127.0.0.1:1/x", endpoints_dir=self.ed), "rejected")


class TestConnectionNames(unittest.TestCase):
    """Round 7: the token label ("For Max") is the ISSUER's name for the
    redeemer; the redeemer used it as its name for the issuer."""

    def test_redeemer_names_the_connection_after_the_issuer(self):
        with mock.patch.dict(os.environ, {"CORVIN_INSTANCE_LABEL": "alice-laptop"}), \
             mock.patch("instance_identity.instance_id_metadata", return_value={"label": ""}):
            _tok, s = ft.create_friendship_token(url="http://10.0.0.5:8775", label="For Max")
        red = ft.parse_and_verify(s)
        self.assertEqual(red.issuer_name, "alice-laptop")
        self.assertEqual(ft.to_origin_dict(red)["label"], "alice-laptop")
        self.assertEqual(ft.to_endpoint_dict(red)["label"], "alice-laptop")
        # A pre-round-7 token without "nam" keeps the old fallback.
        legacy = ft.FriendshipToken(kid="k", key="ab" * 32, url=None, label="Bob", expires=None)
        self.assertEqual(ft.to_origin_dict(legacy)["label"], "Bob")

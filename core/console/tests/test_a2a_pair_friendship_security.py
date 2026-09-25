"""Route-level regression tests for the 2026-09-25 A2A pairing review
(``corvin_console/routes/a2a_pair.py``).

Findings pinned here: 1 (kid / oid path traversal through the import and
CLI-accept routes), 4 (redeemer SSRF via the issuer-chosen token URL),
5 (revoking a created-but-unredeemed token), 7 (single-use CLI invites),
9 (issuer licence refusal must not report ok:true; overwrite not counted).

No network: the reciprocal ack (``send_friendship_ack``) is replaced by a
stub returning the issuer's verdict, and every directory is a temp dir.
"""
from __future__ import annotations

import hmac
import json
import os
import secrets
import sys
import tempfile
import time
import types
import unittest
import unittest.mock as mock
from pathlib import Path

from fastapi import HTTPException

_HERE = Path(__file__).resolve().parent
_CONSOLE_PARENT = _HERE.parent
if str(_CONSOLE_PARENT) not in sys.path:
    sys.path.insert(0, str(_CONSOLE_PARENT))

from corvin_console.routes import a2a_pair as ap  # type: ignore[import-not-found]  # noqa: E402

ft = ap._ft


class _Rec:
    tenant_id = "_default"
    sid_fingerprint = "fp-test"


def _forge_friendship_token(payload: dict) -> str:
    pb = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    sig = hmac.new(ft._derive_sig_key(payload["key"]), pb, "sha256").digest()
    return ft.TOKEN_PREFIX + ft._b64_enc(pb) + "." + ft._b64_enc(sig)


class _RouteSandbox(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory(prefix="a2a-pair-sec-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.od = self.root / "cfg" / "origins"
        self.ed = self.root / "cfg" / "endpoints"
        self.pd = self.root / "cfg" / "pending_friendships"
        for d in (self.od, self.ed, self.pd):   # as on a real install
            d.mkdir(parents=True)
        env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root / "home"),
            "VOICE_AUDIT_PATH": str(self.root / "audit.jsonl"),
            "REMOTE_ORIGINS_DIR": str(self.od),
            "REMOTE_ENDPOINTS_DIR": str(self.ed),
            "REMOTE_PENDING_FRIENDSHIPS_DIR": str(self.pd),
            "REMOTE_PENDING_DIR": str(self.root / "cfg" / "pinv"),
            "CORVIN_A2A_URL": "http://10.9.9.9:8765",
            "CORVIN_A2A_RELAY_URL": "off",
            "CORVIN_A2A_INVITE_REGISTRY_PATH": str(self.root / "home" / "invites.json"),
            "CORVIN_A2A_INVITE_MASTER_KEY_PATH": str(self.root / "home" / "invite_master_key"),
        })
        env.start()
        self.addCleanup(env.stop)
        for name in ("action_performed", "action_failed"):
            p = mock.patch.object(ap.console_audit, name, mock.MagicMock())
            p.start()
            self.addCleanup(p.stop)
        # Unlimited peers unless a test sets a limit (free-tier default is 1).
        p = mock.patch.object(ap, "_lic_get_limit", lambda feature: None)
        p.start()
        self.addCleanup(p.stop)
        p = mock.patch.object(ap, "_conn_wake", lambda *a, **kw: None)
        p.start()
        self.addCleanup(p.stop)
        self.acks: list[dict] = []
        self.ack_result: dict = {"ok": True, "reachable": True, "peer_instance_id": "iid-issuer"}

        def fake_ack(token, **kw):
            self.acks.append({"kid": token.kid, "url": token.url})
            return dict(self.ack_result)

        p = mock.patch.object(ft, "send_friendship_ack", fake_ack)
        p.start()
        self.addCleanup(p.stop)

    def import_(self, token: str, **kw):
        return ap.friendship_import(ap.FriendshipImportRequest(token=token, **kw), _Rec())

    def status_of(self, fn, *a, **kw) -> int:
        try:
            fn(*a, **kw)
        except HTTPException as exc:
            return exc.status_code
        return 200


# ── Finding 1 ────────────────────────────────────────────────────────────

class TestTraversalThroughRoutes(_RouteSandbox):
    def test_import_of_traversal_kid_is_refused_and_writes_nothing(self):
        tok = _forge_friendship_token(
            {"kid": "../../escape/pwned", "key": secrets.token_hex(32), "v": 1,
             "con": {"personas": ["admin"]}})
        self.assertEqual(self.status_of(self.import_, tok), 400)
        self.assertFalse((self.root / "escape").exists())
        self.assertFalse(any(self.root.rglob("pwned.json")))

    def test_cli_accept_of_traversal_oid_is_refused_and_writes_nothing(self):
        import a2a_invite as inv  # type: ignore[import-not-found]
        p = {"v": 1, "iid": "someone-else", "oid": "../../escape2/x",
             "url": "http://10.1.1.1:8765", "rp": "/v1/a2a/receive",
             "hk": secrets.token_hex(32), "rk": secrets.token_hex(32),
             "pa": ["assistant"], "mt": 300, "iat": 1.0}
        pb = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        tok = inv.TOKEN_PREFIX + inv._b64_enc(pb) + "." + inv._b64_enc(b"\x00" * 32)
        code = self.status_of(ap.accept_cli_invite, ap.CLIAcceptRequest(token=tok), _Rec(), None)
        self.assertEqual(code, 400)
        self.assertFalse((self.root / "escape2").exists())

    def test_conn_path_refuses_what_the_regex_would_miss(self):
        for bad in ("..", "a/../b", "../x", ".x", "a\\b"):
            with self.assertRaises(HTTPException, msg=bad):
                ap._conn_path(self.od, bad)

    def test_revoke_recheck_enable_relay_refuse_bad_kid(self):
        self.assertEqual(self.status_of(ap.friendship_revoke, "..%2f", _Rec()), 400)
        self.assertEqual(self.status_of(ap.friendship_recheck, "a.b", _Rec()), 400)


# ── Finding 4 ────────────────────────────────────────────────────────────

class TestImportUrlGate(_RouteSandbox):
    def _tok(self, url: str | None) -> str:
        return ft.create_friendship_token(url=url)[1]

    def test_loopback_and_metadata_token_urls_are_refused(self):
        for url in ("http://127.0.0.1:43003/internal-admin", "http://169.254.169.254",
                    "http://[::1]:8765", "http://0.0.0.0:8765", "http://localhost:8765"):
            self.assertEqual(self.status_of(self.import_, self._tok(url)), 400, url)
        self.assertEqual(self.acks, [], "no ack may be sent to a forbidden host")
        self.assertEqual(list(self.od.glob("*.json")) if self.od.exists() else [], [])

    def test_peer_url_override_is_gated_too(self):
        code = self.status_of(self.import_, self._tok("http://10.0.0.5:8765"),
                              peer_url="http://169.254.169.254/latest")
        self.assertEqual(code, 400)
        self.assertEqual(self.acks, [])

    def test_lan_tailscale_and_global_urls_still_import(self):
        for url in ("http://192.168.1.50:8765", "http://100.101.102.103:8765",
                    "http://10.1.2.3:8765", "https://8.8.8.8"):
            res = self.import_(self._tok(url))
            self.assertTrue(res.ok, url)
            self.assertEqual(res.state, "ACTIVE")
        self.assertEqual(len(self.acks), 4)

    def test_relay_only_token_without_url_still_imports(self):
        res = self.import_(self._tok(None))
        self.assertTrue(res.ok)


# ── Finding 5 ────────────────────────────────────────────────────────────

class TestRevokeUnredeemedToken(_RouteSandbox):
    def test_created_token_can_be_revoked_before_redemption(self):
        # The original review repro (t5): ttl 0 = no expiry.
        created = ap.friendship_create(
            ap.FriendshipCreateRequest(url="http://10.9.9.9:8765", ttl_hours=0), _Rec())
        self.assertIsNone(created.expires)
        self.assertTrue((self.pd / f"{created.kid}.json").exists())
        res = ap.friendship_revoke(created.kid, _Rec())
        self.assertEqual(res, {"ok": True, "kid": created.kid, "pending_token_revoked": True,
                               "peer_notified": False})  # nobody to notify yet
        self.assertFalse((self.pd / f"{created.kid}.json").exists())
        # a later ack for the revoked token pairs nothing
        tok = ft.parse_and_verify(created.token)
        hk, _ = ft._derive_channel_keys(tok.key)
        ts = int(time.time())
        body = {"kid": tok.kid, "issued_at": ts, "peer_url": "http://10.2.2.2:8765"}
        body["signature"] = hmac.new(bytes.fromhex(hk), ft._ack_canonical(
            tok.kid, ts, body["peer_url"], None), "sha256").hexdigest()
        status, _ = ft.process_friendship_ack_request(
            body, pending_dir=self.pd, origins_dir=self.od, endpoints_dir=self.ed)
        self.assertEqual(status, 403)
        self.assertFalse((self.od / f"{tok.kid}.json").exists())

    def test_revoking_an_unknown_kid_is_still_404(self):
        self.assertEqual(self.status_of(ap.friendship_revoke, "no-such-kid", _Rec()), 404)


# ── Finding 9 ────────────────────────────────────────────────────────────

class TestIssuerLicenceRefusal(_RouteSandbox):
    def test_http_402_from_issuer_is_a_402_and_rolls_back(self):
        self.ack_result = {"ok": False, "error": "http_402"}
        tok = ft.create_friendship_token(url="http://10.0.0.5:8765")[1]
        kid = ft.parse_and_verify(tok).kid
        self.assertEqual(self.status_of(self.import_, tok), 402)
        self.assertFalse((self.od / f"{kid}.json").exists())
        self.assertFalse((self.ed / f"{kid}.json").exists())

    def test_http_402_on_overwrite_restores_the_previous_files(self):
        tok = ft.create_friendship_token(url="http://10.0.0.5:8765")[1]
        kid = ft.parse_and_verify(tok).kid
        self.import_(tok)
        before = {p: p.read_bytes() for p in (self.od / f"{kid}.json", self.ed / f"{kid}.json")}
        self.ack_result = {"ok": False, "error": "http_402"}
        self.assertEqual(self.status_of(self.import_, tok, overwrite=True), 402)
        for p, blob in before.items():
            self.assertEqual(json.loads(p.read_bytes()), json.loads(blob))

    def test_overwrite_reimport_is_not_counted_against_peers_max(self):
        tok = ft.create_friendship_token(url="http://10.0.0.5:8765")[1]
        self.import_(tok)
        with mock.patch.object(ap, "_lic_get_limit", lambda feature: 1):
            self.assertTrue(self.import_(tok, overwrite=True).ok)
            other = ft.create_friendship_token(url="http://10.0.0.6:8765")[1]
            self.assertEqual(self.status_of(self.import_, other), 402)

    def test_successful_import_binds_the_issuer_instance(self):
        tok = ft.create_friendship_token(url="http://10.0.0.5:8765")[1]
        kid = ft.parse_and_verify(tok).kid
        self.import_(tok)
        for d in (self.od, self.ed):
            self.assertEqual(json.loads((d / f"{kid}.json").read_text())["_peer_instance_id"],
                             "iid-issuer")


# ── Finding 7 ────────────────────────────────────────────────────────────

class TestCliSingleUseInvite(_RouteSandbox):
    def test_single_use_invite_accepted_once(self):
        import a2a_invite as inv  # type: ignore[import-not-found]
        import a2a_invite_registry as reg  # type: ignore[import-not-found]
        local = "local-iid"
        token, token_str = inv.generate_invite(
            iid=local, origin_id="peer.one", url="http://10.0.0.8:8765", single_use=True)
        reg.InviteRegistry().create(reg.InviteEntry(
            ikey=token.ikey, oid=token.oid, lbl="", iat=token.iat, exp=token.exp, su=True))
        fake_ii = types.ModuleType("instance_identity")
        fake_ii.get_instance_id = lambda: local  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"instance_identity": fake_ii}):
            first = ap.accept_cli_invite(ap.CLIAcceptRequest(token=token_str), _Rec(), None)
            self.assertTrue(first.ok)
            # validate_invite would already refuse a sequential replay; make
            # it look fresh to prove the atomic claim is what holds the line
            # (two concurrent accepts both pass validate before either claims).
            with mock.patch.object(inv, "validate_invite",
                                   lambda *a, **kw: inv.ValidationResult(ok=True)):
                code = self.status_of(ap.accept_cli_invite,
                                      ap.CLIAcceptRequest(token=token_str, overwrite=True),
                                      _Rec(), None)
        self.assertEqual(code, 400)
        self.assertEqual(reg.InviteRegistry().get(token.ikey)["accepted"], True)


class LegacyInviteRound8Tests(_RouteSandbox):
    """Round 8: the legacy invite routes (redeem + accept) must not take over,
    wipe or SSRF, and a correctable refusal must not burn the invite."""

    def _invite(self, **over) -> dict:
        inv = {
            "v": 1, "accept_id": "acc-1",
            "accept_url": "http://10.1.2.3:8765/v1/console/remote-trigger/pair/accept",
            "accept_key": "a" * 64, "issuer_url": "http://10.1.2.3:8775",
            "issuer_instance_id": "iid-mallory", "issuer_label": "Alice",
            "origin_id": "alice",
            "r2i_hmac_key": "b" * 64, "r2i_recv_key": "c" * 64,
            "i2r_hmac_key": "d" * 64, "i2r_recv_key": "e" * 64,
            "max_ttl_s": 300, "expires_at": time.time() + 3600,
        }
        inv.update(over)
        return inv

    def _redeem(self, inv: dict) -> tuple[int, list]:
        import asyncio
        import base64
        posted: list = []

        class _Resp:
            status_code = 500

        class _Client:
            def __init__(self, *a, **kw): pass
            async def __aenter__(self): return self
            async def __aexit__(self, *a): return False
            async def post(self, url, json=None):
                posted.append(url)
                return _Resp()

        code = base64.urlsafe_b64encode(json.dumps(inv).encode()).decode()
        body = ap.RedeemRequest(invite_code=code, our_url="http://10.9.9.9:8775",
                                our_console_url="http://10.9.9.9:8765", our_origin_id="me")
        with mock.patch.object(ap.httpx, "AsyncClient", _Client):
            try:
                asyncio.run(ap.pair_redeem(_Rec(), body))
                return 200, posted
            except HTTPException as exc:
                return exc.status_code, posted

    def _plant_alice(self) -> dict:
        real = {"origin_id": "alice", "hmac_key": "1" * 64, "recv_key": "2" * 64}
        for d in (self.od, self.ed):
            (d / "alice.json").write_text(json.dumps(real))
        return real

    def test_redeem_never_overwrites_or_deletes_an_existing_connection(self):
        real = self._plant_alice()
        code, posted = self._redeem(self._invite())
        self.assertEqual(code, 409)
        self.assertEqual(posted, [], "no request before the id check")
        for d in (self.od, self.ed):
            self.assertEqual(json.loads((d / "alice.json").read_text()), real)

    def test_redeem_gates_invite_urls_before_any_request(self):
        for field, url in (("accept_url", "http://169.254.169.254/latest/x"),
                           ("issuer_url", "http://127.0.0.1:8775"),
                           ("accept_url", "http://localhost:8765/x")):
            code, posted = self._redeem(self._invite(**{field: url}))
            self.assertEqual(code, 400, (field, url))
            self.assertEqual(posted, [])
            self.assertEqual(list(self.od.glob("*.json")) + list(self.ed.glob("*.json")), [])

    def test_redeem_rollback_only_removes_what_it_wrote(self):
        code, posted = self._redeem(self._invite(origin_id="fresh"))
        self.assertEqual(code, 502)
        self.assertEqual(len(posted), 1)
        self.assertFalse((self.od / "fresh.json").exists())

    # ── pair_accept ─────────────────────────────────────────────────────
    def _pending(self) -> Path:
        pd = Path(os.environ["REMOTE_PENDING_DIR"])
        pd.mkdir(parents=True, exist_ok=True)
        f = pd / "acc-1.json"
        f.write_text(json.dumps(self._invite()))
        return f

    def _accept(self, *, url: str, oid: str) -> int:
        payload = {"accept_id": "acc-1", "ts": time.time(), "nonce": "n" * 32,
                   "redeemer_instance_id": "iid-r", "redeemer_url": url,
                   "redeemer_label": "R", "origin_id_for_us": oid}
        sig = ap._sign("a" * 64, json.dumps(payload, sort_keys=True))
        try:
            ap.pair_accept(ap.AcceptRequest(**payload, signature=sig))
            return 200
        except HTTPException as exc:
            return exc.status_code

    def test_accept_correctable_refusals_keep_the_invite_usable(self):
        pending = self._pending()
        used = pending.with_suffix(".used")
        self.assertEqual(self._accept(url="http://127.0.0.1:8775", oid="bob"), 400)
        self.assertTrue(pending.exists())
        self.assertFalse(used.exists(), "no key material left in a .used file")
        self._plant_alice()
        self.assertEqual(self._accept(url="http://10.4.4.4:8775", oid="alice"), 409)
        self.assertTrue(pending.exists())
        self.assertFalse(used.exists())
        # The corrected retry succeeds and consumes the invite.
        self.assertEqual(self._accept(url="http://10.4.4.4:8775", oid="bob"), 200)
        self.assertFalse(pending.exists())
        self.assertFalse(used.exists())
        self.assertTrue((self.od / "bob.json").exists())

    def test_accept_licence_refusal_leaves_no_key_file(self):
        pending = self._pending()
        (self.od / "x.json").write_text("{}")
        with mock.patch.object(ap, "_lic_get_limit", lambda f: 1 if f == "a2a_peers_max" else None):
            self.assertEqual(self._accept(url="http://10.4.4.4:8775", oid="bob"), 402)
        self.assertFalse(pending.exists())
        self.assertFalse(pending.with_suffix(".used").exists())


class ForeignInviteHostGateRound9Tests(_RouteSandbox):
    """Round 9: accepting a FOREIGN ADR-0063 invite stored ``url + rp`` with no
    host check — every later send POSTed a signed envelope there."""

    def _foreign(self, url: str, rp: str = "/v1/a2a/receive", oid: str = "peerx") -> str:
        import a2a_invite as inv  # type: ignore[import-not-found]
        p = {"v": 1, "iid": "someone-else", "oid": oid, "url": url, "rp": rp,
             "hk": secrets.token_hex(32), "rk": secrets.token_hex(32),
             "pa": ["assistant"], "mt": 300, "iat": time.time(),
             "exp": time.time() + 3600}
        pb = json.dumps(p, sort_keys=True, separators=(",", ":")).encode()
        return inv.TOKEN_PREFIX + inv._b64_enc(pb) + "." + inv._b64_enc(b"\x00" * 32)

    def _accept(self, tok: str) -> int:
        fake_ii = types.ModuleType("instance_identity")
        fake_ii.get_instance_id = lambda: "local-iid"  # type: ignore[attr-defined]
        with mock.patch.dict(sys.modules, {"instance_identity": fake_ii}):
            return self.status_of(ap.accept_cli_invite, ap.CLIAcceptRequest(token=tok), _Rec(), None)

    def test_forbidden_hosts_and_host_swapping_paths_are_refused(self):
        for url, rp in (("http://127.0.0.1:8765", "/v1/a2a/receive"),
                        ("http://169.254.169.254", "/latest"),
                        ("http://localhost:8775", "/v1/a2a/receive"),
                        ("http://10.1.1.1:8765", "@169.254.169.254/latest/meta-data"),
                        ("http://10.1.1.1:8765", "/v1/../../x"),
                        ("http://10.1.1.1:8765", "/x?y=1")):
            self.assertEqual(self._accept(self._foreign(url, rp)), 400, (url, rp))
        self.assertEqual(list(self.od.glob("*.json")) + list(self.ed.glob("*.json")), [])

    def test_a_lan_peer_is_still_accepted(self):
        self.assertEqual(self._accept(self._foreign("http://10.1.1.1:8765")), 200)
        self.assertEqual(json.loads((self.ed / "peerx.json").read_text())["url"],
                         "http://10.1.1.1:8765/v1/a2a/receive")

    def test_generate_refuses_a_receive_path_that_is_not_a_plain_path(self):
        import a2a_invite as inv  # type: ignore[import-not-found]
        with self.assertRaises(inv.InviteError):
            inv.generate_invite(iid="i", origin_id="o", url="http://10.1.1.1:8765",
                                receive_path="@evil/x")


if __name__ == "__main__":
    unittest.main(verbosity=2)

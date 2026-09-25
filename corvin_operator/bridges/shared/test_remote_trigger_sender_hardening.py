"""Regression tests for the 2026-09-25 A2A outbound hardening round.

Each class pins one verified defect in remote_trigger_sender.py:

  1. TestReflectionGuard     — a signed response produced by OUR OWN instance
                               (symmetric friendship keys) was accepted as the
                               peer's, for send() and ping(), direct and relay.
     TestTofuInstancePin     — the first verified peer response pins the
                               friendship endpoint's instance_id.
  3. TestRelayFallbackGate   — the relay fallback re-sent the identical
                               envelope after a direct TIMEOUT (nonce replay
                               while the real task still ran); default timeout
                               was shorter than the worker ttl.
  5. TestRelayFromRunningLoop — asyncio.run() inside a running loop killed the
                               relay path for every async caller.
  8. TestAuditVia            — ``via`` was redacted by the audit backstop.
  9. TestNetworkAttestationToken — the EdDSA licence JWT was shipped as an
                               RS256 SesT → network_attestation_bad_sig.

Run: python -m pytest corvin_operator/bridges/shared/test_remote_trigger_sender_hardening.py
"""
from __future__ import annotations

import asyncio
import base64
import http.server
import json
import os
import secrets
import socket
import sys
import tempfile
import threading
import time
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

import a2a_friendship as ft  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402
import remote_trigger_sender as rts  # noqa: E402

IID_A = "aaaaaaaa-0000-4000-8000-00000000000a"
IID_B = "bbbbbbbb-0000-4000-8000-00000000000b"
IID_C = "cccccccc-0000-4000-8000-00000000000c"


class _RecordingSE:
    """forge_se stand-in: records (event_type, details) instead of chaining."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def write_event(self, path, event_type, **kw):  # noqa: ANN001
        self.events.append((event_type, dict(kw.get("details") or {})))

    def get_audit_chain_tail(self, path):  # noqa: ANN001
        return "0" * 64

    def of(self, event_type: str) -> list[dict]:
        return [d for (e, d) in self.events if e == event_type]


class _Pairing:
    """One friendship pairing as BOTH peers see it: the same kid and the same
    symmetric keys; an origins dir (what our receiver trusts) and an
    endpoints dir (what our sender calls)."""

    def __init__(self, root: Path, *, url: str = "http://peer-b.invalid:8765") -> None:
        self.kid = secrets.token_hex(8)
        self.key = secrets.token_hex(32)
        tok = ft.FriendshipToken(kid=self.kid, key=self.key, url=url, label="peer", expires=None)
        self.origins = root / "origins"
        self.endpoints = root / "endpoints"
        self.origins.mkdir(parents=True, exist_ok=True)
        self.endpoints.mkdir(parents=True, exist_ok=True)
        origin = ft.to_origin_dict(tok)
        origin["spawn_worker"] = False
        self._write(self.origins / f"{self.kid}.json", origin)
        self._write(self.endpoints / f"{self.kid}.json", ft.to_endpoint_dict(tok))

    @staticmethod
    def _write(path: Path, data: dict) -> None:
        path.write_text(json.dumps(data))
        path.chmod(0o600)

    def endpoint(self) -> dict:
        return json.loads((self.endpoints / f"{self.kid}.json").read_text())

    def receiver(self, instance_id: str) -> rtr.RemoteTriggerReceiver:
        return rtr.RemoteTriggerReceiver(
            origins_dir=self.origins, nonce_store=rtr.NonceStore(),
            instance_id=instance_id, force_m1_only=True, forge_se=_RecordingSE(),
        )

    def sender(self, instance_id: str, se: _RecordingSE | None = None) -> rts.RemoteTriggerSender:
        return rts.RemoteTriggerSender(
            endpoints_dir=self.endpoints, instance_id=instance_id,
            forge_se=se or _RecordingSE(),
        )


class _Base(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self._env = mock.patch.dict(os.environ, {
            "CORVIN_HOME": str(self.root / "home"),
            "CORVIN_A2A_ATTESTATION_DISABLED": "1",
        })
        self._env.start()
        os.environ.pop(rts._REMOTE_ENDPOINTS_ENV, None)
        # Hermetic: the feed writer and the network attestation are not under test.
        for target in ("remote_trigger_sender._record_feed_task",
                       "remote_trigger_sender._record_feed_response"):
            p = mock.patch(target, lambda *a, **k: None)
            p.start()
            self.addCleanup(p.stop)
        p = mock.patch.object(rts.RemoteTriggerSender, "_build_network_attestation",
                              staticmethod(lambda cfg: None))
        p.start()
        self.addCleanup(p.stop)
        self.pair = _Pairing(self.root)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()


# ── 1. Reflection guard ─────────────────────────────────────────────────

class TestReflectionGuard(_Base):
    def test_send_direct_rejects_response_from_own_instance(self):
        own_receiver = self.pair.receiver(IID_A)
        se = _RecordingSE()
        sender = self.pair.sender(IID_A, se)
        # A network attacker reflects our envelope into OUR receiver and
        # returns its (validly signed — symmetric keys) answer.
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               staticmethod(lambda url, env, t: own_receiver.receive(env).to_dict())):
            res = sender.send(self.pair.kid, "what is 2+2?", result_schema={"type": "object"})
        self.assertFalse(res.ok)
        self.assertEqual(res.error_category, rts.ErrorCategory.AUTH_FAILED)
        self.assertEqual(res.error_detail, "Response originated from this instance")
        rej = se.of("A2A.response_rejected")
        self.assertEqual(rej[-1]["reason"], "self_response")
        self.assertEqual(se.of("A2A.response_received"), [])
        self.assertNotIn("instance_id", self.pair.endpoint())  # never pinned to ourselves

    def test_send_via_relay_rejects_response_from_own_instance(self):
        own_receiver = self.pair.receiver(IID_A)
        sender = self.pair.sender(IID_A)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post",
                               staticmethod(lambda cfg, eid, env, t: own_receiver.receive(env).to_dict())):
            res = sender.send(self.pair.kid, "hi")
        self.assertFalse(res.ok)
        self.assertEqual(res.error_detail, "Response originated from this instance")

    def test_ping_rejects_response_from_own_instance(self):
        from a2a_http_server import process_ping_request
        own_receiver = self.pair.receiver(IID_A)
        sender = self.pair.sender(IID_A)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               staticmethod(lambda url, req, t: process_ping_request(req, own_receiver)[1])):
            res = sender.ping(self.pair.kid)
        self.assertFalse(res.reachable)
        self.assertEqual(res.error_category, rts.ErrorCategory.AUTH_FAILED)
        self.assertEqual(res.error_detail, "Response originated from this instance")

    def test_ping_via_relay_rejects_response_from_own_instance(self):
        from a2a_http_server import process_ping_request
        own_receiver = self.pair.receiver(IID_A)
        sender = self.pair.sender(IID_A)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_ping",
                               staticmethod(lambda cfg, eid, req, t: process_ping_request(req, own_receiver)[1])):
            res = sender.ping(self.pair.kid)
        self.assertFalse(res.reachable)
        self.assertEqual(res.via, "relay")
        self.assertEqual(res.error_detail, "Response originated from this instance")

    def test_genuine_peer_response_still_accepted(self):
        peer = self.pair.receiver(IID_B)
        sender = self.pair.sender(IID_A)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               staticmethod(lambda url, env, t: peer.receive(env).to_dict())):
            res = sender.send(self.pair.kid, "hi")
        self.assertTrue(res.ok, res)
        self.assertEqual(res.instance_id, IID_B)


class TestTofuInstancePin(_Base):
    def _send_via(self, receiver, sender):
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               staticmethod(lambda url, env, t: receiver.receive(env).to_dict())):
            return sender.send(self.pair.kid, "hi")

    def test_first_verified_response_pins_then_other_instance_is_rejected(self):
        se = _RecordingSE()
        sender = self.pair.sender(IID_A, se)
        self.assertTrue(self._send_via(self.pair.receiver(IID_B), sender).ok)
        ep = self.pair.endpoint()
        self.assertEqual(ep["instance_id"], IID_B)
        self.assertEqual(ep["_instance_id_pin_source"], "tofu")
        self.assertEqual(oct(os.stat(self.pair.endpoints / f"{self.pair.kid}.json").st_mode & 0o777), "0o600")
        self.assertEqual(len(se.of("A2A.instance_pinned")), 1)
        # A third instance holding the same (leaked) key can no longer answer.
        res = self._send_via(self.pair.receiver(IID_C), sender)
        self.assertFalse(res.ok)
        self.assertEqual(res.error_detail, "Instance ID mismatch")
        # ...and the pinned peer still can.
        self.assertTrue(self._send_via(self.pair.receiver(IID_B), sender).ok)

    def test_ping_pins_and_enforces(self):
        from a2a_http_server import process_ping_request
        sender = self.pair.sender(IID_A)

        def via(recv):
            return mock.patch.object(
                rts.RemoteTriggerSender, "_http_post",
                staticmethod(lambda url, req, t: process_ping_request(req, recv)[1]))
        with via(self.pair.receiver(IID_B)):
            self.assertTrue(sender.ping(self.pair.kid).reachable)
        self.assertEqual(self.pair.endpoint()["instance_id"], IID_B)
        with via(self.pair.receiver(IID_C)):
            res = sender.ping(self.pair.kid)
        self.assertFalse(res.reachable)
        self.assertEqual(res.error_detail, "Instance ID mismatch")

    def test_non_friendship_endpoint_is_never_pinned(self):
        ep = self.pair.endpoint()
        ep.pop("_friendship")
        _Pairing._write(self.pair.endpoints / f"{self.pair.kid}.json", ep)
        self.assertTrue(self._send_via(self.pair.receiver(IID_B), self.pair.sender(IID_A)).ok)
        self.assertNotIn("instance_id", self.pair.endpoint())

    def test_concurrent_repair_wins_over_pin(self):
        """The RMW re-reads under the lock: if the pairing keys changed since
        the response was verified, nothing is written."""
        sender = self.pair.sender(IID_A)
        cfg = sender._registry.load(self.pair.kid)
        ep = self.pair.endpoint()
        ep["hmac_key"] = secrets.token_hex(32)  # re-paired meanwhile
        _Pairing._write(self.pair.endpoints / f"{self.pair.kid}.json", ep)
        sender._tofu_pin_instance(cfg, self.pair.kid, IID_B)
        self.assertNotIn("instance_id", self.pair.endpoint())


# ── 3. Relay fallback only when provably undelivered ────────────────────

class TestRelayFallbackGate(_Base):
    def _run(self, direct_exc: Exception):
        peer = self.pair.receiver(IID_B)
        relay_calls: list[str] = []

        def relay(cfg, eid, env, t):
            relay_calls.append(env["nonce"])
            return peer.receive(env).to_dict()
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post", side_effect=direct_exc), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post", staticmethod(relay)):
            res = self.pair.sender(IID_A).send(self.pair.kid, "job")
        return res, relay_calls

    def test_read_timeout_does_not_resend_same_envelope(self):
        """dup.py: the peer got the envelope and is still working when our
        read deadline hits; re-sending the SAME nonce was replay-rejected and
        reported as 'rejected' for a task that was actually running."""
        peer = self.pair.receiver(IID_B)
        started = threading.Event()

        def direct(url, env, t):
            threading.Thread(target=lambda: (peer.receive(env), started.set())).start()
            started.wait(5)
            raise rts.TransportError("timeout")
        relay_calls: list[int] = []
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post", staticmethod(direct)), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post",
                               staticmethod(lambda *a: relay_calls.append(1))):
            res = self.pair.sender(IID_A).send(self.pair.kid, "long running job")
        self.assertEqual(relay_calls, [])
        self.assertEqual(res.error_category, rts.ErrorCategory.TIMEOUT_TRANSPORT)
        self.assertNotEqual(res.status, "rejected")

    def test_5xx_and_oversize_do_not_fall_back(self):
        for exc in (rts.TransportError("http_500", http_status=500),
                    rts.TransportError("http_502", http_status=502),
                    rts.TransportError("response_too_large"),
                    rts.TransportError("invalid_response_json")):
            res, calls = self._run(exc)
            self.assertEqual(calls, [], exc.reason)
            self.assertFalse(res.ok)

    def test_not_delivered_falls_back(self):
        for exc in (rts.TransportError("connection_failed"),
                    rts.TransportError("http_404", http_status=404),
                    rts.TransportError("transport_error:ValueError", maybe_delivered=False)):
            res, calls = self._run(exc)
            self.assertEqual(len(calls), 1, exc.reason)
            self.assertTrue(res.ok, (exc.reason, res))

    def test_http_post_classifies_real_failures(self):
        # connect refused → not delivered
        s = socket.socket()
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        with self.assertRaises(rts.TransportError) as cm:
            rts.RemoteTriggerSender._http_post(f"http://127.0.0.1:{port}/v1/a2a/receive", {}, 2)
        self.assertEqual(cm.exception.reason, "connection_failed")
        self.assertFalse(cm.exception.maybe_delivered)
        # empty url (PENDING endpoint) → not delivered, so the relay still runs
        with self.assertRaises(rts.TransportError) as cm:
            rts.RemoteTriggerSender._http_post("", {}, 2)
        self.assertFalse(cm.exception.maybe_delivered)
        # accepted, never answered → read timeout → maybe delivered
        srv = socket.socket()
        srv.bind(("127.0.0.1", 0))
        srv.listen(1)
        port = srv.getsockname()[1]
        conns = []
        t = threading.Thread(target=lambda: conns.append(srv.accept()), daemon=True)
        t.start()
        try:
            with self.assertRaises(rts.TransportError) as cm:
                rts.RemoteTriggerSender._http_post(f"http://127.0.0.1:{port}/v1/a2a/receive", {"x": 1}, 1)
            self.assertEqual(cm.exception.reason, "timeout")
            self.assertTrue(cm.exception.maybe_delivered)
        finally:
            for c, _a in conns:
                c.close()
            srv.close()

    def test_http_5xx_and_404_classification(self):
        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
                code = 404 if self.path.endswith("/missing") else 500
                self.send_response(code)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *a):  # noqa: D401
                pass
        srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        base = f"http://127.0.0.1:{srv.server_address[1]}"
        try:
            with self.assertRaises(rts.TransportError) as cm:
                rts.RemoteTriggerSender._http_post(base + "/v1/a2a/receive", {}, 2)
            self.assertTrue(cm.exception.maybe_delivered)
            with self.assertRaises(rts.TransportError) as cm:
                rts.RemoteTriggerSender._http_post(base + "/missing", {}, 2)
            self.assertFalse(cm.exception.maybe_delivered)
        finally:
            srv.shutdown()

    def test_connect_timeout_is_short_and_not_delivered(self):
        seen: list[float] = []

        def fake_create_connection(addr, timeout=None, *a, **k):
            seen.append(timeout)
            raise TimeoutError("timed out")
        with mock.patch("socket.create_connection", fake_create_connection):
            with self.assertRaises(rts.TransportError) as cm:
                rts.RemoteTriggerSender._http_post("http://192.0.2.1:9/v1/a2a/receive", {}, 75)
        self.assertEqual(seen, [float(rts._CONNECT_TIMEOUT_S)])
        self.assertEqual(cm.exception.reason, "connection_failed")
        self.assertFalse(cm.exception.maybe_delivered)

    def test_default_timeout_covers_worker_ttl(self):
        seen: list[int] = []

        def direct(url, env, t):
            seen.append(t)
            raise rts.TransportError("timeout")
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post", staticmethod(direct)):
            self.pair.sender(IID_A).send(self.pair.kid, "x")               # ttl 60 default
            self.pair.sender(IID_A).send(self.pair.kid, "x", ttl_s=200)
            self.pair.sender(IID_A).send(self.pair.kid, "x", timeout_s=12)  # explicit wins
        self.assertEqual(seen, [60 + rts._TIMEOUT_MARGIN_S, 200 + rts._TIMEOUT_MARGIN_S, 12])

    def test_relay_failure_after_delivery_is_not_reported_unreachable(self):
        """Callers retry 'unreachable' with a NEW task; a relay that delivered
        but got no answer must surface as a timeout, not as unreachable."""
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post",
                               side_effect=rts.TransportError("relay_error:response_timeout",
                                                              maybe_delivered=True)):
            res = self.pair.sender(IID_A).send(self.pair.kid, "x")
        self.assertEqual(res.error_category, rts.ErrorCategory.TIMEOUT_TRANSPORT)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post",
                               side_effect=rts.TransportError("relay_error:message_too_large",
                                                              maybe_delivered=False)):
            res = self.pair.sender(IID_A).send(self.pair.kid, "x")
        self.assertEqual(res.error_category, rts.ErrorCategory.PROTOCOL_ERROR)
        self.assertEqual(res.error_detail, "Message exceeds relay size limit")
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_post",
                               side_effect=rts.TransportError("relay_not_configured")):
            res = self.pair.sender(IID_A).send(self.pair.kid, "x")
        self.assertEqual(res.error_category, rts.ErrorCategory.UNREACHABLE)


# ── 5. Relay transport from inside a running event loop ─────────────────

class TestRelayFromRunningLoop(_Base):
    def _patches(self, seen: list):
        import a2a_relay

        async def fake(**kw):
            seen.append(kw)
            plain = ft.decrypt_from_relay(cfg_key, kw["nonce_hex"], kw["ciphertext_hex"])
            env = json.loads(plain)
            n, c = ft.encrypt_for_relay(cfg_key, json.dumps({"echo": env["task_id"]}).encode())
            return {"nonce": n, "ciphertext": c}
        cfg_key = self.pair.endpoint()["hmac_key"]
        return (mock.patch("corvin_core.feature_flags.is_enabled", return_value=True),
                mock.patch("a2a_friendship.get_my_relay_url", return_value="ws://relay.invalid"),
                mock.patch.object(a2a_relay, "relay_deliver_and_wait", fake))

    def test_relay_post_works_with_and_without_a_running_loop(self):
        cfg = self.pair.endpoint()
        seen: list = []
        p1, p2, p3 = self._patches(seen)
        with p1, p2, p3:
            out_sync = rts.RemoteTriggerSender._relay_post(
                cfg, self.pair.kid, {"task_id": "t-sync", "sender_instance_id": IID_A}, 5)

            async def inside_loop():  # == A2ATaskEnvelope.dispatch / any async caller
                return rts.RemoteTriggerSender._relay_post(
                    cfg, self.pair.kid, {"task_id": "t-loop", "sender_instance_id": IID_A}, 5)
            out_loop = asyncio.run(inside_loop())
        self.assertEqual(out_sync, {"echo": "t-sync"})
        self.assertEqual(out_loop, {"echo": "t-loop"})
        # the sender stamps its instance tag so the relay can skip its own listener
        import a2a_relay
        self.assertEqual(seen[0]["from_instance_tag"], a2a_relay.instance_tag(self.pair.kid, IID_A))


# ── 8. Audit: `via` survives the backstop as a closed enum ──────────────

class TestAuditVia(_Base):
    def test_via_is_allowlisted_closed_enum(self):
        self.assertEqual(rts._assert_audit_details_safe({"via": "relay"}), {"via": "relay"})
        self.assertEqual(rts._assert_audit_details_safe({"via": "direct"}), {"via": "direct"})
        self.assertEqual(rts._assert_audit_details_safe({"via": "peer.example.com"}),
                         {"via": "redacted"})

    def test_ping_result_audits_via(self):
        from a2a_http_server import process_ping_request
        se = _RecordingSE()
        sender = self.pair.sender(IID_A, se)
        peer = self.pair.receiver(IID_B)
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")), \
             mock.patch.object(rts.RemoteTriggerSender, "_relay_ping",
                               staticmethod(lambda cfg, eid, req, t: process_ping_request(req, peer)[1])):
            self.assertTrue(sender.ping(self.pair.kid).reachable)
        self.assertEqual(se.of("A2A.ping_result")[-1]["via"], "relay")
        self.assertEqual(se.of("A2A.relay_fallback_used")[-1]["via"], "relay")


# ── 9. Network attestation only from a genuine RS256 SesT ───────────────

def _jwt(header: dict, prefix: str = "") -> str:
    def b64(d: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(d).encode()).decode().rstrip("=")
    return prefix + b64(header) + "." + b64({"sub": "op"}) + "." + "c2lnbmF0dXJl"


class TestNetworkAttestationToken(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        root = Path(self._tmp.name)
        self.home = root / "home"            # temp HOME: no real session.key is read
        self.corvin = root / "corvin"
        (self.corvin / "global").mkdir(parents=True)
        self.home.mkdir()
        self._env = mock.patch.dict(os.environ, {
            "HOME": str(self.home), "CORVIN_HOME": str(self.corvin)})
        self._env.start()
        os.environ.pop("CORVIN_LICENSE_KEY", None)

    def tearDown(self) -> None:
        self._env.stop()
        self._tmp.cleanup()

    def _licence(self, token: str) -> None:
        (self.corvin / "global" / "license.key").write_text(token)

    def test_eddsa_licence_is_not_used_as_sest(self):
        self._licence(_jwt({"alg": "EdDSA", "typ": "JWT"}, prefix="CORVIN-"))
        self.assertIsNotNone(rts.RemoteTriggerSender._load_sest())  # it IS found...
        self.assertIsNone(rts.RemoteTriggerSender._build_network_attestation({}))  # ...not shipped

    def test_unprefixed_eddsa_and_garbage_are_rejected(self):
        for tok in (_jwt({"alg": "EdDSA"}), _jwt({"alg": "HS256"}), "a.b.c", "not-a-jwt"):
            self._licence(tok)
            self.assertIsNone(rts.RemoteTriggerSender._build_network_attestation({}), tok)

    def test_rs256_sest_still_builds_block(self):
        tok = _jwt({"alg": "RS256", "typ": "JWT"})
        self._licence(tok)
        with mock.patch.object(rts.RemoteTriggerSender, "_compute_layer_integrity_hash",
                               staticmethod(lambda: None)):
            block = rts.RemoteTriggerSender._build_network_attestation({"pairing_id": "p1"})
        self.assertIsNotNone(block)
        self.assertEqual(block["sest_sig"], tok.split(".")[2])
        self.assertEqual(block["pairing_id"], "p1")


if __name__ == "__main__":
    unittest.main()


class TestInstructionLengthCheckedLocally(unittest.TestCase):
    """Round 7: a >16 KiB message was sent and refused by the peer as an
    'injection attempt' (a false security signal); now refused locally."""

    def test_over_long_instruction_never_leaves(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=Path(tempfile.mkdtemp()))
        with mock.patch.object(sender, "_send_impl") as impl:
            res = sender.send("peer", "x" * (16 * 1024 + 1))
        impl.assert_not_called()
        self.assertFalse(res.ok)
        self.assertIn("too long", res.error_detail)

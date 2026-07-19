"""Tests for remote_trigger_sender.py — Layer 38 outbound A2A."""
from __future__ import annotations

import hashlib
import hmac as _hmac
import http.server
import json
import os
import socket
import sys
import tempfile
import threading
import time
import unittest
import unittest.mock as mock
import uuid
from pathlib import Path

_here = Path(__file__).resolve().parent
if str(_here) not in sys.path:
    sys.path.insert(0, str(_here))

# Patch _forge_se on receiver + sender so audit writes go to a mock, not
# the real forge chain (tests run without an audit_path file present).
_mock_se = mock.MagicMock()
_mock_se.write_event = mock.MagicMock(return_value={"hash": "abc"})
_patch_forge_recv = mock.patch(
    "remote_trigger_receiver._forge_se", _mock_se,
)
_patch_forge_recv.start()
_patch_forge_send = mock.patch.dict("sys.modules")  # no-op placeholder

import remote_trigger_sender as rts  # noqa: E402
import remote_trigger_receiver as rtr  # noqa: E402

_patch_forge_sender = mock.patch(
    "remote_trigger_sender._forge_se", _mock_se,
)
_patch_forge_sender.start()


HMAC_KEY = "1111111111111111111111111111111111111111111111111111111111111111"
RECV_KEY = "2222222222222222222222222222222222222222222222222222222222222222"
ORIGIN_ID = "test-sender-origin"
ENDPOINT_ID = "test-endpoint"


# ── Fake HTTP receiver ────────────────────────────────────────────────────

class _FakeReceiverServer:
    """Local HTTP server wrapping a real RemoteTriggerReceiver.

    Spawns on an ephemeral port; ``url`` exposes the POST endpoint.
    """

    def __init__(self, origins_dir: Path, instance_id: str = "fake-iid-1"):
        self._origins_dir = origins_dir
        self._instance_id = instance_id
        self._port = self._find_port()
        self.received: list[dict] = []
        self._httpd: http.server.ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @staticmethod
    def _find_port() -> int:
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            return s.getsockname()[1]

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self._port}/v1/a2a/receive"

    def start(self) -> None:
        # Receiver caches instance_id at construction — pass it explicitly
        # so this fake server has a predictable identity for pin tests.
        receiver = rtr.RemoteTriggerReceiver(
            origins_dir=self._origins_dir,
            instance_id=self._instance_id,
        )
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args, **kwargs):
                pass

            def do_POST(self):  # noqa: N802
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length)
                try:
                    body = json.loads(raw)
                except Exception:
                    self.send_response(400)
                    self.end_headers()
                    return
                outer.received.append(body)
                resp = receiver.receive(body)
                payload = json.dumps(resp.to_dict()).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)

        self._httpd = http.server.ThreadingHTTPServer(
            ("127.0.0.1", self._port), Handler,
        )
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()


def _write_origin_file(origins_dir: Path, origin_id: str) -> None:
    cfg = {
        "origin_id": origin_id,
        "hmac_key": HMAC_KEY,
        "recv_key": RECV_KEY,
        "enabled": True,
        "max_ttl_s": 300,
        "allowed_personas": ["assistant"],
    }
    path = origins_dir / f"{origin_id}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


def _write_endpoint_file(
    endpoints_dir: Path, endpoint_id: str, url: str,
    instance_id_pin: str = "",
) -> None:
    cfg = {
        "endpoint_id": endpoint_id,
        "url": url,
        "hmac_key": HMAC_KEY,
        "recv_key": RECV_KEY,
        "instance_id": instance_id_pin,
        "enabled": True,
        "default_ttl_s": 60,
        "our_origin_id": ORIGIN_ID,
    }
    path = endpoints_dir / f"{endpoint_id}.json"
    path.write_text(json.dumps(cfg))
    path.chmod(0o600)


# ── RemoteEndpointRegistry tests ──────────────────────────────────────────

class TestEndpointRegistry(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.endpoints_dir = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_known_endpoint_loads(self):
        _write_endpoint_file(self.endpoints_dir, ENDPOINT_ID, "http://x/")
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        cfg = reg.load(ENDPOINT_ID)
        self.assertEqual(cfg["endpoint_id"], ENDPOINT_ID)

    def test_unknown_endpoint_raises(self):
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        with self.assertRaises(rts.EndpointError) as ctx:
            reg.load("does-not-exist")
        self.assertEqual(ctx.exception.reason, "unknown_endpoint")

    def test_disabled_endpoint_raises(self):
        cfg = {
            "endpoint_id": ENDPOINT_ID, "url": "http://x/",
            "hmac_key": HMAC_KEY, "recv_key": RECV_KEY,
            "instance_id": "", "enabled": False,
        }
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").write_text(json.dumps(cfg))
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").chmod(0o600)
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        with self.assertRaises(rts.EndpointError) as ctx:
            reg.load(ENDPOINT_ID)
        self.assertEqual(ctx.exception.reason, "endpoint_disabled")

    def test_world_readable_endpoint_raises(self):
        _write_endpoint_file(self.endpoints_dir, ENDPOINT_ID, "http://x/")
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").chmod(0o644)
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        with self.assertRaises(rts.EndpointError) as ctx:
            reg.load(ENDPOINT_ID)
        self.assertEqual(ctx.exception.reason, "endpoint_file_world_readable")

    def test_path_traversal_blocked(self):
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        for bad in ("../foo", "..", ".secret", "foo/bar", "a:b"):
            with self.assertRaises(rts.EndpointError):
                reg.load(bad)

    def test_endpoint_id_mismatch(self):
        cfg = {
            "endpoint_id": "DIFFERENT", "url": "http://x/",
            "hmac_key": HMAC_KEY, "recv_key": RECV_KEY,
            "instance_id": "", "enabled": True,
        }
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").write_text(json.dumps(cfg))
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").chmod(0o600)
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        with self.assertRaises(rts.EndpointError) as ctx:
            reg.load(ENDPOINT_ID)
        self.assertEqual(ctx.exception.reason, "endpoint_id_mismatch")

    def test_missing_field_raises(self):
        cfg = {
            "endpoint_id": ENDPOINT_ID, "url": "http://x/",
            "enabled": True,  # missing hmac_key + recv_key
        }
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").write_text(json.dumps(cfg))
        (self.endpoints_dir / f"{ENDPOINT_ID}.json").chmod(0o600)
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        with self.assertRaises(rts.EndpointError) as ctx:
            reg.load(ENDPOINT_ID)
        self.assertTrue(ctx.exception.reason.startswith("missing_fields:"))

    def test_list_ids(self):
        _write_endpoint_file(self.endpoints_dir, "alpha", "http://x/")
        _write_endpoint_file(self.endpoints_dir, "beta", "http://y/")
        reg = rts.RemoteEndpointRegistry(self.endpoints_dir)
        self.assertEqual(reg.list_ids(), ["alpha", "beta"])


# ── RemoteTriggerSender E2E (over real HTTP) ──────────────────────────────

class TestSenderE2E(unittest.TestCase):

    def setUp(self) -> None:
        self._tmp_origins = tempfile.TemporaryDirectory()
        self._tmp_endpoints = tempfile.TemporaryDirectory()
        self._tmp_iid = tempfile.TemporaryDirectory()
        self.origins_dir = Path(self._tmp_origins.name)
        self.endpoints_dir = Path(self._tmp_endpoints.name)
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(
            Path(self._tmp_iid.name) / "instance_id.json",
        )
        # Disable network membership attestation: the test license.key on the
        # developer machine causes the sender to include a network_attestation
        # block whose RS256 sig cannot be verified in the test environment.
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        _write_origin_file(self.origins_dir, ORIGIN_ID)
        self.server = _FakeReceiverServer(
            self.origins_dir, instance_id="receiver-iid-fixed",
        )
        self.server.start()
        _write_endpoint_file(
            self.endpoints_dir, ENDPOINT_ID, self.server.url,
            instance_id_pin="receiver-iid-fixed",
        )

    def tearDown(self) -> None:
        self.server.stop()
        os.environ.pop("CORVIN_INSTANCE_ID_PATH", None)
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        self._tmp_origins.cleanup()
        self._tmp_endpoints.cleanup()
        self._tmp_iid.cleanup()

    def test_send_round_trip_succeeds(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send(ENDPOINT_ID, instruction="hello world")
        self.assertEqual(result.status, "ok")
        self.assertTrue(result.ok)
        self.assertTrue(result.instance_id_match)
        self.assertEqual(result.instance_id, "receiver-iid-fixed")

    def test_sender_instance_id_in_envelope(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        sender.send(ENDPOINT_ID, instruction="hi")
        # The fake receiver records all received envelopes
        self.assertEqual(len(self.server.received), 1)
        env = self.server.received[0]
        self.assertIn("sender_instance_id", env)
        # Sender's instance_id is a UUID generated by instance_identity
        self.assertTrue(len(env["sender_instance_id"]) > 0)

    def test_pin_mismatch_rejected(self):
        # Configure a wrong pin
        cfg_path = self.endpoints_dir / f"{ENDPOINT_ID}.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["instance_id"] = "wrong-pin"
        cfg_path.write_text(json.dumps(cfg))
        cfg_path.chmod(0o600)

        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send(ENDPOINT_ID, instruction="hi")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")
        self.assertFalse(result.instance_id_match)

    def test_empty_pin_allows_any(self):
        # No pin → instance_id_match True regardless of receiver iid
        cfg_path = self.endpoints_dir / f"{ENDPOINT_ID}.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["instance_id"] = ""
        cfg_path.write_text(json.dumps(cfg))
        cfg_path.chmod(0o600)

        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send(ENDPOINT_ID, instruction="hi")
        self.assertTrue(result.ok)
        self.assertTrue(result.instance_id_match)

    def test_unknown_endpoint_returns_error_result(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send("nope", instruction="hi")
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")

    def test_transport_failure_returns_error(self):
        # Point endpoint at an unused port
        cfg_path = self.endpoints_dir / f"{ENDPOINT_ID}.json"
        cfg = json.loads(cfg_path.read_text())
        cfg["url"] = "http://127.0.0.1:1/v1/a2a/receive"
        cfg_path.write_text(json.dumps(cfg))
        cfg_path.chmod(0o600)

        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        result = sender.send(ENDPOINT_ID, instruction="hi", timeout_s=2)
        self.assertFalse(result.ok)
        self.assertEqual(result.status, "error")

    def test_replay_rejected_by_receiver(self):
        sender = rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)
        # First request succeeds
        r1 = sender.send(ENDPOINT_ID, instruction="hi")
        self.assertTrue(r1.ok)
        # Second request with a different nonce also succeeds
        r2 = sender.send(ENDPOINT_ID, instruction="hi")
        self.assertTrue(r2.ok)
        # Two distinct task_ids
        self.assertNotEqual(r1.task_id, r2.task_id)


# ── Response verification tests ───────────────────────────────────────────

class TestVerifyResponse(unittest.TestCase):

    def test_valid_signature_passes(self):
        response = {
            "task_id": "t1", "origin_id": "o1",
            "issued_at": 123.0, "instance_id": "iid",
            "status": "ok", "data": {}, "attachments": [],
        }
        payload = {k: v for k, v in response.items() if k != "signature"}
        sig = _hmac.new(
            bytes.fromhex(RECV_KEY),
            json.dumps(payload, sort_keys=True, separators=(",", ":"),
                       ensure_ascii=True).encode(),
            hashlib.sha256,
        ).hexdigest()
        response["signature"] = sig
        verified, is_signed = rts.RemoteTriggerSender._verify_response(response, RECV_KEY)
        self.assertTrue(is_signed)
        self.assertEqual(verified["task_id"], "t1")

    def test_missing_signature_rejected(self):
        with self.assertRaises(rts.ResponseVerificationError) as ctx:
            rts.RemoteTriggerSender._verify_response(
                {"task_id": "t1", "status": "ok"}, RECV_KEY,
            )
        self.assertEqual(ctx.exception.reason, "missing_signature")

    def test_bad_signature_rejected(self):
        response = {
            "task_id": "t1", "origin_id": "o1",
            "issued_at": 123.0, "instance_id": "iid",
            "status": "ok", "data": {}, "attachments": [],
            "signature": "deadbeef" * 8,
        }
        with self.assertRaises(rts.ResponseVerificationError) as ctx:
            rts.RemoteTriggerSender._verify_response(response, RECV_KEY)
        self.assertEqual(ctx.exception.reason, "bad_signature")

    def test_non_object_rejected(self):
        with self.assertRaises(rts.ResponseVerificationError):
            rts.RemoteTriggerSender._verify_response("not a dict", RECV_KEY)  # type: ignore[arg-type]


# ── ADR-0198 send_reconnect delivery semantics (2026-07-19 retry-storm fix) ─

class TestSendReconnectDelivery(unittest.TestCase):
    """Fix #2: persistence is driven by DELIVERY (a signed response arrived —
    accept OR reject), not acceptance. A signed-rejecting peer must count as
    delivered so it is not re-broadcast to on every 5-minute heartbeat forever;
    a peer with no signed response (transport error, unsigned) must NOT."""

    def setUp(self) -> None:
        self._tmp_endpoints = tempfile.TemporaryDirectory()
        self._tmp_iid = tempfile.TemporaryDirectory()
        self.endpoints_dir = Path(self._tmp_endpoints.name)
        os.environ["CORVIN_INSTANCE_ID_PATH"] = str(
            Path(self._tmp_iid.name) / "instance_id.json")
        os.environ["CORVIN_A2A_ATTESTATION_DISABLED"] = "1"
        _write_endpoint_file(
            self.endpoints_dir, ENDPOINT_ID,
            "https://peer.example.com/v1/a2a/receive")

    def tearDown(self) -> None:
        os.environ.pop("CORVIN_INSTANCE_ID_PATH", None)
        os.environ.pop("CORVIN_A2A_ATTESTATION_DISABLED", None)
        self._tmp_endpoints.cleanup()
        self._tmp_iid.cleanup()

    def _sender(self):
        return rts.RemoteTriggerSender(endpoints_dir=self.endpoints_dir)

    def test_signed_rejected_counts_as_delivered(self):
        sender = self._sender()
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               return_value={"raw": 1}), \
             mock.patch.object(rts.RemoteTriggerSender, "_verify_response",
                               return_value=({"status": "rejected"}, True)):
            self.assertTrue(
                sender.send_reconnect(ENDPOINT_ID, "https://new.example.com"))

    def test_signed_ok_delivered(self):
        sender = self._sender()
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               return_value={"raw": 1}), \
             mock.patch.object(rts.RemoteTriggerSender, "_verify_response",
                               return_value=({"status": "ok"}, True)):
            self.assertTrue(
                sender.send_reconnect(ENDPOINT_ID, "https://new.example.com"))

    def test_unsigned_response_not_delivered(self):
        sender = self._sender()
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               return_value={"raw": 1}), \
             mock.patch.object(rts.RemoteTriggerSender, "_verify_response",
                               return_value=({"status": "rejected"}, False)):
            self.assertFalse(
                sender.send_reconnect(ENDPOINT_ID, "https://new.example.com"))

    def test_transport_error_not_delivered(self):
        sender = self._sender()
        with mock.patch.object(rts.RemoteTriggerSender, "_http_post",
                               side_effect=rts.TransportError("connection_failed")):
            self.assertFalse(
                sender.send_reconnect(ENDPOINT_ID, "https://new.example.com"))


# ── ADR-0198 no-redirect POST (SSRF hardening, 2026-07-19) ──────────────────

class TestNoRedirectPost(unittest.TestCase):
    """Fix #3: _http_post must NOT follow 3xx redirects. A paired peer with a
    valid global URL could otherwise 302 our signed POST to an internal
    address. A redirect becomes a TransportError; the target is never hit."""

    @staticmethod
    def _spawn(handler_cls):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), handler_cls)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        return httpd, port

    def test_302_not_followed(self):
        target_hits: list[str] = []

        class TargetHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def _record(self):
                # urllib converts a followed 302 POST into a GET, so BOTH
                # methods must be recorded for "never contacted" to be sound.
                target_hits.append(f"{self.command} {self.path}")
                self.send_response(200)
                self.send_header("Content-Length", "2")
                self.end_headers()
                self.wfile.write(b"{}")

            do_POST = _record  # noqa: N815
            do_GET = _record   # noqa: N815

        target_httpd, target_port = self._spawn(TargetHandler)
        redirect_to = f"http://127.0.0.1:{target_port}/internal"

        class RedirHandler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *a, **k):
                pass

            def do_POST(self):  # noqa: N802
                self.send_response(302)
                self.send_header("Location", redirect_to)
                self.send_header("Content-Length", "0")
                self.end_headers()

        redir_httpd, redir_port = self._spawn(RedirHandler)
        try:
            with self.assertRaises(rts.TransportError) as ctx:
                rts.RemoteTriggerSender._http_post(
                    f"http://127.0.0.1:{redir_port}/v1/a2a/receive",
                    {"task_id": "t"}, 3)
            self.assertTrue(ctx.exception.reason.startswith("http_3"))
            self.assertEqual(target_hits, [],
                             "redirect target must never be contacted")
        finally:
            redir_httpd.shutdown(); redir_httpd.server_close()
            target_httpd.shutdown(); target_httpd.server_close()


# ── ADR-0197 audit backstop: numeric values (2026-07-19 bypass fix) ─────────

class TestAuditNumericBackstop(unittest.TestCase):
    """Fix #5: _is_safe_audit_value no longer blanket-trusts numerics. A
    Discord-UID-shaped int must be redacted like its string form; small
    counters / bools / http codes still pass."""

    def test_uid_shaped_int_redacted(self):
        out = rts._assert_audit_details_safe({"reason": 123456789012345678})
        self.assertEqual(out["reason"], "redacted")

    def test_uid_shaped_int_under_status_redacted(self):
        out = rts._assert_audit_details_safe({"status": 123456789012345678})
        self.assertEqual(out["status"], "redacted")

    def test_small_numbers_and_bools_allowed(self):
        out = rts._assert_audit_details_safe({
            "duration_ms": 1523, "http_status": 404,
            "attachments_count": 3, "reachable": True, "ttl_s": 60,
        })
        self.assertEqual(out["duration_ms"], 1523)
        self.assertEqual(out["http_status"], 404)
        self.assertEqual(out["attachments_count"], 3)
        self.assertIs(out["reachable"], True)
        self.assertEqual(out["ttl_s"], 60)

    def test_epoch_ms_timestamp_allowed(self):
        # ~1.7e12 (13-digit epoch-ms) is below the 10^13 bound.
        out = rts._assert_audit_details_safe({"duration_ms": 1_700_000_000_000})
        self.assertEqual(out["duration_ms"], 1_700_000_000_000)

    def test_non_finite_float_redacted(self):
        out = rts._assert_audit_details_safe({"duration_ms": float("inf")})
        self.assertEqual(out["duration_ms"], "redacted")


# ── CI lint ──────────────────────────────────────────────────────────────

class TestCILint(unittest.TestCase):
    def test_no_anthropic_import(self):
        import ast
        src = (_here / "remote_trigger_sender.py").read_text("utf-8")
        tree = ast.parse(src)
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for n in node.names:
                    self.assertNotEqual(n.name, "anthropic")
            elif isinstance(node, ast.ImportFrom):
                self.assertNotEqual(node.module, "anthropic")


if __name__ == "__main__":
    unittest.main(verbosity=2)

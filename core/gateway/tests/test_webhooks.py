"""Per-subtask E2E for ADR-0007 Phase 2.4 — webhook dispatch.

Covers:
  * Pure-function tests: ``sign_body`` deterministic + verifiable;
    ``verify_signature`` uses ``hmac.compare_digest``.
  * ``WebhookSecretStore`` round-trip: set / get / list / delete,
    mode 0o600 enforced, malformed JSON / wrong mode → fail-closed.
  * Happy path against a stub HTTP server: POST → run completes →
    webhook arrives at the stub with a verifiable signature, the
    correct event type, and the expected JSON shape.
  * Retry on transient 5xx: stub returns 503 twice then 200 →
    eventually delivered, attempt count recorded in the audit chain.
  * Give-up after exhausting retries: stub always 500 →
    ``gateway.webhook_delivery_failed`` audited, no further attempts.
  * 4xx is permanent: stub returns 404 once → no retry, audited
    as failure with ``http-404``.
  * Missing secret: ``spec.webhook.secret_ref`` not in the store →
    ``gateway.webhook_secret_missing`` audited, no HTTP attempt.
  * No webhook in spec: dispatcher does not call ``WebhookDispatcher``,
    no audit fired.
  * CLI round-trip: ``cli token webhook secret set --value X`` then
    ``list``, then ``revoke``.

The HTTP stub is :class:`http.server.HTTPServer` in a background
thread bound to an ephemeral port. Tests assert against a per-server
``received`` list that the handler appends to.
"""
from __future__ import annotations

import asyncio
import http.server
import io
import ipaddress
import json
import os
import socket
import socketserver
import ssl
import stat
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager, redirect_stdout
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "core" / "console"))

from fastapi.testclient import TestClient  # noqa: E402

from corvin_gateway import cli, webhooks  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from corvin_gateway.runs import RunRecord  # noqa: E402
from corvin_gateway.webhooks import (  # noqa: E402
    SIGNATURE_HEADER,
    WebhookDispatcher,
    WebhookSecretStore,
    WebhookSecretStoreMalformed,
    sign_body,
    verify_signature,
)
from forge import security_events as _security_events  # noqa: E402


# ── Self-signed TLS cert for the loopback stub ───────────────────────
#
# PENTEST-6 requires webhook.url to be https:// (rejected at POST otherwise),
# so the delivery stub must speak TLS. We mint a self-signed cert for
# 127.0.0.1 once per test run; the WebhookDispatcher VERIFIES against it
# (verify=<cert path>) — verification stays ON, just pinned to this test CA.

def _make_selfsigned(dirpath: Path) -> tuple[str, str]:
    from cryptography import x509
    from cryptography.x509.oid import NameOID
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    import datetime

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "127.0.0.1")])
    san = x509.SubjectAlternativeName([
        x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
        x509.DNSName("localhost"),
    ])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (
        x509.CertificateBuilder()
        .subject_name(name).issuer_name(name)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - datetime.timedelta(days=1))
        .not_valid_after(now + datetime.timedelta(days=3650))
        .add_extension(san, critical=False)
        .sign(key, hashes.SHA256())
    )
    cert_path = dirpath / "cert.pem"
    key_path = dirpath / "key.pem"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL,
        serialization.NoEncryption(),
    ))
    return str(cert_path), str(key_path)


_TLS_DIR = Path(tempfile.mkdtemp(prefix="gw-wh-tls-"))
_TLS_CERT, _TLS_KEY = _make_selfsigned(_TLS_DIR)
# Client-side SSL context that trusts the stub's self-signed cert (verify ON).
_TLS_VERIFY_CTX = ssl.create_default_context(cafile=_TLS_CERT)


# ── Common fixtures ──────────────────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-wh-test-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.02"
        for t in tenants:
            (home / "tenants" / t / "global" / "auth").mkdir(parents=True)
            (home / "tenants" / t / "global" / "forge").mkdir(parents=True)
            (home / "tenants" / t / "global" / "gateway" / "runs").mkdir(parents=True)
        try:
            yield home
        finally:
            os.environ.pop("CORVIN_HOME", None)
            os.environ.pop("ADAPTER_FAKE_CLAUDE", None)
            os.environ.pop("ADAPTER_FAKE_DELAY", None)


# ── Stub HTTP server ─────────────────────────────────────────────────


class _StubServer:
    """Tiny background HTTP server bound to an ephemeral port.

    The ``responses`` list is consumed in order; each entry is either
    an int (status code, empty body) or a (status, body_bytes) tuple.
    If responses is exhausted, the last response repeats.
    """

    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.received: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else b""
                with outer._lock:
                    outer.received.append({
                        "path":      self.path,
                        "headers":   {k.lower(): v for k, v in self.headers.items()},
                        "body":      body,
                    })
                    if not outer.responses:
                        # Repeat the last response if we exhausted
                        if outer.received:
                            resp = (500, b"exhausted")
                        else:
                            resp = (500, b"")
                    else:
                        # Pop the next response, keep the last as the
                        # repeating fallback.
                        if len(outer.responses) == 1:
                            resp = outer.responses[0]
                        else:
                            resp = outer.responses.pop(0)
                if isinstance(resp, int):
                    status, body_out = resp, b""
                else:
                    status, body_out = resp
                self.send_response(status)
                self.send_header("Content-Length", str(len(body_out)))
                self.end_headers()
                if body_out:
                    self.wfile.write(body_out)

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return  # silence stderr noise

        # 127.0.0.1 + port 0 → ephemeral port assigned by kernel
        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        # PENTEST-6: webhook.url must be https, so the stub serves TLS.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(_TLS_CERT, _TLS_KEY)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        # Suppress handshake-error tracebacks (readiness probe below is bare TCP).
        self._server.handle_error = lambda *a, **k: None  # type: ignore[assignment]
        self.port = self._server.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}/callback"
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            daemon=True,
        )

    def __enter__(self) -> "_StubServer":
        self._thread.start()
        # Brief wait for socket to be ready (bare TCP connect; no TLS handshake).
        for _ in range(20):
            try:
                s = socket.create_connection(("127.0.0.1", self.port), 0.1)
                s.close()
                break
            except OSError:
                time.sleep(0.01)
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _wait_for_received(server: _StubServer, *, count: int = 1, timeout: float = 5.0) -> None:
    end = time.time() + timeout
    while time.time() < end:
        if len(server.received) >= count:
            return
        time.sleep(0.02)


# ── Helpers + context managers ───────────────────────────────────────


def _hdr() -> dict[str, str]:
    return {}


def _good_run_body_with_webhook(url: str, secret_ref: str = "wh1"):
    return {
        "apiVersion": "corvin/v1",
        "kind":       "Run",
        "spec": {
            "persona": "docs",
            "input":   "ping",
            "webhook": {"url": url, "secret_ref": secret_ref},
        },
    }


@contextmanager
def gateway_client(
    *,
    max_retries: int = 2,
    backoff_s: tuple[float, ...] = (0.05, 0.05, 0.05),
    timeout_s: float = 5.0,
    guard_bypass: bool = True,
):
    """Engage lifespan with a fast-backoff webhook dispatcher.

    ``verify=_TLS_CERT`` makes the dispatcher trust the loopback stub's
    self-signed cert (verification stays ON). ``guard_bypass`` patches the
    SSRF/L35 guard to a no-op so the delivery tests can reach the 127.0.0.1
    stub — the guard itself is exercised separately in the rejection tests.
    """
    app.state.dispatcher = RunDispatcher(
        webhook_dispatcher=WebhookDispatcher(
            max_retries=max_retries,
            backoff_s=backoff_s,
            timeout_s=timeout_s,
            verify=_TLS_VERIFY_CTX,
        ),
    )
    patcher = (
        # The guard now RETURNS the pinned IP the dispatcher connects to
        # (PENTEST-7). The loopback stub always binds 127.0.0.1, so the bypass
        # pins there — cert SAN includes IPAddress 127.0.0.1, so TLS stays ON.
        patch("corvin_gateway.webhooks._assert_webhook_url_safe",
              lambda url, tenant_id: "127.0.0.1")
        if guard_bypass else None
    )
    if patcher is not None:
        patcher.start()
    try:
        with TestClient(app) as client:
            yield client
    finally:
        if patcher is not None:
            patcher.stop()
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None


def _poll_until_terminal(client, url, headers, *, timeout_s: float = 5.0):
    end = time.time() + timeout_s
    last = None
    while time.time() < end:
        r = client.get(url, headers=headers)
        last = r
        if r.status_code == 200 and r.json().get("status") in (
            "completed", "failed", "budget_exceeded",
        ):
            return r
        time.sleep(0.02)
    return last


# ── Pure-function signature tests ────────────────────────────────────


class SignatureTests(unittest.TestCase):
    def test_sign_body_deterministic(self):
        sig1 = sign_body("secret", b"hello")
        sig2 = sign_body("secret", b"hello")
        self.assertEqual(sig1, sig2)
        self.assertTrue(sig1.startswith("sha256="))
        self.assertEqual(len(sig1), len("sha256=") + 64)

    def test_sign_body_different_secret_yields_different_sig(self):
        a = sign_body("s1", b"hello")
        b = sign_body("s2", b"hello")
        self.assertNotEqual(a, b)

    def test_verify_signature_ok(self):
        body = b'{"event":"run.completed"}'
        sig = sign_body("topsecret", body)
        self.assertTrue(verify_signature("topsecret", body, sig))

    def test_verify_signature_wrong_secret(self):
        body = b'{"event":"x"}'
        sig = sign_body("a", body)
        self.assertFalse(verify_signature("b", body, sig))

    def test_verify_signature_tampered_body(self):
        sig = sign_body("s", b"original")
        self.assertFalse(verify_signature("s", b"tampered", sig))

    def test_verify_signature_bad_input(self):
        self.assertFalse(verify_signature("s", b"x", ""))
        self.assertFalse(verify_signature("s", b"x", None))  # type: ignore[arg-type]


# ── Secret store tests ───────────────────────────────────────────────


class SecretStoreTests(unittest.TestCase):
    def test_round_trip(self):
        with sandbox(("acme",)) as home:
            store = WebhookSecretStore()
            store.set_secret("acme", "wh1", "topsecret-abc")
            self.assertEqual(store.get_secret("acme", "wh1"), "topsecret-abc")
            # mode 0o600
            p = home / "tenants" / "acme" / "global" / "gateway" / "webhook_secrets.json"
            self.assertEqual(p.stat().st_mode & 0o777, 0o600)

    def test_unknown_ref_returns_none(self):
        with sandbox(("acme",)) as home:
            store = WebhookSecretStore()
            self.assertIsNone(store.get_secret("acme", "nope"))

    def test_list_and_delete(self):
        with sandbox(("acme",)) as home:
            store = WebhookSecretStore()
            store.set_secret("acme", "wh1", "v1")
            store.set_secret("acme", "wh2", "v2")
            entries = store.list_secrets("acme")
            self.assertEqual([e["ref"] for e in entries], ["wh1", "wh2"])
            self.assertTrue(store.delete_secret("acme", "wh1"))
            self.assertIsNone(store.get_secret("acme", "wh1"))
            self.assertEqual(store.get_secret("acme", "wh2"), "v2")
            # Idempotent delete
            self.assertFalse(store.delete_secret("acme", "wh1"))

    def test_invalid_ref_shape_rejected(self):
        with sandbox(("acme",)) as home:
            store = WebhookSecretStore()
            with self.assertRaises(ValueError):
                store.set_secret("acme", "../escape", "v")
            with self.assertRaises(ValueError):
                store.set_secret("acme", "", "v")
            with self.assertRaises(ValueError):
                store.set_secret("acme", "wh", "")

    def test_world_readable_store_returns_none(self):
        with sandbox(("acme",)) as home:
            store = WebhookSecretStore()
            store.set_secret("acme", "wh1", "v")
            p = home / "tenants" / "acme" / "global" / "gateway" / "webhook_secrets.json"
            os.chmod(p, 0o644)
            self.assertIsNone(store.get_secret("acme", "wh1"))

    def test_unprovisioned_tenant_rejected(self):
        with tempfile.TemporaryDirectory(prefix="gw-wh-empty-") as td:
            os.environ["CORVIN_HOME"] = td
            try:
                store = WebhookSecretStore()
                with self.assertRaises(WebhookSecretStoreMalformed):
                    store.set_secret("acme", "wh1", "v")
            finally:
                os.environ.pop("CORVIN_HOME", None)


# ── Happy path with stub server ──────────────────────────────────────


class HappyPathTests(unittest.TestCase):
    def test_terminal_status_triggers_signed_webhook(self):
        with sandbox(("acme",)) as home:
            WebhookSecretStore().set_secret("acme", "wh1", "topsecret")
            with _StubServer(responses=[(200, b"")]) as stub:
                with gateway_client() as client:
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body_with_webhook(stub.url),
                        headers=_hdr(),
                    )
                    run_id = r.json()["run_id"]
                    _poll_until_terminal(
                        client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    )
                _wait_for_received(stub, count=1)
            self.assertEqual(len(stub.received), 1)
            req = stub.received[0]
            # Headers
            self.assertEqual(req["headers"]["content-type"], "application/json")
            self.assertIn(SIGNATURE_HEADER.lower(), req["headers"])
            sig = req["headers"][SIGNATURE_HEADER.lower()]
            self.assertTrue(sig.startswith("sha256="))
            # Verify signature with the operator's secret
            self.assertTrue(verify_signature("topsecret", req["body"], sig))
            # Payload shape
            payload = json.loads(req["body"])
            self.assertEqual(payload["event"], "run.completed")
            self.assertEqual(payload["tenant_id"], "acme")
            self.assertEqual(payload["run_id"], run_id)
            self.assertEqual(payload["status"], "completed")
            self.assertIn("ts", payload)
            self.assertIn("ping", payload["result"]["final_text"])
            # Audit
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            dispatched = [e for e in lines if e["event_type"] == "gateway.webhook_dispatched"]
            self.assertEqual(len(dispatched), 1)
            self.assertEqual(dispatched[0]["details"]["attempts"], 1)
            self.assertEqual(dispatched[0]["details"]["host"], "127.0.0.1")
            # Chain still verifies
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, problems)


# ── Retry on 5xx ─────────────────────────────────────────────────────


class RetryTests(unittest.TestCase):
    def test_retry_on_503_then_succeed(self):
        with sandbox(("acme",)) as home:
            WebhookSecretStore().set_secret("acme", "wh1", "s")
            with _StubServer(responses=[(503, b""), (503, b""), (200, b"")]) as stub:
                with gateway_client(max_retries=3) as client:
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body_with_webhook(stub.url),
                        headers=_hdr(),
                    )
                    run_id = r.json()["run_id"]
                    _poll_until_terminal(
                        client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    )
                _wait_for_received(stub, count=3)
            self.assertEqual(len(stub.received), 3)
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            dispatched = [e for e in lines if e["event_type"] == "gateway.webhook_dispatched"]
            self.assertEqual(len(dispatched), 1)
            self.assertEqual(dispatched[0]["details"]["attempts"], 3)

    def test_give_up_after_max_retries(self):
        with sandbox(("acme",)) as home:
            WebhookSecretStore().set_secret("acme", "wh1", "s")
            with _StubServer(responses=[(500, b"")]) as stub:
                with gateway_client(max_retries=2) as client:
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body_with_webhook(stub.url),
                        headers=_hdr(),
                    )
                    run_id = r.json()["run_id"]
                    _poll_until_terminal(
                        client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    )
                _wait_for_received(stub, count=3)
            # 1 initial + 2 retries = 3 attempts
            self.assertEqual(len(stub.received), 3)
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            failed = [e for e in lines if e["event_type"] == "gateway.webhook_delivery_failed"]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["details"]["attempts"], 3)
            self.assertEqual(failed[0]["details"]["last_status"], 500)


class PermanentFailureTests(unittest.TestCase):
    def test_4xx_no_retry(self):
        with sandbox(("acme",)) as home:
            WebhookSecretStore().set_secret("acme", "wh1", "s")
            with _StubServer(responses=[(404, b"")]) as stub:
                with gateway_client(max_retries=3) as client:
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body_with_webhook(stub.url),
                        headers=_hdr(),
                    )
                    run_id = r.json()["run_id"]
                    _poll_until_terminal(
                        client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    )
                # Give the server a moment in case anything queued.
                time.sleep(0.1)
            self.assertEqual(len(stub.received), 1)
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            failed = [e for e in lines if e["event_type"] == "gateway.webhook_delivery_failed"]
            self.assertEqual(len(failed), 1)
            self.assertEqual(failed[0]["details"]["last_status"], 404)


# ── Missing secret + no webhook ──────────────────────────────────────


class SecretMissingTests(unittest.TestCase):
    def test_missing_secret_audited_no_http_attempt(self):
        with sandbox(("acme",)) as home:
            # NO call to set_secret; secret_ref will not resolve
            with _StubServer(responses=[(200, b"")]) as stub:
                with gateway_client() as client:
                    r = client.post(
                        "/v1/tenants/acme/runs",
                        json=_good_run_body_with_webhook(stub.url, secret_ref="missing"),
                        headers=_hdr(),
                    )
                    run_id = r.json()["run_id"]
                    _poll_until_terminal(
                        client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                    )
                time.sleep(0.1)
            self.assertEqual(stub.received, [])
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            missing = [e for e in lines if e["event_type"] == "gateway.webhook_secret_missing"]
            self.assertEqual(len(missing), 1)
            self.assertEqual(missing[0]["details"]["secret_ref"], "missing")


class NoWebhookTests(unittest.TestCase):
    def test_no_webhook_no_audit(self):
        with sandbox(("acme",)) as home:
            with gateway_client() as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json={
                        "apiVersion": "corvin/v1",
                        "kind":       "Run",
                        "spec": {"persona": "x", "input": "y"},
                    },
                    headers=_hdr(),
                )
                run_id = r.json()["run_id"]
                _poll_until_terminal(
                    client, f"/v1/tenants/acme/runs/{run_id}", _hdr(),
                )
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            webhook_events = [
                e for e in lines
                if e["event_type"].startswith("gateway.webhook_")
            ]
            self.assertEqual(webhook_events, [])


# ── PENTEST-6: SSRF / scheme rejection ───────────────────────────────


def _completed_record(tenant_id: str) -> RunRecord:
    now = time.time()
    return RunRecord(
        run_id="run_pentest6ssrf000000001",
        tenant_id=tenant_id,
        status="completed",
        created_at=now,
        updated_at=now,
        request={"spec": {"persona": "docs", "input": "x"}},
        result={"final_text": "x"},
        error=None,
    )


class WebhookSchemeRejectionTests(unittest.TestCase):
    def test_http_webhook_url_rejected_at_post(self):
        """PENTEST-6(a): plaintext http:// webhook.url is refused at validation."""
        with sandbox(("acme",)):
            with gateway_client() as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body_with_webhook("http://acme.example.com/cb"),
                    headers=_hdr(),
                )
            self.assertEqual(r.status_code, 422, r.text)

    def test_non_https_scheme_rejected_at_post(self):
        with sandbox(("acme",)):
            with gateway_client() as client:
                r = client.post(
                    "/v1/tenants/acme/runs",
                    json=_good_run_body_with_webhook("ftp://acme.example.com/cb"),
                    headers=_hdr(),
                )
            self.assertEqual(r.status_code, 422, r.text)


class WebhookSsrfBlockTests(unittest.TestCase):
    """PENTEST-6(b/c): the pre-POST guard rejects loopback / IMDS targets and
    audits a ``gateway.webhook_blocked`` event WITHOUT making any HTTP call."""

    def _assert_blocked(self, home: Path, url: str) -> None:
        WebhookSecretStore().set_secret("acme", "wh1", "s")
        rec = _completed_record("acme")
        disp = WebhookDispatcher(max_retries=0, backoff_s=(0.0,))
        out = asyncio.run(
            disp.dispatch_for_record(rec, url=url, secret_ref="wh1")
        )
        self.assertFalse(out.delivered)
        self.assertEqual(out.attempts, 0)
        self.assertEqual(out.last_error, "ssrf-blocked")
        chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
        lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
        blocked = [e for e in lines if e["event_type"] == "gateway.webhook_blocked"]
        self.assertEqual(len(blocked), 1, lines)
        # No delivery / secret-lookup events for a blocked URL.
        self.assertFalse(
            [e for e in lines if e["event_type"] == "gateway.webhook_dispatched"]
        )

    def test_loopback_url_blocked(self):
        with sandbox(("acme",)) as home:
            self._assert_blocked(home, "https://127.0.0.1:9/callback")

    def test_imds_url_blocked(self):
        with sandbox(("acme",)) as home:
            self._assert_blocked(home, "https://169.254.169.254/latest/meta-data/")

    def test_http_scheme_blocked_at_dispatch_defense_in_depth(self):
        """A legacy on-disk record with an http:// URL is still refused at the
        dispatch guard even though it bypassed the runs.py POST validator."""
        with sandbox(("acme",)) as home:
            self._assert_blocked(home, "http://127.0.0.1:9/callback")


# ── CLI round-trip ───────────────────────────────────────────────────


class CliTests(unittest.TestCase):
    def test_secret_set_list_revoke(self):
        with sandbox(("acme",)) as home:
            # set via --value
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main([
                    "webhook", "secret", "set", "acme", "wh1",
                    "--value", "topsecret-v",
                ])
            self.assertEqual(rc, 0)
            self.assertIn("stored", buf.getvalue())

            # list
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["webhook", "secret", "list", "acme"])
            self.assertEqual(rc, 0)
            self.assertIn("wh1", buf.getvalue())
            self.assertNotIn("topsecret-v", buf.getvalue())

            # revoke
            buf = io.StringIO()
            with redirect_stdout(buf):
                rc = cli.main(["webhook", "secret", "revoke", "acme", "wh1"])
            self.assertEqual(rc, 0)

            # revoke again → 1
            buf = io.StringIO()
            stderr = io.StringIO()
            old_err = sys.stderr
            sys.stderr = stderr
            try:
                with redirect_stdout(buf):
                    rc = cli.main(["webhook", "secret", "revoke", "acme", "wh1"])
            finally:
                sys.stderr = old_err
            self.assertEqual(rc, 1)

    def test_secret_set_from_stdin(self):
        with sandbox(("acme",)) as home:
            old_stdin = sys.stdin
            sys.stdin = io.StringIO("from-stdin-value\n")
            try:
                buf = io.StringIO()
                with redirect_stdout(buf):
                    rc = cli.main(["webhook", "secret", "set", "acme", "wh2"])
                self.assertEqual(rc, 0)
            finally:
                sys.stdin = old_stdin
            self.assertEqual(
                WebhookSecretStore().get_secret("acme", "wh2"),
                "from-stdin-value",
            )


if __name__ == "__main__":
    unittest.main(verbosity=2)

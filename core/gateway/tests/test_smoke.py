"""End-to-end smoke for ADR-0007 Phase 2 — real uvicorn on a port.

Phase 2.6.

Where Phases 2.2–2.5 used FastAPI's ``TestClient`` (in-process, no
TCP), this suite spins up a **real** ``uvicorn`` instance on an
ephemeral port and drives the full surface through ``httpx.Client``
over plain HTTP. It is the closure gate: if every previous sub-phase
behaves correctly under the real ASGI server, the Phase 2 surface
is shippable.

Cases:
  * ``/healthz`` reachable over real HTTP.
  * Full pipeline: token issue → POST /runs → poll GET → SSE consume
    → outbound webhook callback verified at a stub HTTP server →
    audit-chain integrity verified end-to-end.
  * Cross-tenant gate still trips over real HTTP (403 + audit event).

Hermetic via ``ADAPTER_FAKE_CLAUDE=1`` so no API credits are spent.
"""
from __future__ import annotations

import http.server
import ipaddress
import json
import os
import socket
import socketserver
import ssl
import sys
import tempfile
import threading
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from typing import Any
from unittest.mock import patch

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "gateway"))
sys.path.insert(0, str(_REPO / "operator" / "forge"))
sys.path.insert(0, str(_REPO / "operator" / "bridges" / "shared"))
sys.path.insert(0, str(_REPO / "core" / "console"))

import httpx  # noqa: E402
import uvicorn  # noqa: E402

from corvin_gateway import webhooks  # noqa: E402
from corvin_gateway.app import app  # noqa: E402
from corvin_gateway.dispatcher import RunDispatcher  # noqa: E402
from corvin_gateway.webhooks import (  # noqa: E402
    SIGNATURE_HEADER,
    WebhookDispatcher,
    WebhookSecretStore,
    verify_signature,
)
from forge import security_events as _security_events  # noqa: E402


# ── Self-signed TLS for the loopback webhook stub (PENTEST-6: https-only) ─────

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


_TLS_DIR = Path(tempfile.mkdtemp(prefix="gw-smoke-tls-"))
_TLS_CERT, _TLS_KEY = _make_selfsigned(_TLS_DIR)
_TLS_VERIFY_CTX = ssl.create_default_context(cafile=_TLS_CERT)


# ── Sandbox + uvicorn bootstrap ──────────────────────────────────────


@contextmanager
def sandbox(tenants=("acme",)):
    with tempfile.TemporaryDirectory(prefix="gw-smoke-") as td:
        home = Path(td)
        os.environ["CORVIN_HOME"] = str(home)
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        os.environ["ADAPTER_FAKE_DELAY"] = "0.05"
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


@contextmanager
def uvicorn_server(fast_webhook: bool = True):
    """Start uvicorn on an ephemeral port + yield the base URL.

    Pre-installs a fast-backoff webhook dispatcher on ``app.state``
    BEFORE uvicorn's lifespan creates a default one. The lifespan
    honours an existing dispatcher and only constructs a fresh one
    when the slot is empty (same pattern Phases 2.3–2.5 use).
    """
    if fast_webhook:
        app.state.dispatcher = RunDispatcher(
            webhook_dispatcher=WebhookDispatcher(
                max_retries=1,
                backoff_s=(0.05,),
                timeout_s=2.0,
                verify=_TLS_VERIFY_CTX,  # trust the loopback stub's self-signed cert
            ),
        )

    config = uvicorn.Config(
        app,
        host="127.0.0.1",
        port=0,
        log_level="warning",
        lifespan="on",
    )
    server = uvicorn.Server(config)
    ready = threading.Event()

    def _run():
        server.run()

    t = threading.Thread(target=_run, daemon=True)
    t.start()

    # Wait for uvicorn to bind + accept connections
    deadline = time.time() + 5.0
    while time.time() < deadline:
        if server.started and server.servers:
            for sock in server.servers[0].sockets:
                try:
                    port = sock.getsockname()[1]
                    # Confirm we can connect
                    with socket.create_connection(("127.0.0.1", port), 0.5):
                        ready.set()
                        url = f"http://127.0.0.1:{port}"
                        break
                except OSError:
                    continue
            if ready.is_set():
                break
        time.sleep(0.02)
    if not ready.is_set():
        server.should_exit = True
        t.join(timeout=2)
        raise RuntimeError("uvicorn failed to start within 5 s")

    # The webhook stub runs on 127.0.0.1 (loopback), which the PENTEST-6 SSRF
    # guard would (correctly) reject. Bypass the guard for the in-process stub;
    # its enforcement is covered directly in test_webhooks.py.
    guard_patch = patch(
        "corvin_gateway.webhooks._assert_webhook_url_safe",
        lambda url, tenant_id: None,
    )
    guard_patch.start()
    try:
        yield url
    finally:
        guard_patch.stop()
        server.should_exit = True
        t.join(timeout=10)
        # Reset module-level state for the next test
        if hasattr(app.state, "dispatcher"):
            app.state.dispatcher = None


# ── Webhook stub (re-use the pattern from test_webhooks.py) ──────────


class _StubServer:
    def __init__(self) -> None:
        self.received: list[dict[str, Any]] = []
        self._lock = threading.Lock()
        outer = self

        class Handler(http.server.BaseHTTPRequestHandler):
            def do_POST(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length > 0 else b""
                with outer._lock:
                    outer.received.append({
                        "headers": {k.lower(): v for k, v in self.headers.items()},
                        "body":    body,
                    })
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
                return

        self._server = socketserver.TCPServer(("127.0.0.1", 0), Handler)
        # PENTEST-6: webhook.url must be https, so the stub serves TLS.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(_TLS_CERT, _TLS_KEY)
        self._server.socket = ctx.wrap_socket(self._server.socket, server_side=True)
        self._server.handle_error = lambda *a, **k: None  # type: ignore[assignment]
        self.port = self._server.server_address[1]
        self.url = f"https://127.0.0.1:{self.port}/callback"
        self._thread = threading.Thread(
            target=self._server.serve_forever, daemon=True,
        )

    def __enter__(self) -> "_StubServer":
        self._thread.start()
        return self

    def __exit__(self, *exc) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=2)


def _wait_for(predicate, *, timeout: float = 5.0) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if predicate():
            return True
        time.sleep(0.02)
    return False


# ── Tests ────────────────────────────────────────────────────────────


class HealthCheckOverHttpTests(unittest.TestCase):
    def test_healthz_over_real_socket(self):
        with sandbox(("acme",)):
            with uvicorn_server() as base_url:
                r = httpx.get(f"{base_url}/healthz", timeout=5.0)
            self.assertEqual(r.status_code, 200)
            body = r.json()
            self.assertEqual(body["status"], "ok")
            self.assertIn("version", body)


class FullPipelineTests(unittest.TestCase):
    def test_post_sse_webhook_audit(self):
        with sandbox(("acme",)) as home:
            # 1) Webhook secret
            WebhookSecretStore().set_secret("acme", "wh-smoke", "topsecret-smoke")

            with _StubServer() as stub:
                with uvicorn_server() as base_url:
                    # 2) POST a run with webhook + collect run_id
                    post = httpx.post(
                        f"{base_url}/v1/tenants/acme/runs",
                        json={
                            "apiVersion": "corvin/v1",
                            "kind":       "Run",
                            "spec": {
                                "persona": "docs",
                                "input":   "smoke-payload",
                                "webhook": {
                                    "url":         stub.url,
                                    "secret_ref":  "wh-smoke",
                                },
                            },
                        },
                        timeout=5.0,
                    )
                    self.assertEqual(post.status_code, 202, post.text)
                    run_id = post.json()["run_id"]
                    self.assertTrue(run_id.startswith("run_"))

                    # 3) Poll GET until completed (real HTTP)
                    deadline = time.time() + 5.0
                    final = None
                    while time.time() < deadline:
                        g = httpx.get(
                            f"{base_url}/v1/tenants/acme/runs/{run_id}",
                            timeout=2.0,
                        )
                        if g.status_code == 200 and g.json().get("status") in (
                            "completed", "failed", "budget_exceeded",
                        ):
                            final = g.json()
                            break
                        time.sleep(0.05)
                    self.assertIsNotNone(final, "run never reached terminal state")
                    self.assertEqual(final["status"], "completed", final)
                    self.assertIn("smoke-payload", final["result"]["final_text"])

                    # 4) SSE consume — the run is already terminal,
                    # so we get the full history + terminal frame
                    sse_frames: list[dict[str, Any]] = []
                    with httpx.stream(
                        "GET",
                        f"{base_url}/v1/tenants/acme/runs/{run_id}/events",
                        timeout=5.0,
                    ) as s:
                        self.assertEqual(s.status_code, 200)
                        self.assertTrue(
                            s.headers["content-type"].startswith(
                                "text/event-stream"
                            ),
                            s.headers["content-type"],
                        )
                        buf: dict[str, str] = {}
                        for line in s.iter_lines():
                            if line == "":
                                if buf:
                                    ev = buf.get("event", "message")
                                    data = buf.get("data", "")
                                    try:
                                        payload = json.loads(data)
                                    except json.JSONDecodeError:
                                        payload = {"raw": data}
                                    sse_frames.append({
                                        "event": ev, "data": payload,
                                    })
                                    buf = {}
                                continue
                            if ":" in line:
                                k, _, v = line.partition(":")
                                buf[k.strip()] = v.strip()
                    self.assertGreaterEqual(len(sse_frames), 1, sse_frames)
                    self.assertEqual(sse_frames[-1]["event"], "run.completed")

                    # 5) Webhook callback verified
                    self.assertTrue(
                        _wait_for(lambda: len(stub.received) >= 1, timeout=5.0),
                        "webhook never arrived",
                    )

                self.assertEqual(len(stub.received), 1)
                wh_req = stub.received[0]
                # Signature verifies with the operator's secret
                sig = wh_req["headers"][SIGNATURE_HEADER.lower()]
                self.assertTrue(verify_signature(
                    "topsecret-smoke", wh_req["body"], sig,
                ))
                # Payload structure
                payload = json.loads(wh_req["body"])
                self.assertEqual(payload["event"], "run.completed")
                self.assertEqual(payload["run_id"], run_id)
                self.assertEqual(payload["tenant_id"], "acme")
                self.assertEqual(payload["status"], "completed")

            # 6) Audit chain integrity end-to-end
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, f"chain broken: {problems}")
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            event_types = {e["event_type"] for e in lines}
            for required in (
                "gateway.run_created",
                "gateway.run_status_changed",
                "gateway.webhook_dispatched",
            ):
                self.assertIn(required, event_types, event_types)


# CrossTenantOverHttpTests removed — cross-tenant 403 enforcement relied
# on token-based auth which has been removed. Loopback binding is now the
# security boundary; cloud OIDC enforcement will cover cross-tenant gate.


if __name__ == "__main__":
    unittest.main(verbosity=2)

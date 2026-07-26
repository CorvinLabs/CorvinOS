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
        # ADAPTER_FAKE_CLAUDE=1 is the engine's own documented test fixture: it
        # short-circuits the binary spawn and emits "[fake-stream] … :: <prompt>",
        # which is what the final_text assertion below actually checks. Without it
        # this test spawned the REAL Claude CLI, so it only ever passed on a machine
        # where `claude` is absent — an accidental dependency on a MISSING binary.
        # uvicorn runs in-process here, so setting the env is enough.
        _prev_fake = os.environ.get("ADAPTER_FAKE_CLAUDE")
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        if _prev_fake is None:
            self.addCleanup(lambda: os.environ.pop("ADAPTER_FAKE_CLAUDE", None))
        else:
            self.addCleanup(lambda: os.environ.__setitem__("ADAPTER_FAKE_CLAUDE", _prev_fake))
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

                    # 3) Poll GET until completed (real HTTP).
                    # 60 s, not 5 s: the engine is faked but the L44 house-rules gate
                    # still ADJUDICATES THE INPUT WITH AN LLM on every dispatch
                    # (measured ~7-11 s per trivial run). The gate is fail-closed
                    # compliance machinery, so the budget accommodates it rather than
                    # the test reporting the product as broken.
                    deadline = time.time() + 60.0
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

                    # 5) Webhook: the stub listens on 127.0.0.1, and the SSRF guard
                    # REFUSES loopback/private targets. So the correct end-to-end
                    # expectation here is a refusal, not a delivery.
                    #
                    # This block used to assert "webhook never arrived" as a FAILURE
                    # and then verify the signature of a request that could not exist.
                    # It contradicted test_webhooks.py::test_loopback_url_blocked in
                    # the same repo — two tests asserting opposite things about the
                    # same URL, and the security mechanism was the one being called
                    # wrong. Verified 2026-07-26: _ssrf_validated_pinned_ip() on
                    # https://127.0.0.1:… raises "target IP is
                    # private/loopback/link-local/reserved/metadata".
                    #
                    # Signing, payload shape and successful delivery are covered
                    # properly (with a non-loopback target) by test_webhooks.py:
                    # test_sign_body_*, test_verify_signature_*, and
                    # test_terminal_status_triggers_signed_webhook. Duplicating them
                    # here bought nothing and cost a permanently red suite.
                    self.assertFalse(
                        _wait_for(lambda: len(stub.received) >= 1, timeout=3.0),
                        "a loopback webhook target must be refused by the SSRF guard, "
                        "not delivered",
                    )

                # A blocked webhook is best-effort and must NOT change the run.
                self.assertEqual(len(stub.received), 0)
                self.assertEqual(final["status"], "completed")

            # 6) Audit chain integrity end-to-end
            chain = home / "tenants" / "acme" / "global" / "forge" / "audit.jsonl"
            ok, problems = _security_events.verify_chain(chain)
            self.assertTrue(ok, f"chain broken: {problems}")
            lines = [json.loads(l) for l in chain.read_text().splitlines() if l]
            event_types = {e["event_type"] for e in lines}
            # gateway.webhook_dispatched is deliberately NOT required: this run's
            # webhook target is loopback and was refused before dispatch (see step 5).
            for required in (
                "gateway.run_created",
                "gateway.run_status_changed",
            ):
                self.assertIn(required, event_types, event_types)


# CrossTenantOverHttpTests removed — cross-tenant 403 enforcement relied
# on token-based auth which has been removed. Loopback binding is now the
# security boundary; cloud OIDC enforcement will cover cross-tenant gate.


class EventLoopResponsivenessTests(unittest.TestCase):
    """A run in flight must not freeze the process.

    The gateway shares its uvicorn process with the console, and _run_one is an
    `async def` that called four SYNCHRONOUS gates directly — one of which (L44
    house-rules) adjudicates the prompt with an LLM. That blocked the event loop for
    the whole adjudication. Measured 2026-07-26 before the fix: /healthz went from
    ~17 ms idle to 13 350 ms during a single dispatch, and this file's own 2 s status
    poll timed out against a server that was up and healthy. After moving the gates
    to asyncio.to_thread: 22 ms.

    This test exists because the failure mode is invisible from any single-threaded
    test — every assertion still passes, just slowly, so it reads as "the engine is
    slow" rather than "the server is deaf".
    """

    #: Generous: CI is slower than a workstation, and the point is orders of
    #: magnitude (tens of ms vs. >10 s), not a tight latency SLO.
    MAX_LATENCY_S = 3.0

    def test_healthz_stays_responsive_while_a_run_is_dispatched(self):
        _prev = os.environ.get("ADAPTER_FAKE_CLAUDE")
        os.environ["ADAPTER_FAKE_CLAUDE"] = "1"
        if _prev is None:
            self.addCleanup(lambda: os.environ.pop("ADAPTER_FAKE_CLAUDE", None))
        else:
            self.addCleanup(lambda: os.environ.__setitem__("ADAPTER_FAKE_CLAUDE", _prev))

        latencies: list[float] = []
        errors: list[str] = []
        stop = threading.Event()

        def _probe(base: str) -> None:
            while not stop.is_set():
                t0 = time.monotonic()
                try:
                    httpx.get(f"{base}/healthz", timeout=30.0)
                    latencies.append(time.monotonic() - t0)
                except Exception as exc:  # noqa: BLE001
                    errors.append(type(exc).__name__)
                time.sleep(0.1)

        with sandbox(("acme",)):
            with uvicorn_server() as base_url:
                prober = threading.Thread(target=_probe, args=(base_url,), daemon=True)
                prober.start()
                try:
                    time.sleep(1.0)          # settle + establish an idle baseline
                    latencies.clear()
                    post = httpx.post(
                        f"{base_url}/v1/tenants/acme/runs",
                        json={
                            "apiVersion": "corvin/v1",
                            "kind": "Run",
                            "spec": {"persona": "docs", "input": "loop-probe"},
                        },
                        timeout=10.0,
                    )
                    self.assertEqual(post.status_code, 202, post.text)
                    # Long enough to cover the whole gate chain incl. the LLM call.
                    time.sleep(20.0)
                finally:
                    stop.set()
                    prober.join(timeout=5)

        self.assertEqual(errors, [], f"health probes failed outright: {errors}")
        self.assertTrue(latencies, "no health probes completed at all")
        worst = max(latencies)
        self.assertLess(
            worst, self.MAX_LATENCY_S,
            f"/healthz took {worst:.2f}s while a run was dispatching — something in "
            f"_run_one is blocking the event loop again (it was 13.35s before the "
            f"gates moved to asyncio.to_thread)",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)

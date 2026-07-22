"""Layer 38 — minimal stdlib HTTP server wrapping RemoteTriggerReceiver.

Standalone alternative to wiring the receiver into the full FastAPI
gateway. Useful for:

  * Running an A2A endpoint on an instance that does not need the full
    gateway (small node, edge box, sidecar).
  * Bidirectional E2E tests (we spawn two instances on different ports
    in :mod:`test_a2a_bidirectional`).
  * Operators who want a thin, audit-only inbound surface.

Routes (native A2A):
  POST /v1/a2a/receive  → :class:`RemoteTriggerReceiver`
  GET  /healthz          → 200 ok + instance_id

Google A2A routes (opt-in via ``google_a2a_adapter`` parameter):
  GET  /.well-known/agent.json  → A2A agent card (URL derived from Host)
  POST /a2a                     → Google A2A JSON-RPC dispatcher
                                   (Authorization: Bearer <api_key>)

Everything else → 404.

Run via ``python -m a2a_http_server --port 8001 --origins-dir <dir>``
(or import :func:`build_server` + call ``serve_forever`` programmatically).

CI lint: MUST NOT import the anthropic SDK.
"""
from __future__ import annotations

import argparse
import http.server
import json
import os
import re
import sys
import threading
from pathlib import Path
from typing import Any

# Allowlist for Host header values in agent card URL construction (MED-02).
# Matches IPv4, IPv6-in-brackets (RFC 2732), hostnames with optional port.
# Port range 1–65535 enforced via regex + post-match numeric check.
# ADR-0099 iter-4 MED-IT4-04: \d{1,5} allowed ports > 65535; fixed below.
_SAFE_HOST_RE = re.compile(
    r"^(?:[a-zA-Z0-9._-]+|\[[0-9a-fA-F:]+\])(?::(\d{1,5}))?$"
)


def _sanitize_host(host: str) -> str:
    """Return host if it passes the safe-host allowlist, else empty string.

    Validates: hostname/IPv4/IPv6 shape AND port number ≤ 65535.
    """
    m = _SAFE_HOST_RE.fullmatch(host)
    if not m:
        return ""
    port_str = m.group(1)
    if port_str is not None and int(port_str) > 65535:
        return ""
    return host

_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import instance_identity  # type: ignore[import-not-found]
from remote_trigger_receiver import RemoteTriggerReceiver, OriginRegistry  # type: ignore[import-not-found]

# GoogleA2AAdapter is optional — imported lazily so the server stays
# functional even if a2a_google_adapter.py is not yet installed.
try:
    from a2a_google_adapter import GoogleA2AAdapter  # type: ignore[import-not-found]
except ImportError:
    GoogleA2AAdapter = None  # type: ignore[assignment,misc]


class _A2AHandler(http.server.BaseHTTPRequestHandler):
    """HTTP handler bound to a RemoteTriggerReceiver (+ optional GoogleA2AAdapter)."""

    # Set by build_server() (class-level — stdlib constructs handlers per request).
    receiver: RemoteTriggerReceiver  # type: ignore[assignment]
    google_adapter: Any = None       # GoogleA2AAdapter | None
    # Body cap: large enough for one max-size attachment payload
    # (1 MiB raw → ~1.33 MiB base64) plus envelope overhead and JSON
    # wrappers. The attachment-layer caps in a2a_attachments.py are the
    # primary defence; this is the HTTP-layer DoS backstop.
    max_body_bytes: int = 4 * 1024 * 1024  # 4 MiB

    # Per-request socket timeout (HIGH-02, ADR-0099).
    # BaseHTTPRequestHandler.timeout is applied to the socket BEFORE
    # rfile.read() — prevents a Slowloris attacker from blocking a thread
    # indefinitely by sending Content-Length: 4MiB then trickling 1 byte/s.
    # 30 s is generous for legitimate requests (envelope + attachments fit
    # in one TCP burst); an attacker who can send 4 MiB in 30 s is already
    # inside the network and rate-limiting applies.
    timeout: int = 30

    # Silence default request logging (operator can re-enable via env).
    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        if os.environ.get("CORVIN_A2A_HTTP_LOG", "") == "1":
            super().log_message(format, *args)

    # ── Routes ────────────────────────────────────────────────────────

    def do_GET(self):  # noqa: N802
        if self.path == "/healthz":
            # Prefer the receiver's cached instance_id (matches what
            # ResponseEnvelope.instance_id will carry) over re-reading
            # the file — two receivers in one process can hold distinct
            # identities, and healthz must report the local server's.
            iid = getattr(self.receiver, "_instance_id", "") or ""
            if not iid:
                try:
                    iid = instance_identity.get_instance_id()
                except Exception:
                    iid = ""
            body = json.dumps({"ok": True, "instance_id": iid}).encode()
            self._respond(200, body)
            return

        # ADR-0199: Lightweight peer-liveness check. Receiver-side ping endpoint.
        # Sender POSTs a signed ping request; receiver verifies and responds with
        # signature. No nonce store — ±30s freshness window suffices.
        if self.path == "/v1/a2a/ping":
            self._handle_ping()
            return

        # ADR-0141 Tier 4 — Audit Chain Transparency. A peer requests the local
        # chain head + event count to detect a fork that silenced its audit
        # module. Optional ?origin_id=<id> selects the recv_key used to HMAC-sign
        # the response so the requester can authenticate it. Advisory only.
        if self.path.split("?", 1)[0] == "/v1/a2a/audit-head":
            origin_id = ""
            if "?" in self.path:
                from urllib.parse import parse_qs
                qs = parse_qs(self.path.split("?", 1)[1])
                origin_id = (qs.get("origin_id", [""])[0] or "")[:128]
            iid = getattr(self.receiver, "_instance_id", "") or ""
            try:
                import a2a_audit_head  # type: ignore
                head = a2a_audit_head.build_audit_head(
                    origin_id=origin_id, instance_id=iid or None)
                self._respond(200, json.dumps(head).encode())
            except Exception:
                self._respond(500, b'{"reason":"audit_head_error"}\n')
            return

        # Google A2A: agent card discovery endpoint
        if self.path == "/.well-known/agent.json" and self.google_adapter:
            host = self.headers.get("Host", "")
            # Sanitize Host header to prevent Host Header Injection in the
            # agent card URL (MED-02, ADR-0099). Reject headers that don't
            # match the safe-host pattern; fall back to empty (relative URL).
            # _sanitize_host also enforces port ≤ 65535 (MED-IT4-04).
            host = _sanitize_host(host)
            # Detect HTTPS via reverse-proxy forwarded header.  The stdlib
            # server itself is plain HTTP; TLS termination is done upstream.
            # Hardcoding "http" caused the agent card to advertise an HTTP
            # URL even for HTTPS-only deployments (MED-02).
            fwd_proto = self.headers.get("X-Forwarded-Proto", "").lower().strip()
            scheme = "https" if fwd_proto == "https" else "http"
            base_url = f"{scheme}://{host}" if host else ""
            card = self.google_adapter.agent_card(base_url)
            body = json.dumps(card).encode()
            self._respond(200, body)
            return

        self._respond(404, b'{"reason":"not_found"}\n')

    def do_POST(self):  # noqa: N802
        # Google A2A: JSON-RPC dispatch
        if self.path == "/a2a" and self.google_adapter is not None:
            self._handle_google_a2a()
            return

        # Native L38
        if self.path != "/v1/a2a/receive":
            self._respond(404, b'{"reason":"not_found"}\n')
            return

        self._handle_native_a2a()

    # ── Route implementations ─────────────────────────────────────────

    def _handle_native_a2a(self) -> None:
        """Handle POST /v1/a2a/receive → RemoteTriggerReceiver."""
        length_hdr = self.headers.get("Content-Length", "0")
        try:
            length = int(length_hdr)
        except ValueError:
            self._respond(400, b'{"reason":"invalid_content_length"}\n')
            return
        if length <= 0 or length > self.max_body_bytes:
            self._respond(413, b'{"reason":"body_too_large_or_empty"}\n')
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            self._respond(400, b'{"reason":"invalid_json"}\n')
            return
        if not isinstance(body, dict):
            self._respond(400, b'{"reason":"envelope_not_object"}\n')
            return

        try:
            resp = self.receiver.receive(body)
        except Exception as exc:
            # The receiver itself never raises, but be defensive.
            if os.environ.get("CORVIN_A2A_HTTP_LOG", "") == "1":
                import traceback
                traceback.print_exc()
            else:
                print(f"a2a_http_server: receiver crashed: "
                      f"{type(exc).__name__}: {exc}", flush=True)
            self._respond(500, b'{"reason":"internal_error"}\n')
            return

        payload = json.dumps(resp.to_dict()).encode()
        self._respond(200, payload)

    def _handle_google_a2a(self) -> None:
        """Handle POST /a2a → GoogleA2AAdapter (JSON-RPC 2.0)."""
        length_hdr = self.headers.get("Content-Length", "0")
        try:
            length = int(length_hdr)
        except ValueError:
            self._respond(400, b'{"reason":"invalid_content_length"}\n')
            return
        if length <= 0 or length > self.max_body_bytes:
            self._respond(413, b'{"reason":"body_too_large_or_empty"}\n')
            return

        raw = self.rfile.read(length)
        try:
            body = json.loads(raw)
        except Exception:
            rpc_err = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            }
            self._respond(200, json.dumps(rpc_err).encode())
            return
        if not isinstance(body, dict):
            rpc_err = {
                "jsonrpc": "2.0", "id": None,
                "error": {"code": -32600, "message": "Invalid Request"},
            }
            self._respond(200, json.dumps(rpc_err).encode())
            return

        # Extract Bearer token from Authorization header
        auth_header = self.headers.get("Authorization", "")
        api_key: str | None = None
        if auth_header.lower().startswith("bearer "):
            api_key = auth_header[len("bearer "):].strip() or None

        try:
            result = self.google_adapter.dispatch(body, api_key)
        except Exception as exc:
            if os.environ.get("CORVIN_A2A_HTTP_LOG", "") == "1":
                import traceback
                traceback.print_exc()
            else:
                print(f"a2a_http_server: google adapter crashed: "
                      f"{type(exc).__name__}: {exc}", flush=True)
            rpc_err = {
                "jsonrpc": "2.0", "id": body.get("id"),
                "error": {"code": -32603, "message": "Internal error"},
            }
            self._respond(200, json.dumps(rpc_err).encode())
            return

        payload = json.dumps(result).encode()
        self._respond(200, payload)

    # ── Route implementations ─────────────────────────────────────────

    def _handle_ping(self) -> None:
        """ADR-0199: Handle GET /v1/a2a/ping (receiver-side liveness check).

        Expects JSON body: {ping_id, issued_at, origin_id, signature}
        Verifies signature with origin's hmac_key; responds with signed
        {ok, instance_id, protocol_version, server_time, signature}.
        """
        import hashlib
        import hmac as _hmac
        import time as _time_module

        # Read request body
        length_hdr = self.headers.get("Content-Length", "0")
        try:
            length = int(length_hdr)
        except ValueError:
            self._respond(400, b'{"reason":"invalid_content_length"}\n')
            return
        if length <= 0 or length > self.max_body_bytes:
            self._respond(413, b'{"reason":"body_too_large_or_empty"}\n')
            return

        raw = self.rfile.read(length)
        try:
            req = json.loads(raw)
        except Exception:
            self._respond(400, b'{"reason":"invalid_json"}\n')
            return
        if not isinstance(req, dict):
            self._respond(400, b'{"reason":"envelope_not_object"}\n')
            return

        # Extract fields
        ping_id = req.get("ping_id")
        issued_at = req.get("issued_at")
        origin_id = req.get("origin_id")
        signature = req.get("signature")

        if not all((ping_id, issued_at, origin_id, signature)):
            self._respond(400, b'{"reason":"missing_fields"}\n')
            return

        # Load origin config (same as RemoteTriggerReceiver does)
        try:
            registry = OriginRegistry(origins_dir=None)  # Uses default
            origin_cfg = registry.load(origin_id)
        except Exception:
            # OriginError: invalid_origin_id, unknown_origin, file permissions, etc
            self._respond(403, b'{"reason":"unknown_origin"}\n')
            return

        # Verify freshness (±30s window)
        now = int(_time_module.time())
        if abs(now - issued_at) > 30:
            self._respond(400, b'{"reason":"stale_ping"}\n')
            return

        # Verify HMAC signature
        ping_request_canonical = json.dumps(
            {"ping_id": ping_id, "issued_at": issued_at, "origin_id": origin_id},
            separators=(",", ":"),
            sort_keys=True,
        )
        expected_sig = _hmac.new(
            bytes.fromhex(origin_cfg["hmac_key"]),
            ping_request_canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        if not _hmac.compare_digest(signature, expected_sig):
            self._respond(403, b'{"reason":"invalid_signature"}\n')
            return

        # Record heartbeat (if supported)
        try:
            from a2a_friendship import record_endpoint_heartbeat  # type: ignore[import-not-found]
            record_endpoint_heartbeat(origin_id)
        except Exception:
            pass  # Heartbeat is optional; proceed even if recording fails

        # Build signed response
        iid = getattr(self.receiver, "_instance_id", "") or ""
        if not iid:
            try:
                iid = instance_identity.get_instance_id()
            except Exception:
                iid = ""

        response = {
            "ok": True,
            "instance_id": iid,
            "protocol_version": "1.0",
            "server_time": now,
        }

        # Sign response with recv_key
        response_canonical = json.dumps(response, separators=(",", ":"), sort_keys=True)
        response_sig = _hmac.new(
            bytes.fromhex(origin_cfg["recv_key"]),
            response_canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response["signature"] = response_sig

        body = json.dumps(response).encode()
        self._respond(200, body)

    # ── Helpers ───────────────────────────────────────────────────────

    def _respond(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def build_server(
    *,
    host: str = "127.0.0.1",
    port: int = 0,
    origins_dir: Path | None = None,
    engine_factory: Any = None,
    force_m1_only: bool | None = None,
    instance_id: str | None = None,
    nonce_store: Any = None,
    google_a2a_enabled: bool = False,
    agent_card_overrides: dict | None = None,
    forge_se: Any = None,
) -> http.server.ThreadingHTTPServer:
    """Build (but do not start) a ThreadingHTTPServer with a bound receiver.

    Port 0 → OS picks an ephemeral port; read it back via
    ``server.server_address[1]``.

    google_a2a_enabled
        When True, activates ``POST /a2a`` and
        ``GET /.well-known/agent.json`` routes by attaching a
        :class:`GoogleA2AAdapter` to the handler class.
        Requires ``a2a_google_adapter.py`` to be importable.

    agent_card_overrides
        Dict merged over the default agent card (name, description,
        skills, etc.) when ``google_a2a_enabled=True``.

    forge_se
        Optional forge security_events module override for test isolation.
        When provided, the receiver (and sender) use this instead of the
        module-level _forge_se, preventing cross-test mock contamination.
    """
    receiver = RemoteTriggerReceiver(
        origins_dir=origins_dir,
        nonce_store=nonce_store,
        engine_factory=engine_factory,
        force_m1_only=force_m1_only,
        instance_id=instance_id,
        forge_se=forge_se,
    )

    google_adapter: Any = None
    if google_a2a_enabled:
        if GoogleA2AAdapter is None:
            raise ImportError(
                "google_a2a_enabled=True but a2a_google_adapter.py is not importable"
            )
        eff_origins_dir = (
            Path(os.environ.get("REMOTE_ORIGINS_DIR", ""))
            if os.environ.get("REMOTE_ORIGINS_DIR")
            else origins_dir
            or (Path(__file__).resolve().parents[2] / "cowork" / "remote_origins")
        )
        google_adapter = GoogleA2AAdapter(
            receiver=receiver,
            origins_dir=eff_origins_dir,
            instance_id=instance_id,
            agent_card_overrides=agent_card_overrides,
            forge_se=forge_se,
        )

    class _Handler(_A2AHandler):
        pass

    _Handler.receiver = receiver
    _Handler.google_adapter = google_adapter
    return http.server.ThreadingHTTPServer((host, port), _Handler)


def serve_in_thread(server: http.server.ThreadingHTTPServer) -> threading.Thread:
    """Start ``server.serve_forever()`` in a daemon thread; return the
    Thread handle. Caller stops via ``server.shutdown()``."""
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return thread


def _warn_tls_if_needed() -> None:
    """ADR-0077 C-4: emit a WARNING if TLS is not configured for the A2A endpoint.

    Reads ``CORVIN_A2A_PUBLIC_URL``; warns if absent or non-HTTPS.
    Logs to stderr so it is visible in bridge.sh output.
    """
    import sys
    public_url = os.environ.get("CORVIN_A2A_PUBLIC_URL", "")
    if not public_url:
        print(
            "[a2a_http_server] WARNING: CORVIN_A2A_PUBLIC_URL is not set. "
            "A2A HTTP server started without confirmed TLS termination — "
            "set CORVIN_A2A_PUBLIC_URL=https://… and use a TLS-terminating "
            "reverse proxy (ADR-0077 C-4, GDPR Art. 32).",
            file=sys.stderr, flush=True,
        )
    elif not public_url.startswith("https://"):
        print(
            f"[a2a_http_server] WARNING: CORVIN_A2A_PUBLIC_URL={public_url!r} "
            "does not use HTTPS. Instruction content is transmitted in the clear. "
            "Use a TLS-terminating reverse proxy (ADR-0077 C-4, GDPR Art. 32).",
            file=sys.stderr, flush=True,
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="a2a_http_server", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0,
                        help="0 = OS-picked ephemeral port")
    parser.add_argument("--origins-dir", default=None,
                        help="path to remote_origins/ (else default)")
    parser.add_argument("--m1-only", action="store_true",
                        help="force M1-only mode (no worker spawn)")
    args = parser.parse_args(argv)

    _warn_tls_if_needed()

    server = build_server(
        host=args.host,
        port=args.port,
        origins_dir=Path(args.origins_dir) if args.origins_dir else None,
        force_m1_only=args.m1_only,
    )
    host, port = server.server_address[:2]
    print(f"listening on http://{host}:{port}")
    print(f"local instance_id: {instance_identity.get_instance_id()}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

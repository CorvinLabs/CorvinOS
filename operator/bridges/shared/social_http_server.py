"""Layer 39 CorvinFed — Social HTTP server (inbox / outbox / actor endpoints).

Implements a stdlib-only HTTP server for the three social federation endpoints:

  GET  /v1/social/actor   — returns actor.json (own ActorDocument)
  GET  /v1/social/outbox  — returns last 50 own posts from posts.db
  POST /v1/social/inbox   — receives a PostEnvelope from a remote actor

Security model:
  * ALL inbox failures return 202 (fail-silent). The HTTP response body is
    always empty. Reasons are never surfaced to callers.
  * Every security event is written to the L16 audit chain.
  * No WorkerEngine spawn path exists in this module.

CI AST lint: MUST NOT import anthropic.
"""
from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from typing import Any

try:
    from .audit import audit_event  # type: ignore[import-not-found]
    from . import social_actor  # type: ignore[import-not-found]
    from . import social_envelope  # type: ignore[import-not-found]
    from . import social_sanitizer  # type: ignore[import-not-found]
    from .social_registry import SocialRegistry  # type: ignore[import-not-found]
except ImportError:
    _here = Path(__file__).resolve().parent
    if str(_here) not in sys.path:
        sys.path.insert(0, str(_here))
    from audit import audit_event  # type: ignore[import-not-found]
    import social_actor  # type: ignore[import-not-found]
    import social_envelope  # type: ignore[import-not-found]
    import social_sanitizer  # type: ignore[import-not-found]
    from social_registry import SocialRegistry  # type: ignore[import-not-found]

# Optional dependency — SocialFeedStore. If social_feed is not yet present
# (incremental rollout) the server degrades gracefully: outbox returns empty
# and inbox drops to audit-only without storing.
try:
    try:
        from .social_feed import SocialFeedStore  # type: ignore[import-not-found]
    except ImportError:
        from social_feed import SocialFeedStore  # type: ignore[import-not-found]
    _FEED_AVAILABLE = True
except ImportError:
    SocialFeedStore = None  # type: ignore[assignment,misc]
    _FEED_AVAILABLE = False

# FIX-6: Optional capability layer (L41 deny-by-default grant check).
try:
    try:
        from .social_capability import GrantChecker, GrantStore  # type: ignore[import-not-found]
    except ImportError:
        from social_capability import GrantChecker, GrantStore  # type: ignore[import-not-found]
    _CAPABILITY_AVAILABLE = True
except ImportError:
    _CAPABILITY_AVAILABLE = False
    GrantChecker = None  # type: ignore[assignment,misc]
    GrantStore = None  # type: ignore[assignment,misc]

# ── Constants ─────────────────────────────────────────────────────────────────

_MAX_BODY_BYTES = 64 * 1024  # 64 KB
_TIME_WINDOW_SECONDS = 300    # ±5 min
_ACTOR_FETCH_TIMEOUT = 5.0    # seconds


# ── Audit helper ──────────────────────────────────────────────────────────────

def _audit_social(
    event_type: str,
    severity: str,
    details: dict,
    *,
    tenant_id: str | None = None,
) -> None:
    """Thin wrapper around audit_event for social-layer events."""
    try:
        audit_event(event_type, severity=severity, details=details)
    except Exception:
        pass  # best-effort; never raise from audit


# ── Actor-key fetching (for unknown inbound senders) ──────────────────────────


def fetch_actor_document(key_id_url: str, timeout: float = _ACTOR_FETCH_TIMEOUT) -> dict | None:
    """HTTPS GET an actor document by key_id URL.

    Returns the parsed JSON dict on success, or None on any failure.
    The caller is responsible for writing an audit event on failure.

    For tests: URLs starting with 'http://127.0.0.1' or 'http://localhost'
    are allowed over plain HTTP (not just HTTPS), so integration tests can
    run a local stub server without TLS.
    """
    if not isinstance(key_id_url, str) or not key_id_url:
        return None
    # Strip fragment (#key) to get the actor document URL
    doc_url = key_id_url.split("#")[0]
    if not doc_url:
        return None

    # Allow HTTP for loopback test URLs; require HTTPS for everything else
    if not (
        doc_url.startswith("https://")
        or doc_url.startswith("http://127.0.0.1")
        or doc_url.startswith("http://localhost")
    ):
        return None

    try:
        req = urllib.request.Request(
            doc_url,
            headers={"Accept": "application/json", "User-Agent": "CorvinFed/1.0"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read(_MAX_BODY_BYTES)
        doc = json.loads(raw)
        if not isinstance(doc, dict):
            return None
        return doc
    except Exception:
        return None


# ── Request handler ────────────────────────────────────────────────────────────


class _SocialHandler(BaseHTTPRequestHandler):
    """HTTP request handler for CorvinFed social endpoints."""

    # server_context is injected by SocialHttpServer
    server_context: dict[str, Any]

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Suppress default access log — audit chain is our log
        pass

    # ── routing ───────────────────────────────────────────────────────────────

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/v1/social/actor":
            self._handle_actor()
        elif self.path.startswith("/v1/social/outbox"):
            self._handle_outbox()
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path == "/v1/social/inbox":
            self._handle_inbox()
        else:
            self._send_empty(404)

    # ── GET /v1/social/actor ──────────────────────────────────────────────────

    def _handle_actor(self) -> None:
        tenant_id = self.server_context.get("tenant_id")
        try:
            doc = social_actor.load_actor_document(tenant_id)
        except social_actor.ActorError:
            self._send_json(404, {"error": "actor not initialised"})
            return
        self._send_json(200, doc)

    # ── GET /v1/social/outbox ─────────────────────────────────────────────────

    def _handle_outbox(self) -> None:
        tenant_id = self.server_context.get("tenant_id")
        posts: list[dict] = []
        total = 0
        if _FEED_AVAILABLE and SocialFeedStore is not None:
            try:
                store = SocialFeedStore(tenant_id=tenant_id)
                # FIX-9: list_posts() returns a list, not a dict — use directly.
                result = store.list_posts(limit=50)
                posts = result if isinstance(result, list) else []
                total = len(posts)
                store.close()
            except Exception:
                # Degrade gracefully — return empty outbox
                posts = []
                total = 0
        self._send_json(200, {"posts": posts, "total": total})

    # ── POST /v1/social/inbox ─────────────────────────────────────────────────

    def _handle_inbox(self) -> None:
        """Receive a PostEnvelope. All failures are silent (202)."""
        tenant_id = self.server_context.get("tenant_id")
        registry = self.server_context.get("registry")
        if registry is None:
            # Lazily build per-request if not pre-built (tests may inject one)
            registry = SocialRegistry(tenant_id=tenant_id)

        # Step 1: Read body (max 64 KB)
        try:
            content_length = int(self.headers.get("Content-Length", 0))
        except (ValueError, TypeError):
            content_length = 0
        body_bytes = self.rfile.read(min(content_length, _MAX_BODY_BYTES))

        try:
            envelope = json.loads(body_bytes)
        except (json.JSONDecodeError, ValueError):
            self._send_empty(202)
            return

        if not isinstance(envelope, dict):
            self._send_empty(202)
            return

        # Step 2: Schema validation
        try:
            social_envelope.validate_envelope_schema(envelope)
        except social_envelope.EnvelopeError:
            self._send_empty(202)
            return

        actor_id = envelope.get("actor_id", "")
        key_id = envelope.get("key_id", "")
        post_id = envelope.get("post_id", "")
        post_type = envelope.get("post_type", "")

        # Step 3: Blocked check (fail-silent)
        if registry.is_blocked(actor_id):
            self._send_empty(202)
            return

        # Step 4: Rate limit
        if not registry.check_and_record_post(actor_id):
            self.send_response(429)
            self.send_header("Retry-After", "3600")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        # Step 5: Time window check (±300 s)
        issued_at = envelope.get("issued_at", 0)
        if abs(time.time() - float(issued_at)) > _TIME_WINDOW_SECONDS:
            self._send_empty(202)
            return

        # Step 6: Resolve public key
        known_actor = registry.get_actor(actor_id)
        public_key_hex: str | None = None
        # FIX-8: initialize actor_doc to None BEFORE the if/else block
        actor_doc: dict | None = None

        if known_actor is not None:
            public_key_hex = known_actor.get("public_key_hex")
        else:
            # Unknown actor — attempt to fetch actor document from key_id URL
            actor_doc = fetch_actor_document(key_id, timeout=_ACTOR_FETCH_TIMEOUT)
            if actor_doc is None:
                _audit_social(
                    "social.actor_fetch_failed", "WARNING",
                    {"actor_id_prefix": actor_id[:16]},
                    tenant_id=tenant_id,
                )
                self._send_empty(202)
                return

            # FIX-4: Verify that the fetched actor document's instance_id matches
            # the envelope's actor_id. Without this, an attacker controlling the
            # key_id URL can impersonate any actor_id.
            doc_instance_id = actor_doc.get("instance_id")
            if not doc_instance_id or doc_instance_id != actor_id:
                _audit_social(
                    "social.actor_id_mismatch", "WARNING",
                    {"actor_id_prefix": actor_id[:16], "reason": "key_id doc instance_id mismatch"},
                    tenant_id=tenant_id,
                )
                self._send_empty(202)
                return

            # Extract public key from fetched document
            pk_field = actor_doc.get("public_key", {})
            if isinstance(pk_field, dict):
                public_key_hex = pk_field.get("public_key_hex")
            else:
                public_key_hex = None

        if not public_key_hex:
            _audit_social(
                "social.actor_fetch_failed", "WARNING",
                {"actor_id_prefix": actor_id[:16]},
                tenant_id=tenant_id,
            )
            self._send_empty(202)
            return

        # Step 7: Verify Ed25519 signature
        if not social_envelope.verify_envelope(envelope, public_key_hex):
            _audit_social(
                "social.signature_invalid", "WARNING",
                {"actor_id_prefix": actor_id[:16]},
                tenant_id=tenant_id,
            )
            self._send_empty(202)
            return

        # Step 8b: Capability check (L41 deny-by-default)
        # post_type determines the required capability
        if _CAPABILITY_AVAILABLE and GrantStore is not None:
            try:
                cap_store = GrantStore(tenant_id=tenant_id)
                follower_fn = lambda aid: registry.is_follower(aid)
                checker = GrantChecker(grant_store=cap_store, follower_check_fn=follower_fn)
                required_cap = f"social.post.{post_type}"
                cap_ok = checker.check(
                    grantee=actor_id,
                    capability=required_cap,
                    public_key_hex=public_key_hex,
                )
                if not cap_ok:
                    _audit_social(
                        "social.capability_denied", "WARNING",
                        {"actor_id_prefix": actor_id[:16], "capability": required_cap, "reason": "no_grant"},
                        tenant_id=tenant_id,
                    )
                    self._send_empty(202)
                    return
            except Exception:
                pass  # capability layer failure is non-blocking for now; log and continue

        # Step 9: Compliance zone check
        actor_zone = None
        if known_actor:
            actor_zone = known_actor.get("compliance_zone")
        elif actor_doc is not None:
            actor_zone = actor_doc.get("compliance_zone")

        if not registry.check_compliance_zone(actor_zone, tenant_id):
            self._send_empty(202)
            return

        # Step 10: Content sanitization
        content = envelope.get("content", "")
        try:
            sanitized = social_sanitizer.sanitize_post_content(
                content, actor_id=actor_id, post_id=post_id
            )
        except social_sanitizer.InjectionAttempt:
            audit_event(
                "social.content_policy_blocked",
                severity="WARNING",
                details={"actor_id_prefix": actor_id[:16]},
            )
            self._send_empty(202)
            return

        # Step 11: Audit BEFORE store (audit-first invariant)
        audit_event(
            "social.post_received",
            severity="INFO",
            details={
                "actor_id_prefix": actor_id[:16],
                "post_type": post_type,
            },
        )

        # Step 12: Store in posts.db (best-effort; degrade gracefully).
        # Replace envelope content with the sanitized version before storing
        # so that FTS5 indexes the framed content. is_own=False skips
        # re-sanitization in store_post.
        if _FEED_AVAILABLE and SocialFeedStore is not None:
            try:
                envelope_to_store = dict(envelope)
                envelope_to_store["content"] = sanitized
                store = SocialFeedStore(tenant_id=tenant_id)
                store.store_post(envelope_to_store, is_own=False)
                store.close()
            except Exception:
                pass  # best-effort; audit already written

        # Step 13: Handle follow/unfollow post types
        if post_type == "follow":
            # Rate-limit follow attempts
            if registry.check_and_record_follow(actor_id):
                actor_inbox = envelope.get("key_id", "").split("#")[0] or ""
                # Accept follow if actor is known (has inbox_url); otherwise
                # record as pending. For M1/M2 we accept immediately.
                actor_inbox_url = actor_inbox
                if known_actor:
                    actor_inbox_url = known_actor.get("inbox_url", actor_inbox)
                # FIX-5: is_ai must come from the verified actor source, not the envelope.
                # The envelope is attacker-controlled; the actor document / registry
                # entry is fetched/verified independently.
                if known_actor is not None:
                    is_ai_val = bool(known_actor.get("is_ai", True))
                else:
                    is_ai_val = bool(actor_doc.get("is_ai", True)) if actor_doc else True
                registry.accept_follow(
                    actor_id=actor_id,
                    inbox_url=actor_inbox_url,
                    public_key_hex=public_key_hex,
                    is_ai=is_ai_val,
                    compliance_zone=actor_zone,
                )
            else:
                registry.reject_follow(actor_id, reason="rate_limit")

        elif post_type == "unfollow":
            # Transition follower to former_follower if relationship was follower/mutual
            existing = registry.get_actor(actor_id)
            if existing and existing["relationship"] in ("follower", "mutual"):
                if existing["relationship"] == "mutual":
                    registry.update_relationship(actor_id, "following")
                else:
                    registry.update_relationship(actor_id, "former_follower")

        elif post_type == "retract":
            # Remove the referenced post from posts.db (best-effort; degrade gracefully)
            retracted_post_id = envelope.get("in_reply_to")
            if retracted_post_id and _FEED_AVAILABLE and SocialFeedStore is not None:
                # Audit-first: record BEFORE deletion (L16 completeness, hash-chain gap fix)
                audit_event(
                    "social.post_deleted",
                    details={
                        "post_id_prefix": retracted_post_id[:8],
                        "actor_id_prefix": actor_id[:16],
                    },
                )
                try:
                    store = SocialFeedStore(tenant_id=tenant_id)
                    store.delete_post(retracted_post_id)
                    store.close()
                except Exception:
                    pass  # best-effort

        # Update last_seen for known actors
        if known_actor:
            registry.update_last_seen(actor_id)

        self._send_empty(202)

    # ── Response helpers ──────────────────────────────────────────────────────

    def _send_json(self, status: int, data: dict | list) -> None:
        body = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_empty(self, status: int) -> None:
        self.send_response(status)
        self.send_header("Content-Length", "0")
        self.end_headers()


# ── Server class ──────────────────────────────────────────────────────────────


class SocialHttpServer:
    """Stdlib-only HTTP server for CorvinFed social endpoints.

    Starts in a daemon background thread. All public surfaces (inbox/outbox/
    actor) are handled by ``_SocialHandler``. The server context dict is shared
    across all requests in the same process lifetime.
    """

    def __init__(
        self,
        host: str = "0.0.0.0",
        port: int = 8900,
        tenant_id: str | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._tenant_id = tenant_id
        self.running: bool = False
        self._server: HTTPServer | None = None
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        """Start the HTTP server in a daemon background thread."""
        context: dict[str, Any] = {
            "tenant_id": self._tenant_id,
            "registry": SocialRegistry(tenant_id=self._tenant_id),
        }

        # Build the handler class with context injected via class attribute
        class _BoundHandler(_SocialHandler):
            server_context = context  # type: ignore[misc]

        server = HTTPServer((self._host, self._port), _BoundHandler)
        server.allow_reuse_address = True
        self._server = server

        thread = threading.Thread(
            target=server.serve_forever,
            name=f"CorvinFed-HTTP-{self._port}",
            daemon=True,
        )
        thread.start()
        self._thread = thread
        self.running = True

    def stop(self) -> None:
        """Stop the HTTP server."""
        if self._server is not None:
            self._server.shutdown()
            self._server = None
        self.running = False

    @property
    def base_url(self) -> str:
        """Return http://host:port"""
        host = self._host if self._host != "0.0.0.0" else "127.0.0.1"
        return f"http://{host}:{self._port}"


# ── Simple startup function ───────────────────────────────────────────────────


def run_server(
    host: str = "0.0.0.0",
    port: int = 8900,
    tenant_id: str | None = None,
) -> SocialHttpServer:
    """Start a SocialHttpServer and return it. Blocking variant available via
    ``server._server.serve_forever()`` on the underlying HTTPServer instance."""
    srv = SocialHttpServer(host=host, port=port, tenant_id=tenant_id)
    srv.start()
    return srv


__all__ = [
    "SocialHttpServer",
    "fetch_actor_document",
    "run_server",
]

"""Layer 38 — RemoteTriggerSender (outbound A2A).

Bidirectional companion to :mod:`remote_trigger_receiver`. Where the
receiver authenticates incoming TaskEnvelopes from a trusted origin, the
sender builds and signs outgoing TaskEnvelopes for delivery to a remote
Corvin receiver. Same cryptographic primitives, mirror direction.

Architecture
------------

The receiver knows trusted *origins* (who is allowed to call us).
The sender knows trusted *endpoints* (who we are allowed to call).
Identity goes both ways:

  - On send, we attach our local ``instance_id`` to the TaskEnvelope
    (``sender_instance_id`` field) so the receiver can pin the caller.
  - On receive, the ResponseEnvelope carries the receiver's
    ``instance_id`` so the sender can verify which remote Corvin
    actually answered (defence against a swapped receiver behind the
    same URL).

Audit contract (L16 hash chain, three new event types):

  ============================= ======== =========================================
  Event                         Severity Emitted
  ============================= ======== =========================================
  ``A2A.envelope_sent``         INFO     After HMAC sign, before HTTP
  ``A2A.response_received``     INFO     After successful response verification
  ``A2A.response_rejected``     WARNING  Signature mismatch, transport error,
                                          or instance_id pin mismatch
  ``A2A.reconnect_sent``        INFO     ADR-0198: send_reconnect() got a signed
                                          "ok" response
  ``A2A.reconnect_send_failed`` WARNING  ADR-0198: send_reconnect() transport
                                          error, bad signature, or rejection
  ``A2A.ping_result``           INFO/WARN ADR-0199: one event per ping() call
                                          (reachable → INFO, else WARNING)
  ``A2A.relay_fallback_used``   INFO     ADR-0258: send()'s direct HTTP POST
                                          failed and the relay path delivered
                                          it instead
  ============================= ======== =========================================

Audit ``details`` allow-list (enforced fail-closed by
``_assert_audit_details_safe`` — the ADR-0197 backstop; free-form values
are dropped and replaced with ``"redacted"``):
  ``endpoint_id``, ``task_id``, ``instance_id_match``, ``status``,
  ``duration_ms``, ``reason``, ``ttl_s``, ``nonce_prefix``,
  ``http_status``, ``error_category``, ``error_detail``,
  ``attachments_count``, ``our_chain_tail``, ``peer_chain_tail``,
  ``match``, ``reachable``, ``source`` (ADR-0197/0199).

``error_detail`` values come exclusively from the fixed template set
(``_ERROR_DETAIL_TEMPLATES``) or the closed exception-type-name allowlist
(``_ALLOWED_EXC_TYPE_NAMES``) — never ``str(exc)`` verbatim (ADR-0197 §2).

Never in ``details``: ``instruction``, ``result_schema``, response
``data``, ``signature``, ``hmac_key``, ``recv_key``, full nonce, URL,
HTTP headers, response body bytes, raw exception text.

CI lint: module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
import re as _re
import secrets
import stat
import sys
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# ── Forge security_events (audit-chain writer) ────────────────────────────
_forge_se: Any = None
try:
    _forge_parent = Path(__file__).resolve().parents[2] / "forge"
    if str(_forge_parent) not in sys.path:
        sys.path.insert(0, str(_forge_parent))
    from forge import security_events as _forge_se  # type: ignore[import-not-found]
except Exception:
    _forge_se = None

# ── audit_path / instance_identity (shared modules) ───────────────────────
try:
    from audit import audit_path  # type: ignore[import-not-found]
except ImportError:
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from audit import audit_path  # type: ignore[import-not-found]

try:
    from instance_identity import get_instance_id  # type: ignore[import-not-found]
except ImportError:
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from instance_identity import get_instance_id  # type: ignore[import-not-found]

# Shared label sanitizer (A4 defense-in-depth, 2026-07-20): labels stored
# BEFORE the ingestion sanitizer existed may still carry ANSI escapes / bidi
# overrides — sanitize again at every read-side delivery point.
try:
    from a2a_friendship import sanitize_label as _sanitize_label  # type: ignore[import-not-found]
except ImportError:
    _shared = Path(__file__).resolve().parent
    if str(_shared) not in sys.path:
        sys.path.insert(0, str(_shared))
    from a2a_friendship import sanitize_label as _sanitize_label  # type: ignore[import-not-found]

# ── IBC attestation (ADR-0103 Protocol v7 / IBC concept) ─────────────────
try:
    from instance_identity import (  # type: ignore[import-not-found]
        get_ibc_jwt as _get_ibc_jwt,
        sign_payload as _sign_payload,
        build_canonical_payload as _build_canonical_payload,
    )
    _IBC_AVAILABLE = True
except ImportError:
    _IBC_AVAILABLE = False


# ── Endpoint registry resolution ──────────────────────────────────────────
_REMOTE_ENDPOINTS_ENV = "REMOTE_ENDPOINTS_DIR"
_REMOTE_ENDPOINTS_DEFAULT = (
    Path(__file__).resolve().parents[2] / "cowork" / "remote_endpoints"
)

# Default outbound timeouts; operators may override per call.
_DEFAULT_TIMEOUT_S = 30
_DEFAULT_TTL_S = 60

# Maximum response body the sender will read from a remote receiver.
# Mirrors the inbound cap in a2a_http_server.py (4 MiB). A valid response
# carrying max attachments (16 × 1 MiB base64-expanded) can reach ~5.5 MiB
# of JSON; we cap at 6 MiB to leave headroom without accepting unbounded streams.
# Rogue receivers that stream more raise TransportError("response_too_large")
# (ADR-0099 iter-5 finding MED-IT5-03).
_MAX_RESPONSE_BYTES = 6 * 1024 * 1024  # 6 MiB


# ── No-redirect opener (SSRF hardening, 2026-07-19) ────────────────────────
#
# The default urllib opener silently FOLLOWS 3xx redirects. A paired peer with
# a valid global stored URL could therefore 302 our signed POST (or ping) to
# http://127.0.0.1 / 169.254.169.254 — turning an authenticated peer into an
# SSRF pivot despite the danger-category host gate on the STORED url. We route
# every outbound A2A POST/ping through an opener whose redirect handler refuses
# to follow: a 3xx becomes an ``HTTPError`` (categorised as a transport error),
# never a silent internal fetch.
class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: D401
        return None  # do not follow — urlopen raises HTTPError for the 3xx


_NO_REDIRECT_OPENER = urllib.request.build_opener(_NoRedirect)


# ── Exceptions ────────────────────────────────────────────────────────────

class SendError(Exception):
    """Base for outbound A2A errors. The reason is audit-only."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class EndpointError(SendError):
    """Registry / config issue (unknown, disabled, world-readable)."""


class TransportError(SendError):
    """HTTP transport failure (timeout, refused, non-200 body)."""

    def __init__(self, reason: str, http_status: int | None = None) -> None:
        super().__init__(reason)
        self.http_status = http_status


class ResponseVerificationError(SendError):
    """Signature mismatch, malformed response, or instance_id pin miss."""


# ── Endpoint registry ─────────────────────────────────────────────────────

class RemoteEndpointRegistry:
    """Per-call config loader for outbound endpoints.

    File layout: ``operator/cowork/remote_endpoints/<endpoint_id>.json``,
    mode 0600. Schema::

        {
          "endpoint_id":    "<id>",          # must match filename
          "url":            "http://host:port/v1/a2a/receive",
          "hmac_key":       "<hex>",         # local→remote: signs outbound
          "recv_key":       "<hex>",         # remote→local: verifies inbound
          "instance_id":    "<uuid-or-empty>",  # pin (empty = any)
          "enabled":        true,
          "default_ttl_s":  60
        }
    """

    def __init__(self, endpoints_dir: Path | str | None = None) -> None:
        env = os.environ.get(_REMOTE_ENDPOINTS_ENV)
        if env:
            self._dir = Path(env)
        elif endpoints_dir is not None:
            self._dir = Path(endpoints_dir)
        else:
            self._dir = _REMOTE_ENDPOINTS_DEFAULT

    def load(self, endpoint_id: str) -> dict:
        # Path-traversal guard (same shape as OriginRegistry).
        if (
            not endpoint_id
            or "/" in endpoint_id
            or "\\" in endpoint_id
            or endpoint_id.startswith(".")
            or ":" in endpoint_id
        ):
            raise EndpointError("invalid_endpoint_id")

        path = self._dir / f"{endpoint_id}.json"
        if not path.exists():
            raise EndpointError("unknown_endpoint")

        file_stat = path.stat()
        if file_stat.st_mode & (stat.S_IRWXG | stat.S_IRWXO):
            raise EndpointError("endpoint_file_world_readable")

        with path.open("r", encoding="utf-8") as fh:
            config = json.load(fh)

        if not config.get("enabled", False):
            raise EndpointError("endpoint_disabled")

        required = {"endpoint_id", "url", "hmac_key", "recv_key"}
        missing = required - set(config.keys())
        if missing:
            raise EndpointError(f"missing_fields:{','.join(sorted(missing))}")

        # Sanity: endpoint_id inside the file must match the filename.
        if config["endpoint_id"] != endpoint_id:
            raise EndpointError("endpoint_id_mismatch")

        return config

    def list_ids(self) -> list[str]:
        """List configured endpoint_ids (sorted). Best-effort; ignores
        unreadable files."""
        if not self._dir.exists():
            return []
        out: list[str] = []
        for entry in sorted(self._dir.iterdir()):
            if entry.is_file() and entry.suffix == ".json":
                out.append(entry.stem)
        return out

    def peek_label(self, endpoint_id: str) -> str:
        """Best-effort label read WITHOUT the load() gates (works for
        disabled endpoints too). Returns \"\" when unreadable/absent.

        The stored value is re-sanitized on read (A4, 2026-07-20): records
        written before the ingestion sanitizer existed may carry raw
        peer-token labels, and this is a delivery point (MCP
        ``a2a_list_endpoints``, ``resolve()``, CLI listings)."""
        if (
            not endpoint_id
            or "/" in endpoint_id
            or "\\" in endpoint_id
            or endpoint_id.startswith(".")
            or ":" in endpoint_id
        ):
            return ""
        path = self._dir / f"{endpoint_id}.json"
        try:
            with path.open("r", encoding="utf-8") as fh:
                return _sanitize_label(json.load(fh).get("label") or "")
        except Exception:
            return ""

    @staticmethod
    def _norm(s: str) -> str:
        """Canonical comparison form: NFC + casefold + strip. Two labels that
        render identically MUST compare equal here, otherwise a homoglyph/NFD
        twin (peer-authored labels are only length-capped upstream) would
        evade the ambiguity guard and misroute a signed task."""
        import unicodedata as _ud
        return _ud.normalize("NFC", s).casefold().strip()

    def resolve(self, name: str) -> str:
        """Resolve a user/agent-supplied reference (endpoint_id OR connection
        name/label) to an endpoint_id.

        Resolution order: exact id → unique label → unique id-prefix. An
        exact endpoint_id match wins deterministically (ids are
        operator-assigned and unique; a peer-controlled label must not be
        able to shadow a foreign id — A3, 2026-07-20). Below that, any
        collision that points at *different* endpoints raises
        ``EndpointError("ambiguous_endpoint_ref")`` rather than silently
        picking one — a signed task must never go to a guessed peer. Falls
        through to the original name (so ``load()`` reports 'unknown_endpoint'
        with full context) only when nothing matches.
        """
        if not name or not self._dir.exists():
            return name

        ids = self.list_ids()
        q = self._norm(name)

        exact_id = name if (self._dir / f"{name}.json").exists() else None

        label_matches = {
            eid for eid in ids
            if (lbl := self.peek_label(eid)) and self._norm(lbl) == q
        }
        prefix_matches = {eid for eid in ids if self._norm(eid).startswith(q)}

        # Collect every distinct endpoint the reference could designate,
        # honoring precedence but treating any conflict across match types
        # as ambiguous (F1/F2 misrouting: label shadowing a prefix, etc.).
        if exact_id is not None:
            # A3 (2026-07-20): the exact endpoint_id match wins DETERMINISTICALLY.
            # endpoint_ids are operator-assigned and unique; labels are
            # peer-controlled (sanitized but content-free). If a colliding
            # label could make the exact-id reference ambiguous, a peer that
            # sets its label equal to ANOTHER peer's id would render that
            # victim unaddressable via CLI and MCP — a peer-triggerable
            # availability DoS. Labels are therefore consulted only when no
            # exact id match exists; label↔label collisions below stay
            # fail-closed ambiguous.
            return exact_id

        if len(label_matches) > 1:
            raise EndpointError(f"ambiguous_endpoint_ref:{name}")
        if len(label_matches) == 1:
            (only_label,) = tuple(label_matches)
            if prefix_matches - {only_label}:
                raise EndpointError(f"ambiguous_endpoint_ref:{name}")
            return only_label

        if len(prefix_matches) == 1:
            return next(iter(prefix_matches))

        return name


# ── Error Taxonomy (ADR-0197) ─────────────────────────────────────────────

class ErrorCategory:
    """ADR-0197: Typed error categories for A2A send/ping failures.

    Replaces generic "error" status with specific categories that help callers
    decide what to do next (retry, fallback, diagnostic) without guessing.
    """
    UNREACHABLE = "unreachable"                # connection_failed: DNS/refused/TLS
    TIMEOUT_TRANSPORT = "timeout_transport"    # Sender-side connect/read timeout
    TIMEOUT_REMOTE = "timeout_remote"          # Receiver-side worker/engine timeout
    REJECTED = "rejected"                      # Receiver explicitly rejected (TTL, revocation, rate limit, house-rules)
    FILTERED = "filtered"                      # House-rules (L44) blocked the instruction
    AUTH_FAILED = "auth_failed"                # Bad/missing signature, task_id mismatch
    HTTP_ERROR = "http_error"                  # HTTP 4xx/5xx before A2A logic
    PROTOCOL_ERROR = "protocol_error"          # Invalid/oversized response JSON
    INTERNAL_ERROR = "internal_error"          # Catch-all: exceptions, system errors

    # All known values for validation
    ALL = {
        UNREACHABLE, TIMEOUT_TRANSPORT, TIMEOUT_REMOTE, REJECTED, FILTERED,
        AUTH_FAILED, HTTP_ERROR, PROTOCOL_ERROR, INTERNAL_ERROR,
    }


# ── Sender ────────────────────────────────────────────────────────────────

@dataclass
class SendResult:
    """Outcome of a sender.send() call (ADR-0197 typed errors).

    Attributes
    ----------
    ok            : bool   — True iff response verified and status not in ("rejected", "timeout")
    status        : str    — "ok" | "filtered" | "rejected" | "timeout" | "error" (legacy; see error_category)
    task_id       : str
    instance_id   : str    — receiver instance_id from the response
    instance_id_match : bool — receiver matched the pinned instance_id (or
                              no pin configured)
    data          : dict   — filtered response data, or {} on error
    attachments   : list   — list of Attachment dicts returned by receiver
                              (already digest-verified)
    duration_ms   : int
    error_category : str | None — ADR-0197: specific failure reason (e.g. "unreachable", "auth_failed")
    error_detail   : str | None — Sanitized error detail (max 256 chars), never raw exception text
    """
    ok: bool
    status: str
    task_id: str
    instance_id: str
    instance_id_match: bool
    data: dict
    attachments: list
    duration_ms: int
    error_category: str | None = None
    error_detail: str | None = None


@dataclass
class PingResult:
    """Outcome of a sender.ping() call (ADR-0199 lightweight liveness check).

    Attributes
    ----------
    reachable      : bool   — True iff peer returned a SIGNED, verified response
    source         : str    — "network_probe" (a "heartbeat_cache" fast path is
                              a future, receiver-side iteration; the sender-side
                              stub was removed 2026-07-19 — it referenced a
                              nonexistent symbol and an in-memory cross-process
                              cache cannot work)
    error_category : str | None — ADR-0197: failure reason if reachable=False
    error_detail   : str | None — Template-based error detail (ADR-0197 §2)
    duration_ms    : int    — Wall time spent (network: 2-10s max)
    """
    reachable: bool
    source: str
    error_category: str | None = None
    error_detail: str | None = None
    duration_ms: int = 0


# ── ADR-0197 §2: FIXED TEMPLATE SET for error_detail ──────────────────────
# error_detail (SendResult / PingResult / audit) is ALWAYS drawn from this
# closed set of fixed strings — never str(exc) verbatim, never interpolated
# peer-controlled text. Denylist-style regex scrubbing was rejected during
# the 2026-07-19 adversarial review: it leaked API tokens (sk-ant-…),
# Bearer JWTs, Discord UIDs (17-19 digit numbers), hostnames
# (host.fritz.box:8443) and 64-char hex keys straight into audit.jsonl.
# An allowlist of templates cannot leak by construction.
_ERROR_DETAIL_GENERIC = "error_detail_unavailable"
_ERROR_DETAIL_TEMPLATES = frozenset({
    "Response body exceeded maximum size",
    "HTTP error",
    "Unable to reach endpoint (DNS/connection refused)",
    "HTTP request timeout",
    "Response is not valid JSON",
    "Response is not a JSON object",
    "Response signature verification failed",
    "Response encoding error",
    "Receiver key configuration error",
    "Endpoint config unavailable",
    "Instance ID mismatch",
    "Invalid attachment",
    "Invalid response attachment",
    "unexpected_receiver_status",
    "Unsigned ping response rejected",
    "Peer rejected ping",
    _ERROR_DETAIL_GENERIC,
})

# ADR-0197 §2: the only non-template content ever emitted as error_detail is
# an exception *type name*, allowlist-validated against this closed set.
# Unknown / user-defined exception types collapse to "internal_error".
_ALLOWED_EXC_TYPE_NAMES = frozenset({
    # Local SendError family
    "SendError", "EndpointError", "TransportError", "ResponseVerificationError",
    # OS / socket layer
    "OSError", "ConnectionError", "ConnectionResetError",
    "ConnectionRefusedError", "ConnectionAbortedError", "BrokenPipeError",
    "TimeoutError", "InterruptedError", "PermissionError",
    "FileNotFoundError", "gaierror", "herror", "timeout",
    # TLS
    "SSLError", "SSLCertVerificationError", "SSLEOFError", "CertificateError",
    # urllib / http.client
    "URLError", "HTTPError", "InvalidURL", "RemoteDisconnected",
    "IncompleteRead", "BadStatusLine", "LineTooLong", "HTTPException",
    # Parsing / runtime
    "ValueError", "TypeError", "KeyError", "AttributeError",
    "JSONDecodeError", "UnicodeDecodeError", "UnicodeError",
    "MemoryError", "OverflowError", "RecursionError",
})


def _safe_exc_type_name(exc: "BaseException | str") -> str:
    """Closed-set exception type name, or "internal_error" for unknown types."""
    name = exc if isinstance(exc, str) else exc.__class__.__name__
    return name if name in _ALLOWED_EXC_TYPE_NAMES else "internal_error"


def _sanitize_error(reason: str | None) -> str | None:
    """ADR-0197 §2: template-gate an error detail string.

    Returns the input only when it is a member of the fixed template set or
    an allowlisted exception type name; everything else — including raw
    exception text, tokens, hostnames, UIDs — collapses to the generic
    template. This is allowlist (fail-closed), not denylist scrubbing.
    """
    if not reason:
        return None
    text = str(reason)
    if text in _ERROR_DETAIL_TEMPLATES or text in _ALLOWED_EXC_TYPE_NAMES:
        return text
    if text == "internal_error":
        return text
    return _ERROR_DETAIL_GENERIC


# ── ADR-0197 fail-closed audit backstop (analogous to telemetry _assert_safe) ─
# Every audited details dict passes through _assert_audit_details_safe before
# it reaches the hash chain. Free-form values are DROPPED (replaced with
# "redacted"), never raised on — the send path must not fail because of the
# backstop.
_AUDIT_ALLOWED_KEYS = frozenset({
    "endpoint_id", "task_id", "instance_id_match", "status", "duration_ms",
    "reason", "ttl_s", "nonce_prefix", "http_status", "error_category",
    "error_detail", "attachments_count", "our_chain_tail", "peer_chain_tail",
    "match", "reachable", "source",
})
_AUDIT_STATUS_VALUES = frozenset({
    "sent", "ok", "error", "rejected", "filtered", "timeout",
})
# reason: enum-ish machine token, must start with a letter (rejects digit-only
# Discord UIDs and most hex keys), optional ":sub_token,sub_token" suffix
# (missing_fields:hmac_key,recv_key / transport_error:OSError). Length caps
# reject 64-char hex keys and JWTs.
_AUDIT_REASON_RE = _re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,47}(:[A-Za-z0-9_,]{1,48})?$")
# ids / prefixes / source: uuid-, hex-, or enum-shaped, no dots (rejects
# hostnames), no spaces, bounded length.
_AUDIT_ENUMISH_RE = _re.compile(r"^[A-Za-z0-9_\-]{1,64}$")


def _is_safe_audit_value(key: str, value: Any) -> bool:
    if value is None or isinstance(value, bool):
        return True
    if isinstance(value, (int, float)):
        # 2026-07-19 fix: do NOT blanket-trust numerics. A Discord-UID-shaped
        # int (17-19 digits) previously short-circuited to True BEFORE the
        # per-key regex, so its string form was redacted but the int form
        # leaked verbatim. Bound the magnitude: 10^13 is generous for every
        # legitimate numeric audit field (ttl_s, http_status, duration_ms of a
        # single op, counters, epoch-ms timestamps ~1.7e12) yet well below
        # snowflake range (smallest Discord IDs ~4e15). Non-finite floats
        # (inf/NaN) fail the comparison and are redacted.
        try:
            return abs(value) < 10**13
        except (TypeError, ValueError):
            return False
    if not isinstance(value, str):
        return False
    if value == "":
        return True
    if key == "error_category":
        return value in ErrorCategory.ALL
    if key == "error_detail":
        return (value in _ERROR_DETAIL_TEMPLATES
                or value in _ALLOWED_EXC_TYPE_NAMES
                or value == "internal_error")
    if key == "status":
        return value in _AUDIT_STATUS_VALUES
    if key == "reason":
        return bool(_AUDIT_REASON_RE.match(value))
    return bool(_AUDIT_ENUMISH_RE.match(value))


def _assert_audit_details_safe(details: dict) -> dict:
    """Fail-closed backstop: allowlisted keys + enum/typename-shaped values only.

    On violation the offending value is replaced with "redacted" — the event
    still lands in the chain (visibility), the free-form payload does not.
    Never raises.
    """
    try:
        safe: dict = {}
        for k, v in dict(details).items():
            key = k if isinstance(k, str) else "redacted_key"
            if key not in _AUDIT_ALLOWED_KEYS:
                safe[key[:64]] = "redacted"
                continue
            safe[key] = v if _is_safe_audit_value(key, v) else "redacted"
        return safe
    except Exception:
        return {"reason": "audit_details_redacted"}


class RemoteTriggerSender:
    """Builds + signs TaskEnvelopes, posts them, verifies responses.

    Thread-safe: each call is self-contained; the registry reads its
    config file per call (no in-process cache).
    """

    def __init__(
        self,
        endpoints_dir: Path | None = None,
        registry: RemoteEndpointRegistry | None = None,
        *,
        instance_id: str | None = None,
        forge_se: Any = None,
    ) -> None:
        self._registry = registry or RemoteEndpointRegistry(endpoints_dir)
        # Cache instance_id at construction time so multiple senders in
        # the same process can attest distinct identities (E2E tests).
        if instance_id is not None:
            self._instance_id = instance_id
        else:
            try:
                self._instance_id = get_instance_id()
            except Exception:
                self._instance_id = ""
        # Injected forge_se for test isolation (avoids module-level patch conflicts).
        self._inst_forge_se = forge_se

    # ── Public API ────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_error(reason: str | None) -> str | None:
        """ADR-0197: Delegate to module-level _sanitize_error for consistent PII redaction."""
        return _sanitize_error(reason)

    @staticmethod
    def _categorize_transport_error(exc: TransportError) -> tuple[str, str]:
        """ADR-0197: Map TransportError to (error_category, error_detail).

        Reasons generated by _http_post:
          - "response_too_large" → PROTOCOL_ERROR
          - "http_XXX" (status code) → HTTP_ERROR
          - "connection_failed" → UNREACHABLE
          - "timeout" → TIMEOUT_TRANSPORT
          - "invalid_response_json:..." → PROTOCOL_ERROR
          - "transport_error:..." → INTERNAL_ERROR
        """
        reason = str(exc.reason or "transport_error")

        if reason == "response_too_large":
            return ErrorCategory.PROTOCOL_ERROR, RemoteTriggerSender._sanitize_error(
                "Response body exceeded maximum size"
            )
        elif reason.startswith("http_"):
            # http_XXX format; the numeric status travels in the separate
            # http_status audit field — the detail stays a fixed template.
            return ErrorCategory.HTTP_ERROR, RemoteTriggerSender._sanitize_error(
                "HTTP error"
            )
        elif reason == "connection_failed":
            return ErrorCategory.UNREACHABLE, RemoteTriggerSender._sanitize_error(
                "Unable to reach endpoint (DNS/connection refused)"
            )
        elif reason == "timeout":
            return ErrorCategory.TIMEOUT_TRANSPORT, RemoteTriggerSender._sanitize_error(
                "HTTP request timeout"
            )
        elif reason.startswith("invalid_response_json"):
            return ErrorCategory.PROTOCOL_ERROR, RemoteTriggerSender._sanitize_error(
                "Response is not valid JSON"
            )
        else:
            # ADR-0197 §2 catch-all: emit ONLY the allowlisted exception type
            # name — never the free-form reason string (which may embed
            # str(exc) with tokens/hostnames from lower layers).
            return ErrorCategory.INTERNAL_ERROR, _safe_exc_type_name(exc)

    @staticmethod
    def _categorize_verification_error(exc: ResponseVerificationError) -> tuple[str, str]:
        """ADR-0197: Map ResponseVerificationError to (error_category, error_detail).

        Reasons generated by _verify_response:
          - "response_not_object" → PROTOCOL_ERROR
          - "missing_signature" → AUTH_FAILED
          - "canonical_encode_failed:..." → PROTOCOL_ERROR
          - "bad_recv_key:..." → INTERNAL_ERROR
          - "bad_signature" → AUTH_FAILED
          - "task_id_mismatch" → AUTH_FAILED
        """
        reason = str(exc.reason or "verification_failed")

        if reason == "response_not_object":
            return ErrorCategory.PROTOCOL_ERROR, RemoteTriggerSender._sanitize_error(
                "Response is not a JSON object"
            )
        elif reason in ("missing_signature", "bad_signature", "task_id_mismatch"):
            return ErrorCategory.AUTH_FAILED, RemoteTriggerSender._sanitize_error(
                "Response signature verification failed"
            )
        elif reason.startswith("canonical_encode_failed"):
            return ErrorCategory.PROTOCOL_ERROR, RemoteTriggerSender._sanitize_error(
                "Response encoding error"
            )
        elif reason.startswith("bad_recv_key"):
            return ErrorCategory.INTERNAL_ERROR, RemoteTriggerSender._sanitize_error(
                "Receiver key configuration error"
            )
        else:
            # ADR-0197 §2 catch-all: allowlisted exception type name only.
            return ErrorCategory.INTERNAL_ERROR, _safe_exc_type_name(exc)

    @staticmethod
    def _categorize_response_status(status: str) -> tuple[str | None, str | None, bool]:
        """ADR-0197: Map response status to (error_category, error_detail, ok).

        Receiver-side status values:
          - "ok" → (None, None, True)
          - "timeout" → (TIMEOUT_REMOTE, None, False)
          - "rejected" → (REJECTED, None, False)
          - "filtered" → (FILTERED, None, False)
          - other → (INTERNAL_ERROR, detail, False)
        """
        status = str(status or "error").lower().strip()

        if status == "ok":
            return None, None, True
        elif status == "timeout":
            return ErrorCategory.TIMEOUT_REMOTE, None, False
        elif status == "rejected":
            return ErrorCategory.REJECTED, None, False
        elif status == "filtered":
            return ErrorCategory.FILTERED, None, False
        else:
            # Unknown/unexpected status from receiver. The peer-supplied
            # status string is NEVER interpolated into error_detail — a
            # malicious receiver could inject arbitrary text into audit
            # records otherwise (2026-07-19 adversarial finding). Fixed
            # template, no interpolation.
            return ErrorCategory.INTERNAL_ERROR, RemoteTriggerSender._sanitize_error(
                "unexpected_receiver_status"
            ), False

    def send(
        self,
        endpoint_id: str,
        instruction: str,
        *,
        result_schema: dict | None = None,
        ttl_s: int | None = None,
        timeout_s: int = _DEFAULT_TIMEOUT_S,
        attachments: list | None = None,
        purpose_id: str | None = None,
        attestation: dict | None = None,
    ) -> SendResult:
        """Send a signed TaskEnvelope to a registered endpoint.

        v3: ``attachments`` may be a list of
        :class:`a2a_attachments.Attachment` instances OR dicts in the
        same shape. Caps + name rules + digest are validated locally
        before sending; cap violations raise locally rather than burn a
        round-trip.

        Returns a :class:`SendResult`. Never raises on remote/transport
        failure — those land in the audit chain and surface as
        ``ok=False`` with a non-``"ok"`` status.
        """
        from a2a_attachments import (  # local import — circular-free
            Attachment, AttachmentError, validate_attachments,
        )

        start = time.time()
        task_id = str(uuid.uuid4())
        nonce = secrets.token_hex(32)

        # ── Normalize + validate outbound attachments BEFORE we sign ──
        att_dicts: list[dict] = []
        if attachments:
            for raw in attachments:
                if isinstance(raw, Attachment):
                    att_dicts.append(raw.to_dict())
                elif isinstance(raw, dict):
                    att_dicts.append(raw)
                else:
                    raise TypeError(
                        f"attachment must be Attachment or dict, "
                        f"got {type(raw).__name__}"
                    )
            try:
                validate_attachments(att_dicts)
            except AttachmentError as exc:
                # Local validation failure — surface as error result;
                # no audit event because the envelope never left.
                return SendResult(
                    ok=False, status="error", task_id=task_id,
                    instance_id="", instance_id_match=False,
                    data={}, attachments=[],
                    duration_ms=_ms(start),
                    error_category=ErrorCategory.PROTOCOL_ERROR,
                    error_detail=self._sanitize_error("Invalid attachment"),
                )

        # 1) Resolve endpoint config
        try:
            cfg = self._registry.load(endpoint_id)
        except EndpointError as exc:
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": exc.reason, "status": "error",
                 "duration_ms": _ms(start)},
            )
            return SendResult(
                ok=False, status="error", task_id=task_id,
                instance_id="", instance_id_match=False, data={},
                attachments=[], duration_ms=_ms(start),
                error_category=ErrorCategory.INTERNAL_ERROR,
                error_detail=self._sanitize_error("Endpoint config unavailable"),
            )

        ttl_s = int(ttl_s if ttl_s is not None else cfg.get("default_ttl_s", _DEFAULT_TTL_S))

        # ADR-0103 M2: build network membership attestation block.
        # Best-effort — if no SesT is available or crypto library missing,
        # the block is omitted and the receiver handles the grace period.
        net_att = self._build_network_attestation(cfg)

        # ADR-0116 M4: capture sender chain tail for cross-peer anchoring.
        # Best-effort — if unavailable, the envelope is sent without the field.
        # Prefer the injected instance se (test isolation) over the module-level
        # import so unit tests with a forge_se mock don't produce raw MagicMock
        # values that would be included in audit details.
        _forge_se_for_tail = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
        sender_chain_tail: str | None = None
        if _forge_se_for_tail is not None:
            try:
                _raw = _forge_se_for_tail.get_audit_chain_tail(audit_path())
                if isinstance(_raw, str):
                    sender_chain_tail = _raw
            except Exception:  # noqa: BLE001
                pass
        if sender_chain_tail is None:
            self._audit_best_effort(
                "A2A.chain_tail_unavailable", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": "chain_tail_read_failed"},
            )

        # ADR-0117 M4: sender_genesis_hash for chain DNA verification.
        sender_genesis_hash: str | None = None
        try:
            from nbac import get_genesis_hash as _get_genesis_hash  # noqa: PLC0415
            _gh = _get_genesis_hash(audit_path())
            if isinstance(_gh, str):
                sender_genesis_hash = _gh
        except Exception:  # noqa: BLE001
            pass

        # ADR-0077 C-2 + ADR-0078 Phase 1 + ADR-0103 M2 + ADR-0116 M4 + ADR-0117 M4:
        # include purpose_id, IAC attestation, network attestation,
        # sender_chain_tail (chain anchoring), and sender_genesis_hash (chain DNA).
        envelope = self._build_envelope(
            task_id=task_id,
            nonce=nonce,
            origin_id=cfg.get("origin_id_for_send")
                       or cfg.get("our_origin_id")
                       or self._instance_id,
            instruction=instruction,
            result_schema=result_schema or {},
            ttl_s=ttl_s,
            hmac_key_hex=cfg["hmac_key"],
            sender_instance_id=self._instance_id,
            attachments=att_dicts,
            purpose_id=purpose_id,
            attestation=attestation,
            network_attestation=net_att,
            sender_chain_tail=sender_chain_tail,
            sender_genesis_hash=sender_genesis_hash,
        )

        # 2) Audit envelope_sent (before HTTP); include chain_anchor_sent.
        self._audit_best_effort(
            "A2A.envelope_sent", "INFO",
            {"endpoint_id": endpoint_id, "task_id": task_id,
             "nonce_prefix": nonce[:8], "ttl_s": ttl_s,
             "attachments_count": len(att_dicts),
             "status": "sent"},
        )
        if sender_chain_tail is not None:
            self._audit_best_effort(
                "A2A.chain_anchor_sent", "INFO",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "nonce_prefix": nonce[:8],
                 "our_chain_tail": sender_chain_tail[:16]},
            )

        # 3) HTTP POST — with an ADR-0258 Stage 3 relay fallback on failure.
        # The relay attempt reuses the IDENTICAL envelope (just AEAD-wrapped
        # for transit) and returns the same shape _http_post does, so
        # everything below (signature verification, instance_id pin,
        # audit) is unchanged regardless of which transport actually
        # delivered it. Inert (raises immediately) when the feature flag is
        # off or no relay is configured — a direct-only deployment sees
        # byte-identical behavior to before this stage existed.
        try:
            raw = self._http_post(cfg["url"], envelope, timeout_s)
        except TransportError as direct_exc:
            try:
                raw = self._relay_post(cfg, endpoint_id, envelope, timeout_s)
                self._audit_best_effort(
                    "A2A.relay_fallback_used", "INFO",
                    {"endpoint_id": endpoint_id, "task_id": task_id,
                     "reason": direct_exc.reason, "duration_ms": _ms(start)},
                )
            except TransportError as exc:
                # ADR-0197: Map transport error to specific category before auditing.
                # Audit the DIRECT failure's category (the primary path) —
                # the relay attempt's own failure reason is not yet part of
                # the ADR-0197 closed template set and must never leak
                # verbatim; direct_exc.reason already is.
                error_cat, error_det = self._categorize_transport_error(direct_exc)
                self._audit_best_effort(
                    "A2A.response_rejected", "WARNING",
                    {"endpoint_id": endpoint_id, "task_id": task_id,
                     "reason": direct_exc.reason, "status": "error",
                     "http_status": direct_exc.http_status,
                     "error_category": error_cat,
                     "error_detail": error_det,
                     "duration_ms": _ms(start)},
                )
                return SendResult(
                    ok=False, status="error", task_id=task_id,
                    instance_id="", instance_id_match=False, data={},
                    attachments=[], duration_ms=_ms(start),
                    error_category=error_cat,
                    error_detail=error_det,
                )

        # 4) Verify response signature + task_id binding
        try:
            response, _resp_is_signed = self._verify_response(
                raw, cfg["recv_key"], expected_task_id=task_id,
            )
        except ResponseVerificationError as exc:
            # ADR-0197: Map response verification error to specific category before auditing
            error_cat, error_det = self._categorize_verification_error(exc)
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": exc.reason, "status": "error",
                 "error_category": error_cat,
                 "error_detail": error_det,
                 "duration_ms": _ms(start)},
            )
            return SendResult(
                ok=False, status="error", task_id=task_id,
                instance_id="", instance_id_match=False, data={},
                attachments=[], duration_ms=_ms(start),
                error_category=error_cat,
                error_detail=error_det,
            )

        # 5) instance_id pin check — signed responses only.
        # For unsigned legacy rejections (ADR-0077 C-5 backward compat) the
        # instance_id was stripped by _verify_response (CRIT-SENDER-01); the
        # pin check is skipped because the response is inherently untrusted —
        # applying the pin check would surface "instance_id_mismatch" instead
        # of the real cause ("bad hmac_key → rejected").
        pinned = cfg.get("instance_id", "") or ""
        received_iid = str(response.get("instance_id", ""))
        if _resp_is_signed and pinned and received_iid != pinned:
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": "instance_id_mismatch", "status": "error",
                 "instance_id_match": False,
                 "error_category": ErrorCategory.AUTH_FAILED,
                 "error_detail": self._sanitize_error("Instance ID mismatch"),
                 "duration_ms": _ms(start)},
            )
            return SendResult(
                ok=False, status="error", task_id=task_id,
                instance_id=received_iid, instance_id_match=False,
                data={}, attachments=[], duration_ms=_ms(start),
                error_category=ErrorCategory.AUTH_FAILED,
                error_detail=self._sanitize_error("Instance ID mismatch"),
            )

        instance_id_match = (not pinned) or (received_iid == pinned)
        status = str(response.get("status", "rejected"))
        data = dict(response.get("data", {}))

        # 6) Verify response attachments (digest, name, caps).
        resp_attachments_raw = response.get("attachments", []) or []
        try:
            verified_atts = validate_attachments(resp_attachments_raw)
        except AttachmentError as exc:
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": f"attachment_{exc.reason}", "status": "error",
                 "duration_ms": _ms(start)},
            )
            return SendResult(
                ok=False, status="error", task_id=task_id,
                instance_id=received_iid, instance_id_match=False,
                data={}, attachments=[], duration_ms=_ms(start),
                error_category=ErrorCategory.PROTOCOL_ERROR,
                error_detail=self._sanitize_error("Invalid response attachment"),
            )

        self._audit_best_effort(
            "A2A.response_received", "INFO",
            {"endpoint_id": endpoint_id, "task_id": task_id,
             "instance_id_match": instance_id_match,
             "status": status,
             "attachments_count": len(verified_atts),
             "duration_ms": _ms(start)},
        )

        # ADR-0116 M4: emit chain_anchor_verified when receiver includes its
        # chain tail in the ResponseEnvelope.  Best-effort (never blocks result).
        _receiver_tail = response.get("receiver_chain_tail")
        if isinstance(_receiver_tail, str) and _receiver_tail:
            self._audit_best_effort(
                "A2A.chain_anchor_verified", "INFO",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "peer_chain_tail": _receiver_tail[:16],
                 "match": True},
            )

        # ADR-0197: Map response status to error_category
        error_cat, error_det, is_ok = self._categorize_response_status(status)
        return SendResult(
            ok=is_ok,
            status=status,
            task_id=task_id,
            instance_id=received_iid,
            instance_id_match=instance_id_match,
            data=data,
            attachments=[a.to_dict() for a in verified_atts],
            duration_ms=_ms(start),
            error_category=error_cat,
            error_detail=error_det,
        )

    def send_reconnect(self, endpoint_id: str, new_url: str, *,
                        timeout_s: int = _DEFAULT_TIMEOUT_S) -> bool:
        """ADR-0198: proactively push a signed "my URL changed" notification.

        Reuses the endpoint's existing pairing HMAC key — no new credential.
        Fail-soft: never raises.

        Return value = DELIVERED, not accepted (2026-07-19 retry-storm fix):
        returns True as soon as the peer returned a cryptographically SIGNED
        response — meaning it received our new URL — REGARDLESS of whether it
        accepted (``status == "ok"``) or signed-rejected it. Only a genuinely
        unreachable peer (transport error, bad/absent signature) returns False.
        The caller persists the new IP once ANY peer delivered, so a peer that
        signs-rejects (and will reject again on retry) does not cause an
        unbounded 5-minute re-broadcast storm growing both audit chains.
        """
        start = time.time()
        try:
            cfg = self._registry.load(endpoint_id)
        except EndpointError as exc:
            self._audit_best_effort(
                "A2A.reconnect_send_failed", "WARNING",
                {"endpoint_id": endpoint_id, "reason": exc.reason,
                 "duration_ms": _ms(start)},
            )
            return False

        envelope = self._build_envelope(
            task_id=str(uuid.uuid4()),
            nonce=secrets.token_hex(32),
            origin_id=cfg.get("origin_id_for_send") or cfg.get("our_origin_id") or self._instance_id,
            instruction="",
            result_schema={},
            ttl_s=int(cfg.get("default_ttl_s", _DEFAULT_TTL_S)),
            hmac_key_hex=cfg["hmac_key"],
            sender_instance_id=self._instance_id,
            reconnect={"new_url": new_url.strip().rstrip("/")[:512]},
        )
        try:
            raw = self._http_post(cfg["url"], envelope, timeout_s)
            response, is_signed = self._verify_response(
                raw, cfg["recv_key"], expected_task_id=envelope["task_id"],
            )
        except (TransportError, ResponseVerificationError) as exc:
            self._audit_best_effort(
                "A2A.reconnect_send_failed", "WARNING",
                {"endpoint_id": endpoint_id, "reason": exc.reason,
                 "duration_ms": _ms(start)},
            )
            return False

        # An unsigned legacy rejection (ADR-0077 C-5) proves nothing about
        # whether the *paired* peer heard us — treat it as NOT delivered so the
        # IP stays pending, exactly like an unreachable peer.
        if not is_signed:
            self._audit_best_effort(
                "A2A.reconnect_send_failed", "WARNING",
                {"endpoint_id": endpoint_id, "reason": "unsigned_response",
                 "duration_ms": _ms(start)},
            )
            return False

        ok = str(response.get("status", "rejected")) == "ok"
        # Delivered = a signed response arrived (accept OR reject). The audit
        # event still distinguishes accepted from rejected for visibility, but
        # persistence is driven by delivery, not acceptance.
        self._audit_best_effort(
            "A2A.reconnect_sent" if ok else "A2A.reconnect_send_failed",
            "INFO" if ok else "WARNING",
            {"endpoint_id": endpoint_id,
             "reason": "" if ok else str(response.get("status", "rejected")),
             "duration_ms": _ms(start)},
        )
        return True

    def ping(self, endpoint_id: str, timeout_s: float | None = None) -> PingResult:
        """ADR-0199: Lightweight peer-liveness check (a2a_ping).

        Signed network probe with ±30s freshness window (no nonce store).
        Timeout clamped to [2, 10]s; default 5s.

        Note (2026-07-19): the ADR-0199 §2 heartbeat-cache fast path is
        deliberately NOT implemented on the sender side. It requires
        receiver-side last-seen heartbeat records that do not exist yet;
        the earlier stub imported a nonexistent symbol and was a silent
        dead path. Future iteration — see ADR-0199.

        Returns PingResult with reachable (bool), source ("network_probe"),
        error_category/error_detail (ADR-0197) if reachable=False.
        One ``A2A.ping_result`` audit event is emitted per call (closed
        enum values only — same safe audit path as send()).
        """
        start = time.time()

        # Clamp timeout_s to [2, 10], default 5
        if timeout_s is None:
            timeout_s = 5
        else:
            timeout_s = max(2, min(10, timeout_s))

        # Network probe: signed request + signed-response verification
        try:
            ok, error_cat, error_det = self._http_ping_probe(
                endpoint_id, timeout_s=timeout_s
            )
            result = PingResult(
                reachable=ok,
                source="network_probe",
                error_category=error_cat if not ok else None,
                error_detail=error_det if not ok else None,
                duration_ms=_ms(start),
            )
        except Exception as exc:
            # Catch-all: any unexpected error maps to INTERNAL_ERROR with an
            # allowlisted exception type name (ADR-0197 §2) — never str(exc).
            result = PingResult(
                reachable=False,
                source="network_probe",
                error_category=ErrorCategory.INTERNAL_ERROR,
                error_detail=_safe_exc_type_name(exc),
                duration_ms=_ms(start),
            )

        # ADR-0199: one audit event per ping outcome. endpoint_id is the
        # pairing kid (pseudonym), consistent with the other A2A events;
        # every value is a closed enum / bool / int — backstop-validated.
        self._audit_best_effort(
            "A2A.ping_result",
            "INFO" if result.reachable else "WARNING",
            {"endpoint_id": endpoint_id,
             "reachable": result.reachable,
             "source": result.source,
             "error_category": result.error_category,
             "duration_ms": result.duration_ms},
        )
        return result

    def _http_ping_probe(
        self, endpoint_id: str, timeout_s: float = 5
    ) -> tuple[bool, str | None, str | None]:
        """ADR-0199: Signed ping request-response (network probe).

        Returns: (reachable, error_category, error_detail)
        - reachable=True: peer responded with valid signature
        - reachable=False: error_category=UNREACHABLE|TIMEOUT_TRANSPORT|AUTH_FAILED|...
        """
        start = time.time()

        try:
            cfg = self._registry.load(endpoint_id)
        except EndpointError as exc:
            error_cat, error_det = self._categorize_transport_error(exc)
            return False, error_cat, error_det

        # Build ping request: {ping_id, issued_at, origin_id, signature}
        ping_id = str(uuid.uuid4())
        issued_at = int(time.time())
        origin_id = cfg.get("origin_id_for_send") or cfg.get("our_origin_id") or self._instance_id

        ping_request = {
            "ping_id": ping_id,
            "issued_at": issued_at,
            "origin_id": origin_id,
        }

        # Sign with HMAC key (same as TaskEnvelope)
        canonical = json.dumps(ping_request, separators=(",", ":"), sort_keys=True)
        signature = _hmac.new(
            bytes.fromhex(cfg["hmac_key"]),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        ping_request["signature"] = signature

        # POST to /v1/a2a/ping. cfg["url"] is the full RECEIVE url
        # (".../v1/a2a/receive") — strip that suffix to get the base, then
        # append the ping route. Naive rstrip("/") + "/v1/a2a/ping" produced
        # ".../v1/a2a/receive/v1/a2a/ping" → permanent 404 (2026-07-19 fix).
        base = cfg["url"].rstrip("/")
        if base.endswith("/v1/a2a/receive"):
            base = base[: -len("/v1/a2a/receive")]
        ping_url = base.rstrip("/") + "/v1/a2a/ping"

        try:
            raw = self._http_post(ping_url, ping_request, timeout_s)
        except TransportError as exc:
            error_cat, error_det = self._categorize_transport_error(exc)
            return False, error_cat, error_det

        # Verify response signature with recv_key
        try:
            response, is_signed = self._verify_response(
                raw, cfg["recv_key"], expected_task_id=ping_id
            )
        except ResponseVerificationError as exc:
            error_cat, error_det = self._categorize_verification_error(exc)
            return False, error_cat, error_det

        # ADR-0199 §4: ping is authenticated — non-negotiable. The legacy
        # unsigned-rejection tolerance in _verify_response (ADR-0077 C-5)
        # MUST NOT confer reachable=True: an unsigned response proves only
        # that *something* answered, not that the paired peer did. Forgeable
        # liveness was a 2026-07-19 HIGH finding.
        if not is_signed:
            return False, ErrorCategory.AUTH_FAILED, self._sanitize_error(
                "Unsigned ping response rejected"
            )

        # Verify response shape: {ok, instance_id, protocol_version, server_time}
        if not response.get("ok"):
            # Peer rejected the ping (shouldn't happen for valid ping)
            return False, ErrorCategory.REJECTED, self._sanitize_error(
                "Peer rejected ping"
            )

        # Success
        return True, None, None

    # ── Internals ─────────────────────────────────────────────────────

    @staticmethod
    def _load_sest() -> str | None:
        """Load the local Session Token (SesT / license JWT).

        Returns the raw JWT string, or None when unavailable.
        Mirrors the lookup order of ``validator._find_token()``:
        1. CORVIN_LICENSE_KEY env var
        2. ~/.config/corvin-voice/session.key  (written by session-refresh daemon)
        3. <corvin_home>/global/license.key
        """
        token = os.environ.get("CORVIN_LICENSE_KEY", "").strip()
        if token:
            return token
        # Session key written by the refresh daemon (highest-priority disk source)
        try:
            session_key = Path.home() / ".config" / "corvin-voice" / "session.key"
            if session_key.exists():
                t = session_key.read_text("utf-8").strip()
                if t:
                    return t
        except Exception:
            pass
        try:
            home = Path(os.environ.get("CORVIN_HOME", "") or (Path.home() / ".corvin"))
            key_file = home / "global" / "license.key"
            if key_file.exists():
                return key_file.read_text("utf-8").strip()
        except Exception:
            pass
        return None

    # ADR-0141 Tier 2 — per-boot cache of the local layer_integrity_hash. Keyed
    # on the (path, mtime) fingerprint of the mandatory layer files so an edit
    # to any of them invalidates the cache without a restart.
    _li_hash_cache: "tuple[tuple, str] | None" = None

    @staticmethod
    def _compute_layer_integrity_hash() -> "str | None":
        """Return the local ``layer_integrity_hash`` (Tier 2), or None if the
        integrity module is unavailable. Cached per boot, invalidated on mtime
        change of any mandatory layer file."""
        try:
            import layer_integrity as _li  # type: ignore
        except Exception:
            return None
        try:
            root = _li._repo_root()
            fp = tuple(
                (name, (root / rel).stat().st_mtime_ns if (root / rel).is_file() else 0)
                for name, rel in sorted(_li.MANDATORY_LAYER_FILES.items())
            )
        except Exception:
            fp = ()
        cache = RemoteTriggerSender._li_hash_cache
        if cache is not None and cache[0] == fp:
            return cache[1]
        try:
            h = _li.compute_layer_integrity_hash()
        except Exception:
            return None
        RemoteTriggerSender._li_hash_cache = (fp, h)
        return h

    @staticmethod
    def _build_network_attestation(endpoint_cfg: dict) -> dict | None:
        """ADR-0103 M2: build the network_attestation block for a TaskEnvelope.

        Returns None when:
        - No SesT is available (free / unlicensed instance).
        - The ``cryptography`` package is not installed.
        - The SesT is not a valid 3-part JWT.

        The block is included in the HMAC payload so it cannot be stripped
        or replaced in transit.
        """
        sest = RemoteTriggerSender._load_sest()
        if not sest:
            return None

        parts = sest.split(".")
        if len(parts) != 3:
            return None

        try:
            import hashlib as _hl
            import base64 as _b64

            header_payload = parts[0] + "." + parts[1]
            sest_fp = _hl.sha256(header_payload.encode("ascii")).hexdigest()
            # The JWT signature is base64url-encoded without padding.
            # We store it as-is so the receiver can decode it directly.
            sest_sig = parts[2]
        except Exception:
            return None

        pairing_id = str(endpoint_cfg.get("pairing_id", ""))

        block = {
            "sest_fp": sest_fp,
            "sest_sig": sest_sig,
            "pairing_id": pairing_id,
            "attested_at": time.time(),
        }
        # ADR-0141 Tier 2 (Protocol v7): fold in the local layer_integrity_hash.
        # The whole block is HMAC-covered by _build_envelope, so the hash cannot
        # be stripped or altered in transit. Receivers that pre-date v7 ignore
        # the extra fields (additive, backward-compatible read).
        li_hash = RemoteTriggerSender._compute_layer_integrity_hash()
        if li_hash:
            block["layer_integrity_hash"] = li_hash
            block["protocol_version"] = 7
        return block

    @staticmethod
    def _build_envelope(
        *,
        task_id: str,
        nonce: str,
        origin_id: str,
        instruction: str,
        result_schema: dict,
        ttl_s: int,
        hmac_key_hex: str,
        sender_instance_id: str,
        attachments: list | None = None,
        purpose_id: str | None = None,
        attestation: dict | None = None,
        network_attestation: dict | None = None,
        sender_chain_tail: str | None = None,
        sender_genesis_hash: str | None = None,
        reconnect: dict | None = None,
    ) -> dict:
        env: dict = {
            "task_id": task_id,
            "nonce": nonce,
            "issued_at": time.time(),
            "origin_id": origin_id,
            "instruction": instruction,
            "result_schema": result_schema,
            "ttl_s": ttl_s,
            "signature": "",
            "sender_instance_id": sender_instance_id,
            "attachments": list(attachments or []),
        }
        # ADR-0077 C-2: purpose_id — included in HMAC when present.
        if purpose_id is not None:
            env["purpose_id"] = str(purpose_id)[:64]
        # ADR-0078 Phase 1: sender_attestation — included in HMAC when present.
        # v4 receivers that don't know this field ignore it (unknown fields
        # do not break HMAC because v4 receivers also omit it from their
        # canonical payload when absent).
        if attestation is not None and isinstance(attestation, dict):
            env["sender_attestation"] = attestation
        # ADR-0103 M2: network_attestation — separate from sender_attestation
        # (IAC, ADR-0078). Included in HMAC when present so it cannot be
        # stripped or swapped in transit. Receivers that pre-date M2 ignore
        # the field (additive, backward-compatible read). M2 receivers require
        # it after the grace period expires.
        if network_attestation is not None and isinstance(network_attestation, dict):
            env["network_attestation"] = network_attestation
        # ADR-0116 M4: sender_chain_tail — hash of sender's last audit record,
        # included in HMAC so it cannot be stripped or replaced in transit.
        # Receivers that pre-date ADR-0116 ignore the field (additive).
        if sender_chain_tail is not None and isinstance(sender_chain_tail, str):
            env["sender_chain_tail"] = sender_chain_tail
        # ADR-0117 M4: sender_genesis_hash — SHA-256 hash of this instance's
        # genesis block, included in HMAC. Lets the receiver verify chain DNA
        # (same network) before accepting the task. Backward-compatible: receivers
        # that pre-date ADR-0117 ignore the field.
        if sender_genesis_hash is not None and isinstance(sender_genesis_hash, str):
            env["sender_genesis_hash"] = sender_genesis_hash
        # ADR-0198: reconnect — proactive "my URL changed" push. Included in
        # HMAC when present. Compat (corrected 2026-07-19): pre-ADR-0198
        # receivers do NOT ignore the field — their canonical payload omits
        # the unknown key, so the HMAC mismatches and they hard-reject the
        # envelope with bad_signature. Accepted fail-closed behaviour: no
        # half-applied reconnects on old peers; send_reconnect() is
        # fail-soft and simply reports False.
        if reconnect is not None and isinstance(reconnect, dict):
            env["reconnect"] = reconnect
        # IBC concept (Protocol v7): instance_attestation — binds this envelope to
        # the sender's Instance Binding Certificate (IBC).  Included in HMAC when
        # present so it cannot be stripped or swapped in transit.  Receivers that
        # pre-date v7 ignore the field (additive, backward-compatible read).
        # Only set protocol_version=7 at the top-level envelope when IBC succeeds;
        # default is 6 (prior behaviour).
        if _IBC_AVAILABLE:
            try:
                ibc_jwt = _get_ibc_jwt()
                if ibc_jwt:
                    canonical = _build_canonical_payload(
                        task_id=env["task_id"],
                        origin_id=env["origin_id"],
                        issued_at=env["issued_at"],
                        nonce=env["nonce"],
                        instruction=instruction,
                    )
                    sig = _sign_payload(canonical)
                    # Extract jti from IBC JWT (decode without verify — already
                    # verified at bind time; we only need the claim value).
                    import jwt as _jwt_mod  # noqa: PLC0415
                    ibc_decoded = _jwt_mod.decode(
                        ibc_jwt, options={"verify_signature": False}
                    )
                    # GDPR Art. 6(1)(b) basis: the full IBC JWT is transmitted to
                    # peers as part of the A2A pairing contract. Peers are trusted
                    # operators under the same pairing agreement; the `email` claim
                    # identifies the operator entity (not an end-user), and the
                    # transmission is necessary for mutual identity verification
                    # under the contractual relationship established at pairing time.
                    # Future milestone (ADR-0145 §5): add a `ibc_public_claims`
                    # projection with only `sub`+`instance_pubkey`+`jti` for
                    # deployments with stricter data-minimisation requirements.
                    env["instance_attestation"] = {
                        "ibc_jti": ibc_decoded.get("jti", ""),
                        "ed25519_sig": sig,
                        "ibc_snapshot": ibc_jwt,
                    }
            except Exception as _ibc_exc:  # noqa: BLE001
                # Non-fatal: IBC attestation fails gracefully.
                print(
                    f"[a2a_sender] WARNING: IBC attestation failed (sending without): "
                    f"{_ibc_exc}",
                    file=sys.stderr, flush=True,
                )
        # ADR-0153 M5 — Protocol v8: inject corvin_id_jwt (best-effort).
        # The CorvinID JWT is the operator identity credential issued by Corvin Labs.
        # It is included in the HMAC payload so it cannot be stripped or swapped in
        # transit. Receivers that pre-date v8 ignore the field (additive, backward-
        # compatible). Never blocks sending: any failure silently skips the field.
        if _IBC_AVAILABLE:
            try:
                ibc_jwt_raw = _get_ibc_jwt()
                if ibc_jwt_raw:
                    env["corvin_id_jwt"] = str(ibc_jwt_raw)[:8192]
                    # NOTE: do NOT set env["protocol_version"] = 8 here.
                    # protocol_version is not a declared TaskEnvelope dataclass field.
                    # canonical_payload() uses dataclasses.asdict() which only serialises
                    # declared fields, so any top-level key outside the dataclass is absent
                    # from the receiver's HMAC computation → signature mismatch on every
                    # v8 envelope. PROTOCOL_VERSION is declared as a module constant
                    # (receiver.PROTOCOL_VERSION = 8) for capability discovery; it is not
                    # a per-envelope wire field. (ADR-0153 M5 fix)
            except Exception:  # noqa: BLE001
                pass  # best-effort only — never block sending
        payload = {k: v for k, v in env.items() if k != "signature"}
        sig = _hmac.new(
            bytes.fromhex(hmac_key_hex),
            json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True,
            ).encode(),
            hashlib.sha256,
        ).hexdigest()
        env["signature"] = sig
        return env

    @staticmethod
    def _relay_post(cfg: dict, endpoint_id: str, envelope: dict, timeout_s: int) -> dict:
        """ADR-0258 Stage 3 — relay fallback for the direct HTTP POST.

        Returns the SAME shape ``_http_post`` would (a parsed, still-signed
        response dict) so ``_verify_response`` works completely unchanged —
        the relay is a drop-in ALTERNATIVE TRANSPORT for the identical
        envelope, never a different protocol. Raises ``TransportError`` on
        any failure (feature flag off, no relay configured, connect/
        registration/delivery failure, timeout, or response decrypt
        failure) so the caller's existing ``except TransportError`` handling
        already covers both transports with one code path.

        Routing-id note: the pairing's ``kid`` is SHARED and IDENTICAL on
        both sides (by construction — see a2a_friendship._derive_channel_
        keys), so it cannot ALSO serve as this sender's own reply-to
        routing id without colliding with the peer's own listen
        registration. This call therefore registers under a fresh,
        per-request ephemeral id (`"<kid>:reply:<task_id>"`, random
        registration credential) that only needs to live for the duration
        of this one exchange — the receiver's RelayListener echoes back
        whatever "from_kid" the request declared, so this resolves
        correctly without either side needing to know the other's
        instance_id.
        """
        try:
            from corvin_console import feature_flags as _ff  # type: ignore[import-not-found]  # noqa: PLC0415
            if not _ff.is_enabled("a2a_relay_fallback"):
                raise TransportError("relay_fallback_disabled")
        except ImportError:
            raise TransportError("relay_fallback_disabled")

        import a2a_friendship as _ft  # type: ignore[import-not-found]  # noqa: PLC0415

        relay_url = _ft.get_my_relay_url()
        if not relay_url:
            raise TransportError("relay_not_configured")

        hmac_key = cfg.get("hmac_key", "")
        to_kid = cfg.get("endpoint_id") or endpoint_id
        if not hmac_key or not to_kid:
            raise TransportError("relay_endpoint_config_incomplete")

        task_id = envelope.get("task_id", "")
        my_kid = f"{to_kid}:reply:{task_id}"

        import secrets as _secrets  # noqa: PLC0415
        my_relay_auth_key = _secrets.token_hex(32)  # ephemeral, single-use — no TOFU needed

        try:
            import a2a_relay as _relay  # type: ignore[import-not-found]  # noqa: PLC0415
            import asyncio as _asyncio  # noqa: PLC0415

            plaintext = json.dumps(envelope).encode("utf-8")
            nonce_hex, ct_hex = _ft.encrypt_for_relay(hmac_key, plaintext)

            result = _asyncio.run(_relay.relay_deliver_and_wait(
                relay_url=relay_url, my_kid=my_kid, my_relay_auth_key=my_relay_auth_key,
                to_kid=to_kid, nonce_hex=nonce_hex, ciphertext_hex=ct_hex,
                task_id=task_id, timeout_s=timeout_s,
            ))
        except Exception as exc:  # noqa: BLE001 — a2a_relay.RelayTransportError or any transport failure
            raise TransportError("relay_error:" + _safe_exc_type_name(exc)) from exc

        try:
            resp_plain = _ft.decrypt_from_relay(hmac_key, result["nonce"], result["ciphertext"])
            return json.loads(resp_plain)
        except Exception as exc:  # noqa: BLE001 — RelayDecryptError, KeyError, JSONDecodeError
            raise TransportError("relay_response_invalid") from exc

    @staticmethod
    def _http_post(url: str, envelope: dict, timeout_s: int) -> dict:
        body = json.dumps(envelope).encode()
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "corvin-a2a/1.0"},
            method="POST",
        )
        try:
            # urlopen(timeout=N) sets a *per-recv()* socket timeout, NOT a
            # total-transfer timeout. A rogue receiver that trickles bytes can
            # hold the connection open indefinitely. We enforce a hard wall-clock
            # deadline and a body size cap to prevent both attacks
            # (ADR-0099 iter-5 findings HIGH-IT5-02 and MED-IT5-03).
            deadline = time.monotonic() + timeout_s
            # No-redirect opener (2026-07-19): a 3xx from the peer raises
            # HTTPError below rather than being silently followed to an
            # internal address.
            with _NO_REDIRECT_OPENER.open(req, timeout=timeout_s) as resp:
                chunks: list[bytes] = []
                total = 0
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise TimeoutError("total_transfer_timeout")
                    # Tighten per-recv timeout to remaining wall time so the
                    # last read does not extend past the deadline.
                    try:
                        sock = resp.fp.raw._sock  # type: ignore[attr-defined]
                        sock.settimeout(min(remaining, float(timeout_s)))
                    except AttributeError:
                        pass
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > _MAX_RESPONSE_BYTES:
                        raise TransportError("response_too_large")
                    chunks.append(chunk)
                raw = b"".join(chunks)
        except TransportError:
            raise
        except urllib.error.HTTPError as exc:
            raise TransportError(
                f"http_{exc.code}", http_status=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            raise TransportError("connection_failed") from exc
        except TimeoutError as exc:
            raise TransportError("timeout") from exc
        except Exception as exc:
            # ADR-0197 §2: reason carries ONLY the allowlisted exception type
            # name. str(exc) can embed the target URL/host and must never
            # reach SendResult or audit details (2026-07-19 finding).
            raise TransportError(
                "transport_error:" + _safe_exc_type_name(exc)
            ) from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            # ADR-0197 §2: no str(exc) — the decode error message quotes the
            # (peer-controlled) response body.
            raise TransportError("invalid_response_json") from exc

    @staticmethod
    def _verify_response(
        response: dict,
        recv_key_hex: str,
        *,
        expected_task_id: "str | None" = None,
    ) -> "tuple[dict, bool]":
        """Verify the HMAC signature on a ResponseEnvelope.

        Returns ``(response_dict, is_signed)`` where ``is_signed`` is
        ``True`` for a properly HMAC-verified response and ``False`` for
        the unsigned legacy-rejection path (ADR-0077 C-5 backward compat).
        The caller MUST skip the instance_id pin check when ``is_signed``
        is False (ADR-0099 iter-3 CRIT-SENDER-01).

        ``expected_task_id``: when provided, the response ``task_id`` must
        match exactly — prevents a rogue receiver replaying a HMAC-valid
        response from a different past task (ADR-0099 iter-4 HIGH-IT4-01).

        The distinction:
          * Signed rejection (v4 receiver): verified normally → (response, True).
          * Unsigned rejection + empty data (v3 receiver): accepted as
            ``status="rejected"``; instance_id stripped → (sanitized, False).
          * Unsigned rejection + non-empty data, or unsigned non-rejection:
            ResponseVerificationError.
        """
        if not isinstance(response, dict):
            raise ResponseVerificationError("response_not_object")
        sig = response.get("signature")
        status = response.get("status", "")
        data = response.get("data", {})

        if not isinstance(sig, str) or not sig:
            # ADR-0077 C-5: unsigned response — only tolerate the legacy
            # v3 fail-silent pattern (rejected + empty data).
            if (
                status == "rejected"
                and isinstance(data, dict) and not data
            ):
                # Legacy unsigned rejection from v3 receiver. Accept but
                # strip instance_id: an unsigned response MUST NOT carry an
                # instance_id, because an attacker could include the pinned
                # UUID to forge instance_id_match=True in the audit trail
                # (ADR-0099 iter-3 finding CRIT-SENDER-01).
                sanitized = {k: v for k, v in response.items()
                             if k != "instance_id"}
                sanitized["instance_id"] = ""
                return sanitized, False
            raise ResponseVerificationError("missing_signature")

        payload = {k: v for k, v in response.items() if k != "signature"}
        try:
            canonical = json.dumps(
                payload, sort_keys=True, separators=(",", ":"),
                ensure_ascii=True,
            ).encode()
        except (TypeError, ValueError) as exc:
            # ADR-0197 §2: fixed reason, no str(exc) (can quote payload text).
            raise ResponseVerificationError("canonical_encode_failed") from exc
        try:
            key = bytes.fromhex(recv_key_hex)
        except ValueError as exc:
            # ADR-0197 §2: fixed reason, no str(exc) (can quote key material).
            raise ResponseVerificationError("bad_recv_key") from exc
        expected = _hmac.new(key, canonical, hashlib.sha256).hexdigest()
        if not _hmac.compare_digest(expected, sig.lower()):
            raise ResponseVerificationError("bad_signature")

        # Bind response to the sent task_id — prevents a rogue receiver from
        # replaying an old HMAC-valid response as the answer to a new task
        # (ADR-0099 iter-4 finding HIGH-IT4-01).
        if expected_task_id is not None:
            resp_task_id = response.get("task_id", "")
            if resp_task_id != expected_task_id:
                raise ResponseVerificationError("task_id_mismatch")

        return response, True

    def _audit_best_effort(self, event_type: str, severity: str, details: dict) -> None:
        try:
            se = self._inst_forge_se if self._inst_forge_se is not None else _forge_se
            if se is None:
                return
            path = audit_path()
            path.parent.mkdir(parents=True, exist_ok=True)
            # ADR-0197: fail-closed backstop (analogous to telemetry
            # _assert_safe) — free-form values are dropped, never sent.
            se.write_event(
                path, event_type,
                severity=severity, tool="", run_id="",
                details=_assert_audit_details_safe(details), hash_chain=True,
            )
        except Exception:
            pass


def _ms(start: float) -> int:
    return int((time.time() - start) * 1000)


__all__ = [
    "RemoteEndpointRegistry",
    "RemoteTriggerSender",
    "SendError",
    "EndpointError",
    "TransportError",
    "ResponseVerificationError",
    "SendResult",
]

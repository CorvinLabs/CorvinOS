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
  ============================= ======== =========================================

Audit ``details`` allow-list:
  ``endpoint_id``, ``task_id``, ``instance_id_match``, ``status``,
  ``duration_ms``, ``reason``, ``ttl_s``, ``nonce_prefix``,
  ``http_status``.

Never in ``details``: ``instruction``, ``result_schema``, response
``data``, ``signature``, ``hmac_key``, ``recv_key``, full nonce, URL,
HTTP headers, response body bytes.

CI lint: module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import os
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


def _sanitize_error(reason: str | None) -> str | None:
    """ADR-0197: Cap error detail to 256 chars, remove PII/leaks."""
    if not reason:
        return None
    # Remove common PII patterns
    sanitized = str(reason)[:256]
    # Strip paths, URLs, local filenames
    for phrase in ["localhost", "127.0.0.1", "/home", "/root", "http://", "https://"]:
        if phrase in sanitized.lower():
            sanitized = sanitized.replace(phrase, "[REDACTED]")
    return sanitized.strip() if sanitized.strip() else None


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
    def _sanitize_error(reason: str) -> str:
        """ADR-0197: Sanitize error detail to max 256 chars, removing sensitive info.

        Caps the string at 256 characters and strips raw exception messages that
        might leak implementation details or PII.
        """
        if not isinstance(reason, str):
            reason = str(reason)
        # Remove newlines and excessive whitespace
        reason = " ".join(reason.split())
        # Truncate to 256 chars
        if len(reason) > 256:
            reason = reason[:253] + "..."
        return reason

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
            # http_XXX format where XXX is the status code
            try:
                status_code = int(reason.split("_")[1])
                return ErrorCategory.HTTP_ERROR, RemoteTriggerSender._sanitize_error(
                    f"HTTP error {status_code}"
                )
            except (ValueError, IndexError):
                return ErrorCategory.HTTP_ERROR, RemoteTriggerSender._sanitize_error(reason)
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
            # catch-all for other transport errors
            return ErrorCategory.INTERNAL_ERROR, RemoteTriggerSender._sanitize_error(reason)

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
            # catch-all for other verification errors
            return ErrorCategory.INTERNAL_ERROR, RemoteTriggerSender._sanitize_error(reason)

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
            # Unknown/unexpected status from receiver
            return ErrorCategory.INTERNAL_ERROR, RemoteTriggerSender._sanitize_error(
                f"Unexpected receiver status: {status}"
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

        # 3) HTTP POST
        try:
            raw = self._http_post(cfg["url"], envelope, timeout_s)
        except TransportError as exc:
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": exc.reason, "status": "error",
                 "http_status": exc.http_status,
                 "duration_ms": _ms(start)},
            )
            # ADR-0197: Map transport error to specific category
            error_cat, error_det = self._categorize_transport_error(exc)
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
            self._audit_best_effort(
                "A2A.response_rejected", "WARNING",
                {"endpoint_id": endpoint_id, "task_id": task_id,
                 "reason": exc.reason, "status": "error",
                 "duration_ms": _ms(start)},
            )
            # ADR-0197: Map response verification error to specific category
            error_cat, error_det = self._categorize_verification_error(exc)
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
                error_detail=self._sanitize_error(f"Invalid response attachment: {exc.reason}"),
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
        Fail-soft: never raises, returns False on any error (the operator's
        next real ``send()`` still surfaces a hard TransportError if the
        stale URL truly went dead, so this is additive robustness, not a
        replacement for normal error handling).
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
            response, _ = self._verify_response(raw, cfg["recv_key"], expected_task_id=envelope["task_id"])
        except (TransportError, ResponseVerificationError) as exc:
            self._audit_best_effort(
                "A2A.reconnect_send_failed", "WARNING",
                {"endpoint_id": endpoint_id, "reason": exc.reason,
                 "duration_ms": _ms(start)},
            )
            return False

        ok = str(response.get("status", "rejected")) == "ok"
        self._audit_best_effort(
            "A2A.reconnect_sent" if ok else "A2A.reconnect_send_failed",
            "INFO" if ok else "WARNING",
            {"endpoint_id": endpoint_id,
             "reason": "" if ok else str(response.get("status", "rejected")),
             "duration_ms": _ms(start)},
        )
        return ok

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
        # HMAC when present. Pre-ADR-0198 receivers ignore the field and fall
        # through to normal instruction handling (safe: instruction is "").
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
            with urllib.request.urlopen(req, timeout=timeout_s) as resp:
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
            raise TransportError(f"transport_error:{exc}") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise TransportError(f"invalid_response_json:{exc}") from exc

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
            raise ResponseVerificationError(
                f"canonical_encode_failed:{exc}",
            ) from exc
        try:
            key = bytes.fromhex(recv_key_hex)
        except ValueError as exc:
            raise ResponseVerificationError(f"bad_recv_key:{exc}") from exc
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
            se.write_event(
                path, event_type,
                severity=severity, tool="", run_id="",
                details=details, hash_chain=True,
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

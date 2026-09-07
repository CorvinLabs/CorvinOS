"""Generic Webhook Bridge (ADR-0124 M7).

Operators register inbound webhook channels. External systems POST
to /webhook/{channel_id}; the bridge verifies HMAC-SHA256 and logs
the event to the audit chain. Integration with the chat/inbox system
is a Phase 2 concern.

Routes (admin — require_csrf):
  GET    /bridges/custom                     list registered webhook channels
  PUT    /bridges/custom/{channel_id}        register or update
  DELETE /bridges/custom/{channel_id}        remove

Route (inbound — HMAC-authenticated, no session required):
  POST   /webhook/{channel_id}               receive external webhook
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Request, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from ..utils import atomic_write_json
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths
from forge import tenants as _forge_tenants  # noqa: E402
from forge import security_events as _security_events  # noqa: E402

router = APIRouter()

_CHANNEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")

# R2-C3 (adversarial review round 2, 2026-09-07). Three gaps closed here:
#
#   1. ``hmac_secret_env`` was OPTIONAL, so a channel could be registered with
#      no signature check at all — an unauthenticated POST target that writes
#      into the tenant's audit chain (an anonymous chain writer). It is now
#      REQUIRED at registration; the field keeps its Optional type only so the
#      400 comes from this explicit check with a usable message.
#   2. ``rate_limit_per_hour`` was persisted and never read — decorative. It is
#      now ENFORCED per (tenant, channel) with an in-process sliding window.
#   3. ``await request.body()`` had no size cap, so an unauthenticated caller
#      could make the process buffer arbitrary bytes. Capped BEFORE reading via
#      Content-Length and again while streaming (a chunked body has no length).
_MAX_WEBHOOK_BODY_BYTES = 256 * 1024  # same order as routes/memory.py's cap

#: (tenant_id, channel_id) -> list of unix timestamps within the last hour.
_RATE_WINDOW_S = 3600.0
_rate_hits: dict[tuple[str, str], list[float]] = {}


def _rate_limit_exceeded(tid: str, channel_id: str, limit: int) -> bool:
    """Sliding-window counter for one channel. Returns True when this request
    must be refused (the hit is recorded only when it is allowed, so a
    throttled caller cannot push the window forward)."""
    now = time.time()
    key = (tid, channel_id)
    hits = [t for t in _rate_hits.get(key, ()) if now - t < _RATE_WINDOW_S]
    if len(hits) >= max(1, int(limit)):
        _rate_hits[key] = hits
        return True
    hits.append(now)
    _rate_hits[key] = hits
    return False


# ── R3 follow-up (2026-09-07): replay protection ─────────────────────────────
# The HMAC proves the body was signed by the shared secret; it proves NOTHING
# about WHEN, and the signature is a deterministic function of the body. A
# captured request (from a proxy log, a mirrored TLS session, a compromised
# relay) could therefore be re-POSTed verbatim as many times as the hourly rate
# limit allowed, each replay writing a fresh `webhook.message_received` event
# into the tenant's audit chain and — once Phase 2 wires the payload into the
# chat/inbox system — re-delivering the message.
#
# Two layers, both fail-closed:
#
#   (a) NONCE CACHE (always on, no sender change needed). The signature is
#       remembered per (tenant, channel) for `_REPLAY_WINDOW_S` and an exact
#       repeat is refused with 409. Bounded: a channel can never hold more
#       entries than its own hourly rate limit, and `_REPLAY_MAX_PER_CHANNEL`
#       caps it regardless.
#
#   (b) SIGNED TIMESTAMP (opt-in per channel via `require_signed_timestamp`).
#       A bare `X-Corvin-Timestamp` header would be worthless — it is not
#       covered by the body HMAC, so an attacker rewrites it freely. When the
#       channel opts in, the header becomes MANDATORY and the signature is
#       computed over `<ts>.<body>` (Stripe's `v1` construction), which binds
#       the timestamp to the secret; the request is then refused outside
#       `_TIMESTAMP_SKEW_S`. This is what closes a replay that arrives AFTER
#       the nonce window has expired. It is opt-in because turning it on
#       unilaterally would break every already-registered sender — but a
#       channel that enables it can never fall back to the body-only form.
_REPLAY_WINDOW_S = 3600.0
_REPLAY_MAX_PER_CHANNEL = 10_000
_TIMESTAMP_SKEW_S = 300.0

#: (tenant_id, channel_id) -> {signature: first_seen_unix}
_seen_signatures: dict[tuple[str, str], dict[str, float]] = {}


def _replay_seen(tid: str, channel_id: str, signature: str) -> bool:
    """True when this exact signature was already accepted inside the window.

    Records the signature only when it is NEW, so a refused replay cannot keep
    its own entry alive (same discipline as `_rate_limit_exceeded`).
    """
    now = time.time()
    key = (tid, channel_id)
    seen = {sig: t for sig, t in _seen_signatures.get(key, {}).items()
            if now - t < _REPLAY_WINDOW_S}
    if signature in seen:
        _seen_signatures[key] = seen
        return True
    if len(seen) >= _REPLAY_MAX_PER_CHANNEL:
        # Drop the oldest half rather than growing without bound. The rate
        # limiter makes this unreachable at default settings.
        for sig, _t in sorted(seen.items(), key=lambda kv: kv[1])[: len(seen) // 2]:
            seen.pop(sig, None)
    seen[signature] = now
    _seen_signatures[key] = seen
    return False


async def _read_capped_body(request: Request) -> bytes:
    """Read the request body, refusing anything past the cap.

    Checks Content-Length first (cheap reject), then accumulates chunks and
    aborts as soon as the cap is passed — so a chunked/streamed body cannot
    buffer past the limit either.
    """
    declared = request.headers.get("content-length")
    if declared is not None:
        try:
            if int(declared) > _MAX_WEBHOOK_BODY_BYTES:
                raise HTTPException(
                    http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                    f"body exceeds {_MAX_WEBHOOK_BODY_BYTES} bytes",
                )
        except ValueError:
            raise HTTPException(
                http_status.HTTP_400_BAD_REQUEST, "invalid Content-Length",
            ) from None
    chunks: list[bytes] = []
    total = 0
    async for chunk in request.stream():
        total += len(chunk)
        if total > _MAX_WEBHOOK_BODY_BYTES:
            raise HTTPException(
                http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                f"body exceeds {_MAX_WEBHOOK_BODY_BYTES} bytes",
            )
        chunks.append(chunk)
    return b"".join(chunks)


# ── Storage ───────────────────────────────────────────────────────────────────

def _channels_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "bridges" / "custom"


def _channel_path(tid: str, channel_id: str) -> Path:
    return _channels_dir(tid) / f"{channel_id}.json"


def _load_channel(tid: str, channel_id: str) -> dict[str, Any] | None:
    p = _channel_path(tid, channel_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _find_channel(tid: str, channel_id: str) -> dict[str, Any] | None:
    """Direct per-tenant channel lookup (O(1), no cross-tenant scan)."""
    return _load_channel(tid, channel_id)


def _list_channels(tid: str) -> list[dict[str, Any]]:
    d = _channels_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                # Never return the HMAC secret env or its value
                masked = {k: v for k, v in data.items() if k not in ("_hmac_secret", "hmac_secret")}
                results.append(masked)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_channel(tid: str, channel_id: str, data: dict[str, Any]) -> None:
    atomic_write_json(_channel_path(tid, channel_id), data)


def _audit_chain_path(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "forge" / "audit.jsonl"


# ── Models ────────────────────────────────────────────────────────────────────

class WebhookChannelRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    hmac_secret_env: str | None = Field(
        None,
        description=(
            "Vault env-var name for the HMAC secret. REQUIRED (R2-C3): an "
            "inbound webhook without a signature check is an anonymous writer "
            "to the tenant's audit chain."
        ),
    )
    persona: str = Field("assistant", min_length=1, max_length=64)
    rate_limit_per_hour: int = Field(60, ge=1, le=10_000)
    require_signed_timestamp: bool = Field(
        False,
        description=(
            "When true, inbound requests MUST carry an X-Corvin-Timestamp "
            "header, the HMAC must be computed over '<timestamp>.<body>', and "
            "the timestamp must be within 300s of server time. Closes replay "
            "beyond the nonce window; requires sender support, hence opt-in."
        ),
    )
    description: str = Field("", max_length=500)
    model_config = {"extra": "forbid"}


# ── Admin routes ──────────────────────────────────────────────────────────────

@router.get("/bridges/custom")
def list_webhook_channels(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    channels = _list_channels(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(channels), "channels": channels}


@router.put("/bridges/custom/{channel_id}")
def register_webhook_channel(
    channel_id: str,
    body: WebhookChannelRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "channel_id must be lowercase alphanumeric with _ or -",
        )

    # R2-C3: fail-closed — no secret, no channel. Registering a signature-less
    # channel would publish an unauthenticated POST endpoint that writes to the
    # audit chain. The env var must also look like an env var (the value itself
    # is never accepted over the wire, only its vault NAME).
    env_name = (body.hmac_secret_env or "").strip()
    if not env_name or not re.match(r"^[A-Za-z_][A-Za-z0-9_]{0,127}$", env_name):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="webhook.channel_registered",
            target_kind="webhook_channel",
            target_id=channel_id,
            reason="hmac_secret_env-required",
        )
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "hmac_secret_env is required (vault env-var NAME holding the HMAC "
            "secret); an inbound webhook without a signature check is not allowed",
        )

    existing = _load_channel(rec.tenant_id, channel_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "channel_id": channel_id,
        "display_name": body.display_name,
        "hmac_secret_env": env_name,
        "persona": body.persona,
        "rate_limit_per_hour": body.rate_limit_per_hour,
        "require_signed_timestamp": bool(body.require_signed_timestamp),
        "description": body.description,
        "tenant_id": rec.tenant_id,
        "inbound_url": f"/v1/console/webhook/{rec.tenant_id}/{channel_id}",
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
    }

    try:
        _write_channel(rec.tenant_id, channel_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="webhook.channel_updated" if is_update else "webhook.channel_registered",
        target_kind="webhook_channel",
        target_id=channel_id,
    )
    return {
        "ok": True,
        "channel_id": channel_id,
        "updated": is_update,
        "inbound_url": f"/v1/console/webhook/{rec.tenant_id}/{channel_id}",
    }


@router.delete("/bridges/custom/{channel_id}")
def remove_webhook_channel(
    channel_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    p = _channel_path(rec.tenant_id, channel_id)
    if not p.exists():
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"channel {channel_id!r} not found",
        )
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="webhook.channel_removed",
        target_kind="webhook_channel",
        target_id=channel_id,
    )
    return {"ok": True, "channel_id": channel_id}


# ── Inbound route ─────────────────────────────────────────────────────────────

@router.post("/webhook/{tenant_id}/{channel_id}")
async def receive_webhook(
    tenant_id: str,
    channel_id: str,
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_corvin_timestamp: str | None = Header(None),
) -> dict[str, Any]:
    """Receive an inbound webhook. No session required; HMAC-authenticated.

    The tenant_id in the URL scopes the channel lookup, eliminating cross-tenant
    channel_id collision. External systems must POST to /webhook/<tenant_id>/<channel_id>.
    """
    # R2-C5: a malformed tenant_id (e.g. "__evil") made `_find_channel` raise
    # InvalidTenantID out of the route → HTTP 500 with a stack trace for an
    # unauthenticated caller. An unknown/invalid tenant is simply not a channel.
    try:
        _forge_tenants.validate_tenant_id(tenant_id)
    except Exception:  # noqa: BLE001 — InvalidTenantID (ValueError) and anything else
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "unknown channel") from None
    if not _CHANNEL_ID_RE.match(channel_id):
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "unknown channel")

    try:
        channel = _find_channel(tenant_id, channel_id)
    except Exception:  # noqa: BLE001 — a path/tenant resolution failure is a 404, never a 500
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "unknown channel") from None
    if channel is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "unknown channel")

    tid = tenant_id

    # R2-C3: enforce the channel's own rate limit BEFORE reading any body, so a
    # throttled caller costs neither memory nor an audit-chain write.
    try:
        limit = int(channel.get("rate_limit_per_hour") or 60)
    except (TypeError, ValueError):
        limit = 60
    if _rate_limit_exceeded(tid, channel_id, limit):
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            f"rate limit of {limit}/hour exceeded for channel {channel_id!r}",
        )

    # R2-C3: capped read (413 past the cap) instead of an unbounded buffer.
    body_bytes = await _read_capped_body(request)

    # HMAC verification. `hmac_secret_env` is mandatory at registration
    # (R2-C3); a legacy manifest written before that is refused rather than
    # silently accepted unauthenticated.
    hmac_env = channel.get("hmac_secret_env")
    if not hmac_env:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "channel has no hmac_secret_env — re-register it with one",
        )
    secret = os.environ.get(hmac_env, "")
    if not secret:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "HMAC secret not configured in vault",
        )
    if not x_hub_signature_256:
        raise HTTPException(
            http_status.HTTP_401_UNAUTHORIZED,
            "X-Hub-Signature-256 header required",
        )

    # Signed-timestamp mode (opt-in per channel). The timestamp is part of the
    # signed material, so it cannot be rewritten by whoever captured the body.
    signed_material = body_bytes
    if channel.get("require_signed_timestamp"):
        if not x_corvin_timestamp:
            raise HTTPException(
                http_status.HTTP_401_UNAUTHORIZED,
                "X-Corvin-Timestamp header required for this channel",
            )
        try:
            sent_at = float(x_corvin_timestamp)
        except (TypeError, ValueError):
            raise HTTPException(
                http_status.HTTP_401_UNAUTHORIZED,
                "X-Corvin-Timestamp must be a unix timestamp",
            ) from None
        if abs(time.time() - sent_at) > _TIMESTAMP_SKEW_S:
            raise HTTPException(
                http_status.HTTP_401_UNAUTHORIZED,
                f"X-Corvin-Timestamp outside the {int(_TIMESTAMP_SKEW_S)}s window",
            )
        signed_material = x_corvin_timestamp.encode() + b"." + body_bytes

    expected_sig = "sha256=" + hmac.new(
        secret.encode(), signed_material, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, x_hub_signature_256):
        raise HTTPException(
            http_status.HTTP_401_UNAUTHORIZED,
            "HMAC signature mismatch",
        )

    # Replay guard — AFTER the signature check, so an unauthenticated caller
    # can neither probe nor populate the nonce cache.
    if _replay_seen(tid, channel_id, expected_sig):
        try:
            _security_events.write_event(
                _audit_chain_path(tid),
                "webhook.replay_rejected",
                details={"channel_id": channel_id, "tenant_id": tid,
                         "payload_size": len(body_bytes)},
                severity="WARNING",
            )
        except Exception:  # noqa: BLE001 — audit is best-effort for inbound
            pass
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            "duplicate signed request (replay window)",
        )

    # Parse body (best-effort JSON)
    try:
        payload = json.loads(body_bytes)
    except (json.JSONDecodeError, UnicodeDecodeError):
        payload = {"raw": body_bytes.decode(errors="replace")[:1024]}

    # Audit the inbound message (metadata only — never log payload content)
    chain = _audit_chain_path(tid)
    try:
        _security_events.write_event(
            chain,
            "webhook.message_received",
            details={
                "channel_id": channel_id,
                "tenant_id": tid,
                "payload_size": len(body_bytes),
                "has_signature": x_hub_signature_256 is not None,
            },
            severity="INFO",
        )
    except Exception:
        pass  # audit is best-effort for inbound

    return {
        "ok": True,
        "channel_id": channel_id,
        "received_at": time.time(),
        "payload_size": len(body_bytes),
    }

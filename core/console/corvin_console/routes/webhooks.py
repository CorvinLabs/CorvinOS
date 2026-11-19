"""Generic Webhook Bridge (ADR-0124 M7).

Operators register inbound webhook channels. External systems POST
to /webhook/{tenant_id}/{channel_id}; the bridge verifies HMAC-SHA256 and
records the receipt in the audit chain.

What it does NOT do: the payload is not forwarded anywhere - there is no
chat/inbox delivery on this build. The inbound route therefore answers
``202 Accepted`` with ``"delivered": false`` (verified and recorded, not
delivered), never a bare ``200 {"ok": true}`` that reads as "message delivered".

Routes (admin — require_csrf):
  GET    /bridges/custom                     list registered webhook channels
  PUT    /bridges/custom/{channel_id}        register or update
  DELETE /bridges/custom/{channel_id}        remove

Route (inbound — HMAC-authenticated, no session required):
  POST   /webhook/{channel_id}               receive external webhook
"""
from __future__ import annotations

import asyncio
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


def _channel_limit_reached(tid: str, channel_id: str, limit: int) -> bool:
    """True when the channel's ACCEPTED deliveries already fill its hourly
    limit. Records nothing - see :func:`_record_delivery`."""
    now = time.time()
    key = (tid, channel_id)
    hits = [t for t in _rate_hits.get(key, ()) if now - t < _RATE_WINDOW_S]
    if hits:
        _rate_hits[key] = hits
    else:
        _rate_hits.pop(key, None)
    return len(hits) >= max(1, int(limit))


def _record_delivery(tid: str, channel_id: str) -> None:
    """Count one SIGNED, accepted delivery against the channel's limit."""
    _rate_hits.setdefault((tid, channel_id), []).append(time.time())


# ── Review 2026-11-19 round 3 (R3-NEW-21): unauthenticated attempts ──────────
# The channel limit used to be booked BEFORE the signature check, so anyone who
# knew a channel URL could send ``limit`` unsigned POSTs per hour and lock the
# legitimate sender out (every signed delivery then got 429). The channel
# budget is now spent only by requests whose signature verified. Attempts that
# fail authentication are counted in a separate bucket per
# (tenant, channel, client address) with the same hourly size: a caller who
# keeps failing is refused before the body is read, and nobody else is.
# Behind a reverse proxy every caller shares the proxy's address, so there the
# bucket throttles unauthenticated traffic as a whole - it still never touches
# the channel's own delivery budget.
_UNAUTH_MAX_KEYS = 50_000
_unauth_hits: dict[tuple[str, str, str], list[float]] = {}


def _client_addr(request: Request) -> str:
    client = getattr(request, "client", None)
    return str(getattr(client, "host", "") or "unknown")


def _unauth_limit_reached(tid: str, channel_id: str, addr: str, limit: int) -> bool:
    now = time.time()
    key = (tid, channel_id, addr)
    hits = [t for t in _unauth_hits.get(key, ()) if now - t < _RATE_WINDOW_S]
    if hits:
        _unauth_hits[key] = hits
    else:
        _unauth_hits.pop(key, None)
    return len(hits) >= max(1, int(limit))


def _record_unauth(tid: str, channel_id: str, addr: str) -> None:
    if len(_unauth_hits) >= _UNAUTH_MAX_KEYS:
        # Bounded memory under address churn: drop buckets whose window passed,
        # and if that frees nothing, the oldest half.
        now = time.time()
        for k in [k for k, v in _unauth_hits.items() if not v or now - v[-1] >= _RATE_WINDOW_S]:
            _unauth_hits.pop(k, None)
        if len(_unauth_hits) >= _UNAUTH_MAX_KEYS:
            for k in sorted(_unauth_hits, key=lambda k: _unauth_hits[k][-1])[: _UNAUTH_MAX_KEYS // 2]:
                _unauth_hits.pop(k, None)
    _unauth_hits.setdefault((tid, channel_id, addr), []).append(time.time())


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
    its own entry alive.
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

@router.post("/webhook/{tenant_id}/{channel_id}", status_code=http_status.HTTP_202_ACCEPTED)
async def receive_webhook(
    tenant_id: str,
    channel_id: str,
    request: Request,
    x_hub_signature_256: str | None = Header(None),
    x_corvin_timestamp: str | None = Header(None),
) -> dict[str, Any]:
    """Receive an inbound webhook. No session required; HMAC-authenticated.

    Answers ``202 Accepted``: the signature was verified and the receipt is in
    the audit chain. The payload is NOT forwarded to a chat or inbox - that
    delivery does not exist on this build, and the body says so
    (``"delivered": false``).

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

    # Two cheap refusals BEFORE any body is read (no memory, no chain write):
    #   * the channel's accepted deliveries already fill its hourly limit, or
    #   * THIS caller's failed attempts fill its own bucket.
    # Neither books anything here: the channel budget is spent after the
    # signature verified, the caller bucket when authentication fails.
    try:
        limit = int(channel.get("rate_limit_per_hour") or 60)
    except (TypeError, ValueError):
        limit = 60
    addr = _client_addr(request)
    if (_channel_limit_reached(tid, channel_id, limit)
            or _unauth_limit_reached(tid, channel_id, addr, limit)):
        raise HTTPException(
            http_status.HTTP_429_TOO_MANY_REQUESTS,
            f"rate limit of {limit}/hour exceeded for channel {channel_id!r}",
        )

    def _unauthenticated(status_code: int, detail: str) -> HTTPException:
        _record_unauth(tid, channel_id, addr)
        return HTTPException(status_code, detail)

    # R2-C3: capped read (413 past the cap) instead of an unbounded buffer.
    body_bytes = await _read_capped_body(request)

    # HMAC verification. `hmac_secret_env` is mandatory at registration
    # (R2-C3); a legacy manifest written before that is refused rather than
    # silently accepted unauthenticated.
    hmac_env = channel.get("hmac_secret_env")
    if not hmac_env:
        raise _unauthenticated(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "channel has no hmac_secret_env — re-register it with one",
        )
    secret = os.environ.get(hmac_env, "")
    if not secret:
        raise _unauthenticated(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "HMAC secret not configured in vault",
        )
    if not x_hub_signature_256:
        raise _unauthenticated(
            http_status.HTTP_401_UNAUTHORIZED,
            "X-Hub-Signature-256 header required",
        )

    # Signed-timestamp mode (opt-in per channel). The timestamp is part of the
    # signed material, so it cannot be rewritten by whoever captured the body.
    signed_material = body_bytes
    if channel.get("require_signed_timestamp"):
        if not x_corvin_timestamp:
            raise _unauthenticated(
                http_status.HTTP_401_UNAUTHORIZED,
                "X-Corvin-Timestamp header required for this channel",
            )
        try:
            sent_at = float(x_corvin_timestamp)
        except (TypeError, ValueError):
            raise _unauthenticated(
                http_status.HTTP_401_UNAUTHORIZED,
                "X-Corvin-Timestamp must be a unix timestamp",
            ) from None
        if abs(time.time() - sent_at) > _TIMESTAMP_SKEW_S:
            raise _unauthenticated(
                http_status.HTTP_401_UNAUTHORIZED,
                f"X-Corvin-Timestamp outside the {int(_TIMESTAMP_SKEW_S)}s window",
            )
        signed_material = x_corvin_timestamp.encode() + b"." + body_bytes

    expected_sig = "sha256=" + hmac.new(
        secret.encode(), signed_material, hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected_sig, x_hub_signature_256):
        raise _unauthenticated(
            http_status.HTTP_401_UNAUTHORIZED,
            "HMAC signature mismatch",
        )

    # Authenticated: this delivery spends the channel's hourly budget (replays
    # included - they carry a valid signature and must not be free).
    _record_delivery(tid, channel_id)

    # Replay guard — AFTER the signature check, so an unauthenticated caller
    # can neither probe nor populate the nonce cache.
    if _replay_seen(tid, channel_id, expected_sig):
        try:
            await asyncio.to_thread(
                _security_events.write_event,
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

    # The body is not parsed: nothing consumes it. Only its size is recorded.
    # Audit the inbound message (metadata only — never log payload content)
    chain = _audit_chain_path(tid)
    recorded = True
    try:
        # A chain write locks and fsyncs: worker thread, not the event loop.
        await asyncio.to_thread(
            _security_events.write_event,
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
    except Exception:  # noqa: BLE001 - reported to the sender as recorded: false
        recorded = False

    return {
        "ok": True,
        "accepted": True,
        # Verified and recorded - not delivered anywhere (no inbox forwarding
        # on this build). A sender must not read this as "message delivered".
        "delivered": False,
        "delivery": "not_available",
        # Whether the receipt committed to the audit chain.
        "recorded": recorded,
        "detail": (
            ("Signature verified and receipt recorded in the audit log. "
             if recorded else
             "Signature verified; the receipt could not be recorded in the audit log. ")
            + "Forwarding into a chat is not available on this build."),
        "channel_id": channel_id,
        "received_at": time.time(),
        "payload_size": len(body_bytes),
    }

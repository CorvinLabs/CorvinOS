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
from forge import security_events as _security_events  # noqa: E402

router = APIRouter()

_CHANNEL_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


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
        description="Vault env-var name for HMAC secret (omit for no signature check)",
    )
    persona: str = Field("assistant", min_length=1, max_length=64)
    rate_limit_per_hour: int = Field(60, ge=1, le=10_000)
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

    existing = _load_channel(rec.tenant_id, channel_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "channel_id": channel_id,
        "display_name": body.display_name,
        "hmac_secret_env": body.hmac_secret_env,
        "persona": body.persona,
        "rate_limit_per_hour": body.rate_limit_per_hour,
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
) -> dict[str, Any]:
    """Receive an inbound webhook. No session required; HMAC-authenticated.

    The tenant_id in the URL scopes the channel lookup, eliminating cross-tenant
    channel_id collision. External systems must POST to /webhook/<tenant_id>/<channel_id>.
    """
    channel = _find_channel(tenant_id, channel_id)
    if channel is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "unknown channel")

    tid = tenant_id
    body_bytes = await request.body()

    # HMAC verification
    hmac_env = channel.get("hmac_secret_env")
    if hmac_env:
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
        expected_sig = "sha256=" + hmac.new(
            secret.encode(), body_bytes, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(expected_sig, x_hub_signature_256):
            raise HTTPException(
                http_status.HTTP_401_UNAUTHORIZED,
                "HMAC signature mismatch",
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

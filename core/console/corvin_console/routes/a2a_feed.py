"""Layer 38 — Agent Hub live feed (A2A messages with media).

Read/send surface over the tenant-local A2A content store
(``corvin_operator/bridges/shared/a2a_feed.py``). The audit chain stays the
metadata-only proof of every exchange; this is the readable view of the
same messages — instruction text, peer responses and attachments — which
the Agent Hub renders as a chat.

Routes (all behind a live session; mutations additionally need CSRF):

  GET    /a2a/feed                  messages (oldest-first) + peer directory
  GET    /a2a/feed/blob/{sha256}    one stored attachment
  POST   /a2a/feed/send             send a message (+ attachments) to a peer
  DELETE /a2a/feed                  wipe the store (audited ``A2A.feed_cleared``)

Blob serving never trusts the peer-declared MIME type for anything that a
browser could execute: only an allowlist of passive media types is served
inline; everything else goes out as ``application/octet-stream`` download,
always with ``nosniff`` and a ``sandbox`` CSP.
"""
from __future__ import annotations

import base64
import hashlib
from concurrent.futures import ThreadPoolExecutor
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import auth as session_auth
from ..deps import require_session_csrf_on_mutation
from . import remote_trigger_log as _rtl  # sys.path + peer-dir resolvers

import os as _os

_forge_paths = _rtl._forge_paths

import a2a_feed as _feed  # type: ignore[import-not-found]  # noqa: E402

router = APIRouter(dependencies=[Depends(require_session_csrf_on_mutation)])

Session = Annotated[session_auth.SessionRecord, Depends(require_session_csrf_on_mutation)]

# Passive media a browser renders without executing anything. SVG and HTML
# are deliberately absent: both can carry script on this origin.
_INLINE_MIME = frozenset({
    "image/png", "image/jpeg", "image/gif", "image/webp", "image/avif",
    "audio/mpeg", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm",
    "audio/mp4", "audio/aac", "audio/flac",
    "video/mp4", "video/webm", "video/ogg",
    "application/pdf",
    "text/plain", "text/csv", "text/markdown", "application/json",
})


# ── peers ─────────────────────────────────────────────────────────────────

def _read_json(path) -> dict:
    import json
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _peers() -> list[dict[str, Any]]:
    """Known A2A peers — endpoints (we can send) merged with origins (they send)."""
    peers: dict[str, dict[str, Any]] = {}
    for kind, d in (("endpoint", _rtl._endpoints_dir()), ("origin", _rtl._origins_dir())):
        if not d.is_dir():
            continue
        for f in sorted(d.glob("*.json")):
            cfg = _read_json(f)
            pid = cfg.get("endpoint_id") if kind == "endpoint" else cfg.get("origin_id")
            pid = str(pid or f.stem)
            p = peers.setdefault(pid, {
                "peer_id": pid, "label": None, "state": None,
                "can_send": False, "can_receive": False, "enabled": False,
            })
            p["label"] = p["label"] or _rtl._label_out(cfg)
            p["state"] = p["state"] or cfg.get("state")
            p["enabled"] = p["enabled"] or bool(cfg.get("enabled", False))
            if kind == "endpoint":
                p["can_send"] = bool(cfg.get("enabled", False))
            else:
                p["can_receive"] = bool(cfg.get("enabled", False))
                p["spawn_worker"] = bool(cfg.get("spawn_worker", False))
    return sorted(peers.values(), key=lambda p: (p["label"] or p["peer_id"]).lower())


# ── read ──────────────────────────────────────────────────────────────────

def _a2a_tenant(rec: session_auth.SessionRecord) -> str:
    """The tenant whose A2A feed this session may read.

    A2A is host-scoped: the receiver and every sender write the feed of the
    PROCESS tenant (``CORVIN_TENANT_ID``, default ``_default``) and the peer
    directories are not tenant-partitioned. Reading the session's tenant
    instead showed any other tenant an empty feed and let its "clear" audit a
    deletion of nothing. So the feed is bound to the host tenant, and a
    session of another tenant is refused rather than shown a wrong view.
    """
    host = (_os.environ.get("CORVIN_TENANT_ID") or "_default").strip() or "_default"
    if rec.tenant_id != host:
        raise HTTPException(
            status_code=403,
            detail=f"A2A on this instance belongs to tenant {host!r}",
        )
    return host


@router.get("/a2a/feed")
def a2a_feed(
    rec: Session,
    after: int | None = Query(default=None, ge=0, description="live cursor: messages with seq > after, oldest first"),
    before: int | None = Query(default=None, ge=1, description="history: messages with seq < before, newest page"),
    since: float | None = Query(default=None, ge=0, description="legacy wall-clock filter"),
    limit: int = Query(default=200, ge=1, le=1000),
    peer_id: str | None = Query(default=None, max_length=128),
) -> dict[str, Any]:
    tid = _a2a_tenant(rec)
    if since is not None and after is None and before is None:
        msgs, more = _feed.read(since=since, limit=limit, peer_id=peer_id, tenant_id=tid), False
    else:
        msgs, more = _feed.read_page(after=after, before=before, limit=limit,
                                     peer_id=peer_id, tenant_id=tid)
    return {
        "tenant_id": tid,
        "ts": time.time(),
        "retention_days": _feed.RETENTION_DAYS,
        "messages": msgs,
        "has_more": more,
        "last_seq": max((int(m.get("seq") or 0) for m in msgs), default=after or 0),
        "peers": _peers(),
    }


@router.get("/a2a/feed/blob/{sha256}")
def a2a_feed_blob(
    rec: Session,
    sha256: str,
    name: str | None = Query(default=None, max_length=128),
    mime: str | None = Query(default=None, max_length=128),
) -> FileResponse:
    path = _feed.blob_path(sha256, tenant_id=_a2a_tenant(rec))
    if path is None:
        raise HTTPException(status_code=404, detail="attachment not found")
    declared = (mime or "").split(";")[0].strip().lower()
    inline = declared in _INLINE_MIME
    safe_name = "".join(c for c in (name or sha256[:16]) if c.isalnum() or c in "._-")[:128] or "attachment"
    headers = {
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox; default-src 'none'; style-src 'unsafe-inline'",
        "Cache-Control": "private, max-age=86400, immutable",
        "Content-Disposition": f'{"inline" if inline else "attachment"}; filename="{safe_name}"',
    }
    return FileResponse(
        path,
        media_type=declared if inline else "application/octet-stream",
        headers=headers,
    )


# ── send ──────────────────────────────────────────────────────────────────

class _OutAttachment(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    mime: str = Field(default="application/octet-stream", max_length=128)
    content_b64: str


class _SendBody(BaseModel):
    peer_id: str = Field(min_length=1, max_length=128)
    text: str = Field(default="", max_length=_feed.MAX_TEXT_CHARS)
    attachments: list[_OutAttachment] = Field(default_factory=list)
    # None = the sender's default: never shorter than the peer's worker budget
    # (envelope ttl + margin) — a fixed 90 s lost replies of long peer tasks.
    timeout_s: int | None = Field(default=None, ge=5, le=3600)


# Sends queue here instead of one thread each: bounded concurrency, nothing
# dropped (a burst to many peers waits its turn rather than spawning N threads).
_SEND_POOL = ThreadPoolExecutor(max_workers=16, thread_name_prefix="a2a-feed-send")


def _send_in_background(peer_id: str, text: str, atts: list[dict], timeout_s: int | None) -> None:
    try:
        from remote_trigger_sender import RemoteTriggerSender  # type: ignore[import-not-found]
        RemoteTriggerSender().send(peer_id, text, attachments=atts or None, timeout_s=timeout_s)
    except Exception:
        # send() records its own failure in the feed and the audit chain; an
        # exception here means the sender could not even be built.
        pass


@router.post("/a2a/feed/send", status_code=202)
def a2a_feed_send(rec: Session, body: _SendBody) -> dict[str, Any]:
    """Queue a message to a peer. Returns immediately; the outbound message
    and, later, the peer's response appear in ``GET /a2a/feed``."""
    _a2a_tenant(rec)
    if not body.text.strip() and not body.attachments:
        raise HTTPException(status_code=422, detail="message is empty")
    import unicodedata as _ud
    if len(_ud.normalize("NFKC", body.text).encode("utf-8")) > 16 * 1024:
        # The receiving agent refuses more (after NFKC) — say so here, where
        # the user can shorten it or attach the text as a file (round 7).
        raise HTTPException(status_code=422,
                            detail="Message too long (max 16 KiB) — attach longer text as a file")
    peer = next((p for p in _peers() if p["peer_id"] == body.peer_id), None)
    if peer is None or not peer["can_send"]:
        raise HTTPException(status_code=404, detail="no enabled endpoint for this peer")

    from a2a_attachments import AttachmentError, validate_attachments  # type: ignore[import-not-found]
    atts: list[dict] = []
    for a in body.attachments:
        try:
            raw = base64.b64decode(a.content_b64, validate=True)
        except Exception:
            raise HTTPException(status_code=422, detail=f"attachment {a.name!r} is not valid base64")
        atts.append({"name": a.name, "mime": a.mime or "application/octet-stream",
                     "sha256": hashlib.sha256(raw).hexdigest(),
                     "content_b64": a.content_b64})
    if atts:
        try:
            validate_attachments(atts)
        except AttachmentError as exc:
            raise HTTPException(status_code=422, detail=f"attachment rejected: {exc}")

    _SEND_POOL.submit(_send_in_background, body.peer_id, body.text, atts, body.timeout_s)
    return {"accepted": True, "peer_id": body.peer_id}


# ── erase ─────────────────────────────────────────────────────────────────

@router.delete("/a2a/feed")
def a2a_feed_clear(rec: Session) -> dict[str, Any]:
    """Wipe the store. Audit-FIRST: no chain record, no deletion."""
    tid = _a2a_tenant(rec)
    pending = len(_feed.read(limit=0, tenant_id=tid))
    try:
        from forge.security_events import write_event  # type: ignore[import-not-found]
        write_event(
            _forge_paths.tenant_audit_chain(tid), "A2A.feed_cleared",
            severity="WARNING",
            details={"messages_removed": pending, "reason": "operator_request"},
        )
    except Exception:
        raise HTTPException(status_code=503, detail="audit chain unavailable — feed not cleared")
    msgs, blobs = _feed.clear(tenant_id=tid)
    return {"cleared": True, "messages_removed": msgs, "blobs_removed": blobs}

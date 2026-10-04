"""Pending A2A friendship-token requests staged from console chat (ADR-2216).

A chat-triggered "generate a friendship token" MCP tool is the same risk
shape as the existing chat-triggered ``a2a_send`` (ADR-2099 Phase 2): a chat
turn can be steered by injected content, and a friendship token IS a
credential — the 256-bit shared key embedded in it is exactly what lets a
future peer complete a pairing. Minting one is therefore gated the same way
a send is: the MCP tool handler NEVER calls
:func:`a2a_friendship.create_friendship_token` itself. It only writes a
pending REQUEST (label/ttl/personas — no key material yet) via
:func:`create_pending_token_request`. The actual token (and its shared key)
is only minted by :func:`pop_pending_token_request`, called exclusively from
``POST /a2a/feed/friendship-token/confirm/{id}`` — a route gated by a real
browser session + CSRF token, the same structural gate
``a2a_chat_pending_send.py`` already documents.

Pending requests expire after ``_PENDING_TTL_SECONDS`` and are never
auto-confirmed — a stale request is simply gone, never silently minted.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

_PENDING_TTL_SECONDS = 600  # 10 minutes — same window as a2a_chat_pending_send
_PENDING_SUBDIR = ("remote_trigger", "pending_chat_friendship_tokens")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_LABEL_LEN = 64


def _pending_dir(tenant_global_dir: Path) -> Path:
    d = tenant_global_dir
    for part in _PENDING_SUBDIR:
        d = d / part
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(pending_id: str) -> str | None:
    """Reject path-traversal / non-token ids — same rationale as
    ``a2a_chat_pending_send._safe_id``: this id is echoed back from a chat
    tool call result, so it is attacker-influenced even though the server
    only ever generates ``token_urlsafe`` ids itself."""
    if isinstance(pending_id, str) and _ID_RE.fullmatch(pending_id):
        return pending_id
    return None


def create_pending_token_request(
    tenant_global_dir: Path,
    *,
    label: str | None = None,
    ttl_hours: float = 720.0,
    personas: list[str] | None = None,
    requested_by: str = "chat_mcp_tool",
) -> dict[str, Any]:
    """Stage a friendship-token REQUEST for operator confirmation.

    Carries no key material — only the parameters the eventual token would
    use. The real token is minted only on confirm (see module docstring).
    """
    pending_id = secrets.token_urlsafe(16)
    record: dict[str, Any] = {
        "pending_id": pending_id,
        "label": (str(label)[:_MAX_LABEL_LEN] if label else None),
        "ttl_hours": max(0.0, float(ttl_hours)),
        "personas": [str(p)[:64] for p in (personas or [])][:16],
        "created_at": time.time(),
        "requested_by": requested_by,
    }
    path = _pending_dir(tenant_global_dir) / f"{pending_id}.json"
    tmp = path.with_suffix(".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(record, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return record


def _read_valid(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        rec = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    created_at = rec.get("created_at", 0)
    if not isinstance(created_at, (int, float)) or time.time() - created_at > _PENDING_TTL_SECONDS:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        return None
    return rec


def peek_pending_token_request(tenant_global_dir: Path, pending_id: str) -> dict[str, Any] | None:
    """Read without consuming — lets the confirm-dialog UI render the preview."""
    safe = _safe_id(pending_id)
    if safe is None:
        return None
    return _read_valid(_pending_dir(tenant_global_dir) / f"{safe}.json")


def pop_pending_token_request(tenant_global_dir: Path, pending_id: str) -> dict[str, Any] | None:
    """Read + delete atomically — one-time use, no replay of a confirm click."""
    safe = _safe_id(pending_id)
    if safe is None:
        return None
    path = _pending_dir(tenant_global_dir) / f"{safe}.json"
    rec = _read_valid(path)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass
    return rec


def list_pending_token_requests(tenant_global_dir: Path) -> list[dict[str, Any]]:
    """All non-expired pending token requests for this tenant, newest first.

    Same rationale as ``a2a_chat_pending_send.list_pending_sends``: no
    tool_result streaming exists, so this is how a console UI discovers
    what a chat turn staged.
    """
    d = _pending_dir(tenant_global_dir)
    out: list[dict[str, Any]] = []
    for p in d.glob("*.json"):
        rec = _read_valid(p)
        if rec is not None:
            out.append(rec)
    return sorted(out, key=lambda r: r.get("created_at", 0), reverse=True)


def sweep_expired(tenant_global_dir: Path) -> int:
    """Delete expired pending requests. Returns the count removed.

    Best-effort housekeeping, same as ``a2a_chat_pending_send.sweep_expired``
    — not required for correctness, since ``_read_valid`` already refuses an
    expired record.
    """
    removed = 0
    d = _pending_dir(tenant_global_dir)
    now = time.time()
    for p in d.glob("*.json"):
        try:
            rec = json.loads(p.read_text(encoding="utf-8"))
            created_at = rec.get("created_at", 0)
        except Exception:
            created_at = 0
        if not isinstance(created_at, (int, float)) or now - created_at > _PENDING_TTL_SECONDS:
            try:
                p.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed

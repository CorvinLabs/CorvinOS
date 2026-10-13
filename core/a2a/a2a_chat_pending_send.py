"""Pending A2A sends staged from console chat (ADR-2099 Phase 2, gated).

A chat-triggered ``a2a_send`` MCP tool is a NEW, LLM-reachable dispatch path
with no operator action in the loop by default — unlike existing CLI/Console
send paths, which always require a deliberate human click. Dialectical review
(2026-10-04) found this a structurally different risk class: a chat turn can
be steered by injected content (a fetched page, an attachment, a relayed A2A
message in context), and an outbound send to a real peer is irreversible.

Design: the MCP tool handler NEVER calls the network. It only writes a
pending record here via :func:`create_pending_send`. The record can only be
turned into a real send by :func:`pop_pending_send` — called exclusively from
``POST /a2a/feed/send/confirm/{id}``, a route gated by a real browser session
+ CSRF token (``require_session_csrf_on_mutation``). The LLM/MCP process has
no code path that reaches that dependency. This is a structural gate, not a
prompt instruction — the compliance note on "the plugin perimeter is
attribution, not security" is exactly why this is enforced in a dependency
the subprocess cannot satisfy, not in a system-prompt admonition.

Pending records expire after ``_PENDING_TTL_SECONDS`` and are never
auto-confirmed — a stale pending send is simply gone, never silently sent.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any

import a2a_pending_claim as _claim

_PENDING_TTL_SECONDS = 600  # 10 minutes — unconfirmed pendings expire, never auto-fire
_MAX_TEXT_BYTES = 16 * 1024  # the receiving agent's cap, in bytes
_PENDING_SUBDIR = ("remote_trigger", "pending_chat_sends")
_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


def _pending_dir(tenant_global_dir: Path) -> Path:
    d = tenant_global_dir
    for part in _PENDING_SUBDIR:
        d = d / part
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(pending_id: str) -> str | None:
    """Reject path-traversal / non-token ids. ``pending_id`` is echoed back
    from a chat tool call result, so it is attacker-influenced even though
    the server only ever generates ``token_urlsafe`` ids itself."""
    if isinstance(pending_id, str) and _ID_RE.fullmatch(pending_id):
        return pending_id
    return None


def create_pending_send(
    tenant_global_dir: Path,
    *,
    peer_id: str,
    text: str,
    attachments: list[dict[str, Any]] | None = None,
    requested_by: str = "chat_mcp_tool",
) -> dict[str, Any]:
    """Stage a send for operator confirmation. Never sends anything itself.

    Returns the pending record (including its ``pending_id``) so the caller
    (the MCP tool handler) can hand it back to the chat turn as a summary the
    user must explicitly confirm in the console UI.
    """
    text = str(text)
    if len(text.encode("utf-8")) > _MAX_TEXT_BYTES:
        # Refused here, not silently cut: the operator would otherwise
        # confirm a message the receiving instance then rejects (its cap is
        # in bytes), or one whose tail was dropped without anyone seeing it.
        raise ValueError(f"text exceeds {_MAX_TEXT_BYTES} bytes")
    pending_id = secrets.token_urlsafe(16)
    record: dict[str, Any] = {
        "pending_id": pending_id,
        "peer_id": str(peer_id)[:128],
        "text": text,
        "attachments": attachments or [],
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


def peek_pending_send(tenant_global_dir: Path, pending_id: str) -> dict[str, Any] | None:
    """Read without consuming — lets the confirm-dialog UI render the preview."""
    safe = _safe_id(pending_id)
    if safe is None:
        return None
    return _read_valid(_pending_dir(tenant_global_dir) / f"{safe}.json")


def pop_pending_send(tenant_global_dir: Path, pending_id: str) -> dict[str, Any] | None:
    """Claim atomically — one-time use: of two racing confirms only one
    gets the record (see ``a2a_pending_claim``)."""
    safe = _safe_id(pending_id)
    if safe is None:
        return None
    d = _pending_dir(tenant_global_dir)
    _claim.sweep_stale_claims(d)
    return _claim.claim(d / f"{safe}.json", _PENDING_TTL_SECONDS)


def list_pending_sends(tenant_global_dir: Path) -> list[dict[str, Any]]:
    """All non-expired pending sends for this tenant, newest first.

    The console has no other way to discover a chat-staged pending_id — the
    backend streams no tool_result event today, so the operator's only
    signal is the assistant's own text echoing the id. This list is what
    lets a "Pending confirmations" UI exist without that streaming change.
    """
    d = _pending_dir(tenant_global_dir)
    out: list[dict[str, Any]] = []
    for p in d.glob("*.json"):
        rec = _read_valid(p)
        if rec is not None:
            out.append(rec)
    return sorted(out, key=lambda r: r.get("created_at", 0), reverse=True)


def sweep_expired(tenant_global_dir: Path) -> int:
    """Delete expired pending records. Returns the count removed.

    Best-effort housekeeping — not required for correctness (``_read_valid``
    already refuses an expired record), but keeps the directory bounded on a
    busy host.
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

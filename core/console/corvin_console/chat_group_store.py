"""Group-chat message store (ADR-2216).

The first group-conversation backend in CorvinOS — nothing existed before
this, not even for internal-only participants (verified by reading the
live code before writing this: no ``participants`` field anywhere under
``core/console/corvin_console``, no message store keyed by a participant
set). Built so a foreign A2A agent is a first-class participant kind from
the first commit (``Participant.kind in {human, agent, a2a_peer}``) —
retrofitting that kind onto an internal-only model later would be a second
migration.

Storage layout, following the ``a2a_chat_pending_send.py`` convention
(atomic writes, mode 0600, ``secrets.token_urlsafe`` ids)::

    <tenant_global_dir>/chat_groups/<group_id>/meta.json       # Group record
    <tenant_global_dir>/chat_groups/<group_id>/messages.jsonl  # append-only

A group's authorization surface is its own participant list — never the
A2A origin's ``allowed_personas`` (that gates whether a peer may talk to
this instance AT ALL; this gates which conversations it currently sees).
Callers MUST re-check ``require_friendship_active`` before admitting an
``a2a_peer`` participant to any write — see ``routes/chat_groups.py``.
"""
from __future__ import annotations

import json
import os
import re
import secrets
import time
from pathlib import Path
from typing import Any, Literal

ParticipantKind = Literal["human", "agent", "a2a_peer"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_TITLE_LEN = 128
_MAX_DISPLAY_NAME_LEN = 64
_MAX_MESSAGE_LEN = 16 * 1024


class ChatGroupError(Exception):
    """Raised on validation failure or missing group/participant."""


def _groups_dir(tenant_global_dir: Path) -> Path:
    d = tenant_global_dir / "chat_groups"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _safe_id(group_id: str) -> str | None:
    if isinstance(group_id, str) and _ID_RE.fullmatch(group_id):
        return group_id
    return None


def _group_dir(tenant_global_dir: Path, group_id: str) -> Path | None:
    safe = _safe_id(group_id)
    if safe is None:
        return None
    return _groups_dir(tenant_global_dir) / safe


def _atomic_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def create_group(
    tenant_global_dir: Path,
    *,
    tenant_id: str,
    title: str,
    created_by_participant_id: str,
    created_by_kind: ParticipantKind = "human",
) -> dict[str, Any]:
    """Create a new group with the creator as its first participant."""
    group_id = secrets.token_urlsafe(16)
    now = time.time()
    record: dict[str, Any] = {
        "group_id": group_id,
        "tenant_id": tenant_id,
        "title": str(title)[:_MAX_TITLE_LEN] or "Untitled group",
        "created_at": now,
        "created_by": created_by_participant_id,
        "participants": [
            {
                "participant_id": created_by_participant_id,
                "kind": created_by_kind,
                "display_name": created_by_participant_id,
                "peer_endpoint_id": None,
                "added_at": now,
                "added_by": created_by_participant_id,
            }
        ],
    }
    d = _groups_dir(tenant_global_dir) / group_id
    d.mkdir(parents=True, exist_ok=True)
    _atomic_write(d / "meta.json", record)
    (d / "messages.jsonl").touch(mode=0o600, exist_ok=True)
    return record


def get_group(tenant_global_dir: Path, group_id: str) -> dict[str, Any] | None:
    d = _group_dir(tenant_global_dir, group_id)
    if d is None:
        return None
    meta = d / "meta.json"
    if not meta.exists():
        return None
    try:
        return json.loads(meta.read_text(encoding="utf-8"))
    except Exception:
        return None


def list_groups(tenant_global_dir: Path, tenant_id: str) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    gdir = _groups_dir(tenant_global_dir)
    for child in sorted(gdir.iterdir()) if gdir.is_dir() else []:
        meta = child / "meta.json"
        if not meta.exists():
            continue
        try:
            rec = json.loads(meta.read_text(encoding="utf-8"))
        except Exception:
            continue
        if rec.get("tenant_id") == tenant_id:
            out.append(rec)
    return sorted(out, key=lambda r: r.get("created_at", 0))


def add_participant(
    tenant_global_dir: Path,
    group_id: str,
    *,
    participant_id: str,
    kind: ParticipantKind,
    display_name: str,
    added_by: str,
    peer_endpoint_id: str | None = None,
) -> dict[str, Any]:
    """Add a participant to a group. Raises ChatGroupError if the group
    doesn't exist or the participant is already a member.

    Callers adding an ``a2a_peer`` participant MUST have already verified
    ``require_friendship_active(peer_endpoint_id)`` — this function does
    NOT re-check A2A origin state; it only maintains the group's own
    membership list (see module docstring on the two-layer authorization
    model)."""
    d = _group_dir(tenant_global_dir, group_id)
    if d is None or not (d / "meta.json").exists():
        raise ChatGroupError("group not found")
    rec = get_group(tenant_global_dir, group_id)
    if rec is None:
        raise ChatGroupError("group not found")
    if any(p["participant_id"] == participant_id for p in rec["participants"]):
        raise ChatGroupError("participant already in group")
    rec["participants"].append({
        "participant_id": participant_id,
        "kind": kind,
        "display_name": str(display_name)[:_MAX_DISPLAY_NAME_LEN],
        "peer_endpoint_id": peer_endpoint_id,
        "added_at": time.time(),
        "added_by": added_by,
    })
    _atomic_write(d / "meta.json", rec)
    return rec


def remove_participant(
    tenant_global_dir: Path, group_id: str, participant_id: str,
) -> dict[str, Any]:
    d = _group_dir(tenant_global_dir, group_id)
    if d is None or not (d / "meta.json").exists():
        raise ChatGroupError("group not found")
    rec = get_group(tenant_global_dir, group_id)
    if rec is None:
        raise ChatGroupError("group not found")
    before = len(rec["participants"])
    rec["participants"] = [p for p in rec["participants"] if p["participant_id"] != participant_id]
    if len(rec["participants"]) == before:
        raise ChatGroupError("participant not in group")
    _atomic_write(d / "meta.json", rec)
    return rec


def is_participant(tenant_global_dir: Path, group_id: str, participant_id: str) -> bool:
    rec = get_group(tenant_global_dir, group_id)
    if rec is None:
        return False
    return any(p["participant_id"] == participant_id for p in rec["participants"])


def append_message(
    tenant_global_dir: Path,
    group_id: str,
    *,
    sender_participant_id: str,
    text: str,
    delivery: str = "local",
) -> dict[str, Any]:
    """Append a message. Caller must have already verified the sender is
    a current participant (``is_participant``) — this function does not
    re-check membership, matching the single-responsibility split used by
    ``a2a_chat_pending_send.create_pending_send`` (store writes, callers
    authorize)."""
    d = _group_dir(tenant_global_dir, group_id)
    if d is None or not (d / "meta.json").exists():
        raise ChatGroupError("group not found")
    msg: dict[str, Any] = {
        "id": secrets.token_urlsafe(12),
        "group_id": group_id,
        "sender_participant_id": sender_participant_id,
        "ts": time.time(),
        "text": str(text)[:_MAX_MESSAGE_LEN],
        "delivery": delivery,
    }
    path = d / "messages.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(msg) + "\n")
    os.chmod(path, 0o600)
    return msg


def list_messages(
    tenant_global_dir: Path, group_id: str, *, limit: int = 200,
) -> list[dict[str, Any]]:
    d = _group_dir(tenant_global_dir, group_id)
    if d is None:
        return []
    path = d / "messages.jsonl"
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:
                continue
    return out[-limit:] if limit else out

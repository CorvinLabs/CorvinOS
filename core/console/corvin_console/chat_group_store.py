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

import contextlib
import json
import os
import re
import secrets
import shutil
import time
from pathlib import Path
from typing import Any, Literal

ParticipantKind = Literal["human", "agent", "a2a_peer"]

_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_MAX_TITLE_LEN = 128
_MAX_DISPLAY_NAME_LEN = 64
_MAX_MESSAGE_LEN = 16 * 1024
# A group's message log is trimmed to its newest _MAX_MESSAGES_KEPT entries
# once it passes _TRIM_AT_BYTES — inbound A2A messages arrive without an
# operator action, and the log had no bound at all.
_MAX_MESSAGES_KEPT = 2000
_TRIM_AT_BYTES = 2 * 1024 * 1024
# Groups a peer can make this instance create by messaging into an unknown
# group_id (mirror groups), per peer and in total.
MAX_MIRROR_GROUPS_PER_PEER = 20
MAX_GROUPS_TOTAL = 500


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


def attachments_dir(tenant_global_dir: Path, group_id: str) -> Path | None:
    """``<tenant_global_dir>/chat_groups/<group_id>/attachments`` — ``None``
    for a malformed ``group_id`` (same ``_safe_id`` gate ``_group_dir`` uses,
    so a route never has to reach into this module's private helpers to get
    a path-traversal-safe attachment directory)."""
    d = _group_dir(tenant_global_dir, group_id)
    return None if d is None else d / "attachments"


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
    group_id: str | None = None,
    created_by: str | None = None,
) -> dict[str, Any]:
    """Create a new group with the creator as its first participant.

    ``group_id`` is only passed for a MIRROR of a group that lives on another
    instance (ADR-2218): the id travels on the A2A envelope, so both sides
    must store the conversation under the same id. It never overwrites.
    """
    if group_id is None:
        group_id = secrets.token_urlsafe(16)
    elif _safe_id(group_id) is None:
        raise ChatGroupError("invalid group_id")
    elif (_groups_dir(tenant_global_dir) / group_id / "meta.json").exists():
        raise ChatGroupError("group already exists")
    now = time.time()
    record: dict[str, Any] = {
        "group_id": group_id,
        "tenant_id": tenant_id,
        "title": str(title)[:_MAX_TITLE_LEN] or "Untitled group",
        "created_at": now,
        "created_by": created_by or created_by_participant_id,
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


def delete_group(tenant_global_dir: Path, group_id: str) -> bool:
    """Remove a group and its messages from THIS instance. A mirror of the
    group on a peer's instance is theirs and stays. Returns False if absent."""
    d = _group_dir(tenant_global_dir, group_id)
    if d is None or not (d / "meta.json").exists():
        return False
    # Under the log lock: an append that checked meta.json before the delete
    # must not recreate the directory (its lock file + the message) after it.
    with _messages_lock(d):
        (d / "meta.json").unlink(missing_ok=True)
        for child in list(d.iterdir()):
            if child.name == ".a2a_config.lock":
                continue
            if child.is_dir() and not child.is_symlink():
                shutil.rmtree(child)
            else:
                child.unlink(missing_ok=True)
    shutil.rmtree(d, ignore_errors=True)
    return True


def is_participant(tenant_global_dir: Path, group_id: str, participant_id: str) -> bool:
    rec = get_group(tenant_global_dir, group_id)
    if rec is None:
        return False
    return any(p["participant_id"] == participant_id for p in rec["participants"])


@contextlib.contextmanager
def _messages_lock(d: Path):
    """Cross-process lock for one group's message log (appends, trims and
    delivery updates are read-modify-write on the same file)."""
    import a2a_friendship as _ft  # type: ignore[import-not-found]  # noqa: PLC0415
    with _ft.config_file_lock(d):
        yield


def count_groups(tenant_global_dir: Path, *, created_by: str | None = None) -> int:
    gdir = _groups_dir(tenant_global_dir)
    if not gdir.is_dir():
        return 0
    n = 0
    for child in gdir.iterdir():
        meta = child / "meta.json"
        if not meta.exists():
            continue
        if created_by is None:
            n += 1
            continue
        try:
            if json.loads(meta.read_text(encoding="utf-8")).get("created_by") == created_by:
                n += 1
        except Exception:  # noqa: BLE001
            continue
    return n


def _trim(path: Path) -> None:
    """Keep the newest messages, at most _MAX_MESSAGES_KEPT of them AND at most
    half of _TRIM_AT_BYTES — trimming by count alone left a log of large
    messages above the threshold, so every later append re-read and rewrote
    it under the lock (review R3)."""
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    kept: list[str] = []
    size = 0
    for line in reversed(lines[-_MAX_MESSAGES_KEPT:]):
        size += len(line.encode("utf-8"))
        if size > _TRIM_AT_BYTES // 2 and kept:
            break
        kept.append(line)
    kept.reverse()
    if len(kept) == len(lines):
        return
    tmp = path.with_suffix(".jsonl.tmp")
    tmp.write_text("".join(kept), encoding="utf-8")
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def set_delivery(tenant_global_dir: Path, group_id: str, message_id: str, delivery: str) -> bool:
    """Record a message's outbound delivery outcome (pending → delivered /
    failed). Returns False when the message is gone (trimmed / group deleted)."""
    d = _group_dir(tenant_global_dir, group_id)
    if d is None or not (d / "messages.jsonl").exists():
        return False
    path = d / "messages.jsonl"
    with _messages_lock(d):
        lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
        found = False
        for i, line in enumerate(lines):
            try:
                rec = json.loads(line)
            except Exception:  # noqa: BLE001
                continue
            if rec.get("id") == message_id:
                rec["delivery"] = delivery
                lines[i] = json.dumps(rec) + "\n"
                found = True
                break
        if not found:
            return False
        tmp = path.with_suffix(".jsonl.tmp")
        tmp.write_text("".join(lines), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    return True


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
    with _messages_lock(d):
        if not (d / "meta.json").exists():
            raise ChatGroupError("group not found")  # deleted meanwhile
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(msg) + "\n")
        os.chmod(path, 0o600)
        if path.stat().st_size > _TRIM_AT_BYTES:
            _trim(path)
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

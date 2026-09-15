"""pipe_registry.py — Layer 18 inter-session pipes (Phase-1 MVP).

Sessions can compose: the output of one persona flows as input into
another. Three pipe modes:

  - **named FIFO** — multi-writer, multi-reader, persistent until
    removed. Each ``read()`` consumes from the head.
  - **anonymous** — multi-writer, single-reader, auto-deleted on first
    successful ``read()``. The Unix ``cmd1 | cmd2`` analogue.
  - **broadcast** — multi-writer, multi-subscriber. Each subscriber has
    an independent cursor; a write reaches every subscriber exactly
    once. Used for status fan-out (``os`` persona pushes a state
    update, all interested sessions see it).

Storage: ``<corvin_home>/run/pipes/<name>.jsonl`` plus an optional
``<name>.subscribers.json`` for broadcast cursors. fcntl.flock on a
sidecar lock file serialises writes; reads are mtime-cached for cheap
polling.

The MVP is structurally independent of adapter.py — production wiring
(adapter side-channel envelopes for pipe-readers, slash commands like
``/pipe-create`` and ``/pipe-write``, MCP tool surface for personas)
is the follow-up slice.
"""
from __future__ import annotations

import contextlib
from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from paths import corvin_home  # type: ignore


# --------------------------------------------------------------------- types

PIPE_TYPES = ("named", "anonymous", "broadcast")


# --------------------------------------------------------------------- paths

def _pipes_dir() -> Path:
    d = corvin_home() / "run" / "pipes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _data_path(name: str) -> Path:
    return _pipes_dir() / f"{name}.jsonl"


def _meta_path(name: str) -> Path:
    return _pipes_dir() / f"{name}.meta.json"


def _subscribers_path(name: str) -> Path:
    return _pipes_dir() / f"{name}.subscribers.json"


def _lock_path(name: str) -> Path:
    return _pipes_dir() / f"{name}.lock"


# --------------------------------------------------------------------- locks

@contextlib.contextmanager
def _exclusive_lock(name: str) -> Iterator[None]:
    fd = os.open(str(_lock_path(name)), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --------------------------------------------------------------------- helpers

def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _validate_name(name: str) -> None:
    if not name or "/" in name or ".." in name or name.startswith("."):
        raise ValueError(f"invalid pipe name: {name!r}")


def _load_meta(name: str) -> Optional[Dict[str, Any]]:
    p = _meta_path(name)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def _write_meta(name: str, meta: Dict[str, Any]) -> None:
    p = _meta_path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(meta, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, p)


def _load_subs(name: str) -> Dict[str, int]:
    p = _subscribers_path(name)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


def _write_subs(name: str, subs: Dict[str, int]) -> None:
    p = _subscribers_path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    tmp.write_text(
        json.dumps(subs, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, p)


def _read_messages(name: str) -> List[Dict[str, Any]]:
    p = _data_path(name)
    if not p.exists():
        return []
    out: List[Dict[str, Any]] = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(json.loads(line))
        except json.JSONDecodeError:
            continue  # skip corrupt lines
    return out


def _write_messages(name: str, msgs: List[Dict[str, Any]]) -> None:
    p = _data_path(name)
    tmp = p.with_suffix(p.suffix + ".tmp")
    payload = "\n".join(
        json.dumps(m, separators=(",", ":"), sort_keys=True) for m in msgs
    )
    if payload:
        payload += "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, p)


# --------------------------------------------------------------------- api

def create_pipe(
    name: str,
    pipe_type: str = "named",
    *,
    owner: Optional[str] = None,
) -> Dict[str, Any]:
    """Create a pipe. Raises if the name exists or pipe_type is invalid."""
    _validate_name(name)
    if pipe_type not in PIPE_TYPES:
        raise ValueError(f"pipe_type must be one of {PIPE_TYPES}")
    with _exclusive_lock(name):
        if _load_meta(name) is not None:
            raise FileExistsError(f"pipe {name!r} already exists")
        meta = {
            "name": name,
            "type": pipe_type,
            "owner": owner,
            "created_at": _now_iso(),
            "next_seq": 0,
            "write_count": 0,
            "read_count": 0,
        }
        _write_meta(name, meta)
        # touch data file so it exists
        _data_path(name).touch()
        if pipe_type == "broadcast":
            _write_subs(name, {})
    return meta


def write(name: str, payload: Any, *, writer: Optional[str] = None) -> int:
    """Append a message to the pipe. Returns the assigned seq number."""
    _validate_name(name)
    with _exclusive_lock(name):
        meta = _load_meta(name)
        if meta is None:
            raise KeyError(f"pipe {name!r} does not exist")
        seq = meta["next_seq"]
        msgs = _read_messages(name)
        msg = {
            "seq": seq,
            "ts": _now_iso(),
            "writer": writer,
            "payload": payload,
        }
        msgs.append(msg)
        _write_messages(name, msgs)
        meta["next_seq"] = seq + 1
        meta["write_count"] = meta.get("write_count", 0) + 1
        _write_meta(name, meta)
        return seq


def read(
    name: str,
    *,
    subscriber_id: Optional[str] = None,
    max_messages: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Read messages from the pipe.

    Semantics depend on pipe type:

    - **named**: returns all queued messages in order, then truncates
      the queue. ``subscriber_id`` is ignored.
    - **anonymous**: same as named, then deletes the pipe entirely.
    - **broadcast**: returns messages with ``seq > subscriber.cursor``,
      updates the cursor. ``subscriber_id`` is required and must have
      been registered via ``subscribe()``.
    """
    _validate_name(name)
    with _exclusive_lock(name):
        meta = _load_meta(name)
        if meta is None:
            raise KeyError(f"pipe {name!r} does not exist")
        msgs = _read_messages(name)
        ptype = meta["type"]
        if ptype in ("named", "anonymous"):
            out = msgs[:max_messages] if max_messages else msgs
            remaining = msgs[len(out):]
            if ptype == "anonymous":
                if out:
                    # Auto-remove the pipe entirely on first non-empty read
                    _data_path(name).unlink(missing_ok=True)
                    _meta_path(name).unlink(missing_ok=True)
                    return out
                return []
            _write_messages(name, remaining)
            meta["read_count"] = meta.get("read_count", 0) + len(out)
            _write_meta(name, meta)
            return out
        # broadcast
        if subscriber_id is None:
            raise ValueError("broadcast read requires subscriber_id")
        subs = _load_subs(name)
        if subscriber_id not in subs:
            raise KeyError(f"subscriber {subscriber_id!r} not registered")
        cursor = subs[subscriber_id]
        new = [m for m in msgs if m["seq"] > cursor]
        if max_messages:
            new = new[:max_messages]
        if new:
            subs[subscriber_id] = new[-1]["seq"]
            _write_subs(name, subs)
            meta["read_count"] = meta.get("read_count", 0) + len(new)
            _write_meta(name, meta)
        return new


def subscribe(name: str, *, subscriber_id: Optional[str] = None) -> str:
    """Register a subscriber on a broadcast pipe. Returns the subscriber id."""
    _validate_name(name)
    with _exclusive_lock(name):
        meta = _load_meta(name)
        if meta is None:
            raise KeyError(f"pipe {name!r} does not exist")
        if meta["type"] != "broadcast":
            raise ValueError("subscribe is only valid for broadcast pipes")
        sid = subscriber_id or "sub_" + secrets.token_hex(4)
        subs = _load_subs(name)
        if sid in subs:
            raise FileExistsError(
                f"subscriber {sid!r} already registered for {name!r}"
            )
        # Cursor starts at next_seq - 1, so subscriber sees only NEW
        # writes after subscription. Seeding with -1 means "see all".
        subs[sid] = meta["next_seq"] - 1
        _write_subs(name, subs)
        return sid


def unsubscribe(name: str, subscriber_id: str) -> bool:
    """Remove a subscriber. Returns True if found."""
    _validate_name(name)
    with _exclusive_lock(name):
        meta = _load_meta(name)
        if meta is None:
            raise KeyError(f"pipe {name!r} does not exist")
        if meta["type"] != "broadcast":
            return False
        subs = _load_subs(name)
        if subscriber_id in subs:
            del subs[subscriber_id]
            _write_subs(name, subs)
            return True
        return False


def list_pipes() -> List[Dict[str, Any]]:
    """List all pipes with their metadata."""
    out = []
    for p in sorted(_pipes_dir().glob("*.meta.json")):
        try:
            meta = json.loads(p.read_text(encoding="utf-8"))
            out.append(meta)
        except json.JSONDecodeError:
            continue
    return out


def remove_pipe(name: str) -> bool:
    """Remove a pipe and all its state. Returns True if it existed."""
    _validate_name(name)
    with _exclusive_lock(name):
        existed = _meta_path(name).exists()
        for p in (
            _data_path(name),
            _meta_path(name),
            _subscribers_path(name),
        ):
            p.unlink(missing_ok=True)
    # remove lock file outside the lock (we just released it)
    _lock_path(name).unlink(missing_ok=True)
    return existed


def get_meta(name: str) -> Optional[Dict[str, Any]]:
    """Inspect a pipe's metadata without reading messages."""
    _validate_name(name)
    return _load_meta(name)


def queue_depth(name: str) -> int:
    """Return number of messages currently queued."""
    _validate_name(name)
    return len(_read_messages(name))

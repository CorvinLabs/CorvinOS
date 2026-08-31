"""process_table.py — Layer 17 MVP: session registry for /ps + signals.

The bridge adapter manages many concurrent claude subprocesses (one
per chat). This module is the *visible* registry that lets operators
query and signal those sessions from the messenger via /ps, /kill,
/nice, and /sig.

State is kept on disk at ``<corvin_home>/run/sessions.jsonl`` so
slash-command handlers (which run in a different process from the
adapter) can read it without IPC. Writes are atomic via tmp+rename;
reads are mtime-cached for cheap polling.

This module is structurally independent of adapter.py — adapter
integration is a follow-up step. The MVP value is the protocol +
the readable registry; tests exercise the module directly.

Session schema (one JSON object per line in sessions.jsonl):

    {
      "session_id":      "s_abc12",                   # short opaque
      "chat_key":        "discord:1501540900529...",  # bridge:chat_id
      "persona":         "coder",
      "status":          "running" | "idle" | "exited" | "killed",
      "started_at":      "2026-05-08T12:34:56Z",     # UTC ISO8601
      "last_activity":   "2026-05-08T12:35:12Z",
      "tokens_total":    87340,                       # cumulative
      "tokens_in_window": 12404,                      # current ctx
      "parent_session":  null | "s_xyz98",
      "nice":            0,                           # -20..+19
      "in_flight_tool":  null | "Read" | "Bash" | ...,
      "exit_reason":     null | "ok" | "killed" | "stream_idle" | ...,
      "exit_at":         null | "2026-05-08T12:36:00Z"
    }

Concurrency: a sidecar lock file (``sessions.jsonl.lock``) serialises
writes via ``fcntl.flock``. Reads are lock-free; mtime-cache means a
read can race with a write and see the prior consistent state — that
is acceptable for /ps which reflects "recent" rather than "exact".
"""
from __future__ import annotations

import contextlib
import errno
from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from paths import corvin_home  # type: ignore


# --------------------------------------------------------------------- paths

def _run_dir() -> Path:
    """Return the runtime directory, creating it on demand."""
    d = corvin_home() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def sessions_file() -> Path:
    """Path to the sessions.jsonl registry file."""
    return _run_dir() / "sessions.jsonl"


def _lock_file() -> Path:
    return _run_dir() / "sessions.jsonl.lock"


# --------------------------------------------------------------------- lock

@contextlib.contextmanager
def _exclusive_lock() -> Iterator[None]:
    """Serialise writers via fcntl.flock on a sidecar lock file.

    Defensive: the LOCK_UN attempt is wrapped in a separate try/except
    so that a transient OSError on unlock (rare on Linux but possible
    if the fd state was disturbed mid-flight, e.g. by a signal handler)
    doesn't mask the real exception or leak the fd.
    """
    lock_path = _lock_file()
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    locked = False
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        locked = True
        yield
    finally:
        if locked:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass  # transient — fd close below will release in any case
        try:
            os.close(fd)
        except OSError:
            pass


# --------------------------------------------------------------------- io

# Lightweight read-side cache: (mtime, sessions). Avoids parsing the
# file repeatedly when /ps polls every second on /top.
_read_cache: Dict[str, Any] = {"mtime": -1.0, "sessions": []}


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace(
        "+00:00", "Z"
    )


def _read_all_unlocked() -> List[Dict[str, Any]]:
    f = sessions_file()
    try:
        mt = f.stat().st_mtime
    except FileNotFoundError:
        _read_cache.update(mtime=-1.0, sessions=[])
        return []
    if mt == _read_cache["mtime"]:
        # cache hit — return a *copy* so callers can mutate without
        # affecting the cache
        return [dict(s) for s in _read_cache["sessions"]]
    sessions: List[Dict[str, Any]] = []
    try:
        with f.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    sessions.append(json.loads(line))
                except json.JSONDecodeError:
                    # corrupt line — skip, do not poison the registry
                    continue
    except FileNotFoundError:
        sessions = []
    _read_cache.update(mtime=mt, sessions=sessions)
    return [dict(s) for s in sessions]


def _write_all_unlocked(sessions: List[Dict[str, Any]]) -> None:
    f = sessions_file()
    tmp = f.with_suffix(f.suffix + ".tmp")
    payload = "\n".join(
        json.dumps(s, separators=(",", ":"), sort_keys=True) for s in sessions
    )
    if payload:
        payload += "\n"
    tmp.write_text(payload, encoding="utf-8")
    os.replace(tmp, f)
    # invalidate cache so next reader picks up fresh mtime
    _read_cache["mtime"] = -1.0


# --------------------------------------------------------------------- api

def register_session(
    session_id: str,
    chat_key: str,
    persona: str,
    parent_session: Optional[str] = None,
    nice: int = 0,
    **extra: Any,
) -> Dict[str, Any]:
    """Register a freshly-spawned session. Returns the persisted record."""
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if not (-20 <= nice <= 19):
        raise ValueError(f"nice must be in [-20, 19], got {nice}")
    now = _now_iso()
    record = {
        "session_id": session_id,
        "chat_key": chat_key,
        "persona": persona,
        "status": "running",
        "started_at": now,
        "last_activity": now,
        "tokens_total": 0,
        "tokens_in_window": 0,
        "parent_session": parent_session,
        "nice": nice,
        "in_flight_tool": None,
        "exit_reason": None,
        "exit_at": None,
    }
    record.update(extra)
    with _exclusive_lock():
        sessions = _read_all_unlocked()
        # If a session with the same id already exists, replace it
        # (re-registration after a crash recovery is legal).
        sessions = [s for s in sessions if s["session_id"] != session_id]
        sessions.append(record)
        _write_all_unlocked(sessions)
    return record


def update_session(session_id: str, **updates: Any) -> Optional[Dict[str, Any]]:
    """Patch a session record. Returns the updated record or None if missing.

    ``session_id`` is positional-only here, so Python's call-binding
    catches duplicate ``session_id=`` kwargs before the function body
    runs — the field is structurally unchangeable, no defensive check
    needed.
    """
    if "nice" in updates:
        n = updates["nice"]
        if not (-20 <= n <= 19):
            raise ValueError(f"nice must be in [-20, 19], got {n}")
    with _exclusive_lock():
        sessions = _read_all_unlocked()
        out: Optional[Dict[str, Any]] = None
        for s in sessions:
            if s["session_id"] == session_id:
                s.update(updates)
                s["last_activity"] = _now_iso()
                out = dict(s)
                break
        if out is not None:
            _write_all_unlocked(sessions)
        return out


def deregister_session(
    session_id: str,
    *,
    exit_reason: str = "ok",
    keep: bool = True,
) -> bool:
    """Mark a session terminated.

    With ``keep=True`` (default), the record stays in the file with
    ``status="exited"`` so /ps -a can show it. Periodic cleanup removes
    records older than the configured TTL.

    With ``keep=False``, the record is removed entirely (used by /reset
    + /kill -9 paths where post-mortem visibility is undesirable).

    Returns True if the session was found.
    """
    with _exclusive_lock():
        sessions = _read_all_unlocked()
        for i, s in enumerate(sessions):
            if s["session_id"] == session_id:
                if keep:
                    s["status"] = (
                        "killed" if exit_reason == "killed" else "exited"
                    )
                    s["exit_reason"] = exit_reason
                    s["exit_at"] = _now_iso()
                    sessions[i] = s
                else:
                    del sessions[i]
                _write_all_unlocked(sessions)
                return True
        return False


def list_sessions(
    include_terminated: bool = False,
    chat_key: Optional[str] = None,
    persona: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """List sessions, optionally filtered.

    Default: only running / idle. Pass ``include_terminated=True`` for
    /ps -a behaviour.
    """
    sessions = _read_all_unlocked()
    out = []
    for s in sessions:
        if not include_terminated and s["status"] in ("exited", "killed"):
            continue
        if chat_key is not None and s["chat_key"] != chat_key:
            continue
        if persona is not None and s["persona"] != persona:
            continue
        out.append(s)
    # Stable sort: highest-priority (lowest nice) first, then
    # most-recent activity
    out.sort(key=lambda s: (s["nice"], -_iso_to_ts(s["last_activity"])))
    return out


def get_session(session_id: str) -> Optional[Dict[str, Any]]:
    """Look up a single session by id."""
    for s in _read_all_unlocked():
        if s["session_id"] == session_id:
            return s
    return None


def cleanup_terminated(ttl_seconds: int = 3600) -> int:
    """Drop terminated sessions older than ``ttl_seconds``.

    Returns the number of records removed. Run this periodically (the
    adapter's existing ``CLEANUP_INTERVAL`` sweep is the natural site).
    """
    cutoff = time.time() - ttl_seconds
    removed = 0
    with _exclusive_lock():
        sessions = _read_all_unlocked()
        keep = []
        for s in sessions:
            if s["status"] in ("exited", "killed"):
                exit_at = s.get("exit_at")
                if exit_at and _iso_to_ts(exit_at) < cutoff:
                    removed += 1
                    continue
            keep.append(s)
        if removed:
            _write_all_unlocked(keep)
    return removed


# --------------------------------------------------------------------- format

def format_ps_table(
    sessions: List[Dict[str, Any]],
    *,
    tree: bool = False,
) -> str:
    """Render /ps output as a chat-friendly fixed-width table.

    Columns chosen to fit on a phone screen at ~80 cols:
      ID  CHAT          PERSONA  STATUS    TOK    AGE  TOOL
    """
    if not sessions:
        return "(no active sessions)"
    rows = [("ID", "CHAT", "PERSONA", "STATUS", "TOK", "AGE", "TOOL")]
    now = time.time()
    for s in sessions:
        chat = _short_chat(s["chat_key"])
        age = _humanize_age(now - _iso_to_ts(s["started_at"]))
        tok = _humanize_tokens(s.get("tokens_total", 0))
        rows.append(
            (
                s["session_id"],
                chat,
                s["persona"],
                s["status"],
                tok,
                age,
                s.get("in_flight_tool") or "-",
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for i, r in enumerate(rows):
        line = "  ".join(c.ljust(widths[j]) for j, c in enumerate(r))
        out.append(line.rstrip())
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    if tree:
        # Simple tree mark: prepend └─ to children. The flat sort is
        # already stable, so we just annotate.
        annotated = []
        for line, sess in zip(out[2:], sessions):
            if sess.get("parent_session"):
                annotated.append("  └─ " + line)
            else:
                annotated.append(line)
        out = out[:2] + annotated
    return "\n".join(out)


# --------------------------------------------------------------------- helpers

def _iso_to_ts(iso: str) -> float:
    """Parse ISO8601 (with trailing Z) to a unix timestamp."""
    if not iso:
        return 0.0
    s = iso.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(s).timestamp()
    except ValueError:
        return 0.0


def _short_chat(chat_key: str) -> str:
    """Compress 'discord:1501540900529246251' to 'discord:1501…6251'."""
    if ":" not in chat_key:
        return chat_key
    bridge, cid = chat_key.split(":", 1)
    if len(cid) > 12:
        cid = cid[:4] + "…" + cid[-4:]
    return f"{bridge}:{cid}"


def _humanize_age(seconds: float) -> str:
    if seconds < 60:
        return f"{int(seconds)}s"
    if seconds < 3600:
        return f"{int(seconds / 60)}m"
    if seconds < 86400:
        return f"{int(seconds / 3600)}h"
    return f"{int(seconds / 86400)}d"


def _humanize_tokens(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".rstrip("0").rstrip(".")
    return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")

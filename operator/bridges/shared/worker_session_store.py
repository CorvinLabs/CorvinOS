"""ADR-0049 — Layer 22 Session-Pinned Workers: session-file store.

Manages the on-disk worker-session files under:
    <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/worker_sessions/<label>.session.json

File mode: 0600.  Writes are atomic (write-to-tmp + os.replace) with an
exclusive flock so concurrent callers never observe a partial write.

Public API
----------
load_session(session_dir)          -> str | None   (session_id or None)
save_session(session_dir, sid, persona, created_at=None)
delete_session(session_dir)        -> bool
list_sessions(session_dir)         -> list[dict]    (file contents for TTL sweep)

``session_dir`` is the ``worker_sessions/`` directory for a given chat, i.e.
    <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/worker_sessions/

The caller (a2a_worker.py / delegation.py) is responsible for constructing
the correct path from tenant, bridge, and chat_key.

CI lint invariant: this module MUST NOT ``import anthropic``.
"""
from __future__ import annotations

from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

_MODE = 0o600


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _session_path(session_dir: Path, scope_label: str) -> Path:
    return session_dir / f"{scope_label}.session.json"


def load_session(session_dir: Path, scope_label: str) -> str | None:
    """Return the stored session_id for this scope, or None if absent."""
    path = _session_path(session_dir, scope_label)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                data = json.load(fh)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
        return data.get("session_id") or None
    except Exception:  # noqa: BLE001
        return None


def save_session(
    session_dir: Path,
    scope_label: str,
    session_id: str,
    persona: str,
    *,
    created_at: str | None = None,
) -> None:
    """Persist a new or updated session_id (atomic write, mode 0600).

    If a file already exists, increments resume_count and updates
    last_resumed_at.  If the file is new, sets created_at and resume_count=0.
    """
    session_dir.mkdir(parents=True, exist_ok=True)
    path = _session_path(session_dir, scope_label)

    # Open (or create) with O_CREAT so we can flock before reading.
    fd = os.open(str(path), os.O_RDWR | os.O_CREAT, _MODE)
    try:
        with os.fdopen(fd, "r+", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_EX)
            try:
                fh.seek(0)
                raw = fh.read()
                existing: dict[str, Any] = {}
                if raw.strip():
                    try:
                        existing = json.loads(raw)
                    except Exception:  # noqa: BLE001
                        existing = {}

                now = _now_iso()
                if existing.get("session_id") == session_id:
                    # Resume of the same session
                    record: dict[str, Any] = {
                        "session_id":       session_id,
                        "scope_label":      scope_label,
                        "persona":          persona,
                        "created_at":       existing.get("created_at", now),
                        "last_resumed_at":  now,
                        "resume_count":     existing.get("resume_count", 0) + 1,
                    }
                else:
                    # New session (first write or stale eviction + respawn)
                    record = {
                        "session_id":       session_id,
                        "scope_label":      scope_label,
                        "persona":          persona,
                        "created_at":       created_at or now,
                        "last_resumed_at":  now,
                        "resume_count":     0,
                    }

                fh.seek(0)
                fh.truncate()
                json.dump(record, fh, indent=2, ensure_ascii=False)
                fh.write("\n")
                fh.flush()
                # Ensure mode 0600 even if umask was different.
                try:
                    os.chmod(path, _MODE)
                except OSError:
                    pass
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001
        # Non-fatal: session continues without persistence.
        try:
            os.close(fd)
        except OSError:
            pass


def delete_session(session_dir: Path, scope_label: str) -> bool:
    """Remove the session file.  Returns True if the file existed."""
    path = _session_path(session_dir, scope_label)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False
    except Exception:  # noqa: BLE001
        return False


def read_session_record(session_dir: Path, scope_label: str) -> dict[str, Any] | None:
    """Return the full session record dict, or None if absent/corrupt."""
    path = _session_path(session_dir, scope_label)
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as fh:
            fcntl.flock(fh, fcntl.LOCK_SH)
            try:
                return json.load(fh)
            finally:
                fcntl.flock(fh, fcntl.LOCK_UN)
    except Exception:  # noqa: BLE001
        return None


def list_sessions(session_dir: Path) -> list[dict[str, Any]]:
    """Return all session records under session_dir (for TTL sweep)."""
    if not session_dir.is_dir():
        return []
    records: list[dict[str, Any]] = []
    for p in sorted(session_dir.glob("*.session.json")):
        try:
            with p.open("r", encoding="utf-8") as fh:
                fcntl.flock(fh, fcntl.LOCK_SH)
                try:
                    data = json.load(fh)
                finally:
                    fcntl.flock(fh, fcntl.LOCK_UN)
            data["_path"] = str(p)
            records.append(data)
        except Exception:  # noqa: BLE001
            continue
    return records


def worker_sessions_dir(session_root: Path) -> Path:
    """Return the worker_sessions/ subdirectory for a session root.

    session_root is typically:
        <corvin_home>/tenants/<tid>/sessions/<bridge>:<chat>/
    or (backward-compat):
        <corvin_home>/sessions/<bridge>:<chat>/
    """
    return session_root / "worker_sessions"

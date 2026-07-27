"""In-memory interview-session store for the ``/plugin-builder`` console command.

Keyed by ``(tenant_id, fingerprint)`` — one interview at a time per console
session, matching how ``slash_commands.handle()`` already identifies a caller
(see ``core/console/corvin_console/slash_commands.py``). Process-local and
unpersisted: a console restart loses an in-progress interview, the same as any
other in-memory console session state today — nothing about a plugin idea is
compliance-relevant enough to warrant a durable store, and a half-finished
interview is cheap to redo.

Bounded (:data:`MAX_SESSIONS`) and TTL-evicted (:data:`SESSION_TTL_SECONDS`) so
a process that runs for months cannot accumulate abandoned interviews
indefinitely — same shape as ``extension_points._degraded_reported``'s
wholesale-drop policy, applied to sessions instead of audit dedup keys.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

from .interview import InterviewSession

MAX_SESSIONS = 256
SESSION_TTL_SECONDS = 6 * 3600  # long enough for a real interview, not forever


@dataclass
class _Entry:
    session: InterviewSession
    last_touched: float = field(default=0.0)


_lock = threading.Lock()
_sessions: dict[tuple[str, str], _Entry] = {}


def _key(tenant_id: str, fingerprint: str) -> tuple[str, str]:
    return (tenant_id, fingerprint)


def _evict_stale_locked(now: float) -> None:
    stale = [k for k, e in _sessions.items() if now - e.last_touched > SESSION_TTL_SECONDS]
    for k in stale:
        del _sessions[k]


def get(tenant_id: str, fingerprint: str) -> "InterviewSession | None":
    """The in-progress session for this caller, or ``None`` if none is active."""
    with _lock:
        now = time.time()
        _evict_stale_locked(now)
        entry = _sessions.get(_key(tenant_id, fingerprint))
        if entry is None:
            return None
        entry.last_touched = now
        return entry.session


def start(tenant_id: str, fingerprint: str) -> InterviewSession:
    """Start (or restart) an interview for this caller, replacing any prior one."""
    with _lock:
        now = time.time()
        _evict_stale_locked(now)
        if len(_sessions) >= MAX_SESSIONS:
            oldest_key = min(_sessions, key=lambda k: _sessions[k].last_touched)
            del _sessions[oldest_key]
        session = InterviewSession(session_id=f"{tenant_id}:{fingerprint}")
        _sessions[_key(tenant_id, fingerprint)] = _Entry(session=session, last_touched=now)
        return session


def clear(tenant_id: str, fingerprint: str) -> None:
    """Drop the session for this caller, if any. Safe to call when none exists."""
    with _lock:
        _sessions.pop(_key(tenant_id, fingerprint), None)


def active_count() -> int:
    """Number of sessions currently held — for tests and introspection."""
    with _lock:
        return len(_sessions)


__all__ = ["get", "start", "clear", "active_count", "MAX_SESSIONS", "SESSION_TTL_SECONDS"]

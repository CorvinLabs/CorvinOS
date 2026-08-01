"""In-memory interview-session store for the ``/plugin-builder`` console command.

Keyed by ``(tenant_id, session_key)`` — one interview at a time per chat
CONVERSATION. ``session_key`` is the console's per-tab/per-chat ``sid`` (or
the bridge's ``channel:chat_key``) — never the login-cookie fingerprint,
which is shared by every conversation the same browser login has open (see
``core/console/corvin_console/slash_commands.py``'s ``handle()`` docstring).
Process-local and
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
from typing import Callable

from .interview import InterviewSession

MAX_SESSIONS = 256
SESSION_TTL_SECONDS = 6 * 3600  # long enough for a real interview, not forever


@dataclass
class _Entry:
    session: InterviewSession
    last_touched: float = field(default=0.0)


_lock = threading.Lock()
_sessions: dict[tuple[str, str], _Entry] = {}

#: Called, with the removed session, for EVERY way a session ever leaves
#: this store — ``clear()``, TTL eviction, and the MAX_SESSIONS bound —
#: not just the ones a caller happens to remember to pair with their own
#: cleanup. ``turn.py`` registers here to drop ``_checkpoint_state[id(
#: session)]`` the moment its session is gone: keying that dict by object
#: identity (round 4) is only safe for as long as the object cannot be
#: garbage-collected while an entry still references it, and THAT was only
#: true as long as every removal path remembered to call
#: ``turn._forget_checkpoint_state()`` too — three call sites didn't
#: (two flag-off cleanups, one exception handler), so a freed session's
#: ``id()`` could be handed to a brand new session (same tenant OR a
#: DIFFERENT one — the dict carries no tenant scoping) before its stale
#: checkpoint entry was ever cleaned up, corrupting that new session's
#: scaffold destination (ADR-0262/0263 review round 6, Backend finding,
#: reproduced deterministically — no concurrency even needed). Routing every
#: removal through one hook point closes this the same way ``expected=``
#: closed round 5's finding: the dependent cleanup is no longer a contract
#: every caller must independently honor.
_removal_hooks: list[Callable[[InterviewSession], None]] = []


def register_removal_hook(fn: Callable[[InterviewSession], None]) -> None:
    """Register ``fn`` to run, with the removed session, whenever ANY
    session leaves this store. Intended for one-time registration at import
    time by a dependent module (see ``turn.py``); not unregistered — this
    store's own lifetime is the process's."""
    _removal_hooks.append(fn)


def _remove_locked(key: tuple[str, str]) -> None:
    """The ONLY place an entry is ever deleted from ``_sessions`` — call
    sites further down (``clear()``, TTL eviction, the MAX_SESSIONS bound)
    all route through this so ``_removal_hooks`` never has a gap. Must be
    called with ``_lock`` already held."""
    entry = _sessions.pop(key, None)
    if entry is not None:
        for hook in _removal_hooks:
            hook(entry.session)

#: Guards the cross-store "check the OTHER store, clear it, write mine"
#: sequence both this module's `start()` and `ideation.start()`/`command()`
#: use to keep a plain interview and an ideation dialogue mutually exclusive
#: per caller. Each store's OWN dict already has its own lock (`_lock` here,
#: `ideation._store_lock` there) — those protect each store internally, but
#: neither serializes against the OTHER store's check-then-write, so two
#: concurrent turns for the same (tenant_id, session_key) — a double-click,
#: two tabs, a bridge retry — could both pass their "is the other store
#: active?" check before either had written, leaving BOTH stores holding a
#: session at once (verified with a real thread-barrier repro; ADR-0262/0263
#: review round 3, Gates finding). Held only across the check+write of
#: EITHER side's cross-store swap, never across a whole turn.
cross_store_lock = threading.Lock()


def _key(tenant_id: str, session_key: str) -> tuple[str, str]:
    return (tenant_id, session_key)


def _evict_stale_locked(now: float) -> None:
    stale = [k for k, e in _sessions.items() if now - e.last_touched > SESSION_TTL_SECONDS]
    for k in stale:
        _remove_locked(k)


def get(tenant_id: str, session_key: str) -> "InterviewSession | None":
    """The in-progress session for this caller, or ``None`` if none is active."""
    with _lock:
        now = time.time()
        _evict_stale_locked(now)
        entry = _sessions.get(_key(tenant_id, session_key))
        if entry is None:
            return None
        entry.last_touched = now
        return entry.session


def start(
    tenant_id: str,
    session_key: str,
    *,
    idea_first: bool = False,
    checkpoint_enabled: bool = False,
    e2e_tests_enabled: bool = False,
) -> InterviewSession:
    """Start (or restart) an interview for this caller, replacing any prior one.

    The three ADR-0262 flags default to ``False`` — a caller that doesn't
    pass them gets the original ADR-0253 session shape unchanged. The real
    flag values are read by ``slash_commands.py``/the bridge adapter (each
    already owns its own feature-flag lookup) and passed in here; this
    module has no opinion on where a flag value comes from.
    """
    with _lock:
        now = time.time()
        _evict_stale_locked(now)
        key = _key(tenant_id, session_key)
        if key in _sessions:
            # Replacing an existing session for this caller — route through
            # _remove_locked so removal hooks fire for the one being
            # replaced, same as every other way a session leaves this
            # store. A prior version did a bare dict assignment here,
            # silently overwriting the old _Entry without ever telling
            # dependents (turn.py's _checkpoint_state) it was gone
            # (ADR-0262/0263 review round 6, Backend finding — the same
            # root cause as the three unhooked clear() call sites, just a
            # fourth, distinct removal path).
            _remove_locked(key)
        if len(_sessions) >= MAX_SESSIONS:
            oldest_key = min(_sessions, key=lambda k: _sessions[k].last_touched)
            _remove_locked(oldest_key)
        session = InterviewSession(
            session_id=f"{tenant_id}:{session_key}",
            idea_first=idea_first,
            checkpoint_enabled=checkpoint_enabled,
            e2e_tests_enabled=e2e_tests_enabled,
        )
        _sessions[key] = _Entry(session=session, last_touched=now)
        return session


def clear(
    tenant_id: str, session_key: str, *, expected: "InterviewSession | None" = None
) -> None:
    """Drop the session for this caller, if any. Safe to call when none exists.

    Pass ``expected`` — the specific session object you're finishing/
    cancelling — whenever you have it: the check-and-delete then runs
    ATOMICALLY under this store's own lock, so a concurrent
    ``start()`` for the same caller (a double-click, a bridge retry, a
    second browser tab) that has already replaced this slot with a NEWER,
    legitimate session is never destroyed by a stale caller's cleanup.

    Every call site across four rounds of review kept discovering this the
    same way — a caller had a session object in hand, called ``clear()``
    unconditionally anyway, and a concurrent ``start()`` for the same
    caller lost. Rounds 1–4 fixed each occurrence as it was found
    (``turn.drive()``'s finish path, then round 5 found three more:
    ``turn.command()``'s cancel branch, ``ideation.py``'s own cancel
    branch, the bridge's exception handler). Moving the check INTO
    ``clear()`` itself, atomic and opt-in via ``expected=``, makes the safe
    behavior the one line every future caller reaches for instead of a
    convention every new call site has to remember and can silently miss.

    Omit ``expected`` only for a deliberate "drop whatever is there,
    unconditionally" cleanup — e.g. an operator disabling the feature flag
    mid-interview, where there is no specific session to protect.
    """
    with _lock:
        key = _key(tenant_id, session_key)
        if expected is not None:
            entry = _sessions.get(key)
            if entry is None or entry.session is not expected:
                return
        _remove_locked(key)


def active_count() -> int:
    """Number of sessions currently held — for tests and introspection."""
    with _lock:
        return len(_sessions)


__all__ = [
    "get", "start", "clear", "active_count", "register_removal_hook",
    "MAX_SESSIONS", "SESSION_TTL_SECONDS",
]

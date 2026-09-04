"""Session Lifecycle Manager — Recovery, Cleanup, Audit, LRU Cache.

This module manages the complete session lifecycle:
- Recovery: Load active sessions from disk on startup
- Cleanup: Purge expired sessions periodically
- Audit: Log all session operations
- Robustness: Handle corruption, concurrent access, file errors
- Cache: In-memory LRU cache for performance (optional)

Integrated with auth.py's persistent session storage.
"""
from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from . import auth as session_auth

_log = logging.getLogger(__name__)

# Simple LRU Cache (thread-safe via GIL in CPython)
class _SessionLRUCache:
    """In-memory LRU cache for active sessions.

    TODO (CRITICAL): Add tenant_id to cache key for isolation.
    Current implementation caches by sid only. In multi-tenant environments,
    this could allow one tenant's sid to collide with another's.
    See session_manager.py cache_get() for details.
    """

    def __init__(self, max_size: int = 1000):
        self.max_size = max_size
        self.cache: OrderedDict[str, session_auth.SessionRecord] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def get(self, sid: str) -> Optional[session_auth.SessionRecord]:
        """Get from cache (moves to end if hit)."""
        if sid in self.cache:
            self.cache.move_to_end(sid)
            self.hits += 1
            return self.cache[sid]
        self.misses += 1
        return None

    def put(self, sid: str, rec: session_auth.SessionRecord) -> None:
        """Put in cache (evict LRU if full)."""
        if sid in self.cache:
            self.cache.move_to_end(sid)
        self.cache[sid] = rec
        if len(self.cache) > self.max_size:
            oldest_sid, _ = self.cache.popitem(last=False)
            _log.debug("Evicted LRU session from cache: %s", oldest_sid)

    def invalidate(self, sid: str) -> None:
        """Remove from cache."""
        self.cache.pop(sid, None)

    def clear(self) -> None:
        """Clear cache."""
        self.cache.clear()
        self.hits = 0
        self.misses = 0

    def stats(self) -> dict:
        """Return cache statistics."""
        total = self.hits + self.misses
        hit_rate = (self.hits / total * 100) if total > 0 else 0
        return {
            "size": len(self.cache),
            "max_size": self.max_size,
            "hits": self.hits,
            "misses": self.misses,
            "hit_rate_percent": hit_rate,
        }


@dataclass
class SessionManagerStats:
    """Snapshot of session manager state."""
    total_sessions: int
    active_sessions: int
    expired_sessions: int
    corrupted_sessions: int
    last_cleanup_at: float
    recovered_at_boot: int


class SessionManager:
    """Manages session lifecycle: recovery, cleanup, audit, LRU cache."""

    def __init__(self, enable_cache: bool = True, cache_size: int = 1000):
        self._stats = SessionManagerStats(
            total_sessions=0,
            active_sessions=0,
            expired_sessions=0,
            corrupted_sessions=0,
            last_cleanup_at=time.time(),
            recovered_at_boot=0,
        )
        # HIGH FIX #8: Thread-safe statistics
        self._stats_lock = threading.Lock()
        self._initialized = False
        self._cache = _SessionLRUCache(max_size=cache_size) if enable_cache else None
        self._enable_cache = enable_cache

    def _increment_stat(self, field: str, delta: int = 1) -> None:
        """Thread-safe increment of a stat field."""
        with self._stats_lock:
            current = getattr(self._stats, field)
            setattr(self._stats, field, current + delta)

    async def bootstrap(self) -> None:
        """Called on app startup. Recover sessions and initialize cleanup task."""
        if self._initialized:
            return

        _log.info("🔄 SessionManager bootstrap starting...")

        try:
            recovered = self._recover_sessions()
            self._stats.recovered_at_boot = recovered
            _log.info(
                "✅ SessionManager bootstrap complete: "
                "recovered %d sessions, %d active, %d expired, %d corrupted",
                self._stats.total_sessions,
                self._stats.active_sessions,
                self._stats.expired_sessions,
                self._stats.corrupted_sessions,
            )
        except Exception as exc:
            _log.error("❌ SessionManager bootstrap failed: %s", exc, exc_info=True)
            raise

        self._initialized = True

    def _recover_sessions(self) -> int:
        """Load all valid sessions from disk. Return count of recovered sessions."""
        sessions_dir = session_auth._sessions_dir()

        if not sessions_dir.exists():
            _log.info("No sessions directory found, skipping recovery")
            return 0

        recovered = 0
        now = time.time()

        try:
            session_files = sorted(sessions_dir.glob("*.json"))
        except OSError as exc:
            _log.error("Failed to list sessions directory: %s", exc)
            return 0

        # HIGH FIX #8: Thread-safe stat update
        with self._stats_lock:
            self._stats.total_sessions = len(session_files)

        for session_file in session_files:
            try:
                # Extract SID from filename (format: "{sid}.json")
                sid = session_file.stem

                # Validate it looks like a valid SID
                if not session_auth._looks_like_sid(sid):
                    _log.warning("Skipping session file with invalid SID format: %s", session_file.name)
                    self._stats.corrupted_sessions += 1
                    continue

                # Try to load and validate the session
                rec = session_auth.load_session(sid, now=now)

                if rec is None:
                    # Session expired or invalid
                    self._stats.expired_sessions += 1
                else:
                    recovered += 1
                    self._stats.active_sessions += 1
                    _log.debug(
                        "Recovered session: sid_fp=%s tenant=%s expires_in=%.0f",
                        rec.sid_fingerprint,
                        rec.tenant_id,
                        rec.expires_at - now,
                    )

            except session_auth.SessionStoreMalformed as exc:
                _log.warning("Corrupted session file %s: %s", session_file.name, exc)
                self._stats.corrupted_sessions += 1
            except Exception as exc:
                _log.error("Error recovering session %s: %s", session_file.name, exc)
                self._stats.corrupted_sessions += 1

        return recovered

    async def cleanup_expired_sessions(self, max_age_s: int = 86400) -> None:
        """Delete expired sessions. Intended for periodic background task."""
        sessions_dir = session_auth._sessions_dir()

        if not sessions_dir.exists():
            return

        now = time.time()
        deleted = 0
        errors = 0

        try:
            session_files = sorted(sessions_dir.glob("*.json"))
        except OSError as exc:
            _log.error("Failed to list sessions for cleanup: %s", exc)
            return

        for session_file in session_files:
            try:
                sid = session_file.stem
                if not session_auth._looks_like_sid(sid):
                    continue

                # Try to load session — this validates expiry + deletes if dead
                rec = session_auth.load_session(sid, now=now)

                if rec is None:
                    # Session is expired/invalid and was already deleted by load_session
                    # (or doesn't exist). Just count it.
                    deleted += 1
                    _log.debug("Expired session deleted: %s", session_file.name)

            except session_auth.SessionStoreMalformed as exc:
                # Corrupted file — delete it
                try:
                    session_file.unlink()
                    deleted += 1
                    _log.warning("Deleted corrupted session %s: %s", session_file.name, exc)
                except OSError as del_exc:
                    _log.warning("Failed to delete corrupted session %s: %s", session_file.name, del_exc)
                    errors += 1
            except OSError as exc:
                _log.warning("Error checking session file %s: %s", session_file.name, exc)
                errors += 1

        self._stats.last_cleanup_at = now
        _log.info("Session cleanup complete: deleted=%d errors=%d", deleted, errors)

    def audit_session_created(self, rec: session_auth.SessionRecord, via: str = "local-login") -> None:
        """Audit log when a session is created."""
        try:
            _log.info(
                "AUDIT[session.created] sid_fp=%s tenant=%s tier=%s via=%s persistent=%s",
                rec.sid_fingerprint,
                rec.tenant_id,
                rec.tier,
                via,
                rec.persistent,
            )
        except Exception as exc:
            _log.error("Failed to audit session creation: %s", exc)

    def audit_session_loaded(self, rec: session_auth.SessionRecord) -> None:
        """Audit log when a session is loaded/validated."""
        try:
            _log.debug(
                "AUDIT[session.loaded] sid_fp=%s tenant=%s idle_age=%.0f",
                rec.sid_fingerprint,
                rec.tenant_id,
                time.time() - rec.last_seen_at,
            )
        except Exception as exc:
            _log.error("Failed to audit session load: %s", exc)

    def audit_session_ended(self, sid: str, sid_fingerprint: str, reason: str = "logout") -> None:
        """Audit log when a session is ended."""
        try:
            _log.info(
                "AUDIT[session.ended] sid_fp=%s reason=%s",
                sid_fingerprint,
                reason,
            )
        except Exception as exc:
            _log.error("Failed to audit session end: %s", exc)

    def stats(self) -> SessionManagerStats:
        """Return current session statistics."""
        return self._stats

    def cache_get(self, sid: str) -> Optional[session_auth.SessionRecord]:
        """Get session from cache if enabled (TODO: validate tenant isolation)."""
        if self._cache is None:
            return None
        # TODO (CRITICAL): Validate tenant_id on cache hit to prevent isolation bypass
        return self._cache.get(sid)

    def cache_put(self, rec: session_auth.SessionRecord) -> None:
        """Put session in cache if enabled (TODO: validate tenant isolation)."""
        if self._cache is not None:
            self._cache.put(rec.sid, rec)

    def cache_invalidate(self, sid: str) -> None:
        """Invalidate session in cache."""
        if self._cache is not None:
            self._cache.invalidate(sid)

    def cache_stats(self) -> dict:
        """Return cache statistics."""
        if self._cache is None:
            return {"enabled": False}
        return {"enabled": True, **self._cache.stats()}

    def reset_stats(self) -> None:
        """Reset statistics counters (for testing)."""
        self._stats = SessionManagerStats(
            total_sessions=0,
            active_sessions=0,
            expired_sessions=0,
            corrupted_sessions=0,
            last_cleanup_at=time.time(),
            recovered_at_boot=0,
        )
        if self._cache:
            self._cache.clear()


# Singleton instance
_manager: Optional[SessionManager] = None


def get_session_manager() -> SessionManager:
    """Get or create the singleton SessionManager."""
    global _manager
    if _manager is None:
        _manager = SessionManager()
    return _manager


async def bootstrap_session_manager() -> None:
    """Bootstrap the session manager on app startup."""
    manager = get_session_manager()
    await manager.bootstrap()

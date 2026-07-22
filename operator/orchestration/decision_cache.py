"""ADR-0210 Phase 2: Decision Cache Layer.

Persistent, TTL-aware cache for InitialAnalysisRequest results shared by all
workers in a session. Enables 30-40% token reduction through cache hits (target
60%+ hit rate for repeated/similar tasks).

Two storage backends:
  - Memory (in-process, fast, per-process isolation)
  - SQLite (persistent, cross-process visible, survives restarts)

Default: memory-only for Phase 2; Phase 3 will add optional SQLite persistence.

CI lint: module MUST NOT import anthropic.
"""
from __future__ import annotations

import hashlib
import json
import logging
import sqlite3
import time
from pathlib import Path
from typing import Any, Optional

from initial_analysis import InitialAnalysisRequest, parse_task_analysis_response

_logger = logging.getLogger(__name__)


class DecisionCache:
    """In-memory + optional persistent cache for LM task decisions.

    Cache hit on identical task returns cached decision (zero LM cost).
    Cache miss runs full initial_analysis, caches result for future hits.
    TTL default 300s (5 min); configurable per instance.
    Memory cache bounded to max_memory_entries; LRU eviction when full.
    """

    def __init__(
        self,
        *,
        ttl_seconds: int = 300,
        storage_dir: Path | str | None = None,
        enable_sqlite: bool = False,
        max_memory_entries: int = 1000,
    ) -> None:
        """Initialize decision cache.

        Args:
            ttl_seconds: Cache entry lifetime (default 5 min).
            storage_dir: Path to store SQLite db (if enable_sqlite=True).
            enable_sqlite: Enable persistent SQLite backend (Phase 3+).
                           For Phase 2, leave as False (memory-only).
            max_memory_entries: Maximum in-memory cache entries (LRU eviction).
        """
        self._memory: dict[str, tuple[InitialAnalysisRequest, float]] = {}
        self._memory_access_order: list[str] = []  # For LRU tracking
        self._ttl_s = max(1, ttl_seconds)
        self._max_memory_entries = max(1, max_memory_entries)
        self._storage_dir = Path(storage_dir) if storage_dir else None
        self._enable_sqlite = enable_sqlite and storage_dir is not None
        self._db_path: Path | None = None
        self._db_conn: sqlite3.Connection | None = None

        if self._enable_sqlite:
            self._db_path = Path(storage_dir) / "decision_cache.db"
            self._init_db()

    def _init_db(self) -> None:
        """Initialize SQLite database (Phase 3+)."""
        if not self._db_path:
            return
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._db_conn = sqlite3.connect(
                str(self._db_path), check_same_thread=False, timeout=10.0
            )
            self._db_conn.execute("""
                CREATE TABLE IF NOT EXISTS decisions (
                    cache_key TEXT PRIMARY KEY,
                    task_hash TEXT NOT NULL,
                    decision_json TEXT NOT NULL,
                    cached_at REAL NOT NULL,
                    ttl_seconds INTEGER NOT NULL
                )
            """)
            self._db_conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_task_hash
                ON decisions(task_hash)
            """)
            self._db_conn.commit()
        except Exception as e:
            _logger.warning(f"Failed to init SQLite cache: {e}")
            self._db_conn = None
            self._enable_sqlite = False

    def _cache_key_for_task(self, task: str, context: dict[str, Any] | None = None) -> str:
        """Generate a cache key from task text + context (SHA256 hash).

        Includes context in key to avoid stale cache hits when files/environment
        change. If context changes (file modified, env var updated), cache
        key differs → new analysis runs.

        Args:
            task: Plain English task description.
            context: Optional task context (files, state, config). If provided,
                    is included in key to prevent stale hits when context changes.

        Returns:
            Hex-encoded SHA256 hash (deterministic).
        """
        # Canonical JSON of context (sorted keys for determinism)
        context_str = json.dumps(context or {}, sort_keys=True, default=str)
        combined = f"{task}:::{context_str}"
        return hashlib.sha256(combined.encode("utf-8")).hexdigest()

    def _evict_lru(self) -> None:
        """Evict least-recently-used entry if cache is full."""
        if len(self._memory) >= self._max_memory_entries:
            if self._memory_access_order:
                lru_key = self._memory_access_order.pop(0)
                if lru_key in self._memory:
                    del self._memory[lru_key]
                    _logger.debug(f"evicted LRU entry: {lru_key}")

    def _mark_access(self, cache_key: str) -> None:
        """Mark a cache key as recently accessed (for LRU tracking)."""
        if cache_key in self._memory_access_order:
            self._memory_access_order.remove(cache_key)
        self._memory_access_order.append(cache_key)

    async def get_or_analyze(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        *,
        analyzer_fn,  # Async callable that takes (task, context) and returns InitialAnalysisRequest
    ) -> tuple[InitialAnalysisRequest, bool]:
        """Get cached decision or run analysis and cache result.

        Args:
            task: Task description (used to generate cache key).
            context: Optional task context (files, state, config, etc.).
            analyzer_fn: Async function that runs full LM analysis.
                        Called on cache miss: analyzer_fn(task, context) -> InitialAnalysisRequest.

        Returns:
            (decision, is_cache_hit) tuple. is_cache_hit=True when from cache.
        """
        cache_key = self._cache_key_for_task(task, context)
        now = time.time()

        # Check memory cache first (zero-latency fast path)
        if cache_key in self._memory:
            cached_decision, cached_at = self._memory[cache_key]
            if (now - cached_at) < self._ttl_s:
                self._mark_access(cache_key)  # Update LRU order
                _logger.info(f"cache_hit: {cache_key}, reusing decision")
                return cached_decision, True
            else:
                # Expired in memory — remove
                del self._memory[cache_key]
                if cache_key in self._memory_access_order:
                    self._memory_access_order.remove(cache_key)

        # Check SQLite (if enabled)
        decision = self._load_from_sqlite(cache_key, now)
        if decision is not None:
            # Re-populate memory cache for future hits
            self._memory[cache_key] = (decision, now)
            _logger.info(f"cache_hit (sqlite): {cache_key}, reusing decision")
            return decision, True

        # Cache MISS: run analysis, cache result
        _logger.info(f"cache_miss: {cache_key}, running analyzer")
        decision = await analyzer_fn(task, context or {})
        decision.cache_key = cache_key
        decision.ttl_seconds = self._ttl_s
        decision.created_at = now

        # Evict LRU if memory cache is full
        self._evict_lru()

        # Store in memory
        self._memory[cache_key] = (decision, now)
        self._mark_access(cache_key)

        # Store in SQLite (if enabled)
        if self._enable_sqlite:
            self._save_to_sqlite(cache_key, decision, now)

        return decision, False

    def _load_from_sqlite(self, cache_key: str, now: float) -> InitialAnalysisRequest | None:
        """Load a cached decision from SQLite (Phase 3+).

        Returns None if not found, expired, or DB unavailable.
        """
        if not self._db_conn or not self._enable_sqlite:
            return None
        try:
            cursor = self._db_conn.execute(
                "SELECT decision_json, cached_at, ttl_seconds FROM decisions WHERE cache_key = ?",
                (cache_key,),
            )
            row = cursor.fetchone()
            if not row:
                return None

            decision_json, cached_at, ttl_s = row
            # Use < (not <=) to match memory cache behavior: expired when age >= ttl
            if (now - cached_at) < ttl_s:
                # Still valid
                decision = InitialAnalysisRequest.from_dict(json.loads(decision_json))
                return decision
            else:
                # Expired
                self._db_conn.execute(
                    "DELETE FROM decisions WHERE cache_key = ?", (cache_key,)
                )
                self._db_conn.commit()
                return None
        except Exception as e:
            _logger.debug(f"SQLite load failed: {e}")
            return None

    def _save_to_sqlite(
        self, cache_key: str, decision: InitialAnalysisRequest, cached_at: float
    ) -> None:
        """Save a decision to SQLite (Phase 3+)."""
        if not self._db_conn or not self._enable_sqlite:
            return
        try:
            decision_json = json.dumps(decision.to_dict())
            self._db_conn.execute(
                """
                INSERT OR REPLACE INTO decisions
                (cache_key, task_hash, decision_json, cached_at, ttl_seconds)
                VALUES (?, ?, ?, ?, ?)
                """,
                (cache_key, "", decision_json, cached_at, decision.ttl_seconds),
            )
            self._db_conn.commit()
        except Exception as e:
            _logger.debug(f"SQLite save failed: {e}")

    def invalidate(self, cache_key: str, reason: str = "") -> None:
        """Manually invalidate a cached decision (e.g., file changed).

        Args:
            cache_key: The key to invalidate (from get_or_analyze return).
            reason: Optional log message for why it was invalidated.
        """
        if cache_key in self._memory:
            del self._memory[cache_key]

        if self._enable_sqlite and self._db_conn:
            try:
                self._db_conn.execute(
                    "DELETE FROM decisions WHERE cache_key = ?", (cache_key,)
                )
                self._db_conn.commit()
            except Exception as e:
                _logger.debug(f"SQLite invalidate failed: {e}")

        if reason:
            _logger.info(f"invalidated {cache_key}: {reason}")

    def clear(self) -> None:
        """Clear all cached decisions (both memory and SQLite)."""
        self._memory.clear()

        if self._enable_sqlite and self._db_conn:
            try:
                self._db_conn.execute("DELETE FROM decisions")
                self._db_conn.commit()
                _logger.info("cleared all SQLite decisions")
            except Exception as e:
                _logger.debug(f"SQLite clear failed: {e}")

    def stats(self) -> dict[str, Any]:
        """Return cache statistics (hit rate, TTL, etc.)."""
        return {
            "memory_entries": len(self._memory),
            "ttl_seconds": self._ttl_s,
            "sqlite_enabled": self._enable_sqlite,
            "db_path": str(self._db_path) if self._db_path else None,
        }

    def close(self) -> None:
        """Close database connection (if open)."""
        if self._db_conn:
            try:
                self._db_conn.close()
            except Exception:
                pass
            self._db_conn = None

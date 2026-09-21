"""Learning Loop Index — key/value storage for loop metadata + metrics.

This module provides a persistent, tenant-isolated index of learning loops with
runtime metrics (health score, status, event count). The index is the single
source of truth for loop metadata and is updated atomically on each learning event.

Key semantics:
- Key: `{tenant_id}:{plugin_id}:{loop_id}` (UTF-8 encoded)
- Value: JSON-serialized LearningLoopIndexEntry
- ACID semantics via the backing store (sqlite3 by default, LevelDB when
  plyvel is installed — see the backend section below)
- Tenant isolation: every operation validates tenant_id

ADR-0907: KG MCP Learning-Loop Index
ADR-0314: Learning Infrastructure (event schema + persistence)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from threading import RLock
from typing import Literal, Optional, List

logger = logging.getLogger(__name__)


# ── Data Model ──────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class LearningLoopIndexEntry:
    """Immutable record for one learning loop (tenant + plugin + loop_id).

    Composite key: (tenant_id, plugin_id, loop_id)
    Status is computed on demand from metrics.
    """

    # Key fields
    tenant_id: str
    plugin_id: str
    loop_id: str

    # Schema definition (from manifest)
    description: str
    event_source: str
    feedback_types: List[str]
    aggregation: str  # "rolling_mean_7d", "percentile_p95"
    health_threshold: Optional[float]  # 0.0–1.0 or None
    dormancy_alert_hours: int
    owner_skill: Optional[str]

    # Computed metrics (updated on each event)
    last_event_ts: datetime
    event_count_7d: int
    health_score: float  # 0.0–1.0, rolling mean
    status: Literal["active", "dormant", "stale", "degrading"]

    # Metadata
    created_at: datetime
    updated_at: datetime

    def composite_key(self) -> str:
        """Return the composite key for storage."""
        return f"{self.tenant_id}:{self.plugin_id}:{self.loop_id}"

    def to_dict(self) -> dict:
        """Serialize to JSON-compatible dict."""
        d = asdict(self)
        d["last_event_ts"] = self.last_event_ts.isoformat()
        d["created_at"] = self.created_at.isoformat()
        d["updated_at"] = self.updated_at.isoformat()
        return d

    @classmethod
    def from_dict(cls, data: dict) -> LearningLoopIndexEntry:
        """Deserialize from JSON-compatible dict."""
        data = dict(data)
        data["last_event_ts"] = datetime.fromisoformat(data["last_event_ts"])
        data["created_at"] = datetime.fromisoformat(data["created_at"])
        data["updated_at"] = datetime.fromisoformat(data["updated_at"])
        return cls(**data)


# ── In-Memory Cache ─────────────────────────────────────────────────────────


class LearningLoopCache:
    """Simple LRU cache for hot entries (last 100 accessed)."""

    def __init__(self, max_size: int = 100, ttl_seconds: float = 300.0):
        """Initialize cache.

        Args:
            max_size: Maximum entries to keep
            ttl_seconds: Time-to-live per entry (5 minutes default)
        """
        self.max_size = max_size
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, tuple[LearningLoopIndexEntry, datetime]] = {}
        self._lock = RLock()

    def get(self, key: str) -> Optional[LearningLoopIndexEntry]:
        """Retrieve entry if cached and not expired."""
        with self._lock:
            if key not in self._cache:
                return None
            entry, cached_at = self._cache[key]
            age = (datetime.now(timezone.utc) - cached_at).total_seconds()
            if age > self.ttl_seconds:
                del self._cache[key]
                return None
            return entry

    def put(self, key: str, entry: LearningLoopIndexEntry) -> None:
        """Store entry in cache (evict oldest if at capacity)."""
        with self._lock:
            if len(self._cache) >= self.max_size and key not in self._cache:
                # Evict oldest
                oldest_key = min(self._cache.keys(), key=lambda k: self._cache[k][1])
                del self._cache[oldest_key]
            self._cache[key] = (entry, datetime.now(timezone.utc))

    def clear(self) -> None:
        """Clear all entries."""
        with self._lock:
            self._cache.clear()

    def invalidate(self, key: str) -> None:
        """Invalidate a specific key."""
        with self._lock:
            self._cache.pop(key, None)


# ── Key/value backends ──────────────────────────────────────────────────────
#
# The index was written against LevelDB (plyvel). plyvel is a C extension over
# libleveldb and appears in NO requirements file in this repo; neither the
# binding nor the system library is present on a stock install, and it is not
# installable at all on the Windows releases. Every LearningLoopIndexStorage
# construction therefore raised ImportError, the console route caught it and
# answered 503 "Learning loop service not available" — which is exactly what
# /app/learning-loops showed. So the store is backed by sqlite3 from the
# standard library (ACID, single file, present everywhere) and plyvel is used
# only when it happens to be installed, keeping existing LevelDB directories
# readable. Both backends speak the same bytes-in/bytes-out API.


class _SqliteKV:
    """sqlite3-backed ordered key/value store (stdlib, no extra dependency)."""

    def __init__(self, db_path: Path):
        import sqlite3

        self._sqlite3 = sqlite3
        self._path = db_path / "index.sqlite3"
        # check_same_thread=False + an RLock around every call: the console
        # serves these reads from a threadpool, and one connection guarded by
        # our own lock is simpler than a per-thread connection pool.
        self._conn = sqlite3.connect(str(self._path), check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS kv (k BLOB PRIMARY KEY, v BLOB NOT NULL)"
        )
        self._conn.commit()
        self._lock = RLock()

    def put(self, key: bytes, value: bytes) -> None:
        with self._lock:
            self._conn.execute(
                "INSERT INTO kv (k, v) VALUES (?, ?) "
                "ON CONFLICT(k) DO UPDATE SET v = excluded.v",
                (key, value),
            )
            self._conn.commit()

    def get(self, key: bytes) -> Optional[bytes]:
        with self._lock:
            row = self._conn.execute("SELECT v FROM kv WHERE k = ?", (key,)).fetchone()
        return row[0] if row else None

    def delete(self, key: bytes) -> None:
        with self._lock:
            self._conn.execute("DELETE FROM kv WHERE k = ?", (key,))
            self._conn.commit()

    def iterator(self, prefix: bytes):
        """Yield (key, value) for every key starting with `prefix`, key-ordered."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT k, v FROM kv WHERE substr(k, 1, ?) = ? ORDER BY k",
                (len(prefix), prefix),
            ).fetchall()
        return list(rows)

    def close(self) -> None:
        with self._lock:
            self._conn.close()


class _PlyvelKV:
    """plyvel/LevelDB backend — used only when plyvel is actually installed."""

    def __init__(self, plyvel, db_path: Path):
        self._db = plyvel.DB(str(db_path), create_if_missing=True)

    def put(self, key: bytes, value: bytes) -> None:
        self._db.put(key, value)

    def get(self, key: bytes) -> Optional[bytes]:
        return self._db.get(key)

    def delete(self, key: bytes) -> None:
        self._db.delete(key)

    def iterator(self, prefix: bytes):
        return self._db.iterator(prefix=prefix)

    def close(self) -> None:
        self._db.close()


# ── Index storage ───────────────────────────────────────────────────────────


class LearningLoopIndexStorage:
    """Thread-safe key/value-backed storage for the learning loop index."""

    def __init__(self, tenant_id: str, db_path: Optional[Path] = None):
        """Initialize storage.

        Args:
            tenant_id: Tenant identifier (validated)
            db_path: Custom LevelDB path (default: ~/.corvin/tenants/{tenant_id}/learning_loop_index/)

        Raises:
            ValueError: If tenant_id invalid or db_path inaccessible
        """
        from core.tenants.validation import validate_tenant_id

        self.tenant_id = validate_tenant_id(tenant_id)

        if db_path is None:
            from core.paths import tenant_home

            db_path = tenant_home(tenant_id) / "learning_loop_index"

        self.db_path = Path(db_path)
        self.db_path.mkdir(parents=True, exist_ok=True)

        # Backend selection: prefer an existing LevelDB install so a machine
        # that already has one keeps reading its data; fall back to sqlite3,
        # which is what every stock install actually runs on.
        try:
            import plyvel  # noqa: F401
        except ImportError:
            plyvel = None

        try:
            if plyvel is not None:
                self._db = _PlyvelKV(plyvel, self.db_path)
                self.backend = "leveldb"
            else:
                self._db = _SqliteKV(self.db_path)
                self.backend = "sqlite"
        except Exception as exc:
            raise ValueError(
                f"Failed to open learning loop index at {self.db_path}: {exc}"
            ) from exc

        # In-memory cache for hot entries
        self._cache = LearningLoopCache()
        self._lock = RLock()

    def _encode_key(self, composite_key: str) -> bytes:
        """Encode key to UTF-8 bytes."""
        return composite_key.encode("utf-8")

    def _decode_key(self, key_bytes: bytes) -> str:
        """Decode key from UTF-8 bytes."""
        return key_bytes.decode("utf-8")

    def insert(self, entry: LearningLoopIndexEntry) -> None:
        """Insert or update an index entry (atomic).

        Args:
            entry: Index entry to store

        Raises:
            ValueError: If tenant_id mismatch
        """
        if entry.tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: storage bound to {self.tenant_id!r}, "
                f"got {entry.tenant_id!r}"
            )

        key = entry.composite_key()
        value = json.dumps(entry.to_dict())

        with self._lock:
            try:
                self._db.put(self._encode_key(key), value.encode("utf-8"))
                self._cache.put(key, entry)
            except Exception as exc:
                logger.error(f"Failed to insert index entry {key}: {exc}")
                raise

    def get(self, plugin_id: str, loop_id: str) -> Optional[LearningLoopIndexEntry]:
        """Retrieve an index entry by loop_id.

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier

        Returns:
            Index entry or None if not found

        Raises:
            ValueError: If tenant_id or args invalid
        """
        from core.tenants.validation import validate_tenant_id

        validate_tenant_id(self.tenant_id)

        key = f"{self.tenant_id}:{plugin_id}:{loop_id}"

        # Check cache first
        cached = self._cache.get(key)
        if cached is not None:
            return cached

        # Read from LevelDB
        with self._lock:
            try:
                value = self._db.get(self._encode_key(key))
                if value is None:
                    return None
                entry_dict = json.loads(value.decode("utf-8"))
                entry = LearningLoopIndexEntry.from_dict(entry_dict)
                self._cache.put(key, entry)
                return entry
            except Exception as exc:
                logger.error(f"Failed to retrieve index entry {key}: {exc}")
                return None

    def list_all(
        self,
        plugin_id: Optional[str] = None,
        status: Optional[str] = None,
        limit: int = 1000,
    ) -> List[LearningLoopIndexEntry]:
        """List all index entries for this tenant (with optional filters).

        Args:
            plugin_id: Filter by plugin (optional)
            status: Filter by status (optional)
            limit: Maximum entries to return

        Returns:
            List of matching entries

        Raises:
            ValueError: If tenant_id invalid
        """
        from core.tenants.validation import validate_tenant_id

        validate_tenant_id(self.tenant_id)
        entries: List[LearningLoopIndexEntry] = []
        prefix = f"{self.tenant_id}:".encode("utf-8")

        with self._lock:
            try:
                for key_bytes, value in self._db.iterator(prefix=prefix):
                    if len(entries) >= limit:
                        break

                    try:
                        entry_dict = json.loads(value.decode("utf-8"))
                        entry = LearningLoopIndexEntry.from_dict(entry_dict)

                        # Apply filters
                        if plugin_id and entry.plugin_id != plugin_id:
                            continue
                        if status and entry.status != status:
                            continue

                        entries.append(entry)
                    except Exception as exc:
                        logger.error(f"Skipped malformed entry at {self._decode_key(key_bytes)}: {exc}")
                        continue

            except Exception as exc:
                logger.error(f"Failed to list index entries: {exc}")

        return entries

    def delete(self, plugin_id: str, loop_id: str) -> bool:
        """Delete an index entry.

        Args:
            plugin_id: Plugin identifier
            loop_id: Loop identifier

        Returns:
            True if deleted, False if not found
        """
        key = f"{self.tenant_id}:{plugin_id}:{loop_id}"

        with self._lock:
            try:
                self._db.delete(self._encode_key(key))
                self._cache.invalidate(key)
                return True
            except KeyError:
                return False
            except Exception as exc:
                logger.error(f"Failed to delete index entry {key}: {exc}")
                raise

    def close(self) -> None:
        """Close LevelDB connection and clear cache."""
        with self._lock:
            try:
                self._db.close()
                self._cache.clear()
            except Exception as exc:
                logger.error(f"Error closing LevelDB: {exc}")

    def __enter__(self):
        """Context manager entry."""
        return self

    def __exit__(self, *args):
        """Context manager exit."""
        self.close()

"""Lazy-load cache for skill manifest (ADR-0422).

Manifest is read-heavy (resolver queries), write-rare (Skill-Creator updates).
Cache is LRU-bounded (256 skills), TTL-based (30min), thread-safe, with
fallback disk load on cache miss.

Target: >70% hit-rate in production; <5ms P99 latency.

Public API:
  - SkillCache(tenant_id, manifest_path)
  - cache.get(skill_name) -> SkillEntry | None
  - cache.invalidate() -> None (called on manifest write)
  - cache.stats() -> dict (hit/miss/eviction counts)
"""

from collections import OrderedDict
from datetime import datetime, timedelta, timezone
from typing import Optional, Dict, Any
from threading import Lock
import json
import os


class SkillManifestCorrupted(RuntimeError):
    """The skill manifest exists but cannot be parsed (fail-loud, never silent)."""


class SkillCache:
    """LRU cache for skill manifest entries (ADR-0422).

    Attributes:
        tenant_id: Tenant namespace
        manifest_path: Path to manifest.json on disk
        _cache: OrderedDict (LRU, max 256 entries)
        _ttl_map: {skill_name: expiry_datetime}
        _lock: Thread lock for concurrent access
        _stats: Hit/miss/eviction counters
    """

    DEFAULT_TTL_MINUTES = 30
    DEFAULT_MAX_SIZE = 256

    def __init__(
        self,
        tenant_id: str,
        manifest_path: str,
        max_size: int = DEFAULT_MAX_SIZE,
        ttl_minutes: int = DEFAULT_TTL_MINUTES,
    ):
        """Initialize cache.

        Args:
            tenant_id: Tenant identifier
            manifest_path: Path to manifest.json
            max_size: Max entries in LRU cache (default 256)
            ttl_minutes: Entry TTL in minutes (default 30)
        """
        self.tenant_id = tenant_id
        self.manifest_path = manifest_path
        self.max_size = max_size
        self.ttl_minutes = ttl_minutes

        self._cache: OrderedDict[str, Any] = OrderedDict()
        self._ttl_map: Dict[str, datetime] = {}
        self._lock = Lock()
        self._stats = {
            "hits": 0,
            "misses": 0,
            "evictions": 0,
            "invalidations": 0,
        }
        # Identity of the manifest generation the cached entries came from.
        self._manifest_mtime_ns: Optional[tuple] = self._manifest_mtime()

    def _manifest_mtime(self) -> Optional[tuple]:
        """(mtime_ns, inode, size) — inode catches atomic-replace writes that
        land inside one filesystem timestamp tick (mtime alone would miss them)."""
        try:
            st = os.stat(self.manifest_path)
            return (st.st_mtime_ns, st.st_ino, st.st_size)
        except OSError:
            return None

    def _invalidate_if_manifest_changed(self) -> None:
        """Lock held by caller. Drop cached entries if manifest.json changed on disk."""
        current = self._manifest_mtime()
        if current != self._manifest_mtime_ns:
            self._manifest_mtime_ns = current
            if self._cache:
                self._cache.clear()
                self._ttl_map.clear()
                self._stats["invalidations"] += 1

    def get(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Get skill entry from cache or manifest.

        1. Check cache (in-memory, O(1))
        2. If not in cache or expired: load manifest, cache entry, return
        3. On load failure: return None

        Args:
            skill_name: Name of skill (e.g., "assistant.validate_json")

        Returns:
            Skill entry dict, or None if not found
        """
        with self._lock:
            # Cross-process coherence: the registry that writes manifest.json may
            # live in another process (MCP server, CLI). A changed mtime means a
            # write happened behind this cache's back — drop everything cached
            # from the previous manifest generation before serving.
            self._invalidate_if_manifest_changed()

            # Check cache hit
            if skill_name in self._cache:
                expiry = self._ttl_map.get(skill_name)
                if expiry and datetime.now(timezone.utc) < expiry:
                    self._stats["hits"] += 1
                    # Move to end (LRU recency)
                    self._cache.move_to_end(skill_name)
                    return self._cache[skill_name]
                # Expired; remove and fall through
                del self._cache[skill_name]
                del self._ttl_map[skill_name]

            # Cache miss; load from manifest
            self._stats["misses"] += 1
            try:
                manifest = self._load_manifest()
                entry = self._find_in_manifest(manifest, skill_name)
                if entry:
                    self._insert(skill_name, entry)
                return entry
            except FileNotFoundError:
                return None  # no manifest yet == no skills; a legitimate empty state
            except json.JSONDecodeError as exc:
                # A corrupted manifest is a broken install, not "skill not found".
                # Returning None here made EVERY skill vanish silently and kept the
                # hardening circuit breaker blind to the failure.
                raise SkillManifestCorrupted(
                    f"manifest {self.manifest_path} is not valid JSON: {exc.msg} (line {exc.lineno})"
                ) from exc

    def _insert(self, skill_name: str, entry: Dict[str, Any]) -> None:
        """Insert entry into cache (with LRU eviction if needed).

        Args:
            skill_name: Skill name key
            entry: Skill entry dict
        """
        self._cache[skill_name] = entry
        self._ttl_map[skill_name] = datetime.now(timezone.utc) + timedelta(
            minutes=self.ttl_minutes
        )

        # Evict oldest if over max size
        if len(self._cache) > self.max_size:
            evicted_key = next(iter(self._cache))
            del self._cache[evicted_key]
            del self._ttl_map[evicted_key]
            self._stats["evictions"] += 1

    def invalidate(self) -> None:
        """Clear cache (called when manifest is written).

        Thread-safe; waits for ongoing gets to complete.
        """
        with self._lock:
            self._cache.clear()
            self._ttl_map.clear()
            self._stats["invalidations"] += 1
            self._manifest_mtime_ns = self._manifest_mtime()

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics.

        Returns:
            {"hits": N, "misses": N, "evictions": N, "invalidations": N,
             "size": N, "max_size": N, "hit_rate": float}
        """
        with self._lock:
            total = self._stats["hits"] + self._stats["misses"]
            hit_rate = (
                self._stats["hits"] / total if total > 0 else 0.0
            )
            return {
                **self._stats,
                "size": len(self._cache),
                "max_size": self.max_size,
                "hit_rate": hit_rate,
            }

    def _load_manifest(self) -> Dict[str, Any]:
        """Load and parse manifest.json from disk.

        Returns:
            Manifest dict with "skills" list

        Raises:
            FileNotFoundError: Manifest not found
            json.JSONDecodeError: Manifest invalid JSON
        """
        with open(self.manifest_path, "r") as f:
            return json.load(f)

    def _find_in_manifest(
        self, manifest: Dict[str, Any], skill_name: str
    ) -> Optional[Dict[str, Any]]:
        """Find skill entry in manifest.

        Args:
            manifest: Manifest dict
            skill_name: Name to search for

        Returns:
            Entry dict, or None if not found
        """
        for entry in manifest.get("skills", []):
            if entry.get("name") == skill_name:
                return entry
        return None

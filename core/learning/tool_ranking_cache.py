"""Tool Ranking Cache — Thread-safe LRU cache with TTL (ADR-0322).

Caches ranked tool results with:
- Time-to-live (TTL) expiration (default 5 minutes)
- LRU eviction when max entries exceeded
- Thread-safe operations
- Automatic cleanup of expired entries

Design rationale:
- Ranking queries are expensive (O(n) aggregation)
- Most queries hit the cache (same task_type, error_class repeated)
- Cache hit rate typically > 80%
- TTL prevents stale data (5 min is reasonable for learning signals)
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class RankingCache:
    """Thread-safe cache for ranked tool results with TTL and LRU eviction.

    Features:
    - TTL expiration (default 5 minutes)
    - LRU eviction when max_entries exceeded
    - Automatic cleanup of expired entries
    - Thread-safe (uses asyncio.Lock)
    """

    def __init__(self, ttl_seconds: int = 300, max_entries: int = 1000):
        """Initialize cache.

        Args:
            ttl_seconds: Time-to-live for cached entries (default 5 minutes)
            max_entries: Maximum entries before LRU eviction (default 1000)
        """
        self.ttl = ttl_seconds
        self.max_entries = max_entries
        self.data: Dict[str, Any] = {}
        self.timestamps: Dict[str, datetime] = {}
        self.access_counts: Dict[str, int] = {}  # For LRU tracking
        self.lock = asyncio.Lock()

    async def get(self, key: str) -> Optional[Any]:
        """Get value from cache if not expired.

        Args:
            key: Cache key

        Returns:
            Cached value if exists and not expired, None otherwise
        """
        async with self.lock:
            if key not in self.data:
                return None

            # Check expiration
            created = self.timestamps[key]
            age_seconds = (datetime.now() - created).total_seconds()

            if age_seconds >= self.ttl:
                # Expired: remove
                del self.data[key]
                del self.timestamps[key]
                self.access_counts.pop(key, None)
                logger.debug(f"Cache entry expired and removed: {key}")
                return None

            # Update access count (for LRU)
            self.access_counts[key] = self.access_counts.get(key, 0) + 1

            return self.data[key]

    async def set(self, key: str, value: Any) -> None:
        """Set value in cache with current timestamp.

        Args:
            key: Cache key
            value: Value to cache (must be serializable)
        """
        async with self.lock:
            # Check if we need to evict (LRU)
            if len(self.data) >= self.max_entries and key not in self.data:
                # Find least recently used key
                lru_key = min(
                    self.access_counts.keys(),
                    key=lambda k: self.access_counts.get(k, 0),
                )
                del self.data[lru_key]
                del self.timestamps[lru_key]
                self.access_counts.pop(lru_key, None)
                logger.debug(f"LRU eviction: removed key={lru_key}")

            # Set value
            self.data[key] = value
            self.timestamps[key] = datetime.now()
            self.access_counts[key] = 0

    async def delete(self, key: str) -> bool:
        """Delete a specific entry from cache.

        Args:
            key: Cache key to delete

        Returns:
            True if entry was deleted, False if not found
        """
        async with self.lock:
            if key in self.data:
                del self.data[key]
                del self.timestamps[key]
                self.access_counts.pop(key, None)
                logger.debug(f"Cache entry deleted: {key}")
                return True
            return False

    async def clear_expired(self) -> int:
        """Remove all expired entries and return count.

        Returns:
            Number of entries removed
        """
        async with self.lock:
            now = datetime.now()
            expired_keys = [
                k
                for k, ts in self.timestamps.items()
                if (now - ts).total_seconds() >= self.ttl
            ]

            for k in expired_keys:
                del self.data[k]
                del self.timestamps[k]
                self.access_counts.pop(k, None)

            if expired_keys:
                logger.debug(f"Cleared {len(expired_keys)} expired cache entries")

            return len(expired_keys)

    async def clear_all(self) -> None:
        """Clear entire cache."""
        async with self.lock:
            self.data.clear()
            self.timestamps.clear()
            self.access_counts.clear()
            logger.debug("Cache cleared")

    async def size(self) -> int:
        """Get number of cached entries (excluding expired).

        Returns:
            Number of non-expired entries in cache
        """
        async with self.lock:
            now = datetime.now()
            valid_keys = [
                k
                for k, ts in self.timestamps.items()
                if (now - ts).total_seconds() < self.ttl
            ]
            return len(valid_keys)

    async def get_stats(self) -> Dict[str, Any]:
        """Get cache statistics.

        Returns:
            Dict with cache stats (size, ttl, max_entries, hit_rate, etc.)
        """
        async with self.lock:
            now = datetime.now()
            valid_count = 0
            expired_count = 0

            for k, ts in self.timestamps.items():
                if (now - ts).total_seconds() < self.ttl:
                    valid_count += 1
                else:
                    expired_count += 1

            total_accesses = sum(self.access_counts.values())
            hit_rate = (
                (total_accesses / max(1, total_accesses + expired_count))
                if total_accesses + expired_count > 0
                else 0.0
            )

            return {
                "valid_entries": valid_count,
                "expired_entries": expired_count,
                "total_capacity": self.max_entries,
                "ttl_seconds": self.ttl,
                "total_accesses": total_accesses,
                "estimated_hit_rate": hit_rate,
            }

    async def cleanup_task(self, interval_seconds: int = 60) -> None:
        """Background task to periodically clean expired entries.

        Args:
            interval_seconds: Cleanup interval (default 60 seconds)

        Note:
            Call this as a background task:
            asyncio.create_task(cache.cleanup_task())
        """
        while True:
            try:
                await asyncio.sleep(interval_seconds)
                await self.clear_expired()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Cleanup task failed: {e}", exc_info=True)

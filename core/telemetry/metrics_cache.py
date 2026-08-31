"""Metrics caching layer — reduce DB/memory hits (Phase 7c)."""

from datetime import datetime, timedelta
from typing import Any, Optional
import threading


class CachedMetrics:
    """Simple 1-hour TTL cache for feature metrics."""

    def __init__(self, ttl_seconds: int = 3600):
        self.ttl_seconds = ttl_seconds
        self.cache: dict[str, dict[str, Any]] = {}
        self.cache_time: dict[str, datetime] = {}
        self.lock = threading.Lock()

    def get(self, flag_id: str) -> Optional[dict[str, Any]]:
        """Get cached metrics if not expired."""
        with self.lock:
            if flag_id not in self.cache:
                return None

            cached_time = self.cache_time.get(flag_id)
            if cached_time and (datetime.utcnow() - cached_time).total_seconds() < self.ttl_seconds:
                return self.cache[flag_id]

            # Expired
            del self.cache[flag_id]
            del self.cache_time[flag_id]
            return None

    def set(self, flag_id: str, metrics: dict[str, Any]) -> None:
        """Cache metrics for a flag."""
        with self.lock:
            self.cache[flag_id] = metrics
            self.cache_time[flag_id] = datetime.utcnow()

    def invalidate(self, flag_id: Optional[str] = None) -> None:
        """Invalidate cache for a flag or all flags."""
        with self.lock:
            if flag_id:
                self.cache.pop(flag_id, None)
                self.cache_time.pop(flag_id, None)
            else:
                self.cache.clear()
                self.cache_time.clear()

    def stats(self) -> dict[str, Any]:
        """Cache statistics."""
        with self.lock:
            return {
                "cached_flags": len(self.cache),
                "ttl_seconds": self.ttl_seconds,
            }


# Global cache instance
_metrics_cache = CachedMetrics(ttl_seconds=3600)  # 1 hour TTL


def get_cached_metrics(flag_id: str) -> Optional[dict[str, Any]]:
    """Get cached metrics for a flag."""
    return _metrics_cache.get(flag_id)


def set_cached_metrics(flag_id: str, metrics: dict[str, Any]) -> None:
    """Cache metrics for a flag."""
    _metrics_cache.set(flag_id, metrics)


def invalidate_cache(flag_id: Optional[str] = None) -> None:
    """Invalidate cache."""
    _metrics_cache.invalidate(flag_id)


def get_cache_stats() -> dict[str, Any]:
    """Get cache statistics."""
    return _metrics_cache.stats()

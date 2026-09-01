"""
Plugin Cache Framework — Phase 1

Memoize expensive operations (LLM calls, queries).
Target: 40-60% hit rate, 50-80% LLM cost reduction.
"""

from dataclasses import dataclass
from typing import Callable, Any, Dict, Optional, List
import time
import hashlib
import json
from functools import wraps
import threading

# Sentinel for None values in cache
_MISSING = object()


@dataclass
class CacheEntry:
    """Single cache entry."""
    key: str
    value: Any
    created_at_ms: float
    ttl_seconds: int
    invalidate_on: List[str]  # Events that invalidate this entry

    def is_expired(self) -> bool:
        """Check if entry is expired."""
        elapsed_s = (time.time() * 1000 - self.created_at_ms) / 1000
        return elapsed_s > self.ttl_seconds

    def should_invalidate_on(self, event_type: str) -> bool:
        """Check if event type should invalidate."""
        return event_type in self.invalidate_on


class CacheStore:
    """In-memory cache with TTL + event invalidation."""

    def __init__(self):
        self.entries: Dict[str, CacheEntry] = {}
        self.metrics = {
            "hits": 0,
            "misses": 0,
            "invalidations": 0,
        }
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[Any]:
        """Get cached value, return None if expired/missing."""
        with self._lock:
            if key not in self.entries:
                self.metrics["misses"] += 1
                return _MISSING

            entry = self.entries[key]
            if entry.is_expired():
                del self.entries[key]
                self.metrics["misses"] += 1
                return _MISSING

            self.metrics["hits"] += 1
            return entry.value

    def set(self, key: str, value: Any, ttl_seconds: int = 3600, invalidate_on: List[str] = None):
        """Set cached value with TTL."""
        with self._lock:
            self.entries[key] = CacheEntry(
                key=key,
                value=value,
                created_at_ms=time.time() * 1000,
                ttl_seconds=ttl_seconds,
                invalidate_on=invalidate_on or [],
            )

    def invalidate_on_event(self, event_type: str):
        """Invalidate entries that depend on this event."""
        with self._lock:
            to_delete = []
            for key, entry in self.entries.items():
                if entry.should_invalidate_on(event_type):
                    to_delete.append(key)
                    self.metrics["invalidations"] += 1

            for key in to_delete:
                del self.entries[key]

    def clear(self):
        """Clear all cache."""
        with self._lock:
            self.entries.clear()

    def get_metrics(self) -> Dict[str, Any]:
        """Get cache metrics."""
        with self._lock:
            total = self.metrics["hits"] + self.metrics["misses"]
            hit_rate = self.metrics["hits"] / total if total > 0 else 0.0
            return {
                **self.metrics,
                "total_requests": total,
                "hit_rate": hit_rate,
                "entries": len(self.entries),
            }


def make_cache_key(func_name: str, *args, config_version: str = "", **kwargs) -> str:
    """Generate cache key from function + args + config."""
    # Include config version to invalidate on config changes
    key_parts = [func_name, config_version]

    # Hash args + kwargs
    args_str = json.dumps(
        {"args": [str(a) for a in args], "kwargs": {k: str(v) for k, v in kwargs.items()}},
        sort_keys=True,
        default=str,
    )
    args_hash = hashlib.sha256(args_str.encode()).hexdigest()[:8]
    key_parts.append(args_hash)

    return ":".join(key_parts)


def cached(
    ttl_seconds: int = 3600,
    key_fn: Optional[Callable] = None,
    invalidate_on: List[str] = None,
    config_version: str = "",
):
    """
    Decorator for caching plugin method results.

    Args:
        ttl_seconds: Time-to-live for cache entry
        key_fn: Custom function to generate cache key
        invalidate_on: Events that invalidate this cache
        config_version: Include in cache key (invalidates on config change)
    """

    def decorator(func):
        @wraps(func)
        async def async_wrapper(self, *args, **kwargs):
            # Get or create cache store on plugin
            if not hasattr(self, "_cache_store"):
                self._cache_store = CacheStore()

            # Generate cache key
            if key_fn:
                cache_key = key_fn(*args, **kwargs, config_version=config_version)
            else:
                cache_key = make_cache_key(
                    f"{self.__class__.__name__}:{func.__name__}",
                    *args,
                    config_version=config_version,
                    **kwargs,
                )

            # Check cache
            cached_value = self._cache_store.get(cache_key)
            if cached_value is not _MISSING:
                return cached_value

            # Cache miss: execute function
            result = await func(self, *args, **kwargs)

            # Store in cache
            self._cache_store.set(
                cache_key,
                result,
                ttl_seconds=ttl_seconds,
                invalidate_on=invalidate_on or [],
            )

            return result

        @wraps(func)
        def sync_wrapper(self, *args, **kwargs):
            # Get or create cache store on plugin
            if not hasattr(self, "_cache_store"):
                self._cache_store = CacheStore()

            # Generate cache key
            if key_fn:
                cache_key = key_fn(*args, **kwargs, config_version=config_version)
            else:
                cache_key = make_cache_key(
                    f"{self.__class__.__name__}:{func.__name__}",
                    *args,
                    config_version=config_version,
                    **kwargs,
                )

            # Check cache
            cached_value = self._cache_store.get(cache_key)
            if cached_value is not _MISSING:
                return cached_value

            # Cache miss: execute function
            result = func(self, *args, **kwargs)

            # Store in cache
            self._cache_store.set(
                cache_key,
                result,
                ttl_seconds=ttl_seconds,
                invalidate_on=invalidate_on or [],
            )

            return result

        # Return async or sync based on function
        if hasattr(func, "__code__") and "await" in str(func.__code__.co_code):
            return async_wrapper
        else:
            return sync_wrapper

    return decorator


# Global cache store (for cross-plugin caching)
_global_cache = CacheStore()


def get_global_cache() -> CacheStore:
    """Get global cache instance."""
    return _global_cache

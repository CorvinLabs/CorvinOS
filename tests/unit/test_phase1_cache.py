"""
Unit tests for Phase 1 Plugin Cache.
"""

import pytest
import asyncio
import time
from core.plugins.registry.cache import (
    CacheStore,
    cached,
    make_cache_key,
    _MISSING,
)


def test_cache_store_basic():
    """Test basic cache operations."""
    cache = CacheStore()

    # Set and get
    cache.set("key1", "value1", ttl_seconds=10)
    assert cache.get("key1") == "value1"

    # Get non-existent
    assert cache.get("key2") is _MISSING

    # Metrics
    metrics = cache.get_metrics()
    assert metrics["hits"] == 1
    assert metrics["misses"] == 1


def test_cache_expiration():
    """Test cache TTL expiration."""
    cache = CacheStore()

    cache.set("key1", "value1", ttl_seconds=1)
    assert cache.get("key1") == "value1"

    # Wait for expiration
    time.sleep(1.1)
    assert cache.get("key1") is _MISSING  # Expired


def test_cache_invalidation():
    """Test event-based cache invalidation."""
    cache = CacheStore()

    cache.set("key1", "value1", invalidate_on=["config_change"])
    cache.set("key2", "value2", invalidate_on=["session_end"])
    cache.set("key3", "value3", invalidate_on=[])

    # Invalidate on config change
    cache.invalidate_on_event("config_change")

    assert cache.get("key1") is _MISSING  # Invalidated
    assert cache.get("key2") == "value2"  # Not invalidated
    assert cache.get("key3") == "value3"  # Not invalidated


def test_cache_key_generation():
    """Test cache key generation."""
    key1 = make_cache_key("analyze_error", "timeout", config_version="v1")
    key2 = make_cache_key("analyze_error", "timeout", config_version="v1")
    key3 = make_cache_key("analyze_error", "timeout", config_version="v2")

    assert key1 == key2  # Same args, same config → same key
    assert key1 != key3  # Different config → different key


class MockPlugin:
    """Mock plugin for testing @cached decorator."""

    def __init__(self):
        self._cache_store = None

    @cached(ttl_seconds=10)
    def sync_method(self, x):
        """Simple sync method for caching."""
        return x * 2

    @cached(ttl_seconds=10)
    async def async_method(self, x):
        """Simple async method for caching."""
        await asyncio.sleep(0.01)  # Simulate work
        return x * 3


def test_cached_decorator_sync():
    """Test @cached decorator on sync method."""
    plugin = MockPlugin()

    # First call (cache miss)
    result1 = plugin.sync_method(5)
    assert result1 == 10

    # Second call (cache hit)
    result2 = plugin.sync_method(5)
    assert result2 == 10

    # Verify cache was used
    metrics = plugin._cache_store.get_metrics()
    assert metrics["hits"] == 1


@pytest.mark.asyncio
async def test_cached_decorator_async():
    """Test @cached decorator on async method."""
    plugin = MockPlugin()

    # First call (cache miss)
    result1 = await plugin.async_method(5)
    assert result1 == 15

    # Second call (cache hit)
    result2 = await plugin.async_method(5)
    assert result2 == 15

    # Verify cache was used
    metrics = plugin._cache_store.get_metrics()
    assert metrics["hits"] == 1


def test_cache_hit_rate():
    """Test cache hit rate calculation."""
    cache = CacheStore()

    for i in range(10):
        cache.set(f"key_{i}", f"value_{i}")

    # Generate hits
    for i in range(10):
        result = cache.get(f"key_{i}")  # 10 hits
        assert result is not _MISSING

    # Generate misses
    for i in range(10, 15):
        result = cache.get(f"key_{i}")  # 5 misses
        assert result is _MISSING

    metrics = cache.get_metrics()
    assert metrics["hits"] == 10
    assert metrics["misses"] == 5
    assert abs(metrics["hit_rate"] - 0.666) < 0.01  # 10/15 = 66.6%


def test_cache_clear():
    """Test cache clearing."""
    cache = CacheStore()

    cache.set("key1", "value1")
    cache.set("key2", "value2")

    cache.clear()

    assert cache.get("key1") is _MISSING
    assert cache.get("key2") is _MISSING
    assert len(cache.entries) == 0

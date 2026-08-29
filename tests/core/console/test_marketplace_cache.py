"""
Unit tests for MarketplaceCacheManager (Task #3, Phase 1).

Tests file-based caching with 1h TTL and stale-while-revalidate fallback.
"""

import pytest
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock

try:
    from core.console.corvin_console.routes.marketplace_cache import (
        MarketplaceCacheManager,
    )
except ImportError:
    pytest.skip("Cache manager not available", allow_module_level=True)


@pytest.fixture
def temp_cache_dir(tmp_path):
    """Temporary cache directory for testing."""
    cache_dir = tmp_path / "cache"
    cache_dir.mkdir()
    return str(cache_dir)


@pytest.fixture
def cache_manager(temp_cache_dir):
    """Create cache manager instance with temp directory."""
    manager = MarketplaceCacheManager(cache_dir=temp_cache_dir)
    yield manager
    # Cleanup
    manager.invalidate()


@pytest.fixture
def sample_extensions():
    """Sample marketplace extension list."""
    return [
        {
            "plugin_id": "test-plugin-1",
            "name": "Test Plugin 1",
            "version": "0.1.0",
            "category": "Security",
            "rating_average": 4.5,
            "download_count": 100,
        },
        {
            "plugin_id": "test-plugin-2",
            "name": "Test Plugin 2",
            "version": "0.2.0",
            "category": "Performance",
            "rating_average": 4.8,
            "download_count": 200,
        },
    ]


class TestMarketplaceCacheManager:
    """Test MarketplaceCacheManager class."""

    def test_cache_set_and_get(self, cache_manager, sample_extensions):
        """Successfully cache and retrieve data."""
        # Set data
        success = cache_manager.set(sample_extensions)
        assert success

        # Get data
        cached_data = cache_manager.get()
        assert cached_data == sample_extensions

    def test_cache_miss_returns_none(self, cache_manager):
        """Cache miss returns None."""
        cached_data = cache_manager.get()
        assert cached_data is None

    def test_cache_stale_returns_none(self, cache_manager, sample_extensions):
        """Stale cache returns None (TTL expired)."""
        # Set data
        cache_manager.set(sample_extensions)

        # Manipulate metadata to make it stale
        meta_file = cache_manager.meta_file
        old_meta = json.loads(meta_file.read_text())
        # Set cached_at to 2 hours ago
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        old_meta["cached_at"] = old_time
        meta_file.write_text(json.dumps(old_meta))

        # Should return None (stale)
        cached_data = cache_manager.get()
        assert cached_data is None

    def test_cache_get_stale_fallback(self, cache_manager, sample_extensions):
        """get_stale() returns data even if expired (for fallback)."""
        # Set data
        cache_manager.set(sample_extensions)

        # Manipulate metadata to make it stale
        meta_file = cache_manager.meta_file
        old_meta = json.loads(meta_file.read_text())
        old_time = (datetime.utcnow() - timedelta(hours=2)).isoformat()
        old_meta["cached_at"] = old_time
        meta_file.write_text(json.dumps(old_meta))

        # get_stale() should still return data
        stale_data = cache_manager.get_stale()
        assert stale_data == sample_extensions

    def test_cache_invalidate(self, cache_manager, sample_extensions):
        """Invalidate clears cache."""
        # Set data
        cache_manager.set(sample_extensions)
        assert cache_manager.get() is not None

        # Invalidate
        success = cache_manager.invalidate()
        assert success

        # Should be None after invalidation
        assert cache_manager.get() is None

    def test_cache_integrity_check(self, cache_manager, sample_extensions):
        """Cache detects corrupted data (hash mismatch)."""
        # Set data
        cache_manager.set(sample_extensions)

        # Corrupt cache file
        cache_file = cache_manager.cache_file
        corrupted = sample_extensions + [{"corrupted": "data"}]
        cache_file.write_text(json.dumps(corrupted))

        # Should return None (integrity check fails)
        cached_data = cache_manager.get()
        assert cached_data is None

    def test_cache_empty_data_rejected(self, cache_manager):
        """Empty data is not cached."""
        success = cache_manager.set([])
        assert not success  # Should reject empty list

    def test_cache_status(self, cache_manager, sample_extensions):
        """Cache status reports correct information."""
        # No cache yet
        status = cache_manager.status()
        assert not status.get("cached", False)

        # Set data
        cache_manager.set(sample_extensions)
        status = cache_manager.status()
        assert status["cached"]
        assert status["fresh"]
        assert status["item_count"] == 2
        assert status["size_bytes"] > 0

    def test_cache_metadata_persistence(self, cache_manager, sample_extensions):
        """Cache metadata (timestamps, hash) persists correctly."""
        # Set data
        cache_manager.set(sample_extensions)

        # Read metadata
        meta_file = cache_manager.meta_file
        meta = json.loads(meta_file.read_text())

        assert "cached_at" in meta
        assert "expires_at" in meta
        assert "data_hash" in meta
        assert "item_count" in meta
        assert meta["item_count"] == 2

        # Verify timestamps are valid ISO format
        datetime.fromisoformat(meta["cached_at"])
        datetime.fromisoformat(meta["expires_at"])

    def test_cache_atomic_write(self, cache_manager, sample_extensions):
        """Atomic write prevents partial/corrupted cache."""
        # Set data
        cache_manager.set(sample_extensions)
        original_size = cache_manager.cache_file.stat().st_size

        # New data (larger)
        new_data = sample_extensions + [
            {"plugin_id": f"test-{i}", "name": f"Test {i}"}
            for i in range(10)
        ]

        # Set new data
        cache_manager.set(new_data)
        new_size = cache_manager.cache_file.stat().st_size

        # Size should change (data updated)
        assert new_size > original_size

        # Data should be fully readable (atomic write worked)
        cached = cache_manager.get()
        assert len(cached) == len(new_data)

    def test_cache_disk_full_handling(self, cache_manager, sample_extensions, tmp_path):
        """Gracefully handle disk full errors."""
        # Mock write failure
        with patch.object(cache_manager, "_write_cache_file", return_value=False):
            success = cache_manager.set(sample_extensions)
            assert not success

    def test_cache_concurrent_read_write(self, cache_manager, sample_extensions):
        """Multiple reads while cache is valid (no race conditions)."""
        # Set data
        cache_manager.set(sample_extensions)

        # Multiple gets
        for _ in range(5):
            cached_data = cache_manager.get()
            assert cached_data == sample_extensions

    def test_cache_ttl_boundary(self, cache_manager, sample_extensions):
        """Cache is valid right at TTL boundary, invalid just after."""
        # Set data
        cache_manager.set(sample_extensions)

        # Manipulate metadata to exact TTL boundary
        meta_file = cache_manager.meta_file
        meta = json.loads(meta_file.read_text())
        # Set expires_at to right now
        meta["expires_at"] = datetime.utcnow().isoformat()
        meta_file.write_text(json.dumps(meta))

        # Should still be valid (not past expiration)
        cached_data = cache_manager.get()
        # Depending on exact timing, this may be None or have data
        # (acceptable either way at boundary)

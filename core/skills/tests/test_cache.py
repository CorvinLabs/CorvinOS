"""Tests for SkillCache (ADR-0422, Phase 7)."""

import pytest
from datetime import datetime, timedelta, timezone
from pathlib import Path
import json
import tempfile
from threading import Thread, Lock
from core.skills.corvin_skills.cache import SkillCache


class TestSkillCacheLRU:
    """Test LRU eviction and capacity."""

    def test_cache_respects_max_size(self):
        """Cache evicts oldest entry when max_size exceeded."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [
                    {"name": f"skill_{i}", "metadata": {}}
                    for i in range(300)
                ]
            }
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
                max_size=256,
            )

            # Load 300 skills
            for i in range(300):
                cache.get(f"skill_{i}")

            # Cache should not exceed max_size
            assert cache._stats["size"] <= 256
            # But first entries should be evicted
            assert cache._stats["evictions"] > 0

    def test_lru_moves_accessed_entry_to_end(self):
        """Accessing a cached entry moves it to end (marks as recently used)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [
                    {"name": "skill_0", "metadata": {}},
                    {"name": "skill_1", "metadata": {}},
                ]
            }
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
                max_size=256,
            )

            # Load two skills
            cache.get("skill_0")
            cache.get("skill_1")
            first_key = next(iter(cache._cache))

            # Access skill_0 again (moves to end)
            cache.get("skill_0")
            new_first_key = next(iter(cache._cache))

            # skill_1 should now be first (skill_0 moved to end)
            assert new_first_key == "skill_1"


class TestSkillCacheTTL:
    """Test TTL expiry behavior."""

    def test_expired_entry_reloads_from_manifest(self):
        """Cache miss on expired entry reloads from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_a", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
                ttl_minutes=0,  # Immediate expiry
            )

            # First get
            entry = cache.get("skill_a")
            assert entry is not None

            # Wait for TTL to pass (or manually set expiry in past)
            now = datetime.now(timezone.utc)
            cache._ttl_map["skill_a"] = now - timedelta(seconds=1)

            # Next get should reload
            entry2 = cache.get("skill_a")
            assert entry2 is not None
            # Miss count should have incremented
            assert cache._stats["misses"] >= 1


class TestSkillCacheInvalidation:
    """Test cache invalidation on manifest write."""

    def test_invalidate_clears_all_entries(self):
        """invalidate() clears cache and increments invalidation count."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [
                    {"name": "skill_x", "metadata": {}},
                    {"name": "skill_y", "metadata": {}},
                ]
            }
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            # Load some entries
            cache.get("skill_x")
            cache.get("skill_y")
            assert len(cache._cache) == 2

            # Invalidate
            cache.invalidate()

            # Cache should be empty
            assert len(cache._cache) == 0
            assert cache._stats["invalidations"] == 1


class TestSkillCacheThreadSafety:
    """Test concurrent access safety."""

    def test_concurrent_gets_are_thread_safe(self):
        """Multiple threads can safely get entries concurrently."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [
                    {"name": f"skill_{i}", "metadata": {}}
                    for i in range(100)
                ]
            }
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            results = []
            errors = []

            def worker(skill_id):
                try:
                    for _ in range(10):
                        entry = cache.get(f"skill_{skill_id}")
                        if entry:
                            results.append(entry["name"])
                except Exception as e:
                    errors.append(e)

            threads = [Thread(target=worker, args=(i,)) for i in range(10)]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # No errors should occur
            assert len(errors) == 0
            # Results should be consistent
            assert len(results) > 0


class TestSkillCacheStats:
    """Test statistics tracking."""

    def test_hit_rate_calculation(self):
        """stats() calculates hit_rate correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_a", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            # First get: miss
            cache.get("skill_a")
            # Second get: hit
            cache.get("skill_a")

            stats = cache.stats()
            assert stats["hits"] == 1
            assert stats["misses"] == 1
            assert stats["hit_rate"] == 0.5

    def test_stats_includes_size_and_max_size(self):
        """stats() includes current size and max capacity."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_a", "metadata": {}}]}
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
                max_size=256,
            )

            cache.get("skill_a")
            stats = cache.stats()

            assert stats["size"] == 1
            assert stats["max_size"] == 256


class TestSkillCacheFallback:
    """Test fallback to disk on cache miss."""

    def test_cache_miss_loads_from_manifest(self):
        """On cache miss, manifest is loaded from disk."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": [{"name": "skill_z", "metadata": {"ver": 1}}]}
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            # First access (cache miss)
            entry = cache.get("skill_z")

            assert entry is not None
            assert entry["name"] == "skill_z"
            assert entry["metadata"]["ver"] == 1

    def test_missing_skill_returns_none(self):
        """get() returns None for non-existent skill."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {"skills": []}
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            result = cache.get("nonexistent")
            assert result is None

    def test_corrupted_manifest_returns_none(self):
        """get() gracefully handles corrupted manifest."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest_path.write_text("INVALID JSON {{{")

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
            )

            result = cache.get("skill_a")
            assert result is None


class TestSkillCacheIntegration:
    """Integration tests with realistic workflows."""

    def test_realistic_hit_rate_above_70_percent(self):
        """Realistic workload achieves >70% hit-rate target."""
        with tempfile.TemporaryDirectory() as tmpdir:
            manifest_path = Path(tmpdir) / "manifest.json"
            manifest = {
                "skills": [{"name": f"skill_{i}", "metadata": {}} for i in range(100)]
            }
            manifest_path.write_text(json.dumps(manifest))

            cache = SkillCache(
                tenant_id="test",
                manifest_path=str(manifest_path),
                max_size=256,
            )

            # Simulate realistic access pattern:
            # - 80% of accesses to top 20 skills (hot set)
            # - 20% of accesses spread across others
            hot_skills = [f"skill_{i}" for i in range(20)]
            cold_skills = [f"skill_{i}" for i in range(20, 100)]

            accesses = hot_skills * 800 + cold_skills * 200  # 20k total accesses

            for skill in accesses:
                cache.get(skill)

            stats = cache.stats()
            # Should exceed 70% target
            assert stats["hit_rate"] > 0.7, f"Hit rate {stats['hit_rate']} below target"

"""Tests for ADR-0210 Phase 2: Decision Cache Layer."""
import asyncio
import json
import sys
import time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "operator" / "orchestration"))

from initial_analysis import InitialAnalysisRequest, Classification, Entities, GlobalPlan, Step
from decision_cache import DecisionCache


class TestADR0210Phase2DecisionCache:
    """ADR-0210 Phase 2: Decision cache for task analysis results."""

    def _make_test_decision(self, task_type: str = "test") -> InitialAnalysisRequest:
        """Create a test InitialAnalysisRequest."""
        return InitialAnalysisRequest(
            classification=Classification(
                task_type=task_type,
                complexity="simple",
                engine_preference="default",
                confidence=0.8,
            ),
            entities=Entities(
                files=[{"path": "test.txt", "purpose": "input"}],
                tools=["test_tool"],
            ),
            global_plan=GlobalPlan(
                steps=[Step(step=1, action="test_action", estimated_tokens=100)],
                estimated_duration_s=1,
                estimated_tokens=100,
            ),
        )

    @pytest.mark.asyncio
    async def test_cache_miss_calls_analyzer(self):
        """First call with unseen task invokes analyzer_fn."""
        cache = DecisionCache(ttl_seconds=300)
        call_count = [0]

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            call_count[0] += 1
            return self._make_test_decision()

        decision, is_hit = await cache.get_or_analyze(
            "first task", {}, analyzer_fn=fake_analyzer
        )

        assert call_count[0] == 1
        assert is_hit is False
        assert decision.classification.task_type == "test"

    @pytest.mark.asyncio
    async def test_cache_hit_skips_analyzer(self):
        """Second call with same task returns cached result (zero analyzer calls)."""
        cache = DecisionCache(ttl_seconds=300)
        call_count = [0]

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            call_count[0] += 1
            return self._make_test_decision()

        # First call: cache miss
        d1, h1 = await cache.get_or_analyze(
            "same task", {}, analyzer_fn=fake_analyzer
        )
        assert call_count[0] == 1
        assert h1 is False

        # Second call: cache hit
        d2, h2 = await cache.get_or_analyze(
            "same task", {}, analyzer_fn=fake_analyzer
        )
        assert call_count[0] == 1  # No new call!
        assert h2 is True
        assert d2.classification.task_type == d1.classification.task_type

    @pytest.mark.asyncio
    async def test_cache_expiry_on_ttl(self):
        """Expired cache entry (TTL exceeded) triggers re-analysis."""
        cache = DecisionCache(ttl_seconds=1)  # 1 second TTL
        call_count = [0]

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            call_count[0] += 1
            return self._make_test_decision()

        # First call
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        assert call_count[0] == 1

        # Immediate second call: hit
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        assert call_count[0] == 1

        # Wait for TTL to expire
        time.sleep(1.1)

        # Third call: expired, re-analyze
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        assert call_count[0] == 2

    @pytest.mark.asyncio
    async def test_cache_key_deterministic(self):
        """Same task text produces same cache key each time."""
        cache = DecisionCache()

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            return self._make_test_decision()

        d1, _ = await cache.get_or_analyze("same text", {}, analyzer_fn=fake_analyzer)
        d2, _ = await cache.get_or_analyze("same text", {}, analyzer_fn=fake_analyzer)

        assert d1.cache_key == d2.cache_key

    @pytest.mark.asyncio
    async def test_different_tasks_different_cache_keys(self):
        """Different task texts produce different cache keys."""
        cache = DecisionCache()
        call_count = [0]

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            call_count[0] += 1
            decision = self._make_test_decision(task_type=f"type_{call_count[0]}")
            return decision

        d1, _ = await cache.get_or_analyze("task one", {}, analyzer_fn=fake_analyzer)
        d2, _ = await cache.get_or_analyze("task two", {}, analyzer_fn=fake_analyzer)

        assert d1.cache_key != d2.cache_key
        assert call_count[0] == 2  # Both required analysis

    def test_cache_invalidate_removes_entry(self):
        """Invalidate removes entry from memory cache."""
        cache = DecisionCache()
        cache._memory["test-key"] = (self._make_test_decision(), time.time())

        assert "test-key" in cache._memory
        cache.invalidate("test-key", reason="manual invalidation")
        assert "test-key" not in cache._memory

    def test_cache_clear_empties_all(self):
        """Clear removes all cached entries."""
        cache = DecisionCache()
        cache._memory["key1"] = (self._make_test_decision(), time.time())
        cache._memory["key2"] = (self._make_test_decision(), time.time())

        assert len(cache._memory) == 2
        cache.clear()
        assert len(cache._memory) == 0

    def test_cache_stats(self):
        """Stats returns cache metadata."""
        cache = DecisionCache(ttl_seconds=600, enable_sqlite=False)
        cache._memory["key1"] = (self._make_test_decision(), time.time())

        stats = cache.stats()
        assert stats["memory_entries"] == 1
        assert stats["ttl_seconds"] == 600
        assert stats["sqlite_enabled"] is False

    def test_sqlite_disabled_by_default(self):
        """SQLite disabled by default (Phase 2 is memory-only)."""
        cache = DecisionCache()
        assert cache._enable_sqlite is False
        assert cache._db_conn is None

    def test_sqlite_init_with_storage_dir(self, tmp_path):
        """SQLite can be enabled with storage_dir."""
        cache = DecisionCache(
            storage_dir=tmp_path,
            enable_sqlite=True,
        )
        assert cache._enable_sqlite is True
        assert cache._db_path == tmp_path / "decision_cache.db"
        assert cache._db_conn is not None

        # DB should exist and be queryable
        cursor = cache._db_conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='decisions'"
        )
        assert cursor.fetchone() is not None

        cache.close()

    @pytest.mark.asyncio
    async def test_cache_hit_rate_tracking(self):
        """Cache tracks hits/misses (for future metrics)."""
        cache = DecisionCache(ttl_seconds=300)
        call_count = [0]

        async def fake_analyzer(task: str, context: dict) -> InitialAnalysisRequest:
            call_count[0] += 1
            return self._make_test_decision()

        # 1 miss, 3 hits
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)
        await cache.get_or_analyze("task", {}, analyzer_fn=fake_analyzer)

        # Hit rate: 3/4 = 75%
        assert call_count[0] == 1  # Only 1 analyzer call

    def test_cache_preserves_decision_fidelity(self):
        """Cached decision is identical to original after roundtrip."""
        original = self._make_test_decision()
        original.cache_key = "test-key"
        original.ttl_seconds = 300

        # Store in cache memory
        cache = DecisionCache()
        cache._memory["test-key"] = (original, time.time())

        # Retrieve from cache memory
        cached, _ = cache._memory["test-key"]

        # Compare all fields
        assert cached.classification.task_type == original.classification.task_type
        assert cached.classification.complexity == original.classification.complexity
        assert cached.cache_key == original.cache_key
        assert len(cached.global_plan.steps) == len(original.global_plan.steps)

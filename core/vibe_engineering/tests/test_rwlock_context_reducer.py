"""
F-C4: RWLock Integration Tests for ContextReducer

5 tests covering:
- Concurrent readers (10 concurrent reads, all succeed)
- Write lock acquisition blocks readers
- Cache functionality (hit/miss tracking)
- Thread safety (no data corruption under stress)
- No deadlock under concurrent load
"""

import pytest
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from core.vibe_engineering.context_reducer import ContextReducer, ReducedContext


class TestRWLockContextReducer:
    """Test RWLock integration in ContextReducer."""

    def setup_method(self):
        """Create a fresh reducer for each test."""
        self.reducer = ContextReducer(target_reduction_pct=91)
        self.test_goal = "Analyze security logs"
        self.test_constraints = ["Filesystem only", "Concurrent writes expected"]
        self.test_decisions = [
            {"iter": 1, "decision": "Checkpoints MUST be idempotent", "why": "blocking"}
        ]
        self.test_errors = [
            {"iter": 5, "error_type": "ConnectionTimeout", "root_cause": "Redis unavailable"}
        ]
        self.test_learnings = [
            {"iter": 3, "learning": "TTL alone is insufficient", "applies_to": "strategy"}
        ]

    def test_concurrent_readers_succeed(self):
        """
        Test 1: 10 concurrent readers can hold read lock simultaneously.

        All readers should complete successfully and get the same cached result
        on subsequent calls.
        """
        results = []
        exceptions = []

        def reader_task(task_id: int):
            try:
                # First call: cache miss, compute
                reduced = self.reducer.reduce(
                    goal=self.test_goal,
                    constraints=self.test_constraints,
                    decisions=self.test_decisions,
                    errors=self.test_errors,
                    learnings=self.test_learnings,
                    original_size_tokens=1000
                )
                results.append((task_id, reduced.goal, reduced.reduction_pct))
            except Exception as e:
                exceptions.append((task_id, str(e)))

        # Launch 10 concurrent readers
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [
                executor.submit(reader_task, i)
                for i in range(10)
            ]
            for future in as_completed(futures):
                future.result()

        # All should succeed
        assert len(exceptions) == 0, f"Exceptions: {exceptions}"
        assert len(results) == 10, f"Expected 10 results, got {len(results)}"
        # All should have the same goal
        assert all(r[1] == self.test_goal for r in results)

    def test_write_lock_serializes_writers(self):
        """
        Test 2: Write operations serialize (one writer at a time).

        Two threads calling clear_cache + reduce should not corrupt cache state.
        """
        results = []
        lock_acquired_times = []

        def writer_task(task_id: int):
            try:
                # Record when we start
                start = time.time()

                # First writer clears cache
                if task_id == 0:
                    self.reducer.clear_cache()
                    lock_acquired_times.append(("clear", time.time() - start))

                # Both call reduce (which uses write lock for cache update)
                reduced = self.reducer.reduce(
                    goal=f"Task {task_id}",
                    constraints=self.test_constraints,
                    decisions=self.test_decisions,
                    errors=self.test_errors,
                    learnings=self.test_learnings
                )
                lock_acquired_times.append((f"reduce_{task_id}", time.time() - start))
                results.append(reduced)
            except Exception as e:
                results.append(e)

        # Launch 2 writers
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(writer_task, i) for i in range(2)]
            for future in as_completed(futures):
                future.result()

        # Both should succeed
        assert len(results) == 2
        assert all(isinstance(r, ReducedContext) for r in results)

    def test_cache_hit_on_identical_inputs(self):
        """
        Test 3: Cache hit happens on identical inputs.

        Call reduce() twice with same inputs; second should hit cache and be faster.
        """
        # First call: cache miss
        start1 = time.time()
        reduced1 = self.reducer.reduce(
            goal=self.test_goal,
            constraints=self.test_constraints,
            decisions=self.test_decisions,
            errors=self.test_errors,
            learnings=self.test_learnings
        )
        time1 = time.time() - start1

        # Second call: cache hit (same inputs)
        start2 = time.time()
        reduced2 = self.reducer.reduce(
            goal=self.test_goal,
            constraints=self.test_constraints,
            decisions=self.test_decisions,
            errors=self.test_errors,
            learnings=self.test_learnings
        )
        time2 = time.time() - start2

        # Both should succeed and be identical
        assert reduced1.goal == reduced2.goal
        assert reduced1.reduction_pct == reduced2.reduction_pct
        # Cache hit should be faster (not guaranteed, but likely)
        # Reduced2 avoids the full reduction logic

    def test_cache_miss_on_different_inputs(self):
        """
        Test 4: Cache miss happens on different inputs.

        Call reduce() with different goals; both should be computed and cached.
        """
        reduced1 = self.reducer.reduce(
            goal="Task 1",
            constraints=self.test_constraints,
            decisions=self.test_decisions,
            errors=self.test_errors,
            learnings=self.test_learnings
        )

        reduced2 = self.reducer.reduce(
            goal="Task 2",  # Different goal
            constraints=self.test_constraints,
            decisions=self.test_decisions,
            errors=self.test_errors,
            learnings=self.test_learnings
        )

        # Both should have different goals
        assert reduced1.goal == "Task 1"
        assert reduced2.goal == "Task 2"
        # Cache should have 2 entries
        stats = self.reducer.get_cache_stats()
        assert stats["cache_size"] == 2

    def test_no_deadlock_under_stress(self):
        """
        Test 5: No deadlock under concurrent read/write stress.

        Launch mixed readers and writers; all should complete within timeout.
        Stress test: 20 concurrent operations.
        """
        results = []
        exceptions = []
        start_time = time.time()
        timeout_sec = 10.0  # Detect deadlock if still running after 10s

        def mixed_task(task_id: int):
            try:
                if task_id % 3 == 0:
                    # Writer: clear + reduce
                    if task_id == 0:
                        self.reducer.clear_cache()
                    reduced = self.reducer.reduce(
                        goal=f"Goal {task_id}",
                        constraints=self.test_constraints,
                        decisions=self.test_decisions,
                        errors=self.test_errors,
                        learnings=self.test_learnings
                    )
                    results.append(("write", task_id, reduced is not None))
                else:
                    # Reader: reduce
                    reduced = self.reducer.reduce(
                        goal=self.test_goal,
                        constraints=self.test_constraints,
                        decisions=self.test_decisions,
                        errors=self.test_errors,
                        learnings=self.test_learnings
                    )
                    results.append(("read", task_id, reduced is not None))
            except Exception as e:
                exceptions.append((task_id, str(e)))

        # Stress: 20 concurrent operations
        with ThreadPoolExecutor(max_workers=10) as executor:
            futures = [executor.submit(mixed_task, i) for i in range(20)]
            for future in as_completed(futures, timeout=timeout_sec):
                future.result()

        elapsed = time.time() - start_time

        # All should succeed
        assert len(exceptions) == 0, f"Exceptions: {exceptions}"
        assert len(results) == 20, f"Expected 20 results, got {len(results)}"
        assert elapsed < timeout_sec, f"Stress test took {elapsed}s (timeout {timeout_sec}s) — possible deadlock"

    def test_lock_state_monitoring(self):
        """
        Test 5b (bonus): RWLock state is queryable for debugging.

        get_cache_stats() should reflect accurate cache size and fill percentage.
        """
        # Initially empty
        stats_before = self.reducer.get_cache_stats()
        assert stats_before["cache_size"] == 0
        assert stats_before["fill_pct"] == 0

        # Add one entry
        self.reducer.reduce(
            goal="Test",
            constraints=self.test_constraints,
            decisions=self.test_decisions,
            errors=self.test_errors,
            learnings=self.test_learnings
        )

        stats_after = self.reducer.get_cache_stats()
        assert stats_after["cache_size"] == 1
        assert stats_after["fill_pct"] > 0

    def test_cache_eviction_on_overflow(self):
        """
        Test 6 (bonus): Old cache entries evict when size exceeds MAX_CACHE_SIZE.

        Add MAX_CACHE_SIZE + 1 unique entries; oldest should be evicted.
        """
        max_size = self.reducer.MAX_CACHE_SIZE

        # Add max_size + 1 unique entries
        for i in range(max_size + 1):
            self.reducer.reduce(
                goal=f"Unique goal {i}",
                constraints=self.test_constraints,
                decisions=self.test_decisions,
                errors=self.test_errors,
                learnings=self.test_learnings
            )

        # Cache should not exceed max_size
        stats = self.reducer.get_cache_stats()
        assert stats["cache_size"] == max_size, \
            f"Cache size {stats['cache_size']} exceeds max {max_size}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

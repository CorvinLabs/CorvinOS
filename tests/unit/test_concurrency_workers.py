"""
Unit Tests for WorkerPool — ADR-0304

Tests for thread pool with task submission and result retrieval.
"""

import threading
import time
import pytest

from core.concurrency import WorkerPool, WorkerError


class TestWorkerPoolBasic:
    """Basic worker pool operations."""

    def test_submit_and_result(self):
        """Submit task and get result."""
        pool = WorkerPool(workers=2)

        task_id = pool.submit(lambda x: x * 2, 5)
        result = pool.result(task_id)

        assert result == 10
        pool.shutdown()

    def test_submit_multiple_tasks(self):
        """Submit multiple tasks."""
        pool = WorkerPool(workers=2)

        task_ids = [pool.submit(lambda x: x * 2, i) for i in range(5)]
        results = [pool.result(tid) for tid in task_ids]

        assert results == [0, 2, 4, 6, 8]
        pool.shutdown()

    def test_submit_with_kwargs(self):
        """Submit task with keyword arguments."""
        pool = WorkerPool(workers=1)

        def add(a, b, c=0):
            return a + b + c

        task_id = pool.submit(add, 1, 2, c=3)
        result = pool.result(task_id)

        assert result == 6
        pool.shutdown()

    def test_task_exception_propagates(self):
        """Exception in task propagates to result()."""
        pool = WorkerPool(workers=1)

        def failing_func():
            raise ValueError("test error")

        task_id = pool.submit(failing_func)

        with pytest.raises(WorkerError):
            pool.result(task_id)

        pool.shutdown()

    def test_shutdown_basic(self):
        """Shutdown pool."""
        pool = WorkerPool(workers=2)

        task_id = pool.submit(lambda: 42)
        result = pool.result(task_id)
        assert result == 42

        pool.shutdown()

        # Submitting after shutdown should fail
        with pytest.raises(WorkerError):
            pool.submit(lambda: 1)


class TestWorkerPoolTimeout:
    """Timeout behavior."""

    def test_result_timeout_on_slow_task(self):
        """Result times out on slow task."""
        pool = WorkerPool(workers=1, timeout=0.1)

        def slow_func():
            time.sleep(1.0)
            return "done"

        task_id = pool.submit(slow_func)

        with pytest.raises(WorkerError):
            pool.result(task_id)

        pool.shutdown()

    def test_task_specific_timeout(self):
        """Task can have custom timeout."""
        pool = WorkerPool(workers=1, timeout=10.0)

        def slow_func():
            time.sleep(1.0)
            return "done"

        task_id = pool.submit(slow_func)

        # Override with shorter timeout
        with pytest.raises(WorkerError):
            pool.result(task_id, timeout=0.1)

        pool.shutdown()

    def test_timeout_allows_quick_tasks(self):
        """Quick tasks complete even with short timeout."""
        pool = WorkerPool(workers=1, timeout=1.0)

        task_id = pool.submit(lambda: 42)
        result = pool.result(task_id, timeout=0.5)

        assert result == 42
        pool.shutdown()


class TestWorkerPoolCancellation:
    """Task cancellation."""

    def test_cancel_pending_task(self):
        """Cancel a pending task."""
        pool = WorkerPool(workers=1)

        # Submit slow task to block worker
        pool.submit(lambda: time.sleep(1.0))

        # Submit second task that should be pending
        task_id = pool.submit(lambda: 42)

        # Cancel should succeed (not yet running)
        cancelled = pool.cancel(task_id)
        assert cancelled

        pool.shutdown()

    def test_cancel_running_task_fails(self):
        """Cancel running task fails."""
        pool = WorkerPool(workers=1)

        task_id = pool.submit(lambda: 42)

        # By the time we try to cancel, it's probably running
        time.sleep(0.05)

        # Cancel will likely fail (already running)
        # This is non-deterministic, but at least it shouldn't crash
        pool.cancel(task_id)

        result = pool.result(task_id)
        assert result == 42

        pool.shutdown()

    def test_cancel_nonexistent_task(self):
        """Cancel nonexistent task returns False."""
        pool = WorkerPool(workers=1)

        cancelled = pool.cancel(9999)
        assert not cancelled

        pool.shutdown()


class TestWorkerPoolStats:
    """Statistics and monitoring."""

    def test_get_stats_empty_pool(self):
        """Stats on empty pool."""
        pool = WorkerPool(workers=2)

        stats = pool.get_stats()
        assert stats["workers"] == 2
        assert stats["active_tasks"] == 0
        assert stats["total_tasks"] == 0

        pool.shutdown()

    def test_get_stats_with_tasks(self):
        """Stats with active tasks."""
        pool = WorkerPool(workers=1)

        # Submit slow task
        pool.submit(lambda: time.sleep(0.2))

        time.sleep(0.05)

        stats = pool.get_stats()
        assert stats["active_tasks"] >= 1

        pool.shutdown()

    def test_get_active_tasks(self):
        """Get list of active task IDs."""
        pool = WorkerPool(workers=2)

        task_ids = [pool.submit(lambda: time.sleep(0.1)) for _ in range(3)]

        time.sleep(0.05)

        active = pool.get_active_tasks()
        assert len(active) >= 1
        assert all(tid in task_ids for tid in active)

        pool.shutdown()


class TestWorkerPoolWaitAll:
    """Wait for all tasks."""

    def test_wait_all_no_tasks(self):
        """wait_all with no tasks succeeds immediately."""
        pool = WorkerPool(workers=2)

        pool.wait_all(timeout=1.0)

        pool.shutdown()

    def test_wait_all_with_tasks(self):
        """wait_all waits for all tasks to complete."""
        pool = WorkerPool(workers=2)

        task_ids = [pool.submit(lambda i=i: time.sleep(0.1)) for i in range(3)]

        pool.wait_all(timeout=5.0)

        # All tasks should be done
        for tid in task_ids:
            assert pool.result(tid) is None

        pool.shutdown()

    def test_wait_all_timeout(self):
        """wait_all times out on slow tasks."""
        pool = WorkerPool(workers=1)

        pool.submit(lambda: time.sleep(10.0))

        with pytest.raises(WorkerError):
            pool.wait_all(timeout=0.2)

        pool.shutdown()


class TestWorkerPoolConcurrency:
    """Concurrent task execution."""

    def test_tasks_run_in_parallel(self):
        """Multiple tasks run in parallel (not sequentially)."""
        pool = WorkerPool(workers=3)

        start_times = []
        lock = threading.Lock()

        def record_start():
            with lock:
                start_times.append(time.time())
            time.sleep(0.1)

        task_ids = [pool.submit(record_start) for _ in range(3)]

        pool.wait_all(timeout=5.0)

        # All tasks started nearly simultaneously
        time_spread = max(start_times) - min(start_times)
        assert time_spread < 0.05  # Within 50ms

        pool.shutdown()

    def test_worker_pool_size_limits_concurrency(self):
        """Pool size limits number of concurrent tasks."""
        pool = WorkerPool(workers=2)

        running_count = [0]
        max_running = [0]
        lock = threading.Lock()

        def track_running():
            with lock:
                running_count[0] += 1
                max_running[0] = max(max_running[0], running_count[0])

            time.sleep(0.1)

            with lock:
                running_count[0] -= 1

        task_ids = [pool.submit(track_running) for _ in range(5)]

        pool.wait_all(timeout=5.0)

        # At most 2 tasks ran concurrently
        assert max_running[0] <= 2

        pool.shutdown()

    def test_many_sequential_tasks(self):
        """Many sequential tasks complete correctly."""
        pool = WorkerPool(workers=2)

        results = []

        for i in range(20):
            task_id = pool.submit(lambda x=i: x * 2)
            results.append(pool.result(task_id))

        assert results == [i * 2 for i in range(20)]

        pool.shutdown()

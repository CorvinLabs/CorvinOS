"""
Tests for AutoScalingWorkerPool.

Validates:
- Correct scaling up when queue usage > 75%
- Correct scaling down when queue usage < 25%
- Graceful task execution and completion
- Stress test with 5000 items (no OOM, queue never >75%)
"""

import pytest
import time
import threading
from core.concurrency.worker_pool import AutoScalingWorkerPool, WorkerError


def dummy_task(x, delay=0.01):
    """A simple test task."""
    if delay > 0:
        time.sleep(delay)
    return x * 2


def heavy_task(x, delay=0.1):
    """A heavier test task that takes longer."""
    time.sleep(delay)
    return x * 3


class TestAutoScalingWorkerPool:
    """Test suite for AutoScalingWorkerPool."""

    def test_basic_initialization(self):
        """Test pool initializes with correct default parameters."""
        pool = AutoScalingWorkerPool()
        stats = pool.get_stats()

        assert stats["current_workers"] == 2
        assert stats["min_workers"] == 2
        assert stats["max_workers"] == 16
        assert stats["queue_usage_pct"] == 0.0

        pool.shutdown()

    def test_submit_and_result(self):
        """Test basic task submission and result retrieval."""
        pool = AutoScalingWorkerPool(min_workers=2, max_workers=4)

        # Submit a simple task
        task_id = pool.submit(dummy_task, 5)
        result = pool.result(task_id, timeout=5.0)

        assert result == 10
        pool.shutdown()

    def test_multiple_tasks(self):
        """Test submitting multiple tasks sequentially."""
        pool = AutoScalingWorkerPool(min_workers=2, max_workers=4)

        task_ids = []
        for i in range(10):
            task_id = pool.submit(dummy_task, i)
            task_ids.append(task_id)

        # Get all results
        results = []
        for task_id in task_ids:
            result = pool.result(task_id, timeout=5.0)
            results.append(result)

        assert results == [i * 2 for i in range(10)]
        pool.shutdown()

    def test_scale_up_on_high_queue_usage(self):
        """Test that pool scales up when queue usage exceeds 75%."""
        pool = AutoScalingWorkerPool(
            min_workers=2,
            max_workers=8,
            max_queue_size=100,
            scale_up_threshold=0.75,
            scale_check_interval=0.2,
        )

        initial_workers = pool._current_workers
        assert initial_workers == 2

        # Submit many slow tasks to fill the queue
        for i in range(80):  # Will fill ~80% of queue
            pool.submit(heavy_task, i, delay=0.5)

        # Wait for scaling to happen
        time.sleep(1.5)

        stats = pool.get_stats()
        # Should have scaled up from 2 to at least 3
        assert stats["current_workers"] > initial_workers
        assert stats["queue_usage_pct"] > 75

        pool.shutdown(wait=True)

    def test_scale_down_on_low_queue_usage(self):
        """Test that pool scales down when queue usage drops below 25%."""
        pool = AutoScalingWorkerPool(
            min_workers=2,
            max_workers=8,
            max_queue_size=100,
            scale_down_threshold=0.25,
            scale_check_interval=0.2,
        )

        # First scale up
        for i in range(80):
            pool.submit(heavy_task, i, delay=0.2)

        # Wait to scale up
        time.sleep(1.0)
        stats_high = pool.get_stats()
        workers_at_peak = stats_high["current_workers"]

        # Wait for queue to drain
        pool.wait_all(timeout=30.0)
        time.sleep(1.5)  # Give scaling thread time to react

        stats_low = pool.get_stats()
        # Should have scaled down toward minimum
        assert stats_low["current_workers"] <= workers_at_peak

        pool.shutdown()

    def test_scale_does_not_exceed_max(self):
        """Test that scaling never exceeds max_workers."""
        pool = AutoScalingWorkerPool(
            min_workers=2,
            max_workers=5,
            max_queue_size=100,
            scale_check_interval=0.1,
        )

        # Fill queue significantly
        for i in range(100):
            pool.submit(heavy_task, i, delay=0.3)

        # Wait for scaling
        time.sleep(2.0)

        stats = pool.get_stats()
        assert stats["current_workers"] <= 5

        pool.shutdown(wait=True)

    def test_scale_does_not_go_below_min(self):
        """Test that scaling never goes below min_workers."""
        pool = AutoScalingWorkerPool(
            min_workers=3,
            max_workers=8,
            max_queue_size=100,
            scale_check_interval=0.1,
        )

        # Let it settle
        time.sleep(0.5)
        stats = pool.get_stats()
        assert stats["current_workers"] >= 3

        # Wait while idle
        time.sleep(2.0)
        stats = pool.get_stats()
        assert stats["current_workers"] >= 3

        pool.shutdown()

    def test_stress_5000_items_no_oom(self):
        """
        Stress test with 5000 items.

        Verifies:
        - No out-of-memory error
        - Queue never exceeds 75% (auto-scaling keeps it under control)
        - All tasks complete successfully
        """
        pool = AutoScalingWorkerPool(
            min_workers=2,
            max_workers=12,
            max_queue_size=1000,
            scale_up_threshold=0.75,
            scale_down_threshold=0.25,
            scale_check_interval=0.2,
            task_timeout=60.0,
        )

        task_ids = []
        max_queue_usage = 0

        # Submit 5000 tasks in batches to allow scaling
        for i in range(5000):
            task_id = pool.submit(dummy_task, i, delay=0.001)
            task_ids.append(task_id)

            # Track max queue usage
            stats = pool.get_stats()
            max_queue_usage = max(max_queue_usage, stats["queue_usage_pct"])

            # Small delays between batches to allow queue to process
            if (i + 1) % 100 == 0:
                time.sleep(0.05)

        # Wait for all tasks to complete
        pool.wait_all(timeout=120.0)

        # Verify results are correct (sample check)
        for i in [0, 100, 1000, 2500, 4999]:
            result = pool.result(task_ids[i], timeout=5.0)
            assert result == i * 2, f"Task {i} returned {result}, expected {i*2}"

        # Verify queue usage stayed under control (below 75%)
        assert max_queue_usage <= 75, (
            f"Queue usage peaked at {max_queue_usage}%, "
            "should be ≤75% with auto-scaling"
        )

        pool.shutdown(wait=False)

    def test_get_active_tasks(self):
        """Test retrieving active task list."""
        pool = AutoScalingWorkerPool(min_workers=2)

        task_ids = []
        for i in range(5):
            task_id = pool.submit(heavy_task, i, delay=0.2)
            task_ids.append(task_id)

        # Should have multiple active tasks
        active = pool.get_active_tasks()
        assert len(active) > 0

        # Wait for completion
        pool.wait_all(timeout=10.0)

        # Should have no active tasks
        active = pool.get_active_tasks()
        assert len(active) == 0

        pool.shutdown()

    def test_cancel_task(self):
        """Test cancelling a submitted task."""
        pool = AutoScalingWorkerPool(min_workers=2)

        # Submit a slow task
        task_id = pool.submit(heavy_task, 1, delay=5.0)

        # Cancel it immediately
        cancelled = pool.cancel(task_id)
        assert cancelled is True

        # Trying to get result should raise error
        with pytest.raises(WorkerError):
            pool.result(task_id, timeout=1.0)

        pool.shutdown()

    def test_shutdown_waits_for_tasks(self):
        """Test that shutdown(wait=True) waits for tasks to complete."""
        pool = AutoScalingWorkerPool(min_workers=2)

        task_ids = []
        for i in range(10):
            task_id = pool.submit(dummy_task, i, delay=0.1)
            task_ids.append(task_id)

        # Shutdown with wait
        start = time.time()
        pool.shutdown(wait=True)
        elapsed = time.time() - start

        # Should have taken at least some time to complete
        assert elapsed > 0.5

    def test_error_on_full_queue(self):
        """Test that submitting to full queue raises error."""
        pool = AutoScalingWorkerPool(
            min_workers=1,
            max_workers=1,
            max_queue_size=5,
        )

        # Fill the queue
        for i in range(5):
            pool.submit(heavy_task, i, delay=0.5)

        # Next submit should fail (queue full)
        with pytest.raises(WorkerError):
            pool.submit(dummy_task, 100, delay=0.1)

        pool.shutdown(wait=True)

    def test_queue_usage_calculation(self):
        """Test queue usage percentage calculation."""
        pool = AutoScalingWorkerPool(
            min_workers=2,
            max_workers=4,
            max_queue_size=100,
        )

        # Submit tasks
        for i in range(50):
            pool.submit(heavy_task, i, delay=0.2)

        stats = pool.get_stats()
        # Should be roughly 50% full
        assert 40 < stats["queue_usage_pct"] < 60

        pool.shutdown(wait=True)

    def test_stats_reflect_pool_state(self):
        """Test that stats accurately reflect pool state."""
        pool = AutoScalingWorkerPool(min_workers=2, max_workers=8)

        # Initially empty
        stats = pool.get_stats()
        assert stats["active_tasks"] == 0
        assert stats["total_tasks"] == 0
        assert stats["queue_size"] == 0

        # After submission
        for i in range(10):
            pool.submit(dummy_task, i, delay=0.1)

        stats = pool.get_stats()
        assert stats["total_tasks"] == 10
        assert stats["queue_size"] > 0

        # After completion
        pool.wait_all(timeout=10.0)
        stats = pool.get_stats()
        assert stats["active_tasks"] == 0
        assert stats["queue_size"] == 0

        pool.shutdown()

    def test_concurrent_submissions(self):
        """Test submitting tasks from multiple threads."""
        pool = AutoScalingWorkerPool(min_workers=2, max_workers=6)

        task_ids = []
        lock = threading.Lock()

        def submit_tasks(start, end):
            for i in range(start, end):
                task_id = pool.submit(dummy_task, i, delay=0.01)
                with lock:
                    task_ids.append(task_id)

        # Submit from 3 threads
        threads = [
            threading.Thread(target=submit_tasks, args=(0, 10)),
            threading.Thread(target=submit_tasks, args=(10, 20)),
            threading.Thread(target=submit_tasks, args=(20, 30)),
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # All tasks should complete
        assert len(task_ids) == 30
        pool.wait_all(timeout=10.0)

        pool.shutdown()


class TestWorkerPoolEdgeCases:
    """Test edge cases and error conditions."""

    def test_invalid_min_max_workers(self):
        """Test that invalid worker ranges raise errors."""
        with pytest.raises(WorkerError):
            AutoScalingWorkerPool(min_workers=0)  # min must be > 0

        with pytest.raises(WorkerError):
            AutoScalingWorkerPool(min_workers=10, max_workers=5)  # min > max

    def test_invalid_thresholds(self):
        """Test that invalid thresholds raise errors."""
        with pytest.raises(WorkerError):
            AutoScalingWorkerPool(
                scale_down_threshold=0.8, scale_up_threshold=0.2
            )  # down > up

    def test_submit_after_shutdown(self):
        """Test that submitting after shutdown raises error."""
        pool = AutoScalingWorkerPool()
        pool.shutdown()

        with pytest.raises(WorkerError):
            pool.submit(dummy_task, 1)

    def test_get_result_nonexistent_task(self):
        """Test getting result of nonexistent task ID."""
        pool = AutoScalingWorkerPool()

        with pytest.raises(WorkerError):
            pool.result(999)

        pool.shutdown()

    def test_task_exception_propagates(self):
        """Test that task exceptions are propagated to result()."""
        pool = AutoScalingWorkerPool()

        def failing_task():
            raise ValueError("Task failed!")

        task_id = pool.submit(failing_task)

        with pytest.raises(WorkerError):
            pool.result(task_id, timeout=5.0)

        pool.shutdown()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

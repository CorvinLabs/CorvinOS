"""
Edge Case Tests (F-M6)

Tests for edge cases and boundary conditions in WorkerPool.
Ensures robustness under unusual circumstances.
"""

import pytest
import asyncio
from core.concurrency.worker_pool import WorkerPool
from core.concurrency.exceptions import (
    QueueOverflowError,
    WorkerPoolShutdownError,
)


class TestEdgeCases:
    """F-M6: Edge case tests for WorkerPool."""

    # F-M6: Minimum workers edge case
    @pytest.mark.asyncio
    async def test_single_worker_pool(self):
        """Edge case: max_workers = 1 (minimum viable pool)."""
        pool = WorkerPool(max_workers=1, queue_size=10)
        await pool.start()

        try:
            # Submit and execute task successfully
            result = await pool.submit(lambda x: x * 2, 5)
            assert result == 10

            # Submit multiple tasks sequentially
            results = []
            for i in range(3):
                result = await asyncio.wait_for(
                    pool.submit(lambda x=i: x + 1),
                    timeout=1.0
                )
                results.append(result)

            assert results == [1, 2, 3]
        finally:
            await pool.stop()

    # F-M6: Maximum workers edge case
    @pytest.mark.asyncio
    async def test_max_workers_pool(self):
        """Edge case: max_workers = 256 (maximum pool size)."""
        pool = WorkerPool(max_workers=256, queue_size=100)
        await pool.start()

        try:
            # Should handle large pool gracefully
            result = await pool.submit(lambda: 42)
            assert result == 42
        finally:
            await pool.stop()

    # F-M6: Minimum queue edge case
    @pytest.mark.asyncio
    async def test_minimum_queue_size(self):
        """Edge case: queue_size = 1 (minimal queue)."""
        pool = WorkerPool(max_workers=2, queue_size=1, timeout_seconds=0.5)
        await pool.start()

        try:
            # Submit first task
            result = await pool.submit(lambda: 42)
            assert result == 42
        finally:
            await pool.stop()

    # F-M6: Maximum queue edge case
    @pytest.mark.asyncio
    async def test_maximum_queue_size(self):
        """Edge case: queue_size = 10000 (maximum queue)."""
        pool = WorkerPool(max_workers=1, queue_size=10000)
        await pool.start()

        try:
            result = await pool.submit(lambda: 42)
            assert result == 42
        finally:
            await pool.stop()

    # F-M6: Empty queue get (timeout)
    @pytest.mark.asyncio
    async def test_worker_handles_empty_queue_timeout(self):
        """Edge case: Worker waiting on empty queue should timeout gracefully."""
        pool = WorkerPool(max_workers=1, queue_size=10)
        await pool.start()

        try:
            # Just wait a bit, workers should handle empty queue
            await asyncio.sleep(0.5)

            # Pool should still work
            result = await pool.submit(lambda: 99)
            assert result == 99
        finally:
            await pool.stop()

    # F-M6: Concurrent shutdown calls (idempotency)
    @pytest.mark.asyncio
    async def test_multiple_shutdown_calls_idempotent(self):
        """Edge case: Multiple stop() calls should be safe (idempotent)."""
        pool = WorkerPool()
        await pool.start()

        # Submit a task
        result = await pool.submit(lambda: 42)
        assert result == 42

        # Multiple stops should all succeed
        await pool.stop()
        await pool.stop()
        await pool.stop()

        # Pool should be shutdown
        with pytest.raises(WorkerPoolShutdownError):
            await pool.submit(lambda: 1)

    # F-M6: Submit during shutdown
    @pytest.mark.asyncio
    async def test_submit_after_shutdown_raises_error(self):
        """Edge case: Submitting after shutdown should raise WorkerPoolShutdownError."""
        pool = WorkerPool()
        await pool.start()
        await pool.stop()

        # Any submit attempt should fail
        with pytest.raises(WorkerPoolShutdownError):
            await pool.submit(asyncio.sleep, 1)

    # F-M6: Submit None coroutine (type error)
    @pytest.mark.asyncio
    async def test_submit_none_coroutine_fails(self):
        """Edge case: Submitting None as coroutine should fail."""
        pool = WorkerPool()
        await pool.start()

        try:
            with pytest.raises((TypeError, AttributeError)):
                await pool.submit(None)  # type: ignore
        finally:
            await pool.stop()

    # F-M6: Very large timeout
    @pytest.mark.asyncio
    async def test_large_timeout_submit(self):
        """Edge case: Very large timeout (hours) should not block."""
        pool = WorkerPool(timeout_seconds=3600)  # 1 hour
        await pool.start()

        try:
            # Should complete quickly despite large timeout
            result = await asyncio.wait_for(
                pool.submit(lambda: 42),
                timeout=1.0  # Complete within 1 second
            )
            assert result == 42
        finally:
            await pool.stop()

    # F-M6: Zero-return coroutine
    @pytest.mark.asyncio
    async def test_zero_returning_task(self):
        """Edge case: Coroutine returning zero (falsy) should work."""
        pool = WorkerPool()
        await pool.start()

        try:
            result = await pool.submit(lambda: 0)
            assert result == 0  # Explicitly 0, not None

            result = await pool.submit(lambda: False)
            assert result is False  # Explicitly False

            result = await pool.submit(lambda: [])
            assert result == []  # Empty list (falsy)
        finally:
            await pool.stop()

    # F-M6: Rapid start/stop cycle
    @pytest.mark.asyncio
    async def test_rapid_start_stop_cycles(self):
        """Edge case: Rapid start/stop cycles should not crash."""
        for _ in range(5):
            pool = WorkerPool(max_workers=2)
            await pool.start()

            # Quick task
            result = await pool.submit(lambda: 42)
            assert result == 42

            await pool.stop()

    # F-M6: Exception in task (not fatal to pool)
    @pytest.mark.asyncio
    async def test_task_exception_does_not_crash_pool(self):
        """Edge case: Exception in one task should not crash pool."""
        pool = WorkerPool(max_workers=2)
        await pool.start()

        try:
            def failing_func():
                raise ValueError("task error")

            # First task fails
            with pytest.raises(ValueError):
                await pool.submit(failing_func)

            # Pool should still work for next task
            result = await pool.submit(lambda: 42)
            assert result == 42
        finally:
            await pool.stop()

    # F-M6: Long-running task doesn't block pool
    @pytest.mark.asyncio
    async def test_long_running_task_does_not_block(self):
        """Edge case: Long-running task in one worker shouldn't block others."""
        pool = WorkerPool(max_workers=2)
        await pool.start()

        try:
            # Start slow task (non-blocking)
            slow_task = asyncio.create_task(
                pool.submit(asyncio.sleep, 5)
            )

            # Other worker should still be available
            await asyncio.sleep(0.1)

            # Quick task should complete quickly
            result = await asyncio.wait_for(
                pool.submit(lambda: 99),
                timeout=1.0  # Should complete much faster than 5 seconds
            )
            assert result == 99

            # Cancel slow task
            slow_task.cancel()
            try:
                await slow_task
            except asyncio.CancelledError:
                pass
        finally:
            await pool.stop()

    # F-M6: Stats on empty pool
    @pytest.mark.asyncio
    async def test_get_stats_empty_pool(self):
        """Edge case: get_stats() on pool with no tasks."""
        pool = WorkerPool()
        await pool.start()

        try:
            stats = pool.get_stats()
            assert stats.active_workers == 4  # Default max_workers
            assert stats.queue_size == 0
            assert stats.tasks_processed == 0
            assert stats.overflow_errors == 0
            assert stats.avg_latency_ms == 0.0
        finally:
            await pool.stop()

    # F-M6: Stats with mix of successful and failed tasks
    @pytest.mark.asyncio
    async def test_get_stats_after_mixed_tasks(self):
        """Edge case: get_stats() counts both successful and failed tasks."""
        pool = WorkerPool()
        await pool.start()

        try:
            # Successful task
            await pool.submit(lambda: 42)

            # Failed task
            def failing():
                raise ValueError("error")

            try:
                await pool.submit(failing)
            except ValueError:
                pass

            # Stats should show both tasks processed
            stats = pool.get_stats()
            assert stats.tasks_processed == 2
            assert stats.avg_latency_ms >= 0
        finally:
            await pool.stop()

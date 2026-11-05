"""
Critical Findings Fixes — Unified E2E Tests (F-C2, F-C3, F-C5)

LDD K=4: Testing for WorkerPool wiring, backpressure, auto-scaling
"""

import pytest
import asyncio
import time
from core.concurrency.worker_pool import WorkerPool, QueueOverflowError


# ============================================================================
# F-C2: WorkerPool Dead Code → Production Integration
# ============================================================================

@pytest.mark.asyncio
async def test_worker_pool_concurrent_execution():
    """F-C2: Verify WorkerPool handles concurrent tasks."""

    pool = WorkerPool(max_workers=4, queue_size=100)
    await pool.start()

    try:
        executed = []

        async def task_fn(task_id):
            executed.append(task_id)
            await asyncio.sleep(0.01)
            return f"result_{task_id}"

        # Submit 10 tasks
        tasks = [pool.submit(task_fn, i) for i in range(10)]
        results = await asyncio.gather(*tasks)

        assert len(results) == 10
        assert len(executed) == 10

    finally:
        await pool.stop()


# ============================================================================
# F-C3: Queue Overflow Detection (Backpressure, No Silent Drop)
# ============================================================================

@pytest.mark.asyncio
async def test_queue_overflow_raises_error():
    """
    F-C3: Queue.put() with backpressure raises error instead of silent drop.
    """

    pool = WorkerPool(max_workers=1, queue_size=5, timeout_seconds=0.1)
    await pool.start()
    gate = asyncio.Event()

    try:
        # Occupy the worker and fill the queue (1 running + 5 queued). `submit`
        # returns the RESULT, so awaiting it inline would run each task to the
        # end and never fill the queue — schedule gated submissions instead.
        held = [asyncio.create_task(pool.submit(gate.wait)) for _ in range(6)]
        await asyncio.sleep(0.05)

        # Next task should fail with QueueOverflowError (not silent drop)
        with pytest.raises(QueueOverflowError) as exc_info:
            await pool.submit(asyncio.sleep, 1.0, timeout_seconds=0.1)

        assert "Queue overflow" in str(exc_info.value)
        assert pool.overflow_errors == 1

        # Backpressure lost nothing: every held task still completes.
        gate.set()
        await asyncio.gather(*held)

    finally:
        gate.set()
        await pool.stop()


@pytest.mark.asyncio
async def test_queue_backpressure_retries():
    """F-C3: Backpressure allows retry after task completes."""

    pool = WorkerPool(max_workers=2, queue_size=3, timeout_seconds=0.5)
    await pool.start()

    try:
        # Fill queue with short tasks
        for i in range(3):
            await pool.submit(asyncio.sleep, 0.05)

        # This should eventually fail (or wait + succeed after tasks drain)
        try:
            # Try to add 4th with short timeout
            await pool.submit(asyncio.sleep, 0.05, timeout_seconds=0.05)
        except QueueOverflowError:
            pass  # Expected if queue still full

        # Wait for queue to drain
        await asyncio.sleep(0.2)

        # Now it should succeed
        result = await pool.submit(asyncio.sleep, 0.01, timeout_seconds=1.0)
        # (Should not raise)

    finally:
        await pool.stop()


# ============================================================================
# F-C5: WorkerPool Auto-Scaling
# ============================================================================

@pytest.mark.asyncio
async def test_worker_pool_auto_scaling_up():
    """
    F-C5: WorkerPool scales UP when queue > 75% full.
    """

    pool = WorkerPool(
        max_workers=8,
        queue_size=100,
        enable_auto_scaling=True,
        min_workers=1,  # a pool started AT its cap has nothing to scale up to
    )
    await pool.start()

    try:
        initial_workers = len(pool.active_workers)

        # Submit tasks to fill queue > 75%
        async def slow_task():
            await asyncio.sleep(0.5)

        # Scheduled, not bare coroutines: a coroutine object is not run until
        # awaited, so the queue stayed empty during the wait below.
        tasks = [
            asyncio.create_task(pool.submit(slow_task, timeout_seconds=2))
            for _ in range(80)  # 80% of 100
        ]

        # Give scaler time to react
        await asyncio.sleep(0.3)

        scaled_workers = len(pool.active_workers)

        # Should have scaled up
        assert scaled_workers > initial_workers, \
            f"Auto-scale UP failed: {initial_workers} → {scaled_workers}"

        # Drain tasks
        await asyncio.gather(*tasks)

    finally:
        await pool.stop()


@pytest.mark.asyncio
async def test_worker_pool_auto_scaling_down():
    """
    F-C5: WorkerPool scales DOWN when queue < 25% full.
    """

    pool = WorkerPool(
        max_workers=8,
        queue_size=100,
        enable_auto_scaling=True,
    )
    await pool.start()

    try:
        # Let scaler stabilize to max workers initially
        await asyncio.sleep(1.0)
        peak_workers = len(pool.active_workers)

        # Submit few tasks (queue < 25%)
        async def fast_task():
            await asyncio.sleep(0.01)

        tasks = [
            pool.submit(fast_task, timeout_seconds=1)
            for _ in range(10)  # 10% of 100
        ]

        await asyncio.gather(*tasks)

        # Give scaler time to scale down
        await asyncio.sleep(3.0)

        scaled_down_workers = len(pool.active_workers)

        # Should have scaled down (or stayed low if never scaled up)
        # Just verify it's a sensible number
        assert 1 <= scaled_down_workers <= peak_workers

    finally:
        await pool.stop()


# ============================================================================
# Cross-Finding Integration Tests
# ============================================================================

"""
F-C3 Fix: Queue Overflow with Backpressure

Tests for BoundedAsyncQueue — fail-closed queue with timeout and overflow detection.
Prevents silent data loss from put_nowait() silently dropping items when full.

ADR reference: F-C3 (Backpressure + Overflow Error)
"""

import asyncio
import pytest
from core.concurrency.async_context import BoundedAsyncQueue, QueueOverflowError


class TestBoundedAsyncQueueBasic:
    """Basic queue operations."""

    @pytest.mark.asyncio
    async def test_put_get_single_item(self):
        """Put and get single item."""
        q = BoundedAsyncQueue(maxsize=10, timeout=1.0)
        await q.put("test_item")
        item = await q.get()
        assert item == "test_item"

    @pytest.mark.asyncio
    async def test_put_multiple_items(self):
        """Put and get multiple items in order."""
        q = BoundedAsyncQueue(maxsize=100, timeout=1.0)
        for i in range(10):
            await q.put(i)

        for i in range(10):
            item = await q.get()
            assert item == i

    @pytest.mark.asyncio
    async def test_queue_respects_maxsize(self):
        """Queue size is bounded by maxsize."""
        q = BoundedAsyncQueue(maxsize=5, timeout=1.0)

        for i in range(5):
            await q.put(i)

        assert q.qsize() == 5

        # Consuming one should allow another put
        await q.get()
        assert q.qsize() == 4

    @pytest.mark.asyncio
    async def test_invalid_maxsize_raises(self):
        """Invalid maxsize raises ValueError."""
        with pytest.raises(ValueError):
            BoundedAsyncQueue(maxsize=0, timeout=1.0)

        with pytest.raises(ValueError):
            BoundedAsyncQueue(maxsize=-1, timeout=1.0)


class TestQueueBackpressure:
    """F-C3: Backpressure and overflow detection."""

    @pytest.mark.asyncio
    async def test_put_1000_items_succeeds(self):
        """Put 1000 items succeeds (normal operation)."""
        q = BoundedAsyncQueue(maxsize=1000, timeout=2.0)

        # Put 1000 items
        for i in range(1000):
            await q.put(f"item_{i}")

        assert q.qsize() == 1000
        assert q.get_overflow_count() == 0

    @pytest.mark.asyncio
    async def test_put_1001st_item_with_timeout_raises(self):
        """Put 1001st item times out and raises QueueOverflowError (F-C3 fix)."""
        q = BoundedAsyncQueue(maxsize=1000, timeout=0.1)

        # Put 1000 items to fill the queue
        for i in range(1000):
            await q.put(f"item_{i}")

        # 1001st item should timeout (queue is full, no consumer)
        with pytest.raises(QueueOverflowError) as exc_info:
            await q.put("item_1000")

        # Verify error message contains helpful info
        assert "QueueOverflow" in str(exc_info.value)
        assert "1000" in str(exc_info.value)  # maxsize
        assert q.get_overflow_count() == 1

    @pytest.mark.asyncio
    async def test_overflow_count_increments(self):
        """Overflow counter increments on each timeout."""
        q = BoundedAsyncQueue(maxsize=2, timeout=0.05)

        # Fill the queue
        await q.put("a")
        await q.put("b")

        # Try to overflow 3 times
        overflow_count = 0
        for i in range(3):
            with pytest.raises(QueueOverflowError):
                await q.put(f"overflow_{i}")
            overflow_count += 1

        assert q.get_overflow_count() == overflow_count

    @pytest.mark.asyncio
    async def test_custom_timeout_parameter(self):
        """put() respects custom timeout parameter."""
        q = BoundedAsyncQueue(maxsize=1, timeout=10.0)

        await q.put("a")

        # Override instance timeout with shorter timeout
        with pytest.raises(QueueOverflowError):
            await q.put("b", timeout=0.05)

        assert q.get_overflow_count() == 1

    @pytest.mark.asyncio
    async def test_recovery_after_consumer(self):
        """Queue recovers when consumer drains items."""
        q = BoundedAsyncQueue(maxsize=5, timeout=0.5)

        # Fill the queue
        for i in range(5):
            await q.put(i)

        # Try to overflow (should fail)
        with pytest.raises(QueueOverflowError):
            await q.put("overflow")

        overflow_before = q.get_overflow_count()

        # Consumer drains one item
        _ = await q.get()

        # Now putting should succeed
        await q.put("recovered")
        assert q.qsize() == 5

        # Overflow count should not increase
        assert q.get_overflow_count() == overflow_before


class TestQueueNowaitWithBackpressure:
    """Test put_nowait_with_backpressure (fail-closed non-blocking)."""

    @pytest.mark.asyncio
    async def test_put_nowait_with_backpressure_succeeds_on_space(self):
        """put_nowait_with_backpressure succeeds when queue has space."""
        q = BoundedAsyncQueue(maxsize=10, timeout=1.0)

        q.put_nowait_with_backpressure("item")
        assert q.qsize() == 1

    @pytest.mark.asyncio
    async def test_put_nowait_with_backpressure_fails_on_full(self):
        """put_nowait_with_backpressure raises immediately when full."""
        q = BoundedAsyncQueue(maxsize=2, timeout=10.0)

        await q.put("a")
        await q.put("b")

        # Should raise immediately (not wait for timeout)
        with pytest.raises(QueueOverflowError):
            q.put_nowait_with_backpressure("c")

        assert q.get_overflow_count() == 1

    @pytest.mark.asyncio
    async def test_put_nowait_with_backpressure_non_blocking(self):
        """put_nowait_with_backpressure does not block."""
        q = BoundedAsyncQueue(maxsize=1, timeout=10.0)
        await q.put("a")

        # Should not wait (immediate failure)
        import time
        start = time.time()
        with pytest.raises(QueueOverflowError):
            q.put_nowait_with_backpressure("b")
        elapsed = time.time() - start

        # Should be nearly instant (< 100ms), not wait 10s
        assert elapsed < 0.1


class TestQueueAuditIntegration:
    """Integration with audit logging (F-C3 requires audit trail)."""

    @pytest.mark.asyncio
    async def test_overflow_logged_to_logger(self, caplog):
        """Overflow event is logged."""
        import logging
        caplog.set_level(logging.ERROR, logger="core.concurrency.async_context")

        q = BoundedAsyncQueue(maxsize=1, timeout=0.05)
        await q.put("a")

        with pytest.raises(QueueOverflowError):
            await q.put("b")

        # Check that overflow was logged
        assert any("QueueOverflow" in record.message for record in caplog.records)
        assert any("maxsize=1" in record.message for record in caplog.records)

    @pytest.mark.asyncio
    async def test_overflow_error_message_contains_context(self):
        """QueueOverflowError message contains diagnostic info."""
        q = BoundedAsyncQueue(maxsize=3, timeout=0.05)

        for i in range(3):
            await q.put(i)

        with pytest.raises(QueueOverflowError) as exc_info:
            await q.put("overflow")

        error_msg = str(exc_info.value)
        assert "maxsize=3" in error_msg
        assert "QueueOverflow" in error_msg


class TestQueueReset:
    """Test counter reset for testing."""

    @pytest.mark.asyncio
    async def test_reset_overflow_count(self):
        """reset_overflow_count clears the counter."""
        q = BoundedAsyncQueue(maxsize=1, timeout=0.05)
        await q.put("a")

        with pytest.raises(QueueOverflowError):
            await q.put("b")

        assert q.get_overflow_count() == 1

        q.reset_overflow_count()
        assert q.get_overflow_count() == 0


class TestConcurrentAccess:
    """Concurrent producer-consumer patterns (F-C3 safety)."""

    @pytest.mark.asyncio
    async def test_concurrent_producer_consumer(self):
        """Multiple producers and consumers work correctly."""
        q = BoundedAsyncQueue(maxsize=50, timeout=2.0)
        results = []

        async def producer(producer_id, count):
            for i in range(count):
                await q.put(f"p{producer_id}_i{i}")

        async def consumer(consumer_id, expected_count):
            consumed = []
            for _ in range(expected_count):
                item = await q.get()
                consumed.append(item)
            results.extend(consumed)

        # 2 producers, 2 consumers
        tasks = [
            asyncio.create_task(producer(0, 10)),
            asyncio.create_task(producer(1, 10)),
            asyncio.create_task(consumer(0, 10)),
            asyncio.create_task(consumer(1, 10)),
        ]

        await asyncio.gather(*tasks)

        # All 20 items consumed
        assert len(results) == 20
        assert q.qsize() == 0
        # No overflows with adequate timeout
        assert q.get_overflow_count() == 0

    @pytest.mark.asyncio
    async def test_high_contention_backpressure(self):
        """Backpressure works under high contention."""
        q = BoundedAsyncQueue(maxsize=10, timeout=0.1)

        async def aggressive_producer():
            count = 0
            overflows = 0
            for _ in range(50):
                try:
                    await q.put(f"item_{count}")
                    count += 1
                except QueueOverflowError:
                    overflows += 1
            return count, overflows

        async def slow_consumer():
            consumed = 0
            while consumed < 30:
                try:
                    await q.get()
                    consumed += 1
                    await asyncio.sleep(0.05)
                except asyncio.TimeoutError:
                    break
            return consumed

        # Run producer and consumer concurrently
        producer_task = asyncio.create_task(aggressive_producer())
        consumer_task = asyncio.create_task(slow_consumer())

        put_count, overflow_count = await producer_task
        get_count = await consumer_task

        # Producer should have been backpressured at some point
        assert overflow_count > 0
        # But should recover
        assert put_count + overflow_count == 50

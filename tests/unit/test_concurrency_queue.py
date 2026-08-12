"""
Unit Tests for Queue — ADR-0304

Tests for thread-safe FIFO queue with timeout.
"""

import threading
import time
import pytest

from core.concurrency import Queue, QueueError


class TestQueueBasic:
    """Basic queue operations."""

    def test_put_get_single_item(self):
        """Put and get single item."""
        q = Queue()
        q.put("test")
        assert q.get() == "test"

    def test_put_multiple_items(self):
        """Put and get multiple items in order."""
        q = Queue()
        for i in range(5):
            q.put(i)

        for i in range(5):
            assert q.get() == i

    def test_empty_queue_get_blocks(self):
        """Get on empty queue raises (non-blocking)."""
        q = Queue()
        with pytest.raises(QueueError):
            q.get(blocking=False)

    def test_full_queue_put_blocks(self):
        """Put on full queue raises (non-blocking)."""
        q = Queue(maxsize=2)
        q.put(1)
        q.put(2)

        with pytest.raises(QueueError):
            q.put(3, blocking=False)

    def test_queue_size(self):
        """Queue size tracking."""
        q = Queue()
        assert q.qsize() == 0

        q.put(1)
        q.put(2)
        assert q.qsize() == 2

        q.get()
        assert q.qsize() == 1

    def test_queue_empty(self):
        """Empty check."""
        q = Queue()
        assert q.empty()

        q.put(1)
        assert not q.empty()

        q.get()
        assert q.empty()

    def test_queue_full(self):
        """Full check."""
        q = Queue(maxsize=2)
        assert not q.full()

        q.put(1)
        q.put(2)
        assert q.full()

        q.get()
        assert not q.full()


class TestQueueTimeout:
    """Timeout behavior."""

    def test_get_timeout_on_empty(self):
        """Get times out on empty queue."""
        q = Queue(timeout=0.1)

        with pytest.raises(QueueError):
            q.get(blocking=True)

    def test_put_timeout_on_full(self):
        """Put times out on full queue."""
        q = Queue(maxsize=1, timeout=0.1)
        q.put(1)

        with pytest.raises(QueueError):
            q.put(2, blocking=True)

    def test_timeout_clears_on_ready(self):
        """Timeout succeeds if item available."""
        q = Queue(timeout=1.0)

        def delayed_put():
            time.sleep(0.1)
            q.put("late_item")

        thread = threading.Thread(target=delayed_put)
        thread.start()

        result = q.get(blocking=True)
        assert result == "late_item"

        thread.join()


class TestQueueBatch:
    """Batch operations."""

    def test_get_batch_single_item(self):
        """Get batch of 1 item."""
        q = Queue()
        q.put(1)

        batch = q.get_batch(1)
        assert batch == [1]

    def test_get_batch_multiple_items(self):
        """Get batch of multiple items."""
        q = Queue()
        for i in range(5):
            q.put(i)

        batch = q.get_batch(3)
        assert batch == [0, 1, 2]

        # Remaining items
        batch = q.get_batch(2)
        assert batch == [3, 4]

    def test_get_batch_less_than_available(self):
        """Get batch returns less if partial available (non-blocking)."""
        q = Queue()
        q.put(1)
        q.put(2)

        batch = q.get_batch(5, blocking=False)
        assert batch == [1, 2]  # Only 2 available

    def test_get_batch_empty_queue(self):
        """Get batch on empty queue."""
        q = Queue()

        batch = q.get_batch(3, blocking=False)
        assert batch == []

    def test_get_batch_waits_for_partial(self):
        """Get batch waits and returns partial."""
        q = Queue(timeout=1.0)

        def delayed_puts():
            time.sleep(0.1)
            q.put(1)
            time.sleep(0.05)
            q.put(2)

        thread = threading.Thread(target=delayed_puts)
        thread.start()

        batch = q.get_batch(5, blocking=True)
        assert len(batch) >= 2

        thread.join()


class TestQueueClear:
    """Clear operation."""

    def test_clear_empties_queue(self):
        """Clear removes all items."""
        q = Queue()
        for i in range(5):
            q.put(i)

        q.clear()
        assert q.empty()

    def test_clear_unblocks_waiting_puts(self):
        """Clear unblocks threads waiting to put."""
        q = Queue(maxsize=2, timeout=1.0)
        q.put(1)
        q.put(2)

        put_succeeded = [False]

        def delayed_put():
            time.sleep(0.1)
            q.clear()
            q.put(3)
            put_succeeded[0] = True

        # This would timeout normally
        thread = threading.Thread(target=delayed_put)
        thread.start()

        # Main thread tries to put (should wait, then succeed after clear)
        q.put(4, blocking=True)

        thread.join()

        # Both puts succeeded
        assert put_succeeded[0]


class TestQueueConcurrency:
    """Concurrent access patterns."""

    def test_producer_consumer(self):
        """Classic producer-consumer pattern."""
        q = Queue()
        items = []

        def producer():
            for i in range(10):
                q.put(i)
                time.sleep(0.01)

        def consumer():
            for _ in range(10):
                items.append(q.get(blocking=True))

        prod_thread = threading.Thread(target=producer)
        cons_thread = threading.Thread(target=consumer)

        prod_thread.start()
        cons_thread.start()

        prod_thread.join()
        cons_thread.join()

        assert items == list(range(10))

    def test_multiple_consumers(self):
        """Multiple consumers work correctly."""
        q = Queue()
        results = [[] for _ in range(3)]

        def consumer(index):
            for _ in range(5):
                results[index].append(q.get(blocking=True))

        # Put 15 items
        for i in range(15):
            q.put(i)

        consumers = [
            threading.Thread(target=consumer, args=(i,)) for i in range(3)
        ]

        for t in consumers:
            t.start()

        for t in consumers:
            t.join()

        # All items consumed
        consumed = [item for sublist in results for item in sublist]
        assert sorted(consumed) == list(range(15))
        assert len(consumed) == 15

    def test_queue_maxsize_enforced(self):
        """Max size is enforced under concurrent access."""
        q = Queue(maxsize=5)

        def producer():
            for i in range(20):
                q.put(i)

        thread = threading.Thread(target=producer)
        thread.start()

        time.sleep(0.1)  # Let producer run

        # Queue should never exceed maxsize
        assert q.qsize() <= 5

        thread.join()

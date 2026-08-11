"""
Thread-safe FIFO queue with timeout and batch operations.

Fail-closed: timeouts raise exceptions, not silent drops.
"""

import threading
import time
from collections import deque
from typing import Any, Optional, List


class QueueError(Exception):
    """Queue operation error."""

    pass


class Queue:
    """FIFO queue with timeout support and batch operations."""

    def __init__(self, maxsize: int = 0, timeout: float = 5.0):
        """
        Initialize queue.

        Args:
            maxsize: Maximum size (0 = unlimited)
            timeout: Operation timeout in seconds
        """
        self.maxsize = maxsize
        self.timeout = timeout
        self._queue: deque = deque()
        self._lock = threading.Lock()
        self._not_empty = threading.Condition(self._lock)
        self._not_full = threading.Condition(self._lock)

    def put(self, item: Any, blocking: bool = True) -> None:
        """
        Put item in queue.

        Args:
            item: Item to add
            blocking: Wait if queue is full (default True)

        Raises:
            QueueError: If timeout or queue is full (non-blocking)
        """
        deadline = time.time() + self.timeout if blocking else None

        with self._not_full:
            while self.maxsize > 0 and len(self._queue) >= self.maxsize:
                if not blocking:
                    raise QueueError("Queue is full")

                remaining = deadline - time.time() if deadline else self.timeout
                if remaining <= 0:
                    raise QueueError(f"Put timeout after {self.timeout}s")

                self._not_full.wait(timeout=remaining)

            self._queue.append(item)
            self._not_empty.notify()

    def get(self, blocking: bool = True) -> Any:
        """
        Get item from queue.

        Args:
            blocking: Wait if queue is empty (default True)

        Returns:
            Item from queue

        Raises:
            QueueError: If timeout or queue is empty (non-blocking)
        """
        deadline = time.time() + self.timeout if blocking else None

        with self._not_empty:
            while len(self._queue) == 0:
                if not blocking:
                    raise QueueError("Queue is empty")

                remaining = deadline - time.time() if deadline else self.timeout
                if remaining <= 0:
                    raise QueueError(f"Get timeout after {self.timeout}s")

                self._not_empty.wait(timeout=remaining)

            item = self._queue.popleft()
            self._not_full.notify()
            return item

    def get_batch(self, count: int, blocking: bool = True) -> List[Any]:
        """
        Get multiple items (batch).

        Args:
            count: Number of items to get
            blocking: Wait for all items (default True)

        Returns:
            List of items (may be < count if non-blocking and queue too small)

        Raises:
            QueueError: If timeout
        """
        deadline = time.time() + self.timeout if blocking else None
        items = []

        with self._not_empty:
            while len(items) < count:
                if len(self._queue) == 0:
                    if not blocking or len(items) > 0:
                        break

                    remaining = (
                        deadline - time.time() if deadline else self.timeout
                    )
                    if remaining <= 0:
                        raise QueueError(f"Get_batch timeout after {self.timeout}s")

                    self._not_empty.wait(timeout=remaining)
                else:
                    items.append(self._queue.popleft())

            if len(items) > 0:
                self._not_full.notify_all()

        return items

    def qsize(self) -> int:
        """Get approximate queue size."""
        with self._lock:
            return len(self._queue)

    def empty(self) -> bool:
        """Check if queue is empty."""
        with self._lock:
            return len(self._queue) == 0

    def full(self) -> bool:
        """Check if queue is full."""
        with self._lock:
            return self.maxsize > 0 and len(self._queue) >= self.maxsize

    def clear(self) -> None:
        """Clear all items from queue."""
        with self._lock:
            self._queue.clear()
            self._not_full.notify_all()

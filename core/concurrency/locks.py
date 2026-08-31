"""
Read-Write Lock for concurrent access.

Optimized for read-heavy workloads (multiple readers, few writers).
Thread-safe with deadlock detection.
"""

import threading
import time
from contextlib import contextmanager
from typing import Optional


class RWLock:
    """Read-write lock supporting concurrent readers, exclusive writers."""

    def __init__(self, timeout: float = 5.0):
        """
        Initialize RWLock.

        Args:
            timeout: Acquire timeout in seconds (deadlock detection)
        """
        self.timeout = timeout
        self._lock = threading.Lock()
        self._read_ready = threading.Condition(self._lock)
        self._readers = 0
        self._writers = 0
        self._read_waiters = 0
        self._write_waiters = 0

    def acquire_read(self, blocking: bool = True) -> bool:
        """
        Acquire read lock (non-exclusive).

        Multiple readers can hold simultaneously.
        Blocks if writer is active.

        Args:
            blocking: Wait if unavailable (default True)

        Returns:
            True if acquired, False if timeout
        """
        self._read_waiters += 1
        try:
            deadline = time.time() + self.timeout if blocking else None

            with self._lock:
                while self._writers > 0 or self._write_waiters > 0:
                    if not blocking:
                        return False

                    remaining = (
                        deadline - time.time() if deadline else self.timeout
                    )
                    if remaining <= 0:
                        return False

                    self._read_ready.wait(timeout=remaining)

                self._readers += 1
                return True
        finally:
            self._read_waiters -= 1

    def release_read(self) -> None:
        """Release read lock."""
        with self._lock:
            self._readers -= 1
            if self._readers == 0:
                self._read_ready.notify_all()

    def acquire_write(self, blocking: bool = True) -> bool:
        """
        Acquire write lock (exclusive).

        Blocks until all readers and writers release.

        Args:
            blocking: Wait if unavailable (default True)

        Returns:
            True if acquired, False if timeout
        """
        self._write_waiters += 1
        try:
            deadline = time.time() + self.timeout if blocking else None

            with self._lock:
                while self._readers > 0 or self._writers > 0:
                    if not blocking:
                        return False

                    remaining = (
                        deadline - time.time() if deadline else self.timeout
                    )
                    if remaining <= 0:
                        return False

                    self._read_ready.wait(timeout=remaining)

                self._writers += 1
                return True
        finally:
            self._write_waiters -= 1

    def release_write(self) -> None:
        """Release write lock."""
        with self._lock:
            self._writers -= 1
            self._read_ready.notify_all()

    @contextmanager
    def read_lock(self, blocking: bool = True):
        """Context manager for read lock."""
        if not self.acquire_read(blocking=blocking):
            raise TimeoutError(f"Could not acquire read lock within {self.timeout}s")
        try:
            yield
        finally:
            self.release_read()

    @contextmanager
    def write_lock(self, blocking: bool = True):
        """Context manager for write lock."""
        if not self.acquire_write(blocking=blocking):
            raise TimeoutError(f"Could not acquire write lock within {self.timeout}s")
        try:
            yield
        finally:
            self.release_write()

    def get_state(self) -> dict:
        """Get lock state (for monitoring/debugging)."""
        with self._lock:
            return {
                "readers": self._readers,
                "writers": self._writers,
                "read_waiters": self._read_waiters,
                "write_waiters": self._write_waiters,
            }

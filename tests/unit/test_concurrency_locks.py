"""
Unit Tests for RWLock — ADR-0304

Tests for read-write lock with concurrent access.
"""

import threading
import time
import pytest

from core.concurrency import RWLock


class TestRWLockBasic:
    """Basic RWLock functionality."""

    def test_read_lock_acquire_release(self):
        """Acquire and release read lock."""
        lock = RWLock()
        assert lock.acquire_read()
        lock.release_read()

    def test_write_lock_acquire_release(self):
        """Acquire and release write lock."""
        lock = RWLock()
        assert lock.acquire_write()
        lock.release_write()

    def test_multiple_readers_concurrent(self):
        """Multiple readers can hold lock simultaneously."""
        lock = RWLock()
        readers = []

        for _ in range(5):
            assert lock.acquire_read()
            readers.append(True)

        assert len(readers) == 5
        for _ in readers:
            lock.release_read()

    def test_writer_blocks_readers(self):
        """Writer blocks new readers."""
        lock = RWLock()
        lock.acquire_write()

        # Reader should timeout
        assert not lock.acquire_read(blocking=False)

        lock.release_write()

    def test_reader_blocks_writer(self):
        """Readers block writers."""
        lock = RWLock()
        lock.acquire_read()

        # Writer should timeout
        assert not lock.acquire_write(blocking=False)

        lock.release_read()

    def test_writer_exclusivity(self):
        """Only one writer at a time."""
        lock = RWLock()
        lock.acquire_write()

        # Second writer should timeout
        assert not lock.acquire_write(blocking=False)

        lock.release_write()


class TestRWLockContextManager:
    """Context manager support."""

    def test_read_lock_context(self):
        """Context manager for read lock."""
        lock = RWLock()

        with lock.read_lock():
            assert lock.get_state()["readers"] == 1

        assert lock.get_state()["readers"] == 0

    def test_write_lock_context(self):
        """Context manager for write lock."""
        lock = RWLock()

        with lock.write_lock():
            assert lock.get_state()["writers"] == 1

        assert lock.get_state()["writers"] == 0

    def test_context_manager_exception_safety(self):
        """Context manager releases on exception."""
        lock = RWLock()

        try:
            with lock.read_lock():
                raise ValueError("test error")
        except ValueError:
            pass

        # Lock should be released
        assert lock.get_state()["readers"] == 0
        assert lock.acquire_read()  # Should succeed
        lock.release_read()


class TestRWLockTimeout:
    """Timeout behavior."""

    def test_read_timeout(self):
        """Read acquire times out when writer active."""
        lock = RWLock(timeout=0.1)
        lock.acquire_write()

        with pytest.raises(TimeoutError):
            with lock.read_lock():
                pass

        lock.release_write()

    def test_write_timeout(self):
        """Write acquire times out when reader active."""
        lock = RWLock(timeout=0.1)
        lock.acquire_read()

        with pytest.raises(TimeoutError):
            with lock.write_lock():
                pass

        lock.release_read()

    def test_non_blocking_read(self):
        """Non-blocking read acquire fails immediately."""
        lock = RWLock()
        lock.acquire_write()

        assert not lock.acquire_read(blocking=False)

        lock.release_write()

    def test_non_blocking_write(self):
        """Non-blocking write acquire fails immediately."""
        lock = RWLock()
        lock.acquire_read()

        assert not lock.acquire_write(blocking=False)

        lock.release_read()


class TestRWLockConcurrency:
    """Concurrent access patterns."""

    def test_concurrent_readers(self):
        """Many threads reading concurrently."""
        lock = RWLock()
        results = []

        def reader(value):
            with lock.read_lock():
                time.sleep(0.01)  # Simulate work
                results.append(value)

        threads = [
            threading.Thread(target=reader, args=(i,)) for i in range(10)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert len(results) == 10

    def test_reader_writer_interleave(self):
        """Readers and writer interleave correctly."""
        lock = RWLock()
        counter = [0]

        def reader():
            for _ in range(5):
                with lock.read_lock():
                    _ = counter[0]  # Read

        def writer():
            for _ in range(5):
                with lock.write_lock():
                    counter[0] += 1

        threads = [
            threading.Thread(target=reader) for _ in range(3)
        ] + [threading.Thread(target=writer) for _ in range(2)]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        assert counter[0] == 10

    def test_writer_starvation_prevention(self):
        """Writers don't starve when readers are active."""
        lock = RWLock(timeout=1.0)
        write_acquired = [False]

        def reader():
            with lock.read_lock():
                time.sleep(0.05)

        def writer():
            with lock.write_lock():
                write_acquired[0] = True

        # Start many readers
        reader_threads = [
            threading.Thread(target=reader) for _ in range(10)
        ]

        for t in reader_threads:
            t.start()

        time.sleep(0.01)  # Let readers start

        # Writer should eventually acquire
        writer_thread = threading.Thread(target=writer)
        writer_thread.start()

        for t in reader_threads:
            t.join()

        writer_thread.join()

        assert write_acquired[0]


class TestRWLockState:
    """State inspection."""

    def test_get_state_empty(self):
        """State when empty."""
        lock = RWLock()
        state = lock.get_state()

        assert state["readers"] == 0
        assert state["writers"] == 0
        assert state["read_waiters"] == 0
        assert state["write_waiters"] == 0

    def test_get_state_with_readers(self):
        """State with active readers."""
        lock = RWLock()
        lock.acquire_read()
        lock.acquire_read()

        state = lock.get_state()
        assert state["readers"] == 2

        lock.release_read()
        lock.release_read()

    def test_get_state_with_writer(self):
        """State with active writer."""
        lock = RWLock()
        lock.acquire_write()

        state = lock.get_state()
        assert state["writers"] == 1

        lock.release_write()

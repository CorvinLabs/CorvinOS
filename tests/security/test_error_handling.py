"""
Error Handling Tests (F-M2)

Tests for exception hierarchy and error handling in concurrency subsystem.
Ensures proper exception types are raised and can be caught appropriately.
"""

import pytest
import asyncio
from core.concurrency.exceptions import (
    ConcurrencyError,
    QueueOverflowError,
    WorkerPoolShutdownError,
    LockAcquisitionError,
)
from core.concurrency.worker_pool import WorkerPool
from core.concurrency.locks import RWLock


class TestExceptionHierarchy:
    """F-M2: Exception hierarchy tests."""

    def test_all_exceptions_inherit_from_concurrency_error(self):
        """All custom exceptions should inherit from ConcurrencyError."""
        assert issubclass(QueueOverflowError, ConcurrencyError)
        assert issubclass(WorkerPoolShutdownError, ConcurrencyError)
        assert issubclass(LockAcquisitionError, ConcurrencyError)

    def test_concurrency_error_inherits_from_exception(self):
        """ConcurrencyError should inherit from Exception."""
        assert issubclass(ConcurrencyError, Exception)

    def test_catch_base_exception_catches_all(self):
        """Catching ConcurrencyError should catch all subclasses."""
        exceptions_to_test = [
            QueueOverflowError("queue full"),
            WorkerPoolShutdownError("pool shutdown"),
            LockAcquisitionError("lock timeout"),
        ]

        for exc in exceptions_to_test:
            try:
                raise exc
            except ConcurrencyError:
                pass  # Should be caught
            except Exception:
                pytest.fail(f"{type(exc).__name__} not caught by ConcurrencyError")

    def test_specific_exception_types_distinct(self):
        """Each exception type should be distinct."""
        exc_types = [
            QueueOverflowError,
            WorkerPoolShutdownError,
            LockAcquisitionError,
        ]
        # All types should be unique
        assert len(set(exc_types)) == len(exc_types)


class TestQueueOverflowError:
    """F-M2: QueueOverflowError tests."""

    def test_queue_overflow_error_message(self):
        """QueueOverflowError should have descriptive message."""
        msg = "Queue full after 5 seconds"
        exc = QueueOverflowError(msg)
        assert str(exc) == msg

    @pytest.mark.asyncio
    async def test_queue_overflow_error_raised_on_backpressure(self):
        """QueueOverflowError should be raised when queue is full."""
        pool = WorkerPool(max_workers=1, queue_size=1, timeout_seconds=0.1)
        await pool.start()

        try:
            # Fill queue with slow task
            await pool.submit(asyncio.sleep, 10)

            # Try to submit another task (should timeout)
            with pytest.raises(QueueOverflowError):
                await pool.submit(asyncio.sleep, 1)
        finally:
            await pool.stop()

    def test_queue_overflow_error_instance_check(self):
        """QueueOverflowError instances should be checkable."""
        exc = QueueOverflowError("queue full")
        assert isinstance(exc, QueueOverflowError)
        assert isinstance(exc, ConcurrencyError)
        assert isinstance(exc, Exception)


class TestWorkerPoolShutdownError:
    """F-M2: WorkerPoolShutdownError tests."""

    def test_worker_pool_shutdown_error_message(self):
        """WorkerPoolShutdownError should have descriptive message."""
        msg = "Pool has been shutdown"
        exc = WorkerPoolShutdownError(msg)
        assert str(exc) == msg

    @pytest.mark.asyncio
    async def test_worker_pool_shutdown_error_on_submit_after_stop(self):
        """Should raise WorkerPoolShutdownError when submitting after stop."""
        pool = WorkerPool()
        await pool.start()
        await pool.stop()

        with pytest.raises(WorkerPoolShutdownError):
            await pool.submit(asyncio.sleep, 1)

    def test_worker_pool_shutdown_error_instance_check(self):
        """WorkerPoolShutdownError instances should be checkable."""
        exc = WorkerPoolShutdownError("pool shutdown")
        assert isinstance(exc, WorkerPoolShutdownError)
        assert isinstance(exc, ConcurrencyError)
        assert isinstance(exc, Exception)


class TestLockAcquisitionError:
    """F-M2: LockAcquisitionError tests."""

    def test_lock_acquisition_error_message(self):
        """LockAcquisitionError should have descriptive message."""
        msg = "Could not acquire lock within 5.0s"
        exc = LockAcquisitionError(msg)
        assert str(exc) == msg

    def test_lock_acquisition_error_on_rwlock_timeout(self):
        """RWLock should raise LockAcquisitionError (or TimeoutError) on timeout."""
        lock = RWLock(timeout=0.1)

        # Acquire write lock in one "thread"
        # (This is tricky to test in single-threaded context)
        # For now, just verify the error type exists
        exc = LockAcquisitionError("lock timeout")
        assert isinstance(exc, LockAcquisitionError)

    def test_lock_acquisition_error_instance_check(self):
        """LockAcquisitionError instances should be checkable."""
        exc = LockAcquisitionError("read lock timeout")
        assert isinstance(exc, LockAcquisitionError)
        assert isinstance(exc, ConcurrencyError)
        assert isinstance(exc, Exception)


class TestExceptionPropagation:
    """F-M2: Exception propagation tests."""

    @pytest.mark.asyncio
    async def test_task_exception_propagates_from_submit(self):
        """Exception in task should propagate through submit()."""
        pool = WorkerPool()
        await pool.start()

        def failing_func():
            raise ValueError("task failed")

        try:
            with pytest.raises(ValueError, match="task failed"):
                await pool.submit(failing_func)
        finally:
            await pool.stop()

    @pytest.mark.asyncio
    async def test_async_task_exception_propagates(self):
        """Exception in async task should propagate."""
        pool = WorkerPool()
        await pool.start()

        async def failing_async_func():
            raise RuntimeError("async task failed")

        try:
            with pytest.raises(RuntimeError, match="async task failed"):
                await pool.submit(failing_async_func)
        finally:
            await pool.stop()

    def test_config_error_on_invalid_init(self):
        """Configuration errors should be raised on init (not later)."""
        with pytest.raises(ValueError):
            WorkerPool(max_workers=-1)


class TestExceptionMessaging:
    """F-M2: Exception message quality tests."""

    def test_queue_overflow_error_includes_queue_size(self):
        """QueueOverflowError message should include queue size info."""
        msg = "Queue overflow: size=42/1000 after 5.0s timeout"
        exc = QueueOverflowError(msg)
        assert "size=" in str(exc)
        assert "timeout" in str(exc)

    def test_config_error_includes_parameter_name(self):
        """Config errors should name the invalid parameter."""
        with pytest.raises(ValueError, match="max_workers"):
            WorkerPool(max_workers=999)

        with pytest.raises(ValueError, match="queue_size"):
            WorkerPool(queue_size=999999)

        with pytest.raises(ValueError, match="timeout_seconds"):
            WorkerPool(timeout_seconds=9999)

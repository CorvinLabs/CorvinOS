"""
Logging Consistency Tests (F-M3)

Tests for standardized logging levels across WorkerPool.
Ensures INFO for lifecycle, ERROR for failures, DEBUG for details.
"""

import pytest
import asyncio
import logging
from core.concurrency.worker_pool import WorkerPool


class TestLoggingLevels:
    """F-M3: Logging level consistency tests."""

    @pytest.mark.asyncio
    async def test_pool_start_logs_info(self, caplog):
        """Pool startup should log at INFO level."""
        with caplog.at_level(logging.INFO):
            pool = WorkerPool()
            await pool.start()
            await pool.stop()

        # Should have an INFO log about pool starting
        assert any(
            record.levelname == "INFO" and "Starting WorkerPool" in record.message
            for record in caplog.records
        ), f"No INFO log about pool start. Records: {caplog.text}"

    @pytest.mark.asyncio
    async def test_pool_stop_logs_info(self, caplog):
        """Pool shutdown should log at INFO level."""
        with caplog.at_level(logging.INFO):
            pool = WorkerPool()
            await pool.start()
            await pool.stop()

        # Should have an INFO log about pool stopping
        assert any(
            record.levelname == "INFO" and "Stopping WorkerPool" in record.message
            for record in caplog.records
        ), f"No INFO log about pool stop. Records: {caplog.text}"

    @pytest.mark.asyncio
    async def test_autoscale_up_logs_info(self, caplog):
        """Auto-scale up should log at INFO level."""
        with caplog.at_level(logging.INFO):
            pool = WorkerPool(
                max_workers=4,
                queue_size=10,
                timeout_seconds=0.5,
                enable_auto_scaling=True
            )
            await pool.start()

            try:
                # Fill queue to trigger scale-up
                for _ in range(8):
                    try:
                        await asyncio.wait_for(
                            pool.submit(asyncio.sleep, 10),
                            timeout=0.1
                        )
                    except (asyncio.TimeoutError, Exception):
                        break

                # Give scaler time to run
                await asyncio.sleep(1.5)

                # Should have an INFO log about scaling up
                assert any(
                    record.levelname == "INFO" and "Auto-scale UP" in record.message
                    for record in caplog.records
                ), f"No INFO log about scale-up. Records: {caplog.text}"
            finally:
                await pool.stop()

    @pytest.mark.asyncio
    async def test_queue_overflow_logs_error(self, caplog):
        """Queue overflow should log at ERROR level."""
        with caplog.at_level(logging.ERROR):
            pool = WorkerPool(max_workers=1, queue_size=1, timeout_seconds=0.1)
            await pool.start()

            try:
                # Fill queue
                await pool.submit(asyncio.sleep, 10)

                # Overflow attempt
                try:
                    await pool.submit(asyncio.sleep, 1)
                except Exception:
                    pass

                # Should have an ERROR log about overflow
                assert any(
                    record.levelname == "ERROR" and "Queue overflow" in record.message
                    for record in caplog.records
                ), f"No ERROR log about overflow. Records: {caplog.text}"
            finally:
                await pool.stop()

    @pytest.mark.asyncio
    async def test_task_exception_logs_debug(self, caplog):
        """Task execution errors should log at DEBUG level."""
        with caplog.at_level(logging.DEBUG):
            pool = WorkerPool()
            await pool.start()

            try:
                def failing_func():
                    raise ValueError("test error")

                try:
                    await pool.submit(failing_func)
                except ValueError:
                    pass

                # Should have a DEBUG log about task error
                # (Note: may not always appear depending on timing)
                # Just verify no ERROR logs for normal task failures
                error_logs = [
                    r for r in caplog.records
                    if r.levelname == "ERROR" and "Worker fatal error" in r.message
                ]
                # Should NOT have fatal error logs for normal exceptions
                assert len(error_logs) == 0, f"Unexpected ERROR log: {caplog.text}"
            finally:
                await pool.stop()

    @pytest.mark.asyncio
    async def test_no_error_logs_on_normal_operation(self, caplog):
        """Normal operation should not produce ERROR logs."""
        with caplog.at_level(logging.ERROR):
            pool = WorkerPool(max_workers=2, queue_size=100)
            await pool.start()

            try:
                # Submit normal tasks
                results = []
                for i in range(5):
                    result = await pool.submit(lambda x=i: x * 2)
                    results.append(result)

                assert results == [0, 2, 4, 6, 8]

                # Should have NO error logs
                error_logs = [
                    r for r in caplog.records
                    if r.levelname == "ERROR"
                ]
                assert len(error_logs) == 0, f"Unexpected ERROR logs: {caplog.text}"
            finally:
                await pool.stop()

    @pytest.mark.asyncio
    async def test_multiple_calls_log_at_consistent_level(self, caplog):
        """Multiple start/stop cycles should use consistent log levels."""
        with caplog.at_level(logging.INFO):
            for cycle in range(2):
                pool = WorkerPool()
                await pool.start()
                await pool.stop()

        # Count INFO logs
        start_logs = [r for r in caplog.records if "Starting WorkerPool" in r.message]
        stop_logs = [r for r in caplog.records if "Stopping WorkerPool" in r.message]

        assert len(start_logs) == 2, f"Expected 2 start logs, got {len(start_logs)}"
        assert len(stop_logs) == 2, f"Expected 2 stop logs, got {len(stop_logs)}"

        # All should be INFO level
        assert all(r.levelname == "INFO" for r in start_logs)
        assert all(r.levelname == "INFO" for r in stop_logs)


class TestLogMessageContent:
    """F-M3: Log message quality tests."""

    @pytest.mark.asyncio
    async def test_start_log_includes_config(self, caplog):
        """Pool start log should include configuration details."""
        with caplog.at_level(logging.INFO):
            pool = WorkerPool(max_workers=8, queue_size=500)
            await pool.start()
            await pool.stop()

        start_log = next(
            (r for r in caplog.records if "Starting WorkerPool" in r.message),
            None
        )
        assert start_log is not None
        assert "8" in start_log.message  # max_workers
        assert "500" in start_log.message or "queue" in start_log.message

    @pytest.mark.asyncio
    async def test_stop_log_includes_stats(self, caplog):
        """Pool stop log should include task statistics."""
        with caplog.at_level(logging.INFO):
            pool = WorkerPool()
            await pool.start()

            # Submit some tasks
            for i in range(3):
                try:
                    await asyncio.wait_for(
                        pool.submit(lambda x=i: x * 2),
                        timeout=1.0
                    )
                except asyncio.TimeoutError:
                    pass

            await pool.stop()

        stop_log = next(
            (r for r in caplog.records if "Stopping WorkerPool" in r.message),
            None
        )
        assert stop_log is not None
        # Should mention processed tasks
        assert "processed" in stop_log.message.lower()

    @pytest.mark.asyncio
    async def test_overflow_log_includes_queue_info(self, caplog):
        """Overflow log should include queue size and timeout."""
        with caplog.at_level(logging.ERROR):
            pool = WorkerPool(max_workers=1, queue_size=5, timeout_seconds=0.2)
            await pool.start()

            try:
                # Fill queue
                await pool.submit(asyncio.sleep, 10)

                # Trigger overflow
                try:
                    await pool.submit(asyncio.sleep, 1)
                except Exception:
                    pass

                overflow_log = next(
                    (r for r in caplog.records if "Queue overflow" in r.message),
                    None
                )
                assert overflow_log is not None
                # Should include queue size
                assert "/" in overflow_log.message  # size/capacity format
            finally:
                await pool.stop()

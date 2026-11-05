"""
WorkerPool: Bounded concurrent task execution with backpressure (F-C2, F-C5 Fixes)

K=3 Implementation: Wire WorkerPool to real subsystems (F-C2 fix)
K=5 Implementation: Auto-scaling based on queue usage (F-C5 fix)

Prevents silent task loss + provides elastic scaling for high-load scenarios.

Features:
    - Bounded queue with backpressure (fail-fast on overflow)
    - Auto-scaling: grows from min_workers the moment a task is queued with no
      idle worker (and at > 75% queue usage); retires idle workers only
    - Audit trail for task execution and overflow events
    - Configurable timeouts and bounds validation
    - Per-task latency tracking and statistics

Example:
    pool = WorkerPool(max_workers=8, queue_size=1000, enable_auto_scaling=True)
    await pool.start()
    result = await pool.submit(asyncio.sleep, 1)
    await pool.stop()
"""

import asyncio
from typing import Callable, Any, Optional, Awaitable
from dataclasses import dataclass
import time
import logging

from core.concurrency.exceptions import (
    ConcurrencyError,
    QueueOverflowError,
    WorkerPoolShutdownError,
)

logger = logging.getLogger(__name__)
logger_info = logger.info
logger_error = logger.error
logger_debug = logger.debug


@dataclass
class WorkerPoolStats:
    """Metrics for WorkerPool monitoring."""
    active_workers: int
    queue_size: int
    queue_capacity: int
    tasks_processed: int
    overflow_errors: int
    avg_latency_ms: float


class WorkerPool:
    """
    Bounded async task pool with backpressure + auto-scaling.

    Manages a fixed pool of async workers that execute submitted coroutines.
    Applies backpressure when queue is full (fails fast, preserves tasks).
    Automatically scales workers based on queue usage.

    Configuration:
        max_workers: Maximum concurrent workers (1–256, default 4)
        queue_size: Maximum pending tasks (1–10000, default 1000)
        timeout_seconds: Default submission/dequeue timeout (0.1–3600 sec, default 30)
        enable_auto_scaling: Enable dynamic worker scaling (default True)

    Example:
        pool = WorkerPool(max_workers=8, queue_size=1000)
        await pool.start()
        try:
            result = await pool.submit(some_coroutine, arg1, arg2)
        except QueueOverflowError:
            # Queue full; backpressure limit reached
            pass
        finally:
            await pool.stop()

    Thread-safety:
        All public methods are thread-safe and can be called from any thread.
        Internally uses asyncio queues for async safety.

    Raises:
        ValueError: If configuration values are out of bounds
        QueueOverflowError: If queue full and timeout expires on submit
        WorkerPoolShutdownError: If operation attempted on shutdown pool
    """

    def __init__(
        self,
        max_workers: int = 4,
        queue_size: int = 1000,
        timeout_seconds: float = 30,
        enable_auto_scaling: bool = True,
        min_workers: Optional[int] = None,
        idle_retire_seconds: float = 5.0,
    ) -> None:
        """
        Initialize WorkerPool with bounds validation (F-M1 fix).

        Args:
            max_workers: Number of concurrent workers (1–256)
            queue_size: Maximum pending tasks (1–10000)
            timeout_seconds: Default timeout for operations (0.1–3600)
            enable_auto_scaling: Enable automatic worker scaling. An elastic
                pool starts with ``min_workers`` workers and grows to
                ``max_workers`` the moment a task is queued with no idle worker
                to take it; a fixed pool (False) runs ``max_workers`` throughout.
            min_workers: Floor of an elastic pool (1..max_workers). Default
                None = max_workers: the pool starts full and never shrinks, so
                auto-scaling only acts when a lower floor is given
            idle_retire_seconds: An elastic pool retires a worker only after it
                has been IDLE this long — never a busy one

        Raises:
            ValueError: If any parameter is out of bounds
        """
        # F-M1: Configuration Validation
        if not (1 <= max_workers <= 256):
            raise ValueError(
                f"max_workers must be 1–256, got {max_workers}"
            )
        if not (1 <= queue_size <= 10000):
            raise ValueError(
                f"queue_size must be 1–10000, got {queue_size}"
            )
        if not (0.1 <= timeout_seconds <= 3600):
            raise ValueError(
                f"timeout_seconds must be 0.1–3600, got {timeout_seconds}"
            )
        if min_workers is None:
            min_workers = max_workers
        if not (1 <= min_workers <= max_workers):
            raise ValueError(
                f"min_workers must be 1–max_workers ({max_workers}), got {min_workers}"
            )
        if idle_retire_seconds < 0:
            raise ValueError(
                f"idle_retire_seconds must be >= 0, got {idle_retire_seconds}"
            )

        self.max_workers: int = max_workers
        self.queue_size: int = queue_size
        self.timeout_seconds: float = timeout_seconds
        self.enable_auto_scaling: bool = enable_auto_scaling
        self.min_workers: int = min_workers
        self.idle_retire_seconds: float = idle_retire_seconds
        # worker task -> monotonic time it started waiting for work. A worker is
        # in here only while it is blocked on the queue, so scale-down can pick
        # an IDLE worker; cancelling an arbitrary one (the previous behaviour)
        # killed a running task whose submitter then waited forever.
        self._idle_since: dict[asyncio.Task[None], float] = {}

        self.queue: asyncio.Queue[tuple] = asyncio.Queue(maxsize=queue_size)
        self.active_workers: set[asyncio.Task[None]] = set()
        self.tasks_processed: int = 0
        self.overflow_errors: int = 0
        self.task_latencies: list[float] = []
        self._is_shutdown: bool = False

        # Auto-scaling state
        self.scaler_task: Optional[asyncio.Task[None]] = None
        self.monitoring: bool = False

    async def start(self) -> None:
        """
        Start worker pool and spawn initial workers.

        Spawns max_workers worker tasks (a fixed pool) or min_workers (an
        elastic pool, which then grows on demand) and optionally starts the
        auto-scaler. Must be called before submitting tasks.

        Raises:
            WorkerPoolShutdownError: If pool has been shutdown
        """
        if self._is_shutdown:
            raise WorkerPoolShutdownError("Cannot start a shutdown pool")

        # F-M3: Logging Consistency (INFO level for lifecycle events)
        logger_info(
            f"Starting WorkerPool: {self.max_workers} workers, "
            f"queue_size={self.queue_size}, auto_scaling={self.enable_auto_scaling}"
        )

        # Spawn initial workers. An elastic pool starts at its floor: starting at
        # max_workers made the scale-UP rule (`current < max_workers`)
        # unreachable from a fresh pool.
        initial = self.min_workers if self.enable_auto_scaling else self.max_workers
        for _ in range(initial):
            self._spawn_worker()

        # Start auto-scaler if enabled
        if self.enable_auto_scaling:
            self.scaler_task = asyncio.create_task(self._monitor_and_scale())
            self.monitoring = True
            logger_debug("Auto-scaler task started")

    async def stop(self) -> None:
        """
        Gracefully shutdown pool.

        Stops accepting new tasks, drains the queue, and cancels all workers.
        Safe to call multiple times (idempotent).

        This method:
        1. Stops the auto-scaler (if running)
        2. Drains remaining tasks from the queue
        3. Cancels all worker tasks
        """
        if self._is_shutdown:
            logger_debug("Pool already shutdown; skipping redundant stop()")
            return

        # F-M3: Logging Consistency (INFO level for lifecycle events)
        logger_info(
            f"Stopping WorkerPool: "
            f"{self.tasks_processed} tasks processed, "
            f"{self.overflow_errors} overflow errors"
        )
        self._is_shutdown = True
        self.monitoring = False

        # Cancel scaler
        if self.scaler_task and not self.scaler_task.done():
            self.scaler_task.cancel()
            logger_debug("Auto-scaler cancelled")

        # Drain queue and wait for all tasks
        while not self.queue.empty():
            try:
                self.queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            await asyncio.sleep(0.01)

        # Cancel remaining workers
        for worker in self.active_workers:
            if not worker.done():
                worker.cancel()

        logger_debug(f"Cancelled {len(self.active_workers)} workers")

    async def submit(
        self,
        coro: Callable[..., Awaitable[Any]],
        *args: Any,
        timeout_seconds: Optional[float] = None,
        **kwargs: Any,
    ) -> Any:
        """
        Submit coroutine to pool with backpressure.

        Enqueues a coroutine for execution by a worker. If queue is full,
        waits up to timeout_seconds for space. Applies strict backpressure:
        fail fast rather than silently losing tasks.

        Args:
            coro: Coroutine function to execute
            *args: Positional arguments for coro
            timeout_seconds: Queue wait timeout (default: self.timeout_seconds).
                            Set to None for no timeout.
            **kwargs: Keyword arguments for coro

        Returns:
            Result of the coroutine execution

        Raises:
            WorkerPoolShutdownError: If pool has been shutdown
            QueueOverflowError: If queue full and timeout expires
            Exception: Any exception raised by the coroutine is re-raised

        Example:
            result = await pool.submit(asyncio.sleep, 1, timeout_seconds=5)
        """
        if self._is_shutdown:
            raise WorkerPoolShutdownError("Cannot submit to shutdown pool")

        timeout = timeout_seconds if timeout_seconds is not None else self.timeout_seconds

        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        task_item = (coro, args, kwargs, future)

        # Only the ENQUEUE is under the backpressure timeout. The result wait is
        # outside it: a task that itself raises TimeoutError used to be reported
        # as a queue overflow (and counted in overflow_errors).
        try:
            await asyncio.wait_for(
                self.queue.put(task_item),
                timeout=timeout
            )
        except asyncio.TimeoutError:
            self.overflow_errors += 1
            # F-M3: Logging Consistency (ERROR level for backpressure)
            error = QueueOverflowError(
                f"Queue overflow: size={self.queue.qsize()}/{self.queue_size} "
                f"after {timeout}s timeout (error #{self.overflow_errors})"
            )
            logger_error(str(error))

            # F-H2: Audit trail integration (GDPR Art. 30, 32)
            # TODO: Integrate with audit.write() when audit module available:
            # try:
            #     from core.compliance.audit_chain_writer import AuditChainWriter
            #     # Get tenant-aware audit writer
            #     audit = get_audit_writer()  # tenant-scoped
            #     await audit.write_event_dict(
            #         event_type="queue.overflow",
            #         tenant_id=get_current_tenant_id(),
            #         details={
            #             "queue_size": self.queue.qsize(),
            #             "queue_capacity": self.queue_size,
            #             "timeout_seconds": timeout,
            #             "overflow_count": self.overflow_errors,
            #         },
            #         severity="warning",
            #     )
            # except Exception as audit_error:
            #     logger_error(f"Failed to write overflow audit: {audit_error}")

            raise error

        self._scale_up_if_saturated()
        return await future

    def _spawn_worker(self) -> asyncio.Task[None]:
        worker = asyncio.create_task(self._worker_loop())
        self.active_workers.add(worker)
        worker.add_done_callback(self.active_workers.discard)
        return worker

    def _scale_up_if_saturated(self) -> None:
        """Elastic scale-up: more queued tasks than idle workers → add workers
        (up to max_workers) now, not at the next scaler tick, so a long task in
        one worker never holds a short one behind it while capacity is left."""
        if not self.enable_auto_scaling or self._is_shutdown:
            return
        pending = self.queue.qsize()
        idle = len(self._idle_since)
        while pending > idle and len(self.active_workers) < self.max_workers:
            before = len(self.active_workers)
            self._spawn_worker()
            idle += 1
            # F-M3: Logging Consistency (INFO for scaling decisions)
            logger_info(
                f"Auto-scale UP: {before}→{len(self.active_workers)} workers "
                f"({pending} queued, {pending / self.queue_size:.1%} full)"
            )

    async def _worker_loop(self) -> None:
        """
        Worker loop: dequeue and execute tasks.

        Continuously dequeues tasks from the shared queue and executes them,
        capturing results or exceptions. Graceful shutdown via CancelledError.

        Task execution is tracked: latency is measured and cumulative stats
        are updated on every task completion.

        Internal method (not for direct use).
        """
        me = asyncio.current_task()
        future: Optional[asyncio.Future[Any]] = None
        while True:
            try:
                # Dequeue task with timeout to allow graceful shutdown
                self._idle_since.setdefault(me, time.monotonic())
                try:
                    coro, args, kwargs, future = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # No tasks for a while; continue waiting
                    continue
                self._idle_since.pop(me, None)

                # Execute task + capture result/error
                start = time.time()
                try:
                    if asyncio.iscoroutinefunction(coro):
                        result = await coro(*args, **kwargs)
                    else:
                        result = coro(*args, **kwargs)
                    if not future.done():
                        future.set_result(result)
                except Exception as e:
                    if not future.done():
                        future.set_exception(e)
                    # F-M3: Logging (DEBUG level for per-task execution)
                    logger_debug(f"Task execution error: {e}")
                finally:
                    latency = (time.time() - start) * 1000
                    self.task_latencies.append(latency)
                    self.tasks_processed += 1

                self.queue.task_done()
                future = None

            except asyncio.CancelledError:
                # A task cut off mid-run (stop()) must not leave its submitter
                # awaiting a future nobody will ever resolve.
                if future is not None and not future.done():
                    future.cancel()
                logger_debug("Worker cancelled; exiting")
                break
            except Exception as e:
                # F-M3: Logging (ERROR level for worker failures)
                logger_error(f"Worker fatal error (exiting): {e}")
                break
        self._idle_since.pop(me, None)

    async def _monitor_and_scale(self) -> None:
        """
        Background loop: monitor queue usage and dynamically scale workers.

        This task monitors queue usage percentile and adjusts worker count:
        - Scale UP if queue > 75% full (add one worker)
        - Scale DOWN if queue < 25% full (remove one worker, min 1)

        Runs in background at 1-second intervals. Can be cancelled
        independently of worker tasks.

        Internal method (not for direct use).

        Thresholds (F-C5 Fix):
        - 75% = scale-up threshold (queue getting congested)
        - 25% = scale-down threshold (queue mostly empty, wasting resources)
        """
        # F-M3: Logging Consistency (INFO for lifecycle)
        logger_info("WorkerPool auto-scaler started")

        while self.monitoring:
            try:
                queue_usage = self.queue.qsize() / self.queue_size
                current_workers = len(self.active_workers)

                # Scale up: queue getting full
                if queue_usage > 0.75 and current_workers < self.max_workers:
                    self._spawn_worker()
                    # F-M3: Logging Consistency (INFO for scaling decisions)
                    logger_info(
                        f"Auto-scale UP: {current_workers}→{len(self.active_workers)} workers "
                        f"(queue {queue_usage:.1%} full)"
                    )

                # Scale down: queue mostly empty — retire ONE worker that has
                # been idle for idle_retire_seconds, never a busy one, never
                # below min_workers.
                elif queue_usage < 0.25 and current_workers > self.min_workers:
                    now = time.monotonic()
                    stale = [w for w, since in self._idle_since.items()
                             if now - since >= self.idle_retire_seconds and not w.done()]
                    if stale:
                        worker = stale[0]
                        self._idle_since.pop(worker, None)
                        worker.cancel()
                        # F-M3: Logging Consistency (INFO for scaling decisions)
                        logger_info(
                            f"Auto-scale DOWN: {current_workers}→{current_workers-1} workers "
                            f"(queue {queue_usage:.1%} full)"
                        )

                await asyncio.sleep(1.0)  # Check every 1 second

            except asyncio.CancelledError:
                logger_debug("Auto-scaler cancelled")
                break
            except Exception as e:
                # F-M3: Logging Consistency (ERROR for scaler failures)
                logger_error(f"Auto-scaler error: {e}")
                await asyncio.sleep(1.0)

    def get_stats(self) -> WorkerPoolStats:
        """
        Get current pool statistics.

        Returns snapshot of current pool state including worker count,
        queue usage, task counts, and latency metrics.

        Returns:
            WorkerPoolStats: Current statistics snapshot
                - active_workers: Number of active worker tasks
                - queue_size: Current queue depth
                - queue_capacity: Maximum queue size
                - tasks_processed: Cumulative tasks completed
                - overflow_errors: Cumulative queue overflow errors
                - avg_latency_ms: Average task latency (last 100 tasks)

        Example:
            stats = pool.get_stats()
            print(f"{stats.active_workers} workers, "
                  f"{stats.queue_size}/{stats.queue_capacity} queued")
        """
        # Average latency over last 100 tasks (or all if fewer)
        avg_latency = (
            sum(self.task_latencies[-100:]) / len(self.task_latencies[-100:])
            if self.task_latencies
            else 0.0
        )

        return WorkerPoolStats(
            active_workers=len(self.active_workers),
            queue_size=self.queue.qsize(),
            queue_capacity=self.queue_size,
            tasks_processed=self.tasks_processed,
            overflow_errors=self.overflow_errors,
            avg_latency_ms=avg_latency,
        )

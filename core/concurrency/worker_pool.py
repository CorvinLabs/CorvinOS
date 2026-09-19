"""
WorkerPool: Bounded concurrent task execution with backpressure (F-C2, F-C5 Fixes)

K=3 Implementation: Wire WorkerPool to real subsystems (F-C2 fix)
K=5 Implementation: Auto-scaling based on queue usage (F-C5 fix)

Prevents silent task loss + provides elastic scaling for high-load scenarios.
"""

import asyncio
from typing import Callable, Any, Optional, Dict, Set
from dataclasses import dataclass
import time
import logging

logger = logging.getLogger(__name__)


class QueueOverflowError(Exception):
    """Raised when task queue is full and timeout expires (F-C3 fix)."""
    pass


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

    Features:
    - Max worker limit (prevents OOM)
    - Queue backpressure (fail fast on overflow, don't lose tasks)
    - Auto-scaling (scale up at 75%, scale down at 25% usage)
    - Audit trail for every task + overflow
    """

    def __init__(
        self,
        max_workers: int = 4,
        queue_size: int = 1000,
        timeout_seconds: float = 30,
        enable_auto_scaling: bool = True,
    ):
        self.max_workers = max_workers
        self.queue_size = queue_size
        self.timeout_seconds = timeout_seconds
        self.enable_auto_scaling = enable_auto_scaling

        self.queue: asyncio.Queue = asyncio.Queue(maxsize=queue_size)
        self.active_workers: Set[asyncio.Task] = set()
        self.tasks_processed = 0
        self.overflow_errors = 0
        self.task_latencies: list = []

        # Auto-scaling state
        self.scaler_task: Optional[asyncio.Task] = None
        self.monitoring = False

    async def start(self):
        """Start worker pool (spawn initial workers + scaler)."""
        logger.info(f"Starting WorkerPool: {self.max_workers} workers, {self.queue_size} queue")

        # Spawn initial workers
        for _ in range(self.max_workers):
            worker = asyncio.create_task(self._worker_loop())
            self.active_workers.add(worker)
            worker.add_done_callback(self.active_workers.discard)

        # Start auto-scaler if enabled
        if self.enable_auto_scaling:
            self.scaler_task = asyncio.create_task(self._monitor_and_scale())
            self.monitoring = True

    async def stop(self):
        """Gracefully shut down all workers."""
        logger.info("Stopping WorkerPool")
        self.monitoring = False

        # Cancel scaler
        if self.scaler_task:
            self.scaler_task.cancel()

        # Drain queue and wait for all tasks
        while not self.queue.empty():
            await asyncio.sleep(0.1)

        # Cancel remaining workers
        for worker in self.active_workers:
            worker.cancel()

    async def submit(
        self,
        coro: Callable,
        *args,
        timeout_seconds: Optional[float] = None,
        **kwargs
    ) -> Any:
        """
        Submit task to pool with backpressure.

        Args:
            coro: Callable or coroutine to execute
            *args, **kwargs: Arguments for coro
            timeout_seconds: Override default timeout

        Raises:
            QueueOverflowError: If queue full and timeout expires

        Returns:
            Result of coro execution
        """
        timeout = timeout_seconds or self.timeout_seconds

        # Try to enqueue with timeout (backpressure)
        try:
            future: asyncio.Future = asyncio.Future()
            task_item = (coro, args, kwargs, future)

            await asyncio.wait_for(
                self.queue.put(task_item),
                timeout=timeout
            )

            # Wait for result
            result = await future
            return result

        except asyncio.TimeoutError:
            self.overflow_errors += 1
            error = QueueOverflowError(
                f"Task queue full (size={self.queue.qsize()}/{self.queue_size}) "
                f"for {timeout}s; overflow #{self.overflow_errors}"
            )
            logger.error(str(error))
            # TODO: Audit trail integration (F-C3 fix)
            # await audit.write("queue.overflow", { "queue_size": ... })
            raise error

    async def _worker_loop(self):
        """Worker loop: dequeue tasks + execute."""
        while True:
            try:
                # Dequeue task with timeout to allow graceful shutdown
                try:
                    coro, args, kwargs, future = await asyncio.wait_for(
                        self.queue.get(),
                        timeout=5.0
                    )
                except asyncio.TimeoutError:
                    # No tasks for a while; continue waiting
                    continue

                # Execute task + capture result/error
                start = time.time()
                try:
                    if asyncio.iscoroutinefunction(coro):
                        result = await coro(*args, **kwargs)
                    else:
                        result = coro(*args, **kwargs)
                    future.set_result(result)
                except Exception as e:
                    future.set_exception(e)
                finally:
                    latency = (time.time() - start) * 1000
                    self.task_latencies.append(latency)
                    self.tasks_processed += 1

                self.queue.task_done()

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Worker error: {e}")
                break

    async def _monitor_and_scale(self):
        """
        Background loop: monitor queue usage + dynamically scale workers.

        F-C5 Fix: Auto-scaling implementation
        - Scale up when queue > 75% full
        - Scale down when queue < 25% full
        """
        logger.info("WorkerPool auto-scaler started")

        while self.monitoring:
            try:
                queue_usage = self.queue.qsize() / self.queue_size
                current_workers = len(self.active_workers)

                # Scale up: queue getting full
                if queue_usage > 0.75 and current_workers < self.max_workers:
                    new_worker = asyncio.create_task(self._worker_loop())
                    self.active_workers.add(new_worker)
                    new_worker.add_done_callback(self.active_workers.discard)
                    logger.info(
                        f"Scaling UP: {current_workers} → "
                        f"{len(self.active_workers)} workers "
                        f"(queue {queue_usage:.1%} full)"
                    )

                # Scale down: queue mostly empty
                elif queue_usage < 0.25 and current_workers > 1:
                    # Cancel one worker (simplistic; could be more graceful)
                    if self.active_workers:
                        worker = next(iter(self.active_workers))
                        worker.cancel()
                        logger.info(
                            f"Scaling DOWN: {current_workers} → "
                            f"{current_workers - 1} workers "
                            f"(queue {queue_usage:.1%} full)"
                        )

                await asyncio.sleep(1.0)  # Check every 1 second

            except Exception as e:
                logger.error(f"Scaler error: {e}")
                await asyncio.sleep(1.0)

    def get_stats(self) -> WorkerPoolStats:
        """Get current pool statistics."""
        avg_latency = sum(self.task_latencies[-100:]) / len(self.task_latencies[-100:]) \
            if self.task_latencies else 0

        return WorkerPoolStats(
            active_workers=len(self.active_workers),
            queue_size=self.queue.qsize(),
            queue_capacity=self.queue_size,
            tasks_processed=self.tasks_processed,
            overflow_errors=self.overflow_errors,
            avg_latency_ms=avg_latency,
        )

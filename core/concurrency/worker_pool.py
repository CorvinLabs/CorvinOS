"""
Auto-scaling worker pool with queue-based capacity management.

Dynamically scales worker threads based on pending task load:
- Scale up when pending tasks > 75% of queue capacity
- Scale down when pending tasks < 25% of queue capacity
- Graceful worker retirement without dropping tasks
"""

import threading
import time
from typing import Callable, Any, Optional, List
from concurrent.futures import ThreadPoolExecutor, Future
from dataclasses import dataclass


class WorkerError(Exception):
    """Worker pool error."""
    pass


@dataclass
class WorkerTask:
    """One task in the worker pool."""

    func: Callable
    args: tuple
    kwargs: dict
    future: Future
    submitted_at: float
    timeout: float


class AutoScalingWorkerPool:
    """
    Auto-scaling thread pool that adjusts worker count based on pending load.

    Scales dynamically between min_workers and max_workers:
    - Scales up when pending_tasks > scale_up_threshold * max_queue_size
    - Scales down when pending_tasks < scale_down_threshold * max_queue_size
    """

    def __init__(
        self,
        min_workers: int = 2,
        max_workers: int = 16,
        max_queue_size: int = 1000,
        scale_up_threshold: float = 0.75,
        scale_down_threshold: float = 0.25,
        scale_check_interval: float = 0.5,
        worker_idle_timeout: float = 60.0,
        task_timeout: float = 30.0,
    ):
        """
        Initialize auto-scaling worker pool.

        Args:
            min_workers: Minimum number of worker threads
            max_workers: Maximum number of worker threads
            max_queue_size: Maximum queue size (for scaling calculations)
            scale_up_threshold: Threshold for scaling up (0.0-1.0)
            scale_down_threshold: Threshold for scaling down (0.0-1.0)
            scale_check_interval: How often to check and adjust (seconds)
            worker_idle_timeout: Time before idle workers are retired (seconds)
            task_timeout: Default task timeout in seconds
        """
        if not (0 < min_workers <= max_workers):
            raise WorkerError("min_workers must be > 0 and <= max_workers")
        if not (0 < scale_down_threshold < scale_up_threshold < 1.0):
            raise WorkerError(
                "scale_down_threshold must be < scale_up_threshold, both in (0, 1)"
            )

        self.min_workers = min_workers
        self.max_workers = max_workers
        self.max_queue_size = max_queue_size
        self.scale_up_threshold = scale_up_threshold
        self.scale_down_threshold = scale_down_threshold
        self.scale_check_interval = scale_check_interval
        self.worker_idle_timeout = worker_idle_timeout
        self.task_timeout = task_timeout

        # Current executor with fixed worker count
        self._current_workers = min_workers
        self._executor = ThreadPoolExecutor(max_workers=min_workers)

        # Task tracking
        self._tasks: dict[int, WorkerTask] = {}
        self._task_id = 0
        self._lock = threading.Lock()
        self._shutdown = False

        # Scaling state
        self._last_scale_time = time.time()
        self._scale_thread = threading.Thread(
            target=self._monitor_and_scale, daemon=True
        )
        self._scale_thread.start()

    def submit(
        self,
        func: Callable,
        *args: Any,
        timeout: Optional[float] = None,
        **kwargs: Any,
    ) -> int:
        """
        Submit task to pool.

        Args:
            func: Function to execute
            *args: Positional arguments
            timeout: Task-specific timeout (default: pool timeout)
            **kwargs: Keyword arguments

        Returns:
            Task ID

        Raises:
            WorkerError: If pool is shutdown or too many pending tasks
        """
        if self._shutdown:
            raise WorkerError("Worker pool is shutdown")

        task_timeout = timeout or self.task_timeout

        # Check if we're at capacity
        with self._lock:
            pending = sum(
                1 for task in self._tasks.values()
                if not task.future.done()
            )
            if pending >= self.max_queue_size:
                raise WorkerError(
                    f"Queue full with {pending} pending tasks, max is {self.max_queue_size}"
                )

        # Submit task directly to executor
        future = self._executor.submit(func, *args, **kwargs)

        # Record task
        with self._lock:
            task_id = self._task_id
            self._task_id += 1
            task = WorkerTask(
                func=func,
                args=args,
                kwargs=kwargs,
                future=future,
                submitted_at=time.time(),
                timeout=task_timeout,
            )
            self._tasks[task_id] = task

        return task_id

    def result(self, task_id: int, timeout: Optional[float] = None) -> Any:
        """
        Get task result.

        Args:
            task_id: Task ID from submit()
            timeout: Override task timeout

        Returns:
            Task result

        Raises:
            WorkerError: If task not found, timeout, or exception
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                raise WorkerError(f"Task {task_id} not found")

        result_timeout = timeout or task.timeout

        try:
            return task.future.result(timeout=result_timeout)
        except TimeoutError:
            raise WorkerError(f"Task {task_id} timeout after {result_timeout}s")
        except Exception as e:
            raise WorkerError(f"Task {task_id} failed: {e}")

    def cancel(self, task_id: int) -> bool:
        """
        Cancel task.

        Args:
            task_id: Task ID

        Returns:
            True if cancelled, False if already running/done
        """
        with self._lock:
            task = self._tasks.get(task_id)
            if not task:
                return False
            return task.future.cancel()

    def get_active_tasks(self) -> List[int]:
        """Get list of active task IDs."""
        with self._lock:
            return [
                tid
                for tid, task in self._tasks.items()
                if not task.future.done()
            ]

    def _get_active_task_count_unlocked(self) -> int:
        """Get count of active tasks (must be called with lock held)."""
        return sum(
            1 for task in self._tasks.values()
            if not task.future.done()
        )

    def wait_all(self, timeout: float = 60.0) -> None:
        """
        Wait for all active tasks to complete.

        Args:
            timeout: Max wait time

        Raises:
            WorkerError: If timeout
        """
        deadline = time.time() + timeout

        while True:
            with self._lock:
                active_count = self._get_active_task_count_unlocked()
                if active_count == 0:
                    break

            remaining = deadline - time.time()
            if remaining <= 0:
                raise WorkerError(f"wait_all timeout after {timeout}s")

            time.sleep(0.05)

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown pool gracefully.

        Args:
            wait: Wait for all tasks to complete
        """
        self._shutdown = True

        if wait:
            self.wait_all(timeout=self.task_timeout * 2)

        # Shutdown executor
        self._executor.shutdown(wait=wait)

    def get_stats(self) -> dict:
        """Get pool statistics including scaling info."""
        with self._lock:
            active_tasks = self._get_active_task_count_unlocked()
            total_tasks = len(self._tasks)
            pending = active_tasks  # Pending = still running

        queue_usage = pending / self.max_queue_size if self.max_queue_size > 0 else 0

        return {
            "current_workers": self._current_workers,
            "min_workers": self.min_workers,
            "max_workers": self.max_workers,
            "active_tasks": active_tasks,
            "total_tasks": total_tasks,
            "queue_size": pending,
            "max_queue_size": self.max_queue_size,
            "queue_usage_pct": queue_usage * 100,
            "shutdown": self._shutdown,
        }

    def _get_pending_tasks_unlocked(self) -> int:
        """Get number of pending tasks (must be called with lock held)."""
        return self._get_active_task_count_unlocked()

    def _get_queue_usage(self) -> float:
        """
        Get current queue usage as a fraction (0.0-1.0).

        Returns:
            Queue usage percentage
        """
        with self._lock:
            pending = self._get_active_task_count_unlocked()
        if self.max_queue_size <= 0:
            return 0.0
        return min(1.0, pending / self.max_queue_size)

    def _adjust_worker_count(self, target_workers: int) -> None:
        """
        Adjust executor to target worker count.

        Creates a new executor with the target count and shuts down the old.

        Args:
            target_workers: Desired number of workers
        """
        if target_workers == self._current_workers:
            return

        # Avoid thrashing - don't adjust too frequently
        now = time.time()
        if now - self._last_scale_time < 1.0:
            return

        self._last_scale_time = now

        # Create new executor
        new_executor = ThreadPoolExecutor(max_workers=target_workers)

        # Swap executor (old tasks continue on old executor)
        old_executor = self._executor
        self._executor = new_executor
        self._current_workers = target_workers

        # Shutdown old executor gracefully
        old_executor.shutdown(wait=False)

    def _monitor_and_scale(self) -> None:
        """
        Monitor task queue and scale workers up/down.

        Runs in background thread and checks pending load periodically.
        - If pending > scale_up_threshold * max_queue_size: add worker
        - If pending < scale_down_threshold * max_queue_size: remove worker
        """
        while not self._shutdown:
            try:
                queue_usage = self._get_queue_usage()

                # Get pending count briefly
                with self._lock:
                    pending = self._get_active_task_count_unlocked()

                # Scale up if too many pending tasks
                if (
                    queue_usage > self.scale_up_threshold
                    and self._current_workers < self.max_workers
                ):
                    new_count = min(
                        self._current_workers + 1, self.max_workers
                    )
                    self._adjust_worker_count(new_count)

                # Scale down if very few pending tasks and queue empty
                elif (
                    queue_usage < self.scale_down_threshold
                    and self._current_workers > self.min_workers
                    and pending == 0
                ):
                    new_count = max(self._current_workers - 1, self.min_workers)
                    self._adjust_worker_count(new_count)

                time.sleep(self.scale_check_interval)

            except Exception:
                # Don't crash the monitor thread
                if not self._shutdown:
                    time.sleep(0.5)

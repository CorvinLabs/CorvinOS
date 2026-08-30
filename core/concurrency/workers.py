"""
Worker pool for concurrent task execution.

Fixed-size pool with timeout, cancellation, and health monitoring.
"""

import threading
import time
from typing import Callable, Any, Optional, List
from concurrent.futures import Future, ThreadPoolExecutor
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


class WorkerPool:
    """Fixed-size thread pool with timeout and health monitoring."""

    def __init__(self, workers: int = 4, timeout: float = 30.0):
        """
        Initialize worker pool.

        Args:
            workers: Number of worker threads
            timeout: Task timeout in seconds
        """
        self.workers = workers
        self.timeout = timeout
        self._executor = ThreadPoolExecutor(max_workers=workers)
        self._tasks: dict[int, WorkerTask] = {}
        self._task_id = 0
        self._lock = threading.Lock()
        self._shutdown = False

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
            WorkerError: If pool is shutdown
        """
        if self._shutdown:
            raise WorkerError("Worker pool is shutdown")

        task_timeout = timeout or self.timeout
        future = self._executor.submit(func, *args, **kwargs)

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

    def _active_task_ids_locked(self) -> List[int]:
        """Active task IDs. The caller MUST already hold ``self._lock``.

        Split out from :meth:`get_active_tasks` because ``self._lock`` is a
        plain, NON-reentrant ``threading.Lock``: calling the public,
        self-locking method from inside a held lock deadlocks the calling
        thread forever. Both :meth:`wait_all` and :meth:`get_stats` did exactly
        that, so both hung on every single call.
        """
        return [
            tid
            for tid, task in self._tasks.items()
            if not task.future.done()
        ]

    def get_active_tasks(self) -> List[int]:
        """Get list of active task IDs."""
        with self._lock:
            return self._active_task_ids_locked()

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
                active = self._active_task_ids_locked()
                if not active:
                    break

            remaining = deadline - time.time()
            if remaining <= 0:
                raise WorkerError(f"wait_all timeout after {timeout}s")

            time.sleep(0.1)

    def shutdown(self, wait: bool = True) -> None:
        """
        Shutdown pool.

        Args:
            wait: Wait for all tasks to complete
        """
        self._shutdown = True
        self._executor.shutdown(wait=wait)

    def get_stats(self) -> dict:
        """Get pool statistics."""
        with self._lock:
            active = len(self._active_task_ids_locked())
            total = len(self._tasks)

        return {
            "workers": self.workers,
            "active_tasks": active,
            "total_tasks": total,
            "shutdown": self._shutdown,
        }

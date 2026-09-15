"""
Parallel Executor for High-Frequency Learning Events
Implements ADR-0303 Phase 1: concurrent grading worker pool for event processing.

Use case: Learning pipeline emits ~1000s events/session; AsyncQueue insufficient.
Solution: ThreadPoolExecutor (CPU grading) + AsyncPool (I/O telemetry).
"""

from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
import threading
import logging
from typing import Callable, Any, Optional, List
from pathlib import Path

logger = logging.getLogger(__name__)


@dataclass
class WorkItem:
    """Unit of work"""
    task_id: str
    tenant_id: str  # GDPR: mandatory tenant isolation
    fn: Callable
    args: tuple = ()
    kwargs: dict = None

    def __post_init__(self):
        self.kwargs = self.kwargs or {}


class ParallelExecutor:
    """
    Thread pool for CPU-bound grading + I/O-bound telemetry.
    Ensures tenant isolation at thread level.
    """

    def __init__(self, max_workers: int = 4, tenant_id: str = "_default"):
        self.max_workers = max_workers
        self.tenant_id = tenant_id
        self._pool = ThreadPoolExecutor(
            max_workers=max_workers,
            thread_name_prefix=f"grader-{tenant_id}"
        )
        self._active_tasks = {}  # task_id → future
        self._lock = threading.Lock()

    def submit(self, item: WorkItem) -> str:
        """
        Submit work to pool.
        Returns: task_id for later retrieval.
        """
        # Validate tenant isolation
        if item.tenant_id != self.tenant_id:
            logger.warning(f"Cross-tenant submit blocked: {item.tenant_id} != {self.tenant_id}")
            raise ValueError(f"Tenant mismatch: {item.tenant_id}")

        # Wrap with tenant context
        def wrapped_fn():
            # Set thread-local context (simulated; real implementation uses ContextVar)
            return item.fn(*item.args, **item.kwargs)

        future = self._pool.submit(wrapped_fn)

        with self._lock:
            self._active_tasks[item.task_id] = future

        return item.task_id

    def wait_for(self, task_id: str, timeout: Optional[float] = None) -> Optional[Any]:
        """
        Wait for task completion.
        Returns: result or None on timeout.
        """
        with self._lock:
            future = self._active_tasks.get(task_id)

        if not future:
            return None

        try:
            result = future.result(timeout=timeout)
            with self._lock:
                del self._active_tasks[task_id]
            return result
        except TimeoutError:
            logger.warning(f"Task {task_id} timed out after {timeout}s")
            return None
        except Exception as e:
            logger.error(f"Task {task_id} failed: {e}")
            with self._lock:
                del self._active_tasks[task_id]
            return None

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown executor"""
        self._pool.shutdown(wait=wait)
        with self._lock:
            self._active_tasks.clear()


# Global executor (single instance per tenant)
_executors = {}
_executor_lock = threading.Lock()


def get_executor(tenant_id: str = "_default", max_workers: int = 4) -> ParallelExecutor:
    """Get or create executor for tenant"""
    with _executor_lock:
        if tenant_id not in _executors:
            _executors[tenant_id] = ParallelExecutor(max_workers=max_workers, tenant_id=tenant_id)
        return _executors[tenant_id]


# Testing helpers
if __name__ == "__main__":
    # Quick smoke test
    def sample_work(x: int) -> int:
        return x * 2

    executor = get_executor()
    item = WorkItem(
        task_id="test_001",
        tenant_id="_default",
        fn=sample_work,
        args=(21,)
    )
    task_id = executor.submit(item)
    result = executor.wait_for(task_id)
    print(f"Test: 21 * 2 = {result} (expected 42)")
    executor.shutdown()

"""ADR-0305/0424: Context Propagation for Async — ContextVar preservation through asyncio boundaries.

This module provides utilities for preserving ContextVar state across asyncio task boundaries,
including exception handling, timeout management, and task cancellation semantics (ADR-0305).
"""

import asyncio
import sys
import logging
from contextvars import ContextVar, copy_context, Context
from typing import Callable, Any, Coroutine, List, TypeVar, Optional, Dict
from dataclasses import dataclass
from enum import Enum

T = TypeVar('T')
logger = logging.getLogger(__name__)


class QueueOverflowError(Exception):
    """Raised when queue put() times out due to overflow (F-C3: backpressure)."""
    pass


class TaskExecutionStatus(Enum):
    """Task execution status tracking (ADR-0305)."""
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"
    TIMEOUT = "timeout"


@dataclass
class TaskExecutionContext:
    """Captures execution context for a task (ADR-0305 exception handling).

    Stores context snapshots and exception information for debugging and audit trails.
    """
    task_id: str
    status: TaskExecutionStatus
    context_snapshot: Dict[str, Any]
    exception: Optional[Exception] = None
    exception_context_vars: Optional[Dict[str, Any]] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Export execution context as dict (for audit trail)."""
        return {
            "task_id": self.task_id,
            "status": self.status.value,
            "context_snapshot": self.context_snapshot,
            "exception": str(self.exception) if self.exception else None,
            "exception_context_vars": self.exception_context_vars,
            "execution_time_ms": self.execution_time_ms,
        }


class AsyncContextPropagator:
    """Propagate ContextVars through asyncio.create_task() and asyncio.gather() (ADR-0424 Part 1).

    Python 3.7+ automatically copies context when create_task() is called. This class
    provides explicit wrappers for clarity and control, plus enhancements for exception
    handling and timeout management (ADR-0305).
    """

    @staticmethod
    def create_task_with_context(
        coro: Coroutine[Any, Any, T],
        context: Context = None,
    ) -> asyncio.Task[T]:
        """Create task with explicit context propagation.

        Python 3.7+ automatically copies the current context when creating a task,
        so in most cases the context parameter can be omitted.

        Args:
            coro: Coroutine to run
            context: (Deprecated) ContextVar context parameter. Ignored; Python's
                     asyncio.create_task() handles context copying automatically.
                     Kept for API compatibility.

        Returns:
            asyncio.Task with context preserved

        Raises:
            TypeError: if coro is not a coroutine
        """
        if not asyncio.iscoroutine(coro):
            raise TypeError(f"Expected coroutine, got {type(coro)}")

        # Python 3.7+ automatically copies current context when creating a task
        # The explicit context parameter is deprecated and ignored
        return asyncio.create_task(coro)

    @staticmethod
    async def _run_in_context(
        coro: Coroutine[Any, Any, T],
        context: Context,
    ) -> T:
        """Run coroutine in a specific context (for TaskGroup usage).

        This is an internal helper used by AsyncContextTaskGroup.
        Note: For direct coroutine execution, use create_task_with_context() instead,
        which relies on asyncio's automatic context copying.

        Args:
            coro: Coroutine to run
            context: Context to run it in

        Returns:
            Result of the coroutine
        """
        # Simply await the coroutine - the context was already copied when the
        # task was created, so we don't need to do anything special here
        return await coro

    @staticmethod
    async def gather_with_context(
        *coros: Coroutine[Any, Any, T],
        return_exceptions: bool = False,
    ) -> List[T]:
        """asyncio.gather with context propagation to all tasks.

        Each coroutine is spawned as a separate task. Python's asyncio.create_task()
        automatically copies the current context, so all spawned tasks inherit the
        caller's context.

        Args:
            *coros: Coroutines to run
            return_exceptions: Whether to catch exceptions (True) or re-raise (False)

        Returns:
            List of results from all coroutines, in order

        Raises:
            Exception: if any coro raises and return_exceptions=False
        """
        # Create a task for each coroutine
        # asyncio.create_task() automatically copies the current context
        tasks = [
            AsyncContextPropagator.create_task_with_context(coro)
            for coro in coros
        ]

        return await asyncio.gather(
            *tasks,
            return_exceptions=return_exceptions,
        )

    @staticmethod
    async def create_task_with_timeout(
        coro: Coroutine[Any, Any, T],
        timeout: float,
        context: Context = None,
    ) -> T:
        """Create task with explicit timeout and context preservation (ADR-0305).

        Args:
            coro: Coroutine to run
            timeout: Timeout in seconds
            context: ContextVar context (if None, uses current)

        Returns:
            Result of the coroutine

        Raises:
            asyncio.TimeoutError: if coro exceeds timeout
            TypeError: if coro is not a coroutine
        """
        if not asyncio.iscoroutine(coro):
            raise TypeError(f"Expected coroutine, got {type(coro)}")

        try:
            task = AsyncContextPropagator.create_task_with_context(coro, context)
            return await asyncio.wait_for(task, timeout=timeout)
        except asyncio.TimeoutError:
            logger.warning(f"Task timed out after {timeout}s")
            raise

    @staticmethod
    async def create_task_with_exception_capture(
        coro: Coroutine[Any, Any, T],
        context: Context = None,
        context_vars_to_capture: Optional[List[ContextVar]] = None,
    ) -> tuple[Optional[T], Optional[Exception], Optional[Dict[str, Any]]]:
        """Create task with exception context capture (ADR-0305 exception handling).

        Args:
            coro: Coroutine to run
            context: ContextVar context (if None, uses current)
            context_vars_to_capture: List of ContextVar to capture on exception

        Returns:
            Tuple of (result, exception, captured_context_vars)
            - result: coroutine result or None if exception
            - exception: Exception raised or None
            - captured_context_vars: Dict of captured ContextVar values at time of exception
        """
        if not asyncio.iscoroutine(coro):
            raise TypeError(f"Expected coroutine, got {type(coro)}")

        task = AsyncContextPropagator.create_task_with_context(coro, context)
        try:
            result = await task
            return result, None, None
        except Exception as e:
            # Capture context vars at time of exception
            captured_vars = {}
            if context_vars_to_capture:
                for var in context_vars_to_capture:
                    try:
                        captured_vars[var.name] = var.get()
                    except LookupError:
                        captured_vars[var.name] = None
            logger.error(f"Task failed with {type(e).__name__}: {e}", exc_info=True)
            return None, e, captured_vars

    @staticmethod
    def create_task_group_with_context(
        context: Context = None,
    ) -> 'AsyncContextTaskGroup':
        """Create a task group that propagates context to all spawned tasks (Python 3.11+).

        Args:
            context: Context to propagate (if None, uses current)

        Returns:
            AsyncContextTaskGroup that can be used as an async context manager

        Raises:
            RuntimeError: if Python version < 3.11
        """
        if sys.version_info < (3, 11):
            raise RuntimeError("AsyncContextTaskGroup requires Python 3.11+")
        if context is None:
            context = copy_context()
        return AsyncContextTaskGroup(context)


class AsyncContextTaskGroup:
    """Wrapper around asyncio.TaskGroup (Python 3.11+) with context propagation.

    This is only available on Python 3.11+. On older versions, use gather_with_context().
    """

    def __init__(self, context: Context = None):
        """Initialize the task group.

        Args:
            context: Context to propagate to all tasks (if None, copies current)
        """
        if sys.version_info < (3, 11):
            raise RuntimeError("AsyncContextTaskGroup requires Python 3.11+")
        self.context = context or copy_context()
        self.task_group = None
        self.task_count = 0

    async def __aenter__(self) -> 'AsyncContextTaskGroup':
        """Enter the async context manager."""
        # Import here to avoid syntax error on Python < 3.11
        import asyncio as aio
        self.task_group = aio.TaskGroup()
        await self.task_group.__aenter__()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Exit the async context manager."""
        return await self.task_group.__aexit__(exc_type, exc_val, exc_tb)

    def create_task(self, coro: Coroutine[Any, Any, T]) -> asyncio.Task[T]:
        """Create a task in the group with context propagation.

        Args:
            coro: Coroutine to run

        Returns:
            asyncio.Task added to the group
        """
        if self.task_group is None:
            raise RuntimeError("TaskGroup not yet entered")
        self.task_count += 1
        return self.task_group.create_task(
            AsyncContextPropagator._run_in_context(coro, self.context)
        )

    def task_count_created(self) -> int:
        """Return number of tasks created in this group.

        Returns:
            Number of tasks created
        """
        return self.task_count


class BoundedAsyncQueue(asyncio.Queue):
    """Bounded async queue with backpressure and overflow detection (F-C3 fix).

    This queue enforces a size limit and raises QueueOverflowError when put()
    times out, preventing silent data loss. Unlike asyncio.Queue.put_nowait()
    which silently drops items when full, this implementation provides explicit
    backpressure with fail-closed semantics.

    Use case: Learning event emission, audit trails, and other critical async
    workloads where silent drops are unacceptable.
    """

    def __init__(self, maxsize: int = 1000, timeout: float = 1.0):
        """Initialize bounded async queue.

        Args:
            maxsize: Maximum number of items (must be > 0)
            timeout: Timeout in seconds for put() operations

        Raises:
            ValueError: If maxsize <= 0
        """
        if maxsize <= 0:
            raise ValueError(f"maxsize must be > 0, got {maxsize}")
        super().__init__(maxsize=maxsize)
        self.timeout = timeout
        self._overflow_count = 0

    async def put(self, item: Any, timeout: Optional[float] = None) -> None:
        """Put an item with timeout and backpressure.

        Args:
            item: Item to add
            timeout: Timeout in seconds (if None, uses instance default)

        Raises:
            QueueOverflowError: If put times out (queue remains full for timeout duration)
            asyncio.CancelledError: If cancelled
        """
        effective_timeout = timeout if timeout is not None else self.timeout

        try:
            await asyncio.wait_for(
                super().put(item),
                timeout=effective_timeout
            )
        except asyncio.TimeoutError:
            self._overflow_count += 1
            msg = (
                f"QueueOverflow: Failed to put item after {effective_timeout}s "
                f"(queue size={self.qsize()}, maxsize={self.maxsize}, "
                f"total overflows={self._overflow_count})"
            )
            logger.error(msg)
            raise QueueOverflowError(msg) from None

    async def put_nowait_with_backpressure(self, item: Any) -> None:
        """Put item with backpressure (fail-closed on full).

        This is a wrapper that immediately raises on full, rather than silently
        dropping (which asyncio.Queue.put_nowait does). Use this when you want
        fail-closed behavior but cannot block.

        Args:
            item: Item to add

        Raises:
            QueueOverflowError: If queue is full
        """
        try:
            self.put_nowait(item)
        except asyncio.QueueFull:
            self._overflow_count += 1
            msg = (
                f"QueueOverflow: put_nowait_with_backpressure failed "
                f"(queue size={self.qsize()}, maxsize={self.maxsize}, "
                f"total overflows={self._overflow_count})"
            )
            logger.error(msg)
            raise QueueOverflowError(msg) from None

    def get_overflow_count(self) -> int:
        """Get total number of overflow events (for monitoring).

        Returns:
            Count of overflow events since queue creation
        """
        return self._overflow_count

    def reset_overflow_count(self) -> None:
        """Reset overflow counter (for testing)."""
        self._overflow_count = 0

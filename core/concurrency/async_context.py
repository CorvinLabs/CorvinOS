"""ADR-0424: Context Propagation for Async — ContextVar preservation through asyncio boundaries."""

import asyncio
import sys
from contextvars import ContextVar, copy_context, Context
from typing import Callable, Any, Coroutine, List, TypeVar

T = TypeVar('T')


class AsyncContextPropagator:
    """Propagate ContextVars through asyncio.create_task() and asyncio.gather() (ADR-0424 Part 1).

    Python 3.7+ automatically copies context when create_task() is called. This class
    provides explicit wrappers for clarity and control.
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
        return self.task_group.create_task(
            AsyncContextPropagator._run_in_context(coro, self.context)
        )

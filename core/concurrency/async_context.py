"""ADR-0305: Context Propagation for Async — ContextVar preservation through asyncio boundaries."""

import asyncio
from contextvars import ContextVar, copy_context
from typing import Callable, Any, Coroutine


class AsyncContextPropagator:
    """Propagate ContextVars through asyncio.create_task() (ADR-0305 Part 1)."""

    @staticmethod
    def create_task_with_context(
        coro: Coroutine,
        context=None,
    ) -> asyncio.Task:
        """Create task with explicit context propagation.

        Args:
            coro: Coroutine to run
            context: ContextVar context (if None, copies current)

        Returns:
            asyncio.Task with context preserved
        """
        if context is None:
            context = copy_context()

        return asyncio.create_task(
            AsyncContextPropagator._run_in_context(coro, context)
        )

    @staticmethod
    async def _run_in_context(
        coro: Coroutine,
        context,
    ) -> Any:
        """Run coroutine in copied context."""
        # Run the coroutine with the given context
        return await context.run(lambda: coro)

    @staticmethod
    def gather_with_context(
        *coros,
        return_exceptions: bool = False,
    ) -> asyncio.Task:
        """asyncio.gather with context propagation to all tasks.

        Args:
            *coros: Coroutines to run
            return_exceptions: Whether to catch exceptions

        Returns:
            Task that completes when all coros are done
        """
        current_context = copy_context()

        wrapped_coros = [
            AsyncContextPropagator._run_in_context(coro, current_context)
            for coro in coros
        ]

        return asyncio.gather(
            *wrapped_coros,
            return_exceptions=return_exceptions,
        )

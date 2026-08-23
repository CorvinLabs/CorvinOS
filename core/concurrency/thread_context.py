"""ADR-0305: Context Propagation for Threading — ContextVar preservation through threads."""

import threading
from contextvars import ContextVar, copy_context
from concurrent.futures import ThreadPoolExecutor
from typing import Callable, Any


class ThreadContextPropagator:
    """Propagate ContextVars through threading.Thread() (ADR-0305 Part 2)."""

    @staticmethod
    def thread_with_context(
        target: Callable,
        args=(),
        kwargs=None,
        context=None,
    ) -> threading.Thread:
        """Create thread with explicit context propagation.

        Args:
            target: Function to run in thread
            args: Positional arguments
            kwargs: Keyword arguments
            context: ContextVar context (if None, copies current)

        Returns:
            threading.Thread with context preserved
        """
        if kwargs is None:
            kwargs = {}

        if context is None:
            context = copy_context()

        def wrapped_target():
            # Run target in the copied context
            context.run(target, *args, **kwargs)

        return threading.Thread(target=wrapped_target)

    @staticmethod
    def executor_with_context(
        executor: ThreadPoolExecutor,
        fn: Callable,
        *args,
        context=None,
    ):
        """Submit to executor with context propagation.

        Args:
            executor: ThreadPoolExecutor
            fn: Function to run
            *args: Arguments
            context: ContextVar context (if None, copies current)

        Returns:
            Future with context preserved
        """
        if context is None:
            context = copy_context()

        def wrapped_fn():
            return context.run(fn, *args)

        return executor.submit(wrapped_fn)

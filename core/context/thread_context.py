"""
Thread context propagation for threading.Thread() and Executor.submit().
"""

import threading
from concurrent.futures import Executor, ThreadPoolExecutor
from contextvars import copy_context, ContextVar
from typing import Callable, Any, Dict, Optional

from core.context.helpers import ContextError


def thread_with_context(
    target: Callable,
    args: tuple = (),
    kwargs: Dict[str, Any] = None,
    ctx_dict: Optional[Dict[str, Any]] = None,
    **thread_kwargs: Any,
) -> threading.Thread:
    """
    Create thread with explicit context propagation.

    threading.Thread() doesn't inherit ContextVars.
    This wrapper captures context before spawning.

    Args:
        target: Function to run in thread
        args: Positional arguments for target
        kwargs: Keyword arguments for target
        ctx_dict: Context dict (from get_current_context). If None, captures current.
        **thread_kwargs: Additional threading.Thread arguments (name, daemon, etc.)

    Returns:
        threading.Thread with context preserved
    """
    if kwargs is None:
        kwargs = {}

    if ctx_dict is None:
        ctx_dict = dict(copy_context())

    def wrapper():
        # Set all context vars in this thread
        for var, value in ctx_dict.items():
            if isinstance(var, ContextVar):
                try:
                    var.set(value)
                except Exception:
                    pass

        # Run the target function
        return target(*args, **kwargs)

    return threading.Thread(target=wrapper, **thread_kwargs)


def executor_submit_with_context(
    executor: Executor,
    fn: Callable,
    *args: Any,
    ctx_dict: Optional[Dict[str, Any]] = None,
    **kwargs: Any,
):
    """
    Submit work to executor with context preservation.

    ThreadPoolExecutor may lose ContextVars when work is dequeued.
    This ensures context is restored in the worker thread.

    Args:
        executor: concurrent.futures.Executor (typically ThreadPoolExecutor)
        fn: Function to execute
        *args: Positional arguments for fn
        ctx_dict: Context dict (from get_current_context). If None, captures current.
        **kwargs: Keyword arguments for fn

    Returns:
        Future with context-preserved work
    """
    if ctx_dict is None:
        ctx_dict = dict(copy_context())

    def wrapper():
        # Set all context vars in executor's thread
        for var, value in ctx_dict.items():
            if isinstance(var, ContextVar):
                try:
                    var.set(value)
                except Exception:
                    pass

        # Execute the function
        return fn(*args, **kwargs)

    return executor.submit(wrapper)


class ContextPreservingExecutor:
    """Wrapper around Executor that preserves context automatically."""

    def __init__(self, executor: Executor):
        """
        Initialize wrapper.

        Args:
            executor: Base executor to wrap
        """
        self.executor = executor
        self._context = dict(copy_context())

    def submit(self, fn: Callable, *args: Any, **kwargs: Any):
        """Submit with automatic context preservation."""
        return executor_submit_with_context(
            self.executor, fn, *args, ctx_dict=self._context, **kwargs
        )

    def shutdown(self, wait: bool = True) -> None:
        """Shutdown wrapped executor."""
        self.executor.shutdown(wait=wait)

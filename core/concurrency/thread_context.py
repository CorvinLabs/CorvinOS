"""ADR-0424: Context Propagation for Threading — ContextVar preservation through threads."""

import threading
from contextvars import ContextVar, copy_context, Context
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Dict, Optional, TypeVar

T = TypeVar('T')


class ThreadContextPropagator:
    """Propagate ContextVars through threading.Thread() and ThreadPoolExecutor (ADR-0424 Part 2).

    Unlike asyncio, threading.Thread() does NOT automatically copy the current context.
    This class explicitly copies and propagates context to threads.
    """

    @staticmethod
    def thread_with_context(
        target: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        context: Context = None,
        name: str = None,
        daemon: bool = False,
    ) -> threading.Thread:
        """Create thread with explicit context propagation.

        Args:
            target: Function to run in thread
            args: Positional arguments (must be picklable for thread safety)
            kwargs: Keyword arguments (must be picklable for thread safety)
            context: ContextVar context (if None, copies current)
            name: Thread name (optional)
            daemon: Daemon flag (optional)

        Returns:
            threading.Thread with context preserved

        Raises:
            TypeError: if target is not callable
        """
        if not callable(target):
            raise TypeError(f"target must be callable, got {type(target)}")

        if kwargs is None:
            kwargs = {}

        if context is None:
            context = copy_context()

        def wrapped_target():
            # Run target in the copied context
            return context.run(target, *args, **kwargs)

        return threading.Thread(
            target=wrapped_target,
            name=name,
            daemon=daemon,
        )

    @staticmethod
    def executor_with_context(
        executor: ThreadPoolExecutor,
        fn: Callable[..., T],
        *args: Any,
        context: Context = None,
        **kwargs: Any,
    ) -> Future[T]:
        """Submit to executor with context propagation.

        Args:
            executor: ThreadPoolExecutor to submit to
            fn: Function to run
            *args: Positional arguments
            context: ContextVar context (if None, copies current)
            **kwargs: Keyword arguments

        Returns:
            Future that will contain the result

        Raises:
            TypeError: if fn is not callable
        """
        if not callable(fn):
            raise TypeError(f"fn must be callable, got {type(fn)}")

        if context is None:
            context = copy_context()

        def wrapped_fn():
            return context.run(fn, *args, **kwargs)

        return executor.submit(wrapped_fn)


class ThreadLocalContext:
    """Thread-local storage for context-scoped values (analogue to threading.local).

    This provides a simple thread-local storage using threading.local that respects
    context boundaries. Unlike ContextVar which is task-local in async, this is
    thread-local.

    Example:
        storage = ThreadLocalContext()
        storage.set('user_id', '12345')
        assert storage.get('user_id') == '12345'
    """

    def __init__(self):
        """Initialize thread-local storage."""
        self._storage = threading.local()

    def set(self, key: str, value: Any) -> None:
        """Set a value in thread-local storage.

        Args:
            key: Key to store under
            value: Value to store
        """
        if not hasattr(self._storage, '_dict'):
            self._storage._dict = {}
        self._storage._dict[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """Get a value from thread-local storage.

        Args:
            key: Key to retrieve
            default: Default value if key not found

        Returns:
            Value or default
        """
        if not hasattr(self._storage, '_dict'):
            return default
        return self._storage._dict.get(key, default)

    def clear(self, key: str = None) -> None:
        """Clear a value or all values from thread-local storage.

        Args:
            key: Key to clear (if None, clears all)
        """
        if not hasattr(self._storage, '_dict'):
            return
        if key is None:
            self._storage._dict = {}
        else:
            self._storage._dict.pop(key, None)

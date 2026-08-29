"""ADR-0305/0424: Context Propagation for Threading — ContextVar preservation through threads.

This module provides utilities for preserving ContextVar state across threading boundaries,
including lifecycle management, cleanup, and exception handling (ADR-0305).
"""

import threading
import logging
import time
from contextvars import ContextVar, copy_context, Context
from concurrent.futures import ThreadPoolExecutor, Future
from typing import Callable, Any, Dict, Optional, TypeVar, List
from dataclasses import dataclass
from enum import Enum

T = TypeVar('T')
logger = logging.getLogger(__name__)


class ThreadExecutionStatus(Enum):
    """Thread execution status tracking (ADR-0305)."""
    CREATED = "created"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"


@dataclass
class ThreadExecutionContext:
    """Captures execution context for a thread (ADR-0305 lifecycle management).

    Stores thread metadata, context snapshots, and exception information.
    """
    thread_id: int
    thread_name: str
    status: ThreadExecutionStatus
    context_snapshot: Dict[str, Any]
    exception: Optional[Exception] = None
    exception_context_vars: Optional[Dict[str, Any]] = None
    execution_time_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Export execution context as dict (for audit trail)."""
        return {
            "thread_id": self.thread_id,
            "thread_name": self.thread_name,
            "status": self.status.value,
            "context_snapshot": self.context_snapshot,
            "exception": str(self.exception) if self.exception else None,
            "exception_context_vars": self.exception_context_vars,
            "execution_time_ms": self.execution_time_ms,
        }


class ThreadContextPropagator:
    """Propagate ContextVars through threading.Thread() and ThreadPoolExecutor (ADR-0424 Part 2).

    Unlike asyncio, threading.Thread() does NOT automatically copy the current context.
    This class explicitly copies and propagates context to threads, with enhancements
    for exception handling and lifecycle management (ADR-0305).
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
    def thread_with_exception_capture(
        target: Callable[..., Any],
        args: tuple = (),
        kwargs: Optional[Dict[str, Any]] = None,
        context: Context = None,
        name: str = None,
        daemon: bool = False,
        context_vars_to_capture: Optional[List[ContextVar]] = None,
    ) -> tuple[threading.Thread, List[Optional[Exception]]]:
        """Create thread with exception capture (ADR-0305 exception handling).

        Args:
            target: Function to run in thread
            args: Positional arguments
            kwargs: Keyword arguments
            context: ContextVar context (if None, copies current)
            name: Thread name (optional)
            daemon: Daemon flag (optional)
            context_vars_to_capture: List of ContextVar to capture on exception

        Returns:
            Tuple of (thread, exception_container)
            - thread: threading.Thread ready to start
            - exception_container: List containing exception if one occurs, else empty

        Raises:
            TypeError: if target is not callable
        """
        if not callable(target):
            raise TypeError(f"target must be callable, got {type(target)}")

        if kwargs is None:
            kwargs = {}

        if context is None:
            context = copy_context()

        exception_container = []

        def wrapped_target():
            try:
                return context.run(target, *args, **kwargs)
            except Exception as e:
                exception_container.append(e)
                # Capture context vars at time of exception
                if context_vars_to_capture:
                    for var in context_vars_to_capture:
                        try:
                            logger.error(
                                f"Thread {threading.current_thread().name} failed: "
                                f"{type(e).__name__} with context var {var.name}={var.get()}"
                            )
                        except LookupError:
                            pass
                raise

        thread = threading.Thread(
            target=wrapped_target,
            name=name,
            daemon=daemon,
        )
        return thread, exception_container

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

    @staticmethod
    def executor_with_exception_capture(
        executor: ThreadPoolExecutor,
        fn: Callable[..., T],
        *args: Any,
        context: Context = None,
        context_vars_to_capture: Optional[List[ContextVar]] = None,
        **kwargs: Any,
    ) -> Future[tuple[Optional[T], Optional[Exception], Optional[Dict[str, Any]]]]:
        """Submit to executor with context + exception capture (ADR-0305).

        Args:
            executor: ThreadPoolExecutor to submit to
            fn: Function to run
            *args: Positional arguments
            context: ContextVar context (if None, copies current)
            context_vars_to_capture: List of ContextVar to capture on exception
            **kwargs: Keyword arguments

        Returns:
            Future containing (result, exception, captured_context_vars)

        Raises:
            TypeError: if fn is not callable
        """
        if not callable(fn):
            raise TypeError(f"fn must be callable, got {type(fn)}")

        if context is None:
            context = copy_context()

        def wrapped_fn():
            try:
                result = context.run(fn, *args, **kwargs)
                return result, None, None
            except Exception as e:
                captured_vars = {}
                if context_vars_to_capture:
                    for var in context_vars_to_capture:
                        try:
                            captured_vars[var.name] = var.get()
                        except LookupError:
                            captured_vars[var.name] = None
                logger.error(f"Executor worker failed: {type(e).__name__}: {e}", exc_info=True)
                return None, e, captured_vars

        return executor.submit(wrapped_fn)


class ManagedThread:
    """Managed thread wrapper with lifecycle + cleanup (ADR-0305).

    Tracks thread state and ensures cleanup happens via context manager.
    """

    def __init__(self, thread: threading.Thread):
        """Initialize managed thread.

        Args:
            thread: threading.Thread instance to manage
        """
        self.thread = thread
        self.is_alive_flag = False
        self.exception_occurred = False

    def start(self) -> None:
        """Start the managed thread."""
        self.thread.start()
        self.is_alive_flag = True

    def join(self, timeout: Optional[float] = None) -> None:
        """Wait for thread to complete.

        Args:
            timeout: Timeout in seconds (None = infinite)
        """
        self.thread.join(timeout)
        if not self.thread.is_alive():
            self.is_alive_flag = False

    def __enter__(self) -> 'ManagedThread':
        """Enter context manager."""
        self.start()
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit context manager and ensure cleanup."""
        # Try to join with a reasonable timeout
        self.join(timeout=5.0)
        if self.thread.is_alive():
            logger.warning(f"Thread {self.thread.name} did not terminate within timeout")
        self.is_alive_flag = False

    def is_running(self) -> bool:
        """Check if thread is still running.

        Returns:
            True if thread is alive, False otherwise
        """
        return self.thread.is_alive()


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

"""
Context Propagation (Async) — ADR-0305

Explicit ContextVar propagation through async/threading boundaries.
"""

from core.context.helpers import (
    get_current_context,
    set_context,
    ContextError,
)
from core.context.async_context import (
    async_run_with_context,
    async_task_with_context,
)
from core.context.thread_context import (
    thread_with_context,
    executor_submit_with_context,
)

__all__ = [
    "get_current_context",
    "set_context",
    "ContextError",
    "async_run_with_context",
    "async_task_with_context",
    "thread_with_context",
    "executor_submit_with_context",
]

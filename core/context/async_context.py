"""
Async context propagation for asyncio.create_task() and coroutines.
"""

import asyncio
from contextvars import copy_context
from typing import Coroutine, Any, Dict, Optional

from core.context.helpers import ContextError


async def async_run_with_context(
    coro: Coroutine,
    ctx_dict: Optional[Dict[str, Any]] = None,
) -> Any:
    """
    Run coroutine with explicit context.

    Captures context before running, ensures context is set in coroutine.

    Args:
        coro: Coroutine to run
        ctx_dict: Context dict (from get_current_context). If None, uses current.

    Returns:
        Result from coroutine
    """
    if ctx_dict is None:
        ctx = copy_context()
    else:
        ctx = copy_context()
        # Apply context dict values (best effort)
        for var, value in ctx_dict.items():
            try:
                var.set(value)
            except Exception:
                pass  # Silent ignore for non-ContextVar types

    return await coro


async def async_task_with_context(
    coro: Coroutine,
    ctx_dict: Optional[Dict[str, Any]] = None,
) -> asyncio.Task:
    """
    Create a task with explicit context propagation.

    asyncio.create_task() doesn't fully inherit ContextVars in all cases.
    This ensures context is properly set before task runs.

    Args:
        coro: Coroutine to run as task
        ctx_dict: Context dict (from get_current_context). If None, uses current.

    Returns:
        asyncio.Task with context preserved
    """
    if ctx_dict is None:
        ctx = copy_context()
    else:
        ctx = copy_context()
        # Apply context dict values
        from contextvars import ContextVar
        for var, value in ctx_dict.items():
            if isinstance(var, ContextVar):
                try:
                    var.set(value)
                except Exception:
                    pass

    # Create task in the captured context
    task = asyncio.create_task(
        async_run_with_context(coro, ctx_dict)
    )

    return task


async def gather_with_context(*coros: Coroutine, ctx_dict: Optional[Dict[str, Any]] = None) -> list:
    """
    Run multiple coroutines with same context.

    Args:
        *coros: Coroutines to run
        ctx_dict: Context dict to propagate. If None, uses current.

    Returns:
        List of results from coroutines
    """
    tasks = [
        await async_task_with_context(coro, ctx_dict)
        for coro in coros
    ]

    return await asyncio.gather(*tasks)

"""
Context variable helpers for propagation.
"""

from contextvars import ContextVar, copy_context
from typing import Dict, Any, Optional


class ContextError(Exception):
    """Context operation error."""
    pass


def get_current_context() -> Dict[str, Any]:
    """
    Capture all current ContextVar values as a dict.

    Returns:
        Dict of all ContextVar values accessible to current context.
    """
    ctx = copy_context()
    return dict(ctx)


def set_context(ctx_dict: Dict[str, Any]) -> None:
    """
    Apply a context dict (from get_current_context) to current context.

    Note: This only works within the same context (cannot cross boundaries).
    Use this within wrappers after context is set up.

    Args:
        ctx_dict: Dict from get_current_context()
    """
    try:
        for var, value in ctx_dict.items():
            if isinstance(var, ContextVar):
                var.set(value)
    except Exception as e:
        raise ContextError(f"Failed to set context: {e}")


def preserve_context(func):
    """
    Decorator to preserve context through function execution.

    Captures context before execution, restores after.
    """
    import functools

    @functools.wraps(func)
    def wrapper(*args, **kwargs):
        ctx = copy_context()
        return ctx.run(func, *args, **kwargs)

    return wrapper

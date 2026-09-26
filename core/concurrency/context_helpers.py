"""ADR-0424: Context Propagation Helpers — Utilities for decorator + pipeline integration."""

from contextvars import ContextVar, Token
from functools import wraps
from typing import Dict, Any, Optional, Callable


class ContextSnapshot:
    """Capture + restore ContextVar state (for audit logging, error reporting)."""

    def __init__(self):
        """Initialize snapshot (empty)."""
        self.values: Dict[str, Any] = {}

    def capture(self, context_vars: list[ContextVar]) -> None:
        """Capture current value of each ContextVar.

        Args:
            context_vars: List of ContextVar to snapshot
        """
        for var in context_vars:
            try:
                self.values[var.name] = var.get()
            except LookupError:
                # ContextVar not set in this context
                self.values[var.name] = None

    def restore(self, context_vars: list[ContextVar]) -> None:
        """Restore ContextVars to snapshotted state.

        Args:
            context_vars: List of ContextVar to restore
        """
        for var in context_vars:
            value = self.values.get(var.name)
            if value is not None:
                var.set(value)

    def to_dict(self) -> Dict[str, Any]:
        """Export snapshot as dict (for audit trail)."""
        return dict(self.values)


# FIX #2: Canonical ContextVar definitions (single source of truth)
# All modules import these, preventing module-isolation bugs
TASK_ID_VAR: ContextVar = ContextVar("task_id", default=None)
WORKTREE_PATH_VAR: ContextVar = ContextVar("worktree_path", default=None)
BASE_COMMIT_VAR: ContextVar = ContextVar("base_commit", default=None)
PHASE_NAME_VAR: ContextVar = ContextVar("phase_name", default=None)
SESSION_ID_VAR: ContextVar = ContextVar("session_id", default=None)


class TenantContextVar:
    """Specialized ContextVar for tenant_id (load-bearing for GDPR isolation)."""

    _tenant_var = ContextVar("tenant_id", default=None)

    @classmethod
    def set(cls, tenant_id: str) -> Token:
        """Set tenant_id in current context.

        Args:
            tenant_id: Tenant identifier

        Returns:
            The ``contextvars.Token`` for ``reset()`` — callers that set the
            tenant for a bounded scope (request handler, test fixture) restore
            the previous value with it instead of leaking the tenant into the
            surrounding context.
        """
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        return cls._tenant_var.set(tenant_id)

    @classmethod
    def clear(cls) -> Token:
        """Unset tenant_id in the current context (returns the reset token).

        Used by scope boundaries (and the test-suite autouse fixture) so a
        tenant set in one scope can never be observed by the next one —
        ``get_or_fail()`` must fail-closed there.
        """
        return cls._tenant_var.set(None)

    @classmethod
    def reset(cls, token: Token) -> None:
        """Restore the value that was current before the matching set()/clear()."""
        cls._tenant_var.reset(token)

    @classmethod
    def get(cls) -> Optional[str]:
        """Get tenant_id from current context.

        Returns:
            tenant_id or None if not set
        """
        return cls._tenant_var.get()

    @classmethod
    def get_or_fail(cls) -> str:
        """Get tenant_id, raise if not set (fail-closed).

        Returns:
            tenant_id

        Raises:
            RuntimeError: if tenant_id not set
        """
        tenant_id = cls.get()
        if not tenant_id:
            raise RuntimeError("tenant_id not set in context (GDPR isolation failure)")
        return tenant_id


# FIX #7: @require_context decorator for critical paths
class ContextLossError(Exception):
    """Raised when required context is missing (context-helpers version)."""
    pass


def require_context(*var_names: str) -> Callable:
    """Enforce context at function entry (fail-closed).

    Decorator that verifies required ContextVars are set before function execution.
    If any required context variable is missing, raises ContextLossError immediately.

    Args:
        *var_names: Names of context variables to verify (e.g., "task_id", "tenant_id")

    Returns:
        Decorator function

    Example:
        @require_context("task_id", "tenant_id")
        def process_turn(task_id: str) -> dict:
            ...
    """
    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args, **kwargs):
            missing = []
            for var_name in var_names:
                var_attr_name = f"{var_name.upper()}_VAR"
                # Get the ContextVar from the module globals
                var = globals().get(var_attr_name)
                if var is None:
                    # Also check if it's a class method on TenantContextVar
                    if var_name.lower() == "tenant_id":
                        if TenantContextVar.get() is None:
                            missing.append(var_name)
                    continue
                try:
                    if var.get() is None:
                        missing.append(var_name)
                except LookupError:
                    missing.append(var_name)

            if missing:
                raise ContextLossError(
                    f"Missing required context: {missing}. "
                    f"Function {func.__name__} requires context to be initialized."
                )
            return func(*args, **kwargs)
        return wrapper
    return decorator

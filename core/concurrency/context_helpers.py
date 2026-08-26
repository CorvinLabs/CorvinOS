"""ADR-0424: Context Propagation Helpers — Utilities for decorator + pipeline integration."""

from contextvars import ContextVar
from typing import Dict, Any, Optional


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


class TenantContextVar:
    """Specialized ContextVar for tenant_id (load-bearing for GDPR isolation)."""

    _tenant_var = ContextVar("tenant_id", default=None)

    @classmethod
    def set(cls, tenant_id: str) -> None:
        """Set tenant_id in current context.

        Args:
            tenant_id: Tenant identifier
        """
        if not tenant_id:
            raise ValueError("tenant_id cannot be empty")
        cls._tenant_var.set(tenant_id)

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

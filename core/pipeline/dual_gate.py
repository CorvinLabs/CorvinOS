"""
Dual-Gate Pipeline: Capability + Audit

Fail-closed: both gates must pass before execution.
ContextVar-based for transport-agnostic isolation.
"""

import asyncio
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any, Callable, Optional


# ContextVars for pipeline state
_current_actor: ContextVar[Optional[str]] = ContextVar(
    "pipeline_actor", default=None
)
_current_capability: ContextVar[Optional[str]] = ContextVar(
    "pipeline_capability", default=None
)
_current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "pipeline_tenant_id", default=None
)
_current_resource: ContextVar[Optional[str]] = ContextVar(
    "pipeline_resource", default=None
)


@dataclass
class PipelineContext:
    """Pipeline execution context."""

    actor: str  # Who is performing the action
    capability: str  # What capability is required
    action: str  # Action name (e.g., "read", "write", "delete")
    resource: str  # Resource being accessed
    tenant_id: str  # Tenant context
    details: Optional[dict[str, Any]] = None  # Additional metadata


class PipelineExecutionError(Exception):
    """Base pipeline execution error."""

    pass


class CapabilityGateError(PipelineExecutionError):
    """Capability gate check failed."""

    pass


class AuditGateError(PipelineExecutionError):
    """Audit gate check failed."""

    pass


class DualGatePipeline:
    """Fail-closed dual-gate pipeline: Capability → Audit → Execute."""

    def __init__(self, audit_chain: Any, capability_checker: Any):
        """
        Initialize pipeline.

        Args:
            audit_chain: AuditChain instance (from ADR-0299)
            capability_checker: CapabilityRegistry instance (from ADR-0302)
        """
        self.audit_chain = audit_chain
        self.capability_checker = capability_checker

    def set_context(self, context: PipelineContext) -> None:
        """Set execution context via ContextVars."""
        _current_actor.set(context.actor)
        _current_capability.set(context.capability)
        _current_tenant_id.set(context.tenant_id)
        _current_resource.set(context.resource)

    def get_actor(self) -> Optional[str]:
        """Get current actor from context."""
        return _current_actor.get()

    def get_capability(self) -> Optional[str]:
        """Get required capability from context."""
        return _current_capability.get()

    def get_tenant_id(self) -> Optional[str]:
        """Get tenant ID from context."""
        return _current_tenant_id.get()

    def get_resource(self) -> Optional[str]:
        """Get resource from context."""
        return _current_resource.get()

    def check_capability(
        self, actor: str, capability: str, tenant_id: str
    ) -> bool:
        """
        Gate 1: Check if actor has capability (fail-closed).

        Returns:
            True if actor has capability
            False otherwise (denial)

        Raises:
            CapabilityGateError if check fails structurally
        """
        try:
            return self.capability_checker.has_capability(
                actor=actor, capability=capability, tenant_id=tenant_id
            )
        except Exception as e:
            raise CapabilityGateError(
                f"Capability check failed for {actor}/{capability}: {e}"
            )

    def record_audit(
        self,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        result: str,
        tenant_id: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Gate 2: Record audit entry atomically (fail-closed).

        Args:
            event_type: Type of event (e.g., "auth", "write", "delete")
            actor: Who performed the action
            action: What was done
            resource: Resource being accessed
            result: "success" | "failure"
            tenant_id: Tenant context
            details: Additional metadata

        Raises:
            AuditGateError if audit recording fails
        """
        try:
            from core.audit import AuditEntry

            entry = AuditEntry(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                result=result,
                timestamp=self._get_timestamp(),
                details=details or {},
            )
            self.audit_chain.record(entry)
        except Exception as e:
            raise AuditGateError(f"Audit recording failed: {e}")

    def execute_guarded(
        self,
        context: PipelineContext,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute function through both gates.

        Flow:
        1. Gate 1: Check capability (fail-closed)
        2. Pre-audit: Record audit entry (success placeholder)
        3. Gate 2: Execute function
        4. Post-audit: Update result field in audit

        Args:
            context: Pipeline context
            func: Function to execute
            *args, **kwargs: Arguments to func

        Returns:
            Result from func

        Raises:
            CapabilityGateError if Gate 1 fails
            AuditGateError if audit record fails
            Exception from func if execution fails
        """
        # Set context
        self.set_context(context)

        # Gate 1: Capability check (fail-closed)
        if not self.check_capability(
            context.actor, context.capability, context.tenant_id
        ):
            # Audit the denial
            self.record_audit(
                event_type="capability_denied",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "capability_denied"},
            )
            raise CapabilityGateError(
                f"Actor {context.actor} lacks capability {context.capability}"
            )

        # Gate 2: Record audit entry (pre-execution)
        self.record_audit(
            event_type="operation",
            actor=context.actor,
            action=context.action,
            resource=context.resource,
            result="pending",
            tenant_id=context.tenant_id,
            details=context.details,
        )

        # Execute function
        try:
            result = func(*args, **kwargs)

            # Post-execution success audit
            self.record_audit(
                event_type="operation_complete",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="success",
                tenant_id=context.tenant_id,
                details={"output_type": type(result).__name__},
            )

            return result
        except Exception as e:
            # Post-execution failure audit
            self.record_audit(
                event_type="operation_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"error_type": type(e).__name__, "error": str(e)},
            )
            raise

    async def execute_guarded_async(
        self,
        context: PipelineContext,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Async variant of execute_guarded."""
        # Set context
        self.set_context(context)

        # Gate 1: Capability check (fail-closed)
        if not self.check_capability(
            context.actor, context.capability, context.tenant_id
        ):
            # Audit the denial
            self.record_audit(
                event_type="capability_denied",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "capability_denied"},
            )
            raise CapabilityGateError(
                f"Actor {context.actor} lacks capability {context.capability}"
            )

        # Gate 2: Record audit entry (pre-execution)
        self.record_audit(
            event_type="operation",
            actor=context.actor,
            action=context.action,
            resource=context.resource,
            result="pending",
            tenant_id=context.tenant_id,
            details=context.details,
        )

        # Execute async function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Post-execution success audit
            self.record_audit(
                event_type="operation_complete",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="success",
                tenant_id=context.tenant_id,
                details={"output_type": type(result).__name__},
            )

            return result
        except Exception as e:
            # Post-execution failure audit
            self.record_audit(
                event_type="operation_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"error_type": type(e).__name__, "error": str(e)},
            )
            raise

    @staticmethod
    def _get_timestamp() -> str:
        """Get ISO 8601 timestamp."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

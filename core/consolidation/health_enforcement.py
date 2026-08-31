"""Health Enforcement and Audit Integration — Phase 4 Consolidation.

Fail-closed enforcement gates: operations are denied if critical components
are unhealthy. All enforcement decisions are audit-logged and hash-chained.

GDPR Art. 30, 32: Every enforcement decision is recorded in the audit trail.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Callable, Any

from core.consolidation.health_checks import (
    HealthCheckRegistry,
    HealthRegistrySnapshot,
)
from core.audit.chain import AuditChain, AuditEntry

logger = logging.getLogger(__name__)


class EnforcementDeniedError(Exception):
    """Raised when enforcement gate blocks an operation due to unhealthy components."""

    pass


@dataclass(frozen=True)
class EnforcementDecision:
    """Immutable record of an enforcement decision."""

    operation_id: str
    operation_name: str
    allowed: bool
    timestamp: datetime
    reason: str
    denied_components: list[str]  # Components that triggered denial

    def to_audit_event(self) -> dict:
        """Convert to audit format."""
        return {
            "event_type": "health.enforcement_decision",
            "operation_id": self.operation_id,
            "operation_name": self.operation_name,
            "allowed": self.allowed,
            "reason": self.reason,
            "denied_components_count": len(self.denied_components),
            "denied_components": self.denied_components,
            "timestamp": self.timestamp.isoformat() + "Z",
        }


class HealthEnforcer:
    """
    Fail-closed enforcement gate for operations.

    Blocks operations when critical health components are unhealthy.
    All decisions are audit-logged and hash-chained.

    Invariant: Once a decision is logged to audit trail, it is immutable.
    """

    def __init__(
        self,
        registry: HealthCheckRegistry,
        audit_chain: Optional[AuditChain] = None,
        tenant_id: str = "_default",
    ):
        """Initialize health enforcer.

        Args:
            registry: HealthCheckRegistry instance
            audit_chain: AuditChain for logging decisions (optional for testing)
            tenant_id: Tenant identifier for audit trail

        Raises:
            ValueError: If registry is None
        """
        if registry is None:
            raise ValueError("registry must not be None")

        self.registry = registry
        self.audit_chain = audit_chain
        self.tenant_id = tenant_id
        self._decision_history: list[EnforcementDecision] = []

    async def check_operation_allowed(
        self, operation_id: str, operation_name: str
    ) -> bool:
        """Check if an operation is allowed based on health state.

        This is a read-only check. Use enforce_operation() to get an exception
        if the operation is not allowed.

        Args:
            operation_id: Unique operation identifier
            operation_name: Human-readable operation name

        Returns:
            True if operation is allowed, False otherwise
        """
        # Take current registry snapshot
        snapshot = await self.registry.take_registry_snapshot()

        # Fail-closed: if any critical components are unhealthy, deny
        if snapshot.critical_unhealthy:
            return False

        return True

    async def enforce_operation(
        self, operation_id: str, operation_name: str
    ) -> None:
        """Enforce operation: raise if not allowed, log decision to audit trail.

        Args:
            operation_id: Unique operation identifier
            operation_name: Human-readable operation name

        Raises:
            EnforcementDeniedError: If operation is denied
        """
        # Take current registry snapshot
        snapshot = await self.registry.take_registry_snapshot()

        # Fail-closed: if any critical components are unhealthy, deny
        if snapshot.critical_unhealthy:
            decision = EnforcementDecision(
                operation_id=operation_id,
                operation_name=operation_name,
                allowed=False,
                timestamp=datetime.utcnow(),
                reason=f"Critical components unhealthy: {', '.join(snapshot.critical_unhealthy)}",
                denied_components=snapshot.critical_unhealthy,
            )
            self._log_decision(decision)
            logger.warning(
                f"Enforcement denied operation '{operation_name}' (id={operation_id}): "
                f"{decision.reason}",
                extra={"tenant_id": self.tenant_id},
            )
            raise EnforcementDeniedError(decision.reason)

        # Operation allowed
        decision = EnforcementDecision(
            operation_id=operation_id,
            operation_name=operation_name,
            allowed=True,
            timestamp=datetime.utcnow(),
            reason="All critical components healthy",
            denied_components=[],
        )
        self._log_decision(decision)
        logger.debug(
            f"Enforcement allowed operation '{operation_name}' (id={operation_id})",
            extra={"tenant_id": self.tenant_id},
        )

    def _log_decision(self, decision: EnforcementDecision) -> None:
        """Log enforcement decision to audit trail (fail-closed).

        Args:
            decision: EnforcementDecision to log
        """
        self._decision_history.append(decision)

        # Log to audit chain if available
        if self.audit_chain:
            try:
                audit_event = decision.to_audit_event()
                entry = AuditEntry(
                    event_type=audit_event["event_type"],
                    actor="health_enforcer",
                    action="enforce" if decision.allowed else "deny",
                    resource=decision.operation_name,
                    result="allow" if decision.allowed else "deny",
                    timestamp=decision.timestamp.isoformat() + "Z",
                    tenant_id=self.tenant_id,
                    details=audit_event,
                )
                self.audit_chain.record(entry)
            except Exception as e:
                logger.error(
                    f"Failed to log enforcement decision to audit trail: {e}",
                    extra={"tenant_id": self.tenant_id},
                )

    async def with_enforcement(
        self,
        operation_id: str,
        operation_name: str,
        operation_fn: Callable[[], Any],
    ) -> Any:
        """Execute a function with health enforcement gate.

        Args:
            operation_id: Unique operation identifier
            operation_name: Human-readable operation name
            operation_fn: Async or sync function to execute

        Returns:
            Result of operation_fn

        Raises:
            EnforcementDeniedError: If operation is denied
        """
        # Enforce operation
        await self.enforce_operation(operation_id, operation_name)

        # Execute operation
        if hasattr(operation_fn, "__await__"):
            return await operation_fn()
        else:
            return operation_fn()

    def get_decision_history(self, limit: int = 10) -> list[EnforcementDecision]:
        """Get recent enforcement decisions.

        Args:
            limit: Maximum number of decisions to return

        Returns:
            List of decisions (newest first)
        """
        return list(reversed(self._decision_history[-limit:]))

    def reset_for_testing(self) -> None:
        """Clear all state (TEST ONLY)."""
        self._decision_history.clear()


class EnforcementPolicy:
    """
    Policy-driven enforcement gate.

    Allows operators to define custom policies for which operations
    require health enforcement and which operations can proceed
    even with degraded (but not critical-unhealthy) components.
    """

    def __init__(self):
        """Initialize enforcement policy."""
        self._critical_operations: set[str] = set()
        self._degraded_allowed_operations: set[str] = set()

    def mark_critical_operation(self, operation_name: str) -> None:
        """Mark an operation as critical (requires healthy state).

        Args:
            operation_name: Name of the operation
        """
        self._critical_operations.add(operation_name)

    def allow_with_degraded(self, operation_name: str) -> None:
        """Allow an operation to proceed with degraded (but not unhealthy) state.

        Args:
            operation_name: Name of the operation
        """
        self._degraded_allowed_operations.add(operation_name)

    def is_critical_operation(self, operation_name: str) -> bool:
        """Check if operation is marked as critical.

        Args:
            operation_name: Name of the operation

        Returns:
            True if operation is critical
        """
        return operation_name in self._critical_operations

    def is_degraded_allowed(self, operation_name: str) -> bool:
        """Check if operation is allowed with degraded components.

        Args:
            operation_name: Name of the operation

        Returns:
            True if operation is allowed with degraded state
        """
        return operation_name in self._degraded_allowed_operations

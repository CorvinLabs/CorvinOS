"""Security role protocols (interfaces)."""

from typing import Protocol, runtime_checkable
from .context import SecurityContext, GateResult


@runtime_checkable
class CapabilityChecker(Protocol):
    """Role 1: Verify actor is authorized for capability."""

    async def check(self, context: SecurityContext) -> GateResult:
        """Check if actor has required capability."""
        ...


@runtime_checkable
class InputValidator(Protocol):
    """Role 2a: Verify input conforms to schema."""

    async def validate(self, context: SecurityContext) -> GateResult:
        """Validate input_data against schema."""
        ...


@runtime_checkable
class PIIDetector(Protocol):
    """Role 2b: Detect PII/secrets in input + context."""

    async def detect(self, context: SecurityContext) -> GateResult:
        """Scan input_data + context_brief for PII."""
        ...


@runtime_checkable
class ContextEngineer(Protocol):
    """Role 3: Build context via CEL."""

    async def engineer(self, context: SecurityContext) -> GateResult:
        """Call CEL to build context_brief."""
        ...


@runtime_checkable
class AuditRecorder(Protocol):
    """Role 4: Record immutable audit trail."""

    async def record(self, context: SecurityContext) -> GateResult:
        """Write decision to immutable hash-chained audit trail."""
        ...

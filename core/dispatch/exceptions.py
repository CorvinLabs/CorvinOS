"""Unified Exception Hierarchy for All Dispatchers

Consolidates error types across dispatchers (RunDispatcher, SkillDispatcher,
TierDispatcher, AlertDispatcher, etc.) with standardized properties:
- tenant_id: For tenant isolation (ADR-0007)
- lom: Line-of-moral-responsibility (code location that raised it)
- audit_event_type: Type of audit event to emit

All exceptions carry traceability info for audit trails (GDPR Art. 30).
"""

from dataclasses import dataclass
from typing import Optional


@dataclass
class DispatcherException(Exception):
    """Base class for all dispatcher exceptions.

    Every dispatcher exception carries:
    - message: Human-readable error description
    - tenant_id: Tenant isolation (ADR-0007)
    - lom: Line-of-moral-responsibility (code location)
    - context: Optional dict for structured data

    Used by: RunDispatcher, SkillDispatcher, TierDispatcher, AlertDispatcher
    """
    message: str
    tenant_id: Optional[str] = None
    lom: Optional[str] = None  # "dispatcher.py:L123"
    context: Optional[dict] = None

    def __str__(self):
        return self.message

    def __repr__(self):
        return f"{self.__class__.__name__}({self.message}, tenant={self.tenant_id}, lom={self.lom})"


class DispatcherTimeout(DispatcherException):
    """Timeout during dispatch.

    Raised when:
    - RunDispatcher: Engine spawn exceeds wall-clock budget
    - TierDispatcher: Render tier exceeds timeout threshold
    - SkillDispatcher: Skill execution exceeds timeout
    - Any dispatcher: Async operation doesn't complete in time

    Audit: Emit 'dispatcher_timeout' event with latency_ms
    """
    pass


class DispatcherConfigError(DispatcherException):
    """Invalid dispatcher configuration.

    Raised when:
    - RunDispatcher: Invalid engine config or model not found
    - TierDispatcher: Tier configuration missing or malformed
    - AlertDispatcher: Alert signature config invalid
    - Any dispatcher: Config validation fails

    Audit: Emit 'dispatcher_config_error' event (fail-closed)
    """
    pass


class DispatcherFailed(DispatcherException):
    """Dispatch failed with non-recoverable error.

    Raised when:
    - All tiers in TierDispatcher fail (final failure)
    - SkillDispatcher: No fallback available
    - RunDispatcher: Engine crash (not timeout)
    - AlertDispatcher: Alert processing failed

    Audit: Emit 'dispatcher_failed' event with error type
    """
    pass


class DispatcherAuthError(DispatcherException):
    """Authentication/Authorization error during dispatch.

    Raised when:
    - RunDispatcher: Tenant auth check fails (L16)
    - AlertDispatcher: Signature verification fails (security)
    - Any dispatcher: Permission check fails

    Audit: Emit 'dispatcher_auth_error' event (security event)
    """
    pass


class DispatcherNotFound(DispatcherException):
    """Requested dispatcher or endpoint not found.

    Raised when:
    - FlowDispatcher: No node satisfies requirements
    - SkillDispatcher: Skill not registered
    - CommandDispatcher: Command not found

    Audit: Emit 'dispatcher_not_found' event (not an error, just missing)
    """
    pass


# Mapping of exception types to audit event names
EXCEPTION_AUDIT_MAP = {
    "DispatcherTimeout": "dispatcher_timeout",
    "DispatcherConfigError": "dispatcher_config_error",
    "DispatcherFailed": "dispatcher_failed",
    "DispatcherAuthError": "dispatcher_auth_error",
    "DispatcherNotFound": "dispatcher_not_found",
    "DispatcherException": "dispatcher_error",
}

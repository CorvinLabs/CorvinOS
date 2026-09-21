"""Unified Dispatcher Framework

Consolidation of all dispatcher implementations in CorvinOS with shared patterns:

- Dispatcher Base Interface (protocol)
- Unified Exception Hierarchy
- Standardized Audit Event Schema
- Common Error Handling & Retry Logic
- Tenant Isolation (ADR-0007)

Architecture:
  - Each dispatcher (RunDispatcher, SkillDispatcher, TierDispatcher, etc.)
    serves a distinct domain but shares audit/error/timeout patterns
  - Audit events are tenant-scoped and hash-chained (GDPR Art. 30, 32)
  - Exceptions carry lom (line-of-moral-responsibility) for traceability
  - Results standardized to {success, status, output, error, latency_ms}

See docs/claude-ref/renderer-dispatcher-architecture.md for full details.
"""

from .exceptions import (
    DispatcherException,
    DispatcherTimeout,
    DispatcherConfigError,
    DispatcherFailed,
    DispatcherAuthError,
)
from .audit_event import (
    DispatcherAuditEvent,
    AuditEventType,
)

__all__ = [
    "DispatcherException",
    "DispatcherTimeout",
    "DispatcherConfigError",
    "DispatcherFailed",
    "DispatcherAuthError",
    "DispatcherAuditEvent",
    "AuditEventType",
]

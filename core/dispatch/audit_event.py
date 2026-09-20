"""Unified Audit Event Schema for All Dispatchers

Consolidates audit events across dispatchers with consistent structure:
- tenant_id: Tenant isolation (ADR-0007, GDPR Art. 5)
- event_type: Type of dispatch event (skill_executed, render_completed, etc.)
- dispatcher_id: Which dispatcher emitted this (tier_dispatcher, skill_dispatcher)
- latency_ms: How long the dispatch took
- status: Outcome (success, failed, timeout)
- lom: Line-of-moral-responsibility (code location, cryptographically bound)
- timestamp: ISO 8601, immutable
- input_hash: SHA256 of request payload (for integrity)
- output_hash: SHA256 of result (for integrity)

Every event is:
1. Immutable (frozen dataclass)
2. Tenant-scoped (isolation)
3. Hash-chained (audit trail integrity, ADR-0232)
4. Traceable (lom binding)

Used by: RunDispatcher, SkillDispatcher, TierDispatcher, AlertDispatcher, etc.

Compliance: GDPR Art. 5 (minimization), 30 (processing record), 32 (security)
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional


class AuditEventType(str, Enum):
    """Standard audit event types emitted by dispatchers."""

    # Skill Dispatcher
    SKILL_EXECUTED = "skill_executed"
    SKILL_FAILED = "skill_failed"
    SKILL_TIMEOUT = "skill_timeout"

    # Render/Tier Dispatcher
    RENDER_STARTED = "render_started"
    RENDER_COMPLETED = "render_completed"
    RENDER_FAILED = "render_failed"
    RENDER_TIMEOUT = "render_timeout"
    TIER_FALLBACK = "tier_fallback"

    # Engine/Run Dispatcher
    ENGINE_SPAWNED = "engine_spawned"
    ENGINE_COMPLETED = "engine_completed"
    ENGINE_FAILED = "engine_failed"
    ENGINE_TIMEOUT = "engine_timeout"

    # Alert Dispatcher
    ALERT_PROCESSED = "alert_processed"
    ALERT_REJECTED = "alert_rejected"
    ALERT_RATE_LIMITED = "alert_rate_limited"
    ALERT_SIGNATURE_INVALID = "alert_signature_invalid"

    # Generic Dispatcher Events
    DISPATCH_ROUTED = "dispatch_routed"
    DISPATCH_TIMEOUT = "dispatch_timeout"
    DISPATCH_CONFIG_ERROR = "dispatch_config_error"
    DISPATCH_AUTH_ERROR = "dispatch_auth_error"
    DISPATCH_NOT_FOUND = "dispatch_not_found"

    # Console Dispatcher Events (M1 ADR-0954)
    CONSOLE_REQUEST_RECEIVED = "console_request_received"
    CONSOLE_DISPATCH_ROUTED = "console_dispatch_routed"
    CONSOLE_TOKEN_VALIDATED = "console_token_validated"
    CONSOLE_TOKEN_INVALID = "console_token_invalid"


@dataclass(frozen=True)
class DispatcherAuditEvent:
    """Immutable audit event emitted by all dispatchers.

    This event is:
    1. Frozen (immutable) — cannot be modified after creation
    2. Hash-chainable — carries prev_hash for chain verification (ADR-0232)
    3. Tenant-scoped — tenant_id ensures no cross-tenant leakage (ADR-0007)
    4. Traceable — lom field cryptographically binds to code location

    Stored in: ~/.corvin/tenants/{tenant_id}/global/forge/audit.jsonl
    """

    # Event identity
    event_id: str = field(default_factory=lambda: __import__('uuid').uuid4().hex[:12])
    event_type: AuditEventType = AuditEventType.DISPATCH_ROUTED
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Dispatcher identity
    dispatcher_id: str = ""  # "skill_dispatcher", "tier_dispatcher", "run_dispatcher", etc.
    dispatcher_version: str = ""  # "1.0.0" (semantic versioning)

    # Tenant isolation (GDPR Art. 5, 6, 32 — fail-closed if missing)
    tenant_id: str = ""  # MANDATORY — audit drops records with missing tenant_id

    # Request/response hashing (integrity)
    input_hash: Optional[str] = None  # SHA256 of request payload
    output_hash: Optional[str] = None  # SHA256 of result

    # Execution metrics
    latency_ms: int = 0  # How long the dispatch took (milliseconds)
    status: str = "unknown"  # "success", "failed", "timeout", "denied", "not_found"
    error_type: Optional[str] = None  # Exception class name if failed

    # Traceability (line-of-moral-responsibility)
    lom: Optional[str] = None  # "dispatcher.py:L123" or "skill_dispatcher.py::dispatch:L89"
    lom_hash: Optional[str] = None  # SHA256(lom + source_code_context) for anti-spoofing

    # Chain integrity (ADR-0232 audit tripwire)
    prev_hash: Optional[str] = None  # SHA256 of previous event (forms the chain)
    event_hash: Optional[str] = None  # SHA256 of this event (for next event's prev_hash)

    # Contextual data (no PII)
    context_data: Optional[dict] = None  # {tier: "tier_2", quality_score: 0.85, etc.}

    def __repr__(self):
        return f"DispatcherAuditEvent({self.event_type.value}, status={self.status}, latency_ms={self.latency_ms})"

    def is_valid_tenant_scope(self) -> bool:
        """Verify tenant isolation. Fail-closed: no tenant_id = invalid."""
        return bool(self.tenant_id)

    def is_chained(self) -> bool:
        """Verify this event is part of a hash chain."""
        return bool(self.prev_hash and self.event_hash)

    def is_complete(self) -> bool:
        """Verify all required fields are present."""
        required = [self.event_id, self.dispatcher_id, self.tenant_id, self.timestamp]
        return all(required) and self.event_type is not None

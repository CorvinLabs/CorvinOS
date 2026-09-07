"""CompletionEvent schema — immutable, audit-safe, ADR-0655 compliant.

Every event is frozen after creation, hash-chained to audit trail, and tagged
with tenant_id for isolation (GDPR Art. 5, 6).
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class CompletionStatus(str, Enum):
    """Terminal states for background tasks."""
    COMPLETE = "COMPLETE"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    PARTIAL = "PARTIAL"  # Multi-phase; phase complete but task continues


class CompletionTaskType(str, Enum):
    """Type of background task."""
    AGENT = "AGENT"
    WORKFLOW = "WORKFLOW"
    LOOP = "LOOP"
    SKILL = "SKILL"


@dataclass(frozen=True)
class CompletionEvent:
    """Immutable event emitted when a background task reaches terminal state.

    Frozen after creation; all fields immutable. Hash-chained to audit trail.
    Tenant-scoped (no cross-tenant events).
    """

    # Core fields
    task_id: str                              # UUID of task
    task_type: CompletionTaskType            # AGENT | WORKFLOW | LOOP | SKILL
    status: CompletionStatus                  # COMPLETE | FAILED | CANCELLED | PARTIAL
    duration_sec: float                       # Time from spawn to completion

    # Optional context
    phase_reached: Optional[int] = None       # For multi-phase tasks (workflow phase 5/5)
    output_summary: Optional[str] = None      # One-line result (scrubbed of PII)
    metadata: dict[str, Any] = field(default_factory=dict)  # Tool-specific (scrubbed)

    # Audit context
    tenant_id: str = "_default"               # Tenant isolation (GDPR Art. 5)
    origin: dict[str, Any] = field(default_factory=dict)  # {channel, timestamp}
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Hash chain (audit trail linking)
    prev_hash: str = ""                       # Link to prior event in chain
    hash: str = ""                            # SHA256 of this event (computed at emit)

    def validate(self) -> None:
        """Fail-fast validation before audit emission."""
        if not self.task_id:
            raise ValueError("task_id is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required (no multi-tenant events)")
        if not self.timestamp:
            raise ValueError("timestamp is required")
        if self.duration_sec < 0:
            raise ValueError("duration_sec must be non-negative")

    @staticmethod
    def compute_hash(payload: dict) -> str:
        """Compute SHA256 hash of event payload (deterministic, no randomness).

        Used for:
        1. Hash chaining (prev_hash → this event's hash)
        2. Dedup detection (same event hash = already emitted)
        """
        canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(canonical.encode()).hexdigest()

    def to_dict(self) -> dict[str, Any]:
        """Convert to dict for JSON serialization."""
        return {
            **asdict(self),
            "task_type": self.task_type.value,
            "status": self.status.value,
        }

    @classmethod
    def from_dict(cls, data: dict) -> CompletionEvent:
        """Reconstruct from dict."""
        return cls(
            task_id=data["task_id"],
            task_type=CompletionTaskType(data["task_type"]),
            status=CompletionStatus(data["status"]),
            duration_sec=data["duration_sec"],
            phase_reached=data.get("phase_reached"),
            output_summary=data.get("output_summary"),
            metadata=data.get("metadata", {}),
            tenant_id=data.get("tenant_id", "_default"),
            origin=data.get("origin", {}),
            timestamp=data.get("timestamp", datetime.utcnow().isoformat() + "Z"),
            prev_hash=data.get("prev_hash", ""),
            hash=data.get("hash", ""),
        )


@dataclass(frozen=True)
class NotificationSentEvent:
    """Audit event: notification was sent (or failed)."""
    completion_event_id: str       # Hash of CompletionEvent
    channel: str                   # discord | console | email | sms
    envelope_id: str               # Unique ID of notification envelope
    status: str                    # sent_to_outbox | error | ...
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


@dataclass(frozen=True)
class PIIScrubEvent:
    """Audit event: PII was detected and redacted."""
    task_id: str
    pattern_detected: str          # password | email | api_key | ssn | ...
    redacted_length: int           # Length of redacted text
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


@dataclass(frozen=True)
class ConsentCheckEvent:
    """Audit event: consent check performed (GDPR Art. 6, 7)."""
    tenant_id: str
    consent_type: str              # voice_notify | email_notify | ...
    granted: bool
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

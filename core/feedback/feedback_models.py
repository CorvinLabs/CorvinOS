"""Data models for Phase 9 User Feedback Loop (ADR-2028, ADR-2029).

Immutable feedback types: Bug reports, Feature requests, NPS surveys.
All models are tenant-scoped (GDPR Art. 32) and audit-first.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional
from datetime import datetime, timezone
from uuid import uuid4
import hashlib
import json


class FeedbackType(str, Enum):
    """Feedback category (immutable)."""
    BUG_REPORT = "bug_report"
    FEATURE_REQUEST = "feature_request"
    NPS_SURVEY = "nps_survey"
    GENERAL = "general"


class FeedbackSeverity(str, Enum):
    """Bug severity level for triage."""
    CRITICAL = "critical"  # System down, data loss risk
    HIGH = "high"          # Major feature broken
    MEDIUM = "medium"      # Partial feature loss
    LOW = "low"            # Minor annoyance


class FeedbackPriority(str, Enum):
    """Triage priority (assigned by TriageEngine)."""
    P0 = "p0"  # Critical, fix within 1h
    P1 = "p1"  # High, fix within 24h
    P2 = "p2"  # Medium, fix within 1 week
    P3 = "p3"  # Low, backlog


@dataclass(frozen=True)
class FeedbackReport:
    """Immutable feedback report (audit-first, tenant-scoped).

    All reports are:
    - Immutable (frozen dataclass)
    - Tenant-scoped (tenant_id required)
    - Audit-logged (audit_event_id included)
    - Hash-verified (signature field)
    """
    # Metadata
    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = field(default="")
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))

    # Content
    feedback_type: FeedbackType = FeedbackType.GENERAL
    title: str = ""
    description: str = ""
    severity: Optional[FeedbackSeverity] = None  # For bug reports
    nps_score: Optional[int] = None  # For NPS surveys (0-10)

    # Metadata
    user_id: Optional[str] = None
    user_email: str = ""
    component: str = ""  # e.g., "console", "voice", "video_producer"
    environment: str = ""  # e.g., "production", "staging", "local"
    version: str = ""  # CorvinOS version

    # Context
    session_id: Optional[str] = None
    related_task_id: Optional[str] = None
    reproduction_steps: Optional[str] = None  # For bugs
    attachments: list = field(default_factory=list)  # File names only (no content)

    # System fields
    audit_event_id: Optional[str] = None  # Links to audit.jsonl
    source: str = "portal"  # "portal" | "api" | "in-app"
    signature: str = field(default="")  # Hash for integrity verification

    def __post_init__(self):
        """Compute signature after initialization (frozen dataclass trick)."""
        # Compute signature as hash of immutable fields
        sig_data = json.dumps({
            "feedback_id": self.feedback_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "feedback_type": self.feedback_type.value,
            "title": self.title,
            "description": self.description,
        }, sort_keys=True)
        sig = hashlib.sha256(sig_data.encode()).hexdigest()
        # Use object.__setattr__ for frozen dataclass
        object.__setattr__(self, "signature", sig)

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "feedback_id": self.feedback_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "feedback_type": self.feedback_type.value if self.feedback_type else None,
            "title": self.title,
            "description": self.description,
            "severity": self.severity.value if self.severity else None,
            "nps_score": self.nps_score,
            "user_id": self.user_id,
            "user_email": self.user_email,
            "component": self.component,
            "environment": self.environment,
            "version": self.version,
            "session_id": self.session_id,
            "related_task_id": self.related_task_id,
            "reproduction_steps": self.reproduction_steps,
            "attachments": self.attachments,
            "audit_event_id": self.audit_event_id,
            "source": self.source,
            "signature": self.signature,
        }


@dataclass(frozen=True)
class TriagedFeedback:
    """Triaged feedback report with priority assignment."""
    feedback_report: FeedbackReport
    priority: FeedbackPriority
    triage_reason: str  # Why assigned this priority
    estimated_effort: str  # "quick_fix" | "1-2h" | "half_day" | "multi_day"
    assigned_to: Optional[str] = None  # Team member or None if unassigned
    triage_timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace('+00:00', 'Z'))

    def to_dict(self) -> dict:
        """Convert to dictionary for serialization."""
        return {
            "feedback_report": self.feedback_report.to_dict(),
            "priority": self.priority.value if self.priority else None,
            "triage_reason": self.triage_reason,
            "estimated_effort": self.estimated_effort,
            "assigned_to": self.assigned_to,
            "triage_timestamp": self.triage_timestamp,
        }


__all__ = [
    "FeedbackType",
    "FeedbackSeverity",
    "FeedbackPriority",
    "FeedbackReport",
    "TriagedFeedback",
]

"""Data models for Quality Gates System (ADR-0688).

Frozen dataclasses representing:
- GateResult: Verdict of a gate validator
- AuditEvent: Immutable audit trail event
- KG nodes and edges: Knowledge graph entities
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum


class VerdictType(str, Enum):
    """Gate verdict types."""
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"


class EventType(str, Enum):
    """Audit event types."""
    QUALITY_GATE_DECIDED = "quality_gate_decided"


class KGNodeType(str, Enum):
    """Knowledge graph node types."""
    ADR = "ADR"
    CONCEPT = "Concept"
    IDEA = "Idea"
    IMPLEMENTATION_PLAN = "ImplementationPlan"
    COMMIT = "Commit"
    TASK = "Task"


@dataclass(frozen=True, slots=True)
class GateResult:
    """Result of a gate validation."""
    gate_name: str
    artifact_id: str
    verdict: VerdictType
    confidence: float  # [0.0, 1.0]
    reason: str
    tenant_id: str
    timestamp: Optional[str] = None
    findings: List[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate confidence is in [0, 1]."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be in [0, 1], got {self.confidence}")
        if not self.tenant_id:
            raise ValueError("tenant_id is required and must not be empty")


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """Immutable audit trail event for gate decisions."""
    event_type: EventType
    tenant_id: str
    timestamp: str
    gate_name: str
    artifact_id: str
    verdict: VerdictType
    confidence: float
    reason: Optional[str]
    event_hash: str
    prior_hash: Optional[str] = None
    findings_count: int = 0

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.tenant_id:
            raise ValueError("tenant_id is required")
        if not self.event_hash:
            raise ValueError("event_hash is required")


@dataclass(frozen=True, slots=True)
class KGNode:
    """Knowledge graph node."""
    id: str
    node_type: KGNodeType
    tenant_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.id:
            raise ValueError("id is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")


@dataclass(frozen=True, slots=True)
class KGEdge:
    """Knowledge graph edge."""
    source_id: str
    target_id: str
    relationship_type: str
    tenant_id: str
    data: Dict[str, Any] = field(default_factory=dict)
    created_at: Optional[str] = None

    def __post_init__(self) -> None:
        """Validate required fields."""
        if not self.source_id:
            raise ValueError("source_id is required")
        if not self.target_id:
            raise ValueError("target_id is required")
        if not self.relationship_type:
            raise ValueError("relationship_type is required")
        if not self.tenant_id:
            raise ValueError("tenant_id is required")

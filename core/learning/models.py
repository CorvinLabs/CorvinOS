"""TreeOfThoughts data models (Phase 1)."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Literal


class CompositionType(Enum):
    """How children compose into parent."""
    AND = "and"  # min(children) — all must work
    OR = "or"  # max(children) — any works
    AVG = "avg"  # weighted_avg(children)


@dataclass(frozen=True)
class ConfidenceEvent:
    """Immutable record of a confidence change."""
    timestamp: str  # ISO8601
    old_confidence: float
    new_confidence: float
    delta: float  # new - old
    event_type: str  # "used" | "failed" | "graded" | "refuted" | "decay" | "antipattern_detected"
    reason: str
    context: dict = field(default_factory=dict)  # {task_id, user_id, metrics, etc}


@dataclass(frozen=True)
class LearningEvent:
    """Immutable event: pattern/method used, failed, graded, etc."""
    subject_id: str  # pattern_id or method_id
    event_type: Literal["used", "failed", "graded", "refuted", "antipattern_detected", "decay"]
    confidence_delta: float  # -1.0 to +1.0
    reason: str
    timestamp: str = field(default_factory=lambda: datetime.now().isoformat())
    context: dict = field(default_factory=dict)


@dataclass
class TreeNode:
    """3-level learning node: Pattern, Method, or Framework."""
    
    # Identity
    id: str  # "pattern_retry_backoff" or "method_voice_synthesis"
    level: Literal["pattern", "method", "framework"]
    name: str
    
    # Learning
    confidence: float = 0.5  # [0.0, 1.0], learned from events
    confidence_history: list[ConfidenceEvent] = field(default_factory=list)
    
    # Semantics
    body: str = ""  # code, prose, or reference
    when: list[str] = field(default_factory=list)  # use cases
    anti_when: list[str] = field(default_factory=list)  # don't use when...
    
    # Composition (for Method/Framework)
    children: list[str] = field(default_factory=list)  # IDs of Patterns/Methods
    composition_type: CompositionType = CompositionType.AVG
    
    # Proof
    e2e_tests: list[str] = field(default_factory=list)  # test file paths
    metrics: dict = field(default_factory=dict)  # {latency_ms, success_rate, calls_in_production}
    calls_in_production: int = 0
    
    # Documentation
    operator_notes: list[tuple[str, str, str]] = field(default_factory=list)  # (date, author, text)
    adr_link: str | None = None  # "ADR-0351"
    
    # Audit
    created_at: str = field(default_factory=lambda: datetime.now().isoformat())
    modified_at: str = field(default_factory=lambda: datetime.now().isoformat())
    modified_by: list[str] = field(default_factory=list)
    
    def add_operator_note(self, author: str, text: str) -> None:
        """Append immutable operator note."""
        self.operator_notes.append((datetime.now().isoformat(), author, text))
    
    def add_confidence_event(self, event: ConfidenceEvent) -> None:
        """Record confidence change."""
        self.confidence_history.append(event)
        self.confidence = event.new_confidence
        self.modified_at = datetime.now().isoformat()

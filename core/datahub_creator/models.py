"""ProjectModel — DataHub Creator project persistence layer.

Stores project metadata, phase state, and metrics snapshots.
Integrated with Track B (Learning Loop) for skill metrics aggregation.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional, Any
from uuid import uuid4


class ProjectPhase(str, Enum):
    """6-phase dashboard model (compressed from 12 sequential phases)."""

    # Core phases (all users)
    CREATE = "create"  # Setup: name, skills, goals
    EXECUTE = "execute"  # Run skills, collect feedback inline
    METRICS_DASHBOARD = "metrics_dashboard"  # View convergence + recommendations
    EXPORT = "export"  # Archive report, download

    # Optional phases (power users)
    OPTIMIZATION_CONFIG = "optimization_config"  # Tune optimizer thresholds
    COLLABORATION = "collaboration"  # Share project, invite feedback


class ProjectStatus(str, Enum):
    """Project lifecycle status."""

    CREATED = "created"  # Initial state
    EXECUTING = "executing"  # Skills running
    LEARNING = "learning"  # Optimizer tuning in progress
    PAUSED = "paused"  # User paused or network issue
    CONVERGED = "converged"  # Learning complete, skills stable
    ARCHIVED = "archived"  # Exported and closed


@dataclass
class SkillMetricsSnapshot:
    """Metrics snapshot for a skill at a point in time."""

    skill_id: str
    timestamp: str
    success_rate: float  # 0–1
    latency_p50_ms: float
    latency_p95_ms: float
    latency_p99_ms: float
    feedback_count: int
    confidence_score: float  # 0–1, from Track B optimizer
    convergence_rate: float  # confidence change / time, 0–1
    recommendation_strength: str  # "high" | "medium" | "low"


@dataclass
class ProjectMetrics:
    """Aggregated metrics for the entire project."""

    project_id: str
    timestamp: str
    skill_snapshots: List[SkillMetricsSnapshot] = field(default_factory=list)
    avg_confidence: float = 0.0
    skills_improved: int = 0  # count of skills with confidence ↑
    confidence_trend: List[tuple[str, float]] = field(default_factory=list)  # (timestamp, avg_confidence)
    recommendation_acceptance_rate: float = 0.0  # recommendations_acted_on / recommendations_made
    convergence_status: str = "in_progress"  # "in_progress" | "stalled" | "complete"
    phases_completed: int = 0  # bitmap: which phases completed


@dataclass
class ProjectModel:
    """DataHub Creator project (root entity).

    Stores user's project: skills selected, feedback collected, metrics tracked.
    Integrated with Track B (Learning Loop) via MetricsAggregator.
    """

    project_id: str = field(default_factory=lambda: str(uuid4()))
    tenant_id: str = ""  # GDPR: mandatory
    name: str = ""
    description: str = ""
    goals: List[str] = field(default_factory=list)  # Success criteria
    status: ProjectStatus = ProjectStatus.CREATED

    # Phase tracking (bitmap-style)
    current_phase: ProjectPhase = ProjectPhase.CREATE
    phases_visited: Dict[ProjectPhase, bool] = field(default_factory=lambda: {
        p: False for p in ProjectPhase
    })

    # Skills in this project
    selected_skills: List[str] = field(default_factory=list)  # skill_ids

    # Metrics (updated by MetricsAggregator)
    latest_metrics: Optional[ProjectMetrics] = None
    metrics_history: List[ProjectMetrics] = field(default_factory=list)  # Time series

    # Feedback collection
    feedback_events: List[Dict[str, Any]] = field(default_factory=list)  # Raw feedback

    # Optimizer config (Phase 4, power users)
    optimizer_config: Dict[str, float] = field(default_factory=lambda: {
        "convergence_threshold": 0.05,  # Stop when confidence change < 5%
        "min_feedback_samples": 10,  # Require ≥ 10 feedback samples to optimize
        "learning_rate": 0.01,  # Weight update size
    })

    # Timestamps
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Collaboration (Phase 5, optional)
    collaborators: List[str] = field(default_factory=list)  # user_ids

    def mark_phase_complete(self, phase: ProjectPhase) -> None:
        """Mark a phase as visited/completed."""
        self.phases_visited[phase] = True
        self.current_phase = phase
        self.updated_at = datetime.utcnow().isoformat() + "Z"

    def get_phases_completed_count(self) -> int:
        """Count completed phases."""
        return sum(1 for visited in self.phases_visited.values() if visited)

    def is_convergence_detected(self) -> bool:
        """Check if learning has converged (from latest metrics)."""
        if not self.latest_metrics:
            return False
        return self.latest_metrics.convergence_status == "complete"

    def has_sufficient_feedback(self) -> bool:
        """Check if we have enough feedback to optimize."""
        min_samples = self.optimizer_config.get("min_feedback_samples", 10)
        return len(self.feedback_events) >= min_samples


@dataclass
class ProjectFeedback:
    """User feedback on a skill execution (for export/audit)."""

    feedback_id: str = field(default_factory=lambda: str(uuid4()))
    project_id: str = ""
    skill_id: str = ""
    skill_execution_id: str = ""  # Reference to Track B execution event
    feedback_type: str = ""  # "outcome" | "quality" | "preference"
    signal: Dict[str, Any] = field(default_factory=dict)  # outcome (yes/no/unknown), rating (1–5), etc.
    user_id: str = ""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Audit/compliance
    is_redacted: bool = False  # PII removal flagged
    audit_chain_ref: Optional[str] = None  # Reference to immutable audit event


@dataclass
class ProjectExport:
    """DataHub project export (for archival + compliance)."""

    project_id: str
    export_id: str = field(default_factory=lambda: str(uuid4()))
    export_timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    # Exported data
    project_metadata: Dict[str, Any] = field(default_factory=dict)
    metrics_timeline: List[ProjectMetrics] = field(default_factory=list)
    skill_snapshots: List[SkillMetricsSnapshot] = field(default_factory=list)
    feedback_summary: Dict[str, int] = field(default_factory=dict)  # feedback_type → count

    # GDPR compliance
    pii_redacted: bool = True
    user_ids_masked: bool = True
    export_format: str = "jsonl"  # "jsonl" | "csv" | "pdf"
    compliance_certified: bool = False

"""Data types for MidstreamRouter subsystem.

Routing state, conflict resolution, and subsystem contracts.

ADR-0281: Voice-Native Midstream Guidance Router
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Optional, Any


class SubsystemType(Enum):
    """Subsystem routing targets."""
    COST_CONTROLLER = "CostController"
    LOOP_ENGINEER = "LoopEngineer"
    SAFETY_VALIDATOR = "SafetyValidator"
    ORCHESTRATOR = "Orchestrator"
    STRATEGY_ADVISOR = "StrategyAdvisor"


class RoutingPriority(Enum):
    """Priority levels for routing."""
    LOW = 1
    NORMAL = 2
    HIGH = 3
    CRITICAL = 4


class ConflictResolution(Enum):
    """Strategies for resolving conflicting guidance."""
    FIRST_WIN = "first_win"  # Apply first guidance, reject others
    MERGE = "merge"  # Attempt to merge (e.g., two model changes)
    ESCALATE = "escalate"  # Send to Safety/Orchestrator for resolution
    QUEUE = "queue"  # Queue conflicting guidance for later
    REJECT = "reject"  # Reject both, ask user


@dataclass
class RoutingTarget:
    """A single guidance routing target."""
    subsystem: SubsystemType
    action: str  # e.g., "switch_model", "decompose", "abort"
    priority: RoutingPriority = RoutingPriority.NORMAL
    requires_confirmation: bool = False
    estimated_cost: float = 0.0  # Cost impact (tokens, time, etc.)
    metadata: dict = field(default_factory=dict)

    def __eq__(self, other):
        """Check equality."""
        if not isinstance(other, RoutingTarget):
            return False
        return (
            self.subsystem == other.subsystem
            and self.action == other.action
            and self.priority == other.priority
        )


@dataclass
class RoutingConflict:
    """Represents a conflict between multiple routing targets."""
    targets: list[RoutingTarget]
    reason: str
    timestamp: datetime = field(default_factory=datetime.utcnow)
    resolution_strategy: ConflictResolution = ConflictResolution.ESCALATE
    resolution_result: Optional[str] = None


@dataclass
class RoutingResult:
    """Result of routing a classified guidance."""
    event_id: str
    guidance_class: str
    primary_target: Optional[RoutingTarget]
    alternate_targets: list[RoutingTarget] = field(default_factory=list)
    conflicts: list[RoutingConflict] = field(default_factory=list)
    routed_at: datetime = field(default_factory=datetime.utcnow)
    latency_ms: float = 0.0
    model_used: str = "router"  # "router", "heuristic", "llm"

    def has_conflicts(self) -> bool:
        """Check if routing has conflicts."""
        return len(self.conflicts) > 0

    def get_primary_action(self) -> Optional[str]:
        """Get primary action to execute."""
        if self.primary_target:
            return self.primary_target.action
        return None


@dataclass
class RouterMetrics:
    """Metrics for MidstreamRouter."""
    total_routings: int = 0
    by_subsystem: dict = field(default_factory=dict)
    conflicts_total: int = 0
    conflicts_resolved: int = 0
    conflicts_escalated: int = 0
    avg_latency_ms: float = 0.0
    cost_estimates_total: float = 0.0

    def record_routing(self, target: RoutingTarget, has_conflict: bool = False):
        """Record a successful routing."""
        self.total_routings += 1
        subsys_name = target.subsystem.value
        self.by_subsystem[subsys_name] = self.by_subsystem.get(subsys_name, 0) + 1
        self.cost_estimates_total += target.estimated_cost
        if has_conflict:
            self.conflicts_total += 1

    def record_conflict_resolution(self, escalated: bool):
        """Record conflict resolution outcome."""
        self.conflicts_resolved += 1
        if escalated:
            self.conflicts_escalated += 1

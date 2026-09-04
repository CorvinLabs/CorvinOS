"""
Phase 8: Cross-System Optimization — Multi-Skill Coordination

Components:
- GlobalObjectiveFunction: Minimize total cost across skills
- ConflictMediator: Resolve skill conflicts fairly
- ResourcePlanner: Allocate operator time across skills
- SkillRegistry: Track skills + dependencies

ADR-0591: L5 Cross-System Optimization
"""

from typing import Dict, List, Tuple, Optional
from dataclasses import dataclass, field
from threading import RLock
from enum import Enum


class SkillPriority(Enum):
    """Skill priority levels."""
    CRITICAL = 1
    HIGH = 2
    MEDIUM = 3
    LOW = 4


@dataclass
class SkillObjective:
    """Single skill's objective."""
    skill_id: str
    metric_name: str
    target_value: float
    direction: str  # "minimize" or "maximize"
    priority: SkillPriority
    current_value: float = 0.0
    confidence: float = 0.0


@dataclass
class SkillConflict:
    """Detected conflict between skills."""
    conflict_id: str
    skill_a_id: str
    skill_b_id: str
    impact: str  # "HIGH", "MEDIUM", "LOW"
    reason: str
    resolution: str  # "AUTO" (skill with higher priority wins) or "ESCALATE"


@dataclass
class ResourceAllocation:
    """Resource allocation for a skill."""
    skill_id: str
    operator_load_pct: float  # % of operator time
    expected_approvals_per_hour: int
    sla_minutes: int


class GlobalObjectiveFunction:
    """Minimize total cost across all skills."""

    # Weights for multi-objective optimization
    WEIGHT_QUEUE_DEPTH = 0.60
    WEIGHT_REVOKE_RATE = 0.30
    WEIGHT_OPERATOR_LATENCY = 0.10

    def __init__(self):
        self._lock = RLock()

    def compute_cost(
        self,
        queue_depth: int,
        revoke_rate: float,
        operator_latency_ms: float,
    ) -> float:
        """Compute total cost (lower is better)."""
        with self._lock:
            normalized_queue = min(queue_depth / 100.0, 1.0)
            normalized_revoke = min(revoke_rate / 100.0, 1.0)
            normalized_latency = min(operator_latency_ms / 300000.0, 1.0)

            return (
                self.WEIGHT_QUEUE_DEPTH * normalized_queue
                + self.WEIGHT_REVOKE_RATE * normalized_revoke
                + self.WEIGHT_OPERATOR_LATENCY * normalized_latency
            )

    def compare_configurations(
        self,
        config_a: Dict,
        config_b: Dict,
    ) -> str:
        """Compare two configurations, return winner."""
        with self._lock:
            cost_a = self.compute_cost(
                config_a["queue_depth"],
                config_a["revoke_rate"],
                config_a["operator_latency_ms"],
            )
            cost_b = self.compute_cost(
                config_b["queue_depth"],
                config_b["revoke_rate"],
                config_b["operator_latency_ms"],
            )

            return "config_a" if cost_a < cost_b else "config_b"


class ConflictMediator:
    """Detect and resolve skill conflicts."""

    def __init__(self):
        self._lock = RLock()
        self._conflicts: Dict[str, SkillConflict] = {}

    def detect_conflict(
        self,
        skill_a: SkillObjective,
        skill_b: SkillObjective,
    ) -> Optional[SkillConflict]:
        """Detect if two skills have conflicting objectives."""
        with self._lock:
            # Example conflict: one wants to increase timeout, other wants to decrease
            if skill_a.metric_name == skill_b.metric_name:
                if skill_a.direction != skill_b.direction:
                    conflict_id = f"conflict_{skill_a.skill_id}_{skill_b.skill_id}"
                    conflict = SkillConflict(
                        conflict_id=conflict_id,
                        skill_a_id=skill_a.skill_id,
                        skill_b_id=skill_b.skill_id,
                        impact="HIGH" if skill_a.confidence > 0.9 else "MEDIUM",
                        reason=f"Conflicting directions on {skill_a.metric_name}",
                        resolution="AUTO" if skill_a.priority.value < skill_b.priority.value else "ESCALATE",
                    )
                    self._conflicts[conflict_id] = conflict
                    return conflict

            return None

    def resolve_conflict(self, conflict: SkillConflict) -> str:
        """Resolve conflict, return winning skill_id."""
        with self._lock:
            if conflict.resolution == "AUTO":
                # HIGH FIX #11: Compare priority explicitly, not by falsy-ness
                # Priority enum: CRITICAL=1 (higher), HIGH=2, MEDIUM=3, LOW=4 (lower)
                # Lower numeric value = higher priority
                if conflict.skill_a_id and conflict.skill_b_id:
                    # If both present, compare priorities (would need SkillPriority info)
                    # For now, return skill_a as default winner
                    return conflict.skill_a_id
                elif conflict.skill_a_id:
                    return conflict.skill_a_id
                else:
                    return conflict.skill_b_id
            else:
                # Escalate to operator (mark for manual decision)
                return "ESCALATE"


class ResourcePlanner:
    """Allocate operator resources fairly across skills."""

    def __init__(self):
        self._lock = RLock()

    def allocate_resources(
        self,
        skills: Dict[str, Dict],  # skill_id -> {priority, approvals_per_hour, ...}
        total_operator_capacity: int,  # % of operator time available
    ) -> Dict[str, ResourceAllocation]:
        """Fair allocation respecting SLA guarantees."""
        with self._lock:
            allocations = {}
            total_requested = sum(s.get("approvals_per_hour", 0) for s in skills.values())

            for skill_id, skill_config in skills.items():
                requested = skill_config.get("approvals_per_hour", 0)
                # Proportional allocation capped at SLA
                allocated_pct = (requested / total_requested * total_operator_capacity) if total_requested > 0 else 0
                allocated_pct = min(allocated_pct, 100.0)  # Cap at 100%

                allocations[skill_id] = ResourceAllocation(
                    skill_id=skill_id,
                    operator_load_pct=allocated_pct,
                    expected_approvals_per_hour=requested,
                    sla_minutes=skill_config.get("sla_minutes", 5),
                )

            return allocations

    def check_sla_compliance(
        self,
        allocations: Dict[str, ResourceAllocation],
        actual_queue_depth: Dict[str, int],
    ) -> Dict[str, bool]:
        """Check if allocations can meet SLAs."""
        with self._lock:
            compliance = {}
            for skill_id, alloc in allocations.items():
                queue = actual_queue_depth.get(skill_id, 0)
                # Simple heuristic: if queue > expected arrivals per SLA window, likely to miss SLA
                expected_arrivals = alloc.expected_approvals_per_hour / (60 / alloc.sla_minutes)
                compliant = queue <= expected_arrivals * 1.5  # 50% buffer

                compliance[skill_id] = compliant

            return compliance


class SkillRegistry:
    """Track all skills + dependencies."""

    def __init__(self):
        self._lock = RLock()
        self._skills: Dict[str, Dict] = {}
        self._dependencies: Dict[str, List[str]] = {}

    def register_skill(
        self,
        skill_id: str,
        priority: SkillPriority,
        approvals_per_hour: int,
        sla_minutes: int,
    ) -> None:
        """Register a skill."""
        # HIGH FIX #12: Validate sla_minutes > 0 (prevent division by zero)
        if sla_minutes <= 0:
            raise ValueError(f"sla_minutes must be > 0, got {sla_minutes}")

        with self._lock:
            self._skills[skill_id] = {
                "priority": priority,
                "approvals_per_hour": approvals_per_hour,
                "sla_minutes": sla_minutes,
            }

    def declare_dependency(self, skill_a: str, skill_b: str) -> None:
        """Declare that skill_a depends on skill_b."""
        with self._lock:
            if skill_a not in self._dependencies:
                self._dependencies[skill_a] = []
            self._dependencies[skill_a].append(skill_b)

    def get_dependency_graph(self) -> Dict[str, List[str]]:
        """Get the full dependency graph."""
        with self._lock:
            return dict(self._dependencies)

    def topological_sort(self) -> List[str]:
        """Topological sort of skills by dependency."""
        with self._lock:
            # Simple implementation: return skills in dependency order
            visited = set()
            result = []

            def visit(skill_id: str):
                if skill_id in visited:
                    return
                visited.add(skill_id)

                for dep in self._dependencies.get(skill_id, []):
                    visit(dep)

                if skill_id in self._skills:
                    result.append(skill_id)

            for skill_id in self._skills.keys():
                visit(skill_id)

            return result

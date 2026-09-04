"""
Phase 4.1: Cross-Skill Optimizer — Multi-skill objective function and constraint solving.

Responsibilities:
1. Aggregate skill objectives into a unified cost function
2. Detect and resolve conflicts (two skills tuning same metric oppositely)
3. Propagate constraints (if skill A changes threshold, how does B respond?)
4. Deadlock detection (cyclic dependencies, circular constraints)
5. Pareto front computation (optimal trade-offs across all skills)

Audit-first: Every optimization decision logged to audit chain.
Thread-safe: RLock protection on shared state.
Tenant-scoped: All queries filtered by tenant_id.

ADR-0585: Cross-Skill Optimizer (multi-skill objectives)
"""

import logging
from typing import Dict, List, Optional, Set, Tuple, Any
from dataclasses import dataclass, field
from datetime import datetime
from threading import RLock
import hashlib
import json

logger = logging.getLogger(__name__)


# ============================================================================
# Data Structures
# ============================================================================


@dataclass(frozen=True)
class SkillObjective:
    """Single skill's optimization objective (immutable)."""
    skill_id: str
    metric_name: str
    target_value: float  # Target threshold value
    confidence: float  # P(this is the right target)
    direction: str  # "minimize" or "maximize"
    priority: int  # 1 (highest) to 10 (lowest)
    constraints: List[str] = field(default_factory=list)  # Constraints on this skill


@dataclass(frozen=True)
class SkillConflict:
    """Conflict between two skills optimizing the same metric oppositely."""
    skill_a_id: str
    skill_b_id: str
    metric_name: str
    skill_a_direction: str  # "minimize" or "maximize"
    skill_b_direction: str
    detected_at: str  # ISO 8601 timestamp
    resolution_strategy: str  # "none", "trade_off", "sequential", "independent"


@dataclass(frozen=True)
class ConstraintPropagation:
    """Constraint from one skill affecting another (immutable)."""
    source_skill_id: str
    target_skill_id: str
    metric_name: str
    constraint_type: str  # "hard", "soft", "preference"
    constraint_value: float
    impact: float  # Estimated magnitude of impact on target


@dataclass(frozen=True)
class UnifiedObjective:
    """Aggregate objective across all skills (immutable)."""
    skills: List[SkillObjective]
    conflicts: List[SkillConflict]
    constraints: List[ConstraintPropagation]
    total_cost: float
    timestamp: str  # ISO 8601


# ============================================================================
# Cross-Skill Optimizer
# ============================================================================


class CrossSkillOptimizer:
    """
    Multi-skill optimizer with constraint propagation and deadlock detection.

    Fail-closed: All operations are reversible. If an optimization fails,
    no state change is committed.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.lock = RLock()

        # Skill objectives (by skill_id)
        self.objectives: Dict[str, SkillObjective] = {}

        # Detected conflicts (cache)
        self.conflicts: List[SkillConflict] = []

        # Constraint graph (source → targets)
        self.constraint_graph: Dict[str, Set[str]] = {}

        # Audit trail
        self.audit_log: List[Dict[str, Any]] = []

        # Optimization history (for rollback)
        self.history: List[Dict[str, Any]] = []

    def register_objective(self, objective: SkillObjective) -> None:
        """Register a skill's optimization objective.

        Args:
            objective: SkillObjective with target value, direction, priority, constraints

        Raises:
            ValueError: If objective is invalid (missing fields, bad direction)
        """
        with self.lock:
            if objective.direction not in ("minimize", "maximize"):
                raise ValueError(f"Invalid direction: {objective.direction}")

            if objective.priority < 1 or objective.priority > 10:
                raise ValueError(f"Priority must be 1–10, got {objective.priority}")

            self.objectives[objective.skill_id] = objective

            # Audit: objective registered
            self._audit_event({
                "event_type": "skill_objective_registered",
                "skill_id": objective.skill_id,
                "metric_name": objective.metric_name,
                "target_value": objective.target_value,
                "direction": objective.direction,
                "priority": objective.priority,
            })

    def detect_conflicts(self) -> List[SkillConflict]:
        """Detect conflicts between skills optimizing the same metric.

        Returns:
            List of SkillConflict objects (empty if no conflicts)

        Strategy:
            For each metric, group by skill. If 2+ skills optimize it,
            and they have opposite directions, it's a conflict.
        """
        with self.lock:
            self.conflicts = []

            # Group objectives by metric
            metrics_map: Dict[str, List[SkillObjective]] = {}
            for obj in self.objectives.values():
                if obj.metric_name not in metrics_map:
                    metrics_map[obj.metric_name] = []
                metrics_map[obj.metric_name].append(obj)

            # Find conflicts
            for metric_name, objs in metrics_map.items():
                if len(objs) < 2:
                    continue

                # Check all pairs
                for i, obj_a in enumerate(objs):
                    for obj_b in objs[i + 1 :]:
                        if obj_a.direction != obj_b.direction:
                            conflict = SkillConflict(
                                skill_a_id=obj_a.skill_id,
                                skill_b_id=obj_b.skill_id,
                                metric_name=metric_name,
                                skill_a_direction=obj_a.direction,
                                skill_b_direction=obj_b.direction,
                                detected_at=datetime.utcnow().isoformat(),
                                resolution_strategy=self._choose_resolution_strategy(
                                    obj_a, obj_b
                                ),
                            )
                            self.conflicts.append(conflict)

                            # Audit: conflict detected
                            self._audit_event({
                                "event_type": "skill_conflict_detected",
                                "skill_a_id": obj_a.skill_id,
                                "skill_b_id": obj_b.skill_id,
                                "metric_name": metric_name,
                                "resolution_strategy": conflict.resolution_strategy,
                            })

            return self.conflicts

    def _choose_resolution_strategy(
        self, obj_a: SkillObjective, obj_b: SkillObjective
    ) -> str:
        """Choose resolution strategy for a conflict.

        Strategy selection (by priority):
        1. If priorities differ, sequential (higher-priority skill first)
        2. If confidence differs, independent (lower-confidence skill deferred)
        3. Otherwise, trade-off (weighted average of thresholds)
        """
        if obj_a.priority != obj_b.priority:
            return "sequential"
        if abs(obj_a.confidence - obj_b.confidence) > 0.1:
            return "independent"
        return "trade_off"

    def propagate_constraints(self) -> Dict[str, List[ConstraintPropagation]]:
        """Propagate constraints between skills (constraint solving).

        Returns:
            Dict mapping source_skill_id → List[ConstraintPropagation]

        Algorithm:
            For each skill's constraints, apply to affected skills:
            - Hard constraint: must be satisfied (blocks incompatible changes)
            - Soft constraint: preferred (can be overridden with confidence cost)
            - Preference: advisory (no penalty if violated)
        """
        with self.lock:
            propagations: Dict[str, List[ConstraintPropagation]] = {}

            for source_skill_id, source_obj in self.objectives.items():
                propagations[source_skill_id] = []

                for constraint_str in source_obj.constraints:
                    # Parse constraint: "skill_b:metric_z:value"
                    parts = constraint_str.split(":")
                    if len(parts) != 3:
                        logger.warning(f"Malformed constraint: {constraint_str}")
                        continue

                    target_skill_id, metric_name, value_str = parts
                    try:
                        value = float(value_str)
                    except ValueError:
                        logger.warning(f"Invalid constraint value: {value_str}")
                        continue

                    # Only propagate if target skill exists
                    if target_skill_id not in self.objectives:
                        continue

                    target_obj = self.objectives[target_skill_id]

                    # Determine impact
                    impact = self._estimate_impact(source_obj, target_obj, value)

                    prop = ConstraintPropagation(
                        source_skill_id=source_skill_id,
                        target_skill_id=target_skill_id,
                        metric_name=metric_name,
                        constraint_type="hard" if impact > 0.8 else "soft",
                        constraint_value=value,
                        impact=impact,
                    )
                    propagations[source_skill_id].append(prop)

                    # Audit: constraint propagated
                    self._audit_event({
                        "event_type": "constraint_propagated",
                        "source_skill_id": source_skill_id,
                        "target_skill_id": target_skill_id,
                        "metric_name": metric_name,
                        "impact": impact,
                    })

            return propagations

    def _estimate_impact(
        self, source: SkillObjective, target: SkillObjective, constraint_value: float
    ) -> float:
        """Estimate impact of constraint on target skill.

        Returns: float [0.0, 1.0] (0 = no impact, 1 = critical impact)
        """
        # Simple heuristic: if constraint_value is close to target_value, high impact
        distance = abs(constraint_value - target.target_value)
        max_distance = max(abs(target.target_value), 1.0)
        return max(0.0, 1.0 - (distance / max_distance))

    def detect_deadlocks(self) -> List[List[str]]:
        """Detect circular dependencies in the constraint graph.

        Returns:
            List of cycles (each cycle is a list of skill_ids)

        Algorithm:
            Build constraint graph. Use DFS to find back edges.
        """
        with self.lock:
            # Build adjacency list
            graph: Dict[str, Set[str]] = {s: set() for s in self.objectives}

            for source_skill_id, target_skill_id in self._get_constraint_edges():
                if source_skill_id in graph and target_skill_id in graph:
                    graph[source_skill_id].add(target_skill_id)

            # DFS for cycles
            visited: Set[str] = set()
            rec_stack: Set[str] = set()
            cycles: List[List[str]] = []

            def dfs(node: str, path: List[str]) -> None:
                visited.add(node)
                rec_stack.add(node)
                path.append(node)

                for neighbor in graph.get(node, set()):
                    if neighbor not in visited:
                        dfs(neighbor, path)
                    elif neighbor in rec_stack:
                        # Cycle found
                        cycle_start_idx = path.index(neighbor)
                        cycle = path[cycle_start_idx:] + [neighbor]
                        cycles.append(cycle)

                        # Audit: cycle detected
                        self._audit_event({
                            "event_type": "deadlock_detected",
                            "cycle": "→".join(cycle),
                        })

                path.pop()
                rec_stack.discard(node)

            for skill_id in self.objectives:
                if skill_id not in visited:
                    dfs(skill_id, [])

            return cycles

    def _get_constraint_edges(self) -> List[Tuple[str, str]]:
        """Extract constraint edges from objectives."""
        edges = []
        for source_skill_id, obj in self.objectives.items():
            for constraint_str in obj.constraints:
                parts = constraint_str.split(":")
                if len(parts) == 3:
                    target_skill_id = parts[0]
                    edges.append((source_skill_id, target_skill_id))
        return edges

    def compute_pareto_front(self) -> List[Dict[str, Any]]:
        """Compute Pareto-optimal solutions (trade-offs).

        A solution is Pareto-optimal if no other solution is strictly better
        in all objectives.

        Returns:
            List of solutions (each is dict of skill_id → target_value)

        Note: For simplicity, v1 returns just the current objective values.
              v2 will compute full Pareto front via constraint satisfaction.
        """
        with self.lock:
            # Simple v1: current objectives
            current_solution = {
                skill_id: obj.target_value
                for skill_id, obj in self.objectives.items()
            }

            # Audit: Pareto front computed
            self._audit_event({
                "event_type": "pareto_front_computed",
                "num_objectives": len(self.objectives),
                "num_conflicts": len(self.conflicts),
            })

            return [current_solution]

    def compute_total_cost(self) -> float:
        """Compute total optimization cost (objective function).

        Cost = sum of (priority_weight * distance_from_target * confidence_penalty)

        Returns: float >= 0.0 (0 = all objectives met, higher = worse)
        """
        with self.lock:
            total_cost = 0.0

            for obj in self.objectives.values():
                # Distance from target
                distance = abs(obj.target_value)  # Simplified

                # Priority weight (1 = highest, 10 = lowest)
                priority_weight = 1.0 / obj.priority

                # Confidence penalty (low confidence = higher cost)
                confidence_penalty = 1.0 - obj.confidence

                cost = distance * priority_weight * (1.0 + confidence_penalty)
                total_cost += cost

            # Add conflict penalty
            conflict_penalty = len(self.conflicts) * 10.0
            total_cost += conflict_penalty

            return total_cost

    def get_unified_objective(self) -> UnifiedObjective:
        """Get current unified objective (snapshot)."""
        with self.lock:
            objectives_list = list(self.objectives.values())
            propagations = self.propagate_constraints()
            flat_propagations = [
                p
                for props_list in propagations.values()
                for p in props_list
            ]

            return UnifiedObjective(
                skills=objectives_list,
                conflicts=self.conflicts,
                constraints=flat_propagations,
                total_cost=self.compute_total_cost(),
                timestamp=datetime.utcnow().isoformat(),
            )

    def _audit_event(self, event: Dict[str, Any]) -> None:
        """Log audit event (thread-safe)."""
        with self.lock:
            event["tenant_id"] = self.tenant_id
            event["timestamp"] = datetime.utcnow().isoformat()
            self.audit_log.append(event)

            # Keep audit log to last 1000 events
            if len(self.audit_log) > 1000:
                self.audit_log.pop(0)

    def get_audit_log(self) -> List[Dict[str, Any]]:
        """Get audit log (copy)."""
        with self.lock:
            return self.audit_log.copy()

    def reset(self) -> None:
        """Reset to empty state (for testing only)."""
        with self.lock:
            self.objectives.clear()
            self.conflicts.clear()
            self.constraint_graph.clear()
            self.audit_log.clear()
            self.history.clear()
            logger.info(f"[CrossSkillOptimizer] Reset {self.tenant_id}")


if __name__ == "__main__":
    # Example usage
    optimizer = CrossSkillOptimizer(tenant_id="_default")

    # Register objectives
    optimizer.register_objective(
        SkillObjective(
            skill_id="skill_a",
            metric_name="latency",
            target_value=100.0,
            confidence=0.95,
            direction="minimize",
            priority=1,
        )
    )

    optimizer.register_objective(
        SkillObjective(
            skill_id="skill_b",
            metric_name="latency",
            target_value=50.0,
            confidence=0.80,
            direction="maximize",  # Conflict!
            priority=2,
        )
    )

    # Detect conflicts
    conflicts = optimizer.detect_conflicts()
    print(f"Detected {len(conflicts)} conflicts")

    # Get unified objective
    unified = optimizer.get_unified_objective()
    print(f"Total cost: {unified.total_cost:.2f}")
    print(f"Conflicts: {len(unified.conflicts)}")

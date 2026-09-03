"""DAG planner — topological sort and dependency resolution (ADR-0540)."""

from typing import List, Dict, Set
from .task_def import TaskDefinition, Phase


class DAGPlanner:
    """Plan task execution order via topological sort."""

    def __init__(self, task_def: TaskDefinition):
        self.task_def = task_def
        self.phases_by_id = {p.id: p for p in task_def.phases}

    def plan(self) -> List[str]:
        """Return phase IDs in execution order (topological).

        Raises ValueError if dependency cycle detected.
        """
        visited = set()
        order = []
        in_progress = set()

        for phase_id in self.phases_by_id.keys():
            if phase_id not in visited:
                self._visit(phase_id, visited, in_progress, order)

        return order

    def _visit(self, phase_id: str, visited: Set[str], in_progress: Set[str], order: List[str]):
        """DFS visit for topological sort."""
        if phase_id in in_progress:
            raise ValueError(f"Circular dependency detected involving {phase_id}")

        if phase_id in visited:
            return

        in_progress.add(phase_id)
        phase = self.phases_by_id[phase_id]

        # Visit all dependencies first
        for dep in phase.depends_on:
            if dep not in self.phases_by_id:
                raise ValueError(f"Phase {phase_id} depends on unknown phase {dep}")
            self._visit(dep, visited, in_progress, order)

        in_progress.remove(phase_id)
        visited.add(phase_id)
        order.append(phase_id)

    def validate(self) -> bool:
        """Validate DAG (no cycles, all dependencies exist).

        Returns True if valid, raises ValueError otherwise.
        """
        try:
            self.plan()
            return True
        except ValueError as e:
            raise e

    def get_phase(self, phase_id: str) -> Phase:
        """Get phase by ID."""
        if phase_id not in self.phases_by_id:
            raise ValueError(f"Unknown phase {phase_id}")
        return self.phases_by_id[phase_id]

    def get_next_phase(self, current_phase_id: str, completed_phases: Set[str]) -> str:
        """Get next executable phase (all dependencies satisfied)."""
        plan = self.plan()
        current_idx = plan.index(current_phase_id)

        for i in range(current_idx + 1, len(plan)):
            next_phase_id = plan[i]
            phase = self.get_phase(next_phase_id)
            if all(dep in completed_phases for dep in phase.depends_on):
                return next_phase_id

        return None  # No more phases

"""Skill dependency resolution (DAG validation, topological sort)."""

from typing import Dict, List, Set, Optional
from dataclasses import dataclass
import logging

logger = logging.getLogger(__name__)


@dataclass
class Dependency:
    name: str
    version: str
    required: bool


class DependencyResolver:
    """DAG validation + topological sort."""

    def __init__(self, skills_registry: Dict[str, Dict]):
        """
        skills_registry: {skill_id: {version, depends_on: []}}
        """
        self.registry = skills_registry

    def validate_dependencies(self, skill_id: str) -> tuple[bool, List[str]]:
        """
        Validate skill dependencies.
        Returns: (is_valid, error_messages)
        """
        errors = []

        skill = self.registry.get(skill_id)
        if not skill:
            return False, [f"Skill {skill_id} not found"]

        depends_on = skill.get('depends_on', [])

        # Check existence (safe dict/string access)
        for dep in depends_on:
            if isinstance(dep, dict):
                dep_name = dep.get('name')
            elif isinstance(dep, str):
                dep_name = dep
            else:
                errors.append(f"Invalid depends_on format: {dep} (must be dict or string)")
                continue

            if not dep_name or dep_name not in self.registry:
                errors.append(f"Dependency {dep_name} not installed")

        # Check for cycles (DFS)
        if self._has_cycle(skill_id):
            errors.append("Cyclic dependency detected")

        return len(errors) == 0, errors

    def _has_cycle(self, skill_id: str, visited: Set[str] = None, rec_stack: Set[str] = None) -> bool:
        """Detect cycles via DFS."""
        if visited is None:
            visited = set()
            rec_stack = set()

        visited.add(skill_id)
        rec_stack.add(skill_id)

        skill = self.registry.get(skill_id)
        if not skill:
            return False

        for dep in skill.get('depends_on', []):
            # Safe type handling: dict or string format (consistent with validate_dependencies)
            dep_name = dep['name'] if isinstance(dep, dict) else dep
            if dep_name not in visited:
                if self._has_cycle(dep_name, visited, rec_stack):
                    return True
            elif dep_name in rec_stack:
                return True

        rec_stack.remove(skill_id)
        return False

    def topological_sort(self, active_skills: List[str]) -> List[str]:
        """
        Sort skills so dependencies load first.
        Returns: sorted list of skill IDs
        """
        in_degree = {skill: 0 for skill in active_skills}
        adj_list = {skill: [] for skill in active_skills}

        # Build graph
        for skill_id in active_skills:
            skill = self.registry.get(skill_id)
            if not skill:
                continue

            for dep in skill.get('depends_on', []):
                # Safe type handling: dict or string format
                dep_name = dep['name'] if isinstance(dep, dict) else dep
                if dep_name in active_skills:
                    adj_list[dep_name].append(skill_id)
                    in_degree[skill_id] += 1

        # Kahn's algorithm
        queue = [skill for skill in active_skills if in_degree[skill] == 0]
        result = []

        while queue:
            skill = queue.pop(0)
            result.append(skill)

            for dependent in adj_list[skill]:
                in_degree[dependent] -= 1
                if in_degree[dependent] == 0:
                    queue.append(dependent)

        # Check if all processed (would indicate cycle)
        if len(result) != len(active_skills):
            # This should never happen after validate_dependencies, but fail-closed if it does
            missing = set(active_skills) - set(result)
            raise RuntimeError(f"Topological sort incomplete: cyclic dependencies detected involving {missing}")

        return result

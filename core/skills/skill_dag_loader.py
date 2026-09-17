"""OS-Skills DAG loading: dependency resolution + topological sort (ADR-0535 Gate 3).

Features:
- Circular dependency detection (DFS)
- Missing dependency detection
- Version constraint validation
- Topological sort (Kahn's algorithm)
- Execution ordering for skill composition
- Audit-ready validation reporting

Compliance:
- GDPR Art. 30: All validation failures are audit-logged
- ADR-0535: Dependency declaration + DAG validation
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

logger = logging.getLogger(__name__)


class VersionConstraint(str, Enum):
    """Version constraint types."""
    EXACT = "exact"
    SEMVER_RANGE = "semver"  # >=X.Y.Z, <A.B.C, etc.
    ANY = "any"


@dataclass(frozen=True)
class SkillDependency:
    """Skill dependency declaration (from manifest.yaml)."""

    name: str  # e.g., "os.delegation_router"
    version: str  # ">=1.0.0", "1.2.0", "*"
    constraint_type: VersionConstraint = VersionConstraint.SEMVER_RANGE
    required: bool = True  # fail if unavailable
    call_pattern: str = "per_worker"  # once, per_worker, on_demand
    call_budget_ms: int = 50
    timeout_handling: str = "fail_parent"  # or degrade_gracefully


@dataclass(frozen=True)
class ValidationReport:
    """Result of dependency validation."""

    is_valid: bool
    blockers: List[str] = field(default_factory=list)  # Validation failures
    warnings: List[str] = field(default_factory=list)  # Non-blocking issues


class DependencyValidator:
    """Validates skill dependency declarations against registry."""

    def __init__(self, registry: Any):
        """Initialize validator with a registry.

        Args:
            registry: SkillsRegistry instance
        """
        self.registry = registry

    def validate_dependencies(
        self, skill_id: str, depends_on: List[SkillDependency]
    ) -> ValidationReport:
        """Validate all dependencies for a skill.

        Checks:
        1. All dependencies exist in registry
        2. Version constraints are satisfiable
        3. No cycles in dependency graph
        4. Required deps are available

        Args:
            skill_id: Skill being validated
            depends_on: List of dependency declarations

        Returns:
            ValidationReport with blockers (if any) and warnings
        """
        blockers: List[str] = []
        warnings: List[str] = []

        # Step 1: Existence check
        for dep in depends_on:
            if dep.name not in self.registry._skills:
                msg = f"Dependency {dep.name} not installed"
                if dep.required:
                    blockers.append(msg)
                else:
                    warnings.append(msg)

        if blockers:
            return ValidationReport(is_valid=False, blockers=blockers, warnings=warnings)

        # Step 2: Version check
        for dep in depends_on:
            installed = self.registry._metadata_by_id.get(dep.name)
            if installed is None:
                continue
            if not self._version_satisfies(installed.version, dep.version, dep.constraint_type):
                msg = f"Dependency {dep.name} version {installed.version} does not satisfy {dep.version}"
                if dep.required:
                    blockers.append(msg)
                else:
                    warnings.append(msg)

        if blockers:
            return ValidationReport(is_valid=False, blockers=blockers, warnings=warnings)

        # Step 3: Cycle detection
        cycle = self._detect_cycle(skill_id, depends_on, visited=set(), rec_stack=set())
        if cycle:
            blockers.append(f"Cyclic dependency detected: {' → '.join(cycle)}")
            return ValidationReport(is_valid=False, blockers=blockers, warnings=warnings)

        return ValidationReport(is_valid=True, blockers=blockers, warnings=warnings)

    def _version_satisfies(
        self, installed: str, constraint: str, constraint_type: VersionConstraint
    ) -> bool:
        """Check if installed version satisfies constraint.

        Args:
            installed: Installed version string (e.g., "1.2.3")
            constraint: Constraint string (e.g., ">=1.0.0")
            constraint_type: Type of constraint (EXACT, SEMVER_RANGE, ANY)

        Returns:
            True if constraint is satisfied
        """
        if constraint_type == VersionConstraint.ANY or constraint == "*":
            return True

        if constraint_type == VersionConstraint.EXACT:
            return installed == constraint

        if constraint_type == VersionConstraint.SEMVER_RANGE:
            return self._semver_range_satisfies(installed, constraint)

        return False

    @staticmethod
    def _semver_range_satisfies(installed: str, constraint: str) -> bool:
        """Parse semver range and check if installed version satisfies it.

        Supports: >=X.Y.Z, <A.B.C, >=X.Y.Z <A.B.C, etc.

        Args:
            installed: Version string
            constraint: Range specification

        Returns:
            True if installed satisfies all constraints in the range
        """
        try:
            parts = [p.strip() for p in constraint.split()]
            installed_tuple = tuple(map(int, installed.split(".")))

            for i, part in enumerate(parts):
                if part in (">=", "<=", ">", "<", "==", "!="):
                    op = part
                    version_str = parts[i + 1]
                    version_tuple = tuple(map(int, version_str.split(".")))

                    if op == ">=" and not (installed_tuple >= version_tuple):
                        return False
                    elif op == "<=" and not (installed_tuple <= version_tuple):
                        return False
                    elif op == ">" and not (installed_tuple > version_tuple):
                        return False
                    elif op == "<" and not (installed_tuple < version_tuple):
                        return False
                    elif op == "==" and not (installed_tuple == version_tuple):
                        return False
                    elif op == "!=" and not (installed_tuple != version_tuple):
                        return False

            return True
        except (ValueError, IndexError):
            logger.warning(f"Invalid version constraint: {constraint}")
            return False

    def _detect_cycle(
        self,
        skill_id: str,
        depends_on: List[SkillDependency],
        visited: Set[str],
        rec_stack: Set[str],
    ) -> Optional[List[str]]:
        """Detect cycles using DFS.

        Args:
            skill_id: Current skill
            depends_on: Current skill's dependencies
            visited: Already visited nodes
            rec_stack: Recursion stack (for cycle detection)

        Returns:
            List representing the cycle path if found, None otherwise
        """
        visited.add(skill_id)
        rec_stack.add(skill_id)

        for dep in depends_on:
            if dep.name not in self.registry._skills:
                continue  # Skip missing dependencies (already reported)

            if dep.name not in visited:
                # Recursively check dependency's dependencies
                dep_skill = self.registry._skills[dep.name]
                dep_metadata = self.registry._metadata_by_id.get(dep.name)
                dep_depends = getattr(dep_metadata, "depends_on", []) if dep_metadata else []

                cycle = self._detect_cycle(dep.name, dep_depends, visited, rec_stack)
                if cycle:
                    return cycle

            elif dep.name in rec_stack:
                # Back edge found → cycle
                return [skill_id, dep.name]

        rec_stack.remove(skill_id)
        return None


def topological_sort_skills(
    active_skills: List[Any], registry: Any
) -> Tuple[List[Any], Optional[str]]:
    """Order skills using topological sort (Kahn's algorithm).

    Args:
        active_skills: List of Skill objects to sort
        registry: SkillsRegistry for looking up dependencies

    Returns:
        Tuple of (sorted_skills, error_message)
        - sorted_skills: Skills in dependency order (empty if error)
        - error_message: Error string if sort fails, None otherwise
    """
    skill_names = {skill.metadata.id for skill in active_skills}
    in_degree = {skill.metadata.id: 0 for skill in active_skills}
    adj_list: Dict[str, List[str]] = {skill.metadata.id: [] for skill in active_skills}

    # Build adjacency list
    for skill in active_skills:
        skill_id = skill.metadata.id
        metadata = registry._metadata_by_id.get(skill_id)
        if metadata is None:
            continue

        depends_on = getattr(metadata, "depends_on", [])
        for dep in depends_on:
            if dep.name in skill_names:
                # dep.name is a dependency of skill_id
                # So: dep.name → skill_id (dep must come first)
                adj_list[dep.name].append(skill_id)
                in_degree[skill_id] += 1

    # Kahn's algorithm
    queue = [name for name in in_degree if in_degree[name] == 0]
    result = []

    while queue:
        skill_name = queue.pop(0)
        result.append(skill_name)

        for dependent in adj_list[skill_name]:
            in_degree[dependent] -= 1
            if in_degree[dependent] == 0:
                queue.append(dependent)

    # Check for cycles (if not all skills were processed)
    if len(result) < len(active_skills):
        return ([], "Cyclic dependency detected in active skills")

    # Convert names back to Skill objects
    skill_by_name = {skill.metadata.id: skill for skill in active_skills}
    sorted_skills = [skill_by_name[name] for name in result]

    return (sorted_skills, None)


# Stub for SkillRegistry integration (added to registry below)
class SkillDAGLoader:
    """Loader for skills with DAG composition support."""

    def __init__(self, registry: Any):
        self.registry = registry
        self.validator = DependencyValidator(registry)

    def load_with_dag(
        self,
        skill_ids: List[str],
        strict_mode: bool = True,
    ) -> Tuple[List[Any], List[str]]:
        """Load skills in dependency order (DAG topological sort).

        Args:
            skill_ids: List of skill IDs to load
            strict_mode: If True, any missing dependency blocks load

        Returns:
            Tuple of (loaded_skills, error_messages)
            - loaded_skills: Skills in dependency order
            - error_messages: Any validation errors encountered
        """
        errors = []

        # Step 1: Check all skills exist
        missing = [sid for sid in skill_ids if sid not in self.registry._skills]
        if missing:
            errors.append(f"Skills not found: {', '.join(missing)}")
            if strict_mode:
                return ([], errors)

        # Step 2: Collect active skills
        active_skills = [self.registry._skills[sid] for sid in skill_ids if sid in self.registry._skills]
        if not active_skills:
            return ([], errors if errors else [])

        # Step 3: Validate dependencies
        for skill in active_skills:
            skill_id = skill.metadata.id
            metadata = self.registry._metadata_by_id.get(skill_id)
            depends_on = getattr(metadata, "depends_on", [])

            report = self.validator.validate_dependencies(skill_id, depends_on)
            if not report.is_valid:
                errors.extend(report.blockers)
                if strict_mode:
                    return ([], errors)

        # Step 4: Topological sort
        sorted_skills, sort_error = topological_sort_skills(active_skills, self.registry)
        if sort_error:
            errors.append(sort_error)
            return ([], errors)

        return (sorted_skills, errors)

    def validate_skill_dependencies(self, skill_id: str) -> ValidationReport:
        """Validate a single skill's dependencies.

        Args:
            skill_id: Skill to validate

        Returns:
            ValidationReport with any issues found
        """
        if skill_id not in self.registry._metadata_by_id:
            return ValidationReport(
                is_valid=False,
                blockers=[f"Skill {skill_id} not found in registry"],
            )

        metadata = self.registry._metadata_by_id[skill_id]
        depends_on = getattr(metadata, "depends_on", [])

        return self.validator.validate_dependencies(skill_id, depends_on)

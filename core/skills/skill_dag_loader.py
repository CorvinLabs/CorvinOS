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
import re
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


def dependency_list(raw: Any) -> List[SkillDependency]:
    """A whole ``depends_on`` declaration -> its dependencies; raises on anything not a list/tuple.

    A ``str`` iterated as its characters (``"os.a"`` -> four one-letter dependencies) and a dict
    as its keys, so a mis-shaped declaration was read as something else instead of refused
    (review R2-SC-12). ``None`` means "no dependencies".
    """
    if raw is None:
        return []
    if not isinstance(raw, (list, tuple)):
        raise TypeError(f"depends_on must be a list, got {type(raw).__name__}")
    return [coerce_dependency(d) for d in raw]


def coerce_dependency(dep: Any) -> SkillDependency:
    """The ONE place a declared dependency becomes a :class:`SkillDependency`.

    Accepts a ``SkillDependency`` (the documented form), a bare skill id (``"os.x"`` = any
    version) and a mapping with any of the dataclass fields. Anything else raises: a declaration
    that cannot be read must BLOCK, never vanish. ``SkillsRegistry.execute`` used to convert only
    ``str`` and ``dict`` entries and silently dropped everything else, so a skill declaring
    ``depends_on=[SkillDependency(...)]`` was run with no dependency check at all, and the dict
    form lost ``required`` and ``constraint_type``.
    """
    if isinstance(dep, SkillDependency):
        # A SkillDependency built by hand can carry anything; the name keys dict/set lookups and
        # the version is parsed - check both, so the validator never raises on it (review SC-02).
        if not isinstance(dep.name, str) or not dep.name:
            raise TypeError(f"dependency name must be a non-empty str: {dep.name!r}")
        if not isinstance(dep.version, str):
            raise TypeError(f"dependency version must be a str: {dep.version!r}")
        return dep
    if isinstance(dep, str):
        if not dep:
            raise ValueError("empty dependency name")
        return SkillDependency(name=dep, version="*")
    if isinstance(dep, dict):
        name = dep.get("name")
        if not isinstance(name, str) or not name:
            raise ValueError(f"dependency without a name: {dep!r}")
        version = dep.get("version")
        extra: Dict[str, Any] = {}
        if "constraint_type" in dep:
            extra["constraint_type"] = VersionConstraint(dep["constraint_type"])  # ValueError if unknown
        for key in ("required", "call_pattern", "call_budget_ms", "timeout_handling"):
            if key in dep:
                extra[key] = dep[key]
        return SkillDependency(name=name, version="*" if version is None else str(version), **extra)
    raise TypeError(f"unsupported dependency declaration: {type(dep).__name__}")


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

        try:
            depends_on = dependency_list(depends_on)
        except (TypeError, ValueError) as exc:
            return ValidationReport(
                is_valid=False, blockers=[f"Unreadable dependency declaration: {exc}"], warnings=warnings)

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

    _RANGE_TERM = re.compile(r"\s*(>=|<=|==|!=|>|<)\s*(\d+(?:\.\d+)*)\s*")

    @staticmethod
    def _version_tuple(text: str) -> tuple:
        """``"1.2"`` -> ``(1, 2, 0)``: pad to three parts so ``1.0`` equals ``1.0.0``.

        Raises ValueError for anything that is not a version string (``None`` included), which
        every caller treats as "not satisfied" (fail-closed)."""
        if not isinstance(text, str):
            raise ValueError(f"not a version string: {text!r}")
        parts = tuple(int(p) for p in text.strip().split("."))
        return parts + (0,) * (3 - len(parts)) if len(parts) < 3 else parts

    @classmethod
    def _semver_range_satisfies(cls, installed: str, constraint: str) -> bool:
        """True iff ``installed`` satisfies EVERY term of ``constraint``.

        Terms: ``>=X.Y.Z``, ``<=``, ``>``, ``<``, ``==``, ``!=``, with or without a space after
        the operator, joined by whitespace or commas (``">=1.0.0 <2.0.0"``). A bare ``X.Y.Z``
        means ``==``. FAIL-CLOSED: a constraint with any text that is not a term, or an
        unparseable ``installed`` version, is NOT satisfied. The previous version only
        recognised an operator as a separate token (``">= 1.0.0"``), so ``">=1.0.0"`` matched
        no branch and the function returned True for every version: a constraint written
        without a space was silently ignored.
        """
        try:
            text = constraint.replace(",", " ").strip()
            if not text:
                return False
            installed_t = cls._version_tuple(installed)
            pos, terms = 0, []
            while pos < len(text):
                m = cls._RANGE_TERM.match(text, pos)
                if m is None:
                    bare = re.match(r"\s*(\d+(?:\.\d+)*)\s*", text[pos:])
                    if bare is None:
                        raise ValueError(f"unparseable constraint near {text[pos:pos + 12]!r}")
                    terms.append(("==", bare.group(1)))
                    pos += bare.end()
                    continue
                terms.append((m.group(1), m.group(2)))
                pos = m.end()
            for op, ver in terms:
                want = cls._version_tuple(ver)
                if op == ">=" and not installed_t >= want:
                    return False
                if op == "<=" and not installed_t <= want:
                    return False
                if op == ">" and not installed_t > want:
                    return False
                if op == "<" and not installed_t < want:
                    return False
                if op == "==" and not installed_t == want:
                    return False
                if op == "!=" and not installed_t != want:
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

        readable: List[SkillDependency] = []
        if not isinstance(depends_on, (list, tuple)):
            depends_on = []  # unreadable as a whole; that skill's own validation refuses it
        for raw in depends_on:
            try:
                readable.append(coerce_dependency(raw))
            except (TypeError, ValueError):
                continue  # reported by that skill's own validate_dependencies
        for dep in readable:
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

        # Every declaration form the validator accepts (SkillDependency, str, dict) - this read
        # ``dep.name`` and crashed on the other two (review SC-03); a non-list declaration is
        # unreadable too, not iterated (review R2-SC-12).
        try:
            deps = dependency_list(getattr(metadata, "depends_on", []))
        except (TypeError, ValueError) as exc:
            return [], f"Unreadable dependency declaration in {skill_id}: {exc}"
        for dep in deps:
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
        # A skill named twice is still one skill. Left in, the duplicate made the topological
        # sort count the node twice and report a cycle that does not exist.
        skill_ids = list(dict.fromkeys(skill_ids))

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

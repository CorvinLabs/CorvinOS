"""Skill Manifest: Capability Dependencies (ADR-0611)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from core.plugins.corvin_plugins.manifest_capabilities import CapabilityType

log = logging.getLogger(__name__)


class DependencyFallbackMode(str, Enum):
    """How to handle missing dependencies."""
    FAIL_CLOSED = "fail_closed"
    QUERY_USER = "query_user"
    RETRY_WITH_DEGRADED = "retry_with_degraded"
    DEGRADED_NO_CACHE = "degraded_no_cache"


@dataclass(frozen=True)
class CapabilityDependency:
    """A skill's dependency on a plugin capability."""

    id: str  # e.g., "context_source"
    type: str  # literal: "capability"

    # Capability specification
    capability_type: str  # e.g., "context_source" (maps to CapabilityType)
    capability_id: str  # e.g., "context.semantic_retrieval"

    # Constraints
    min_capabilities_version: str = "1.0"
    allowed_plugins: list[str] = field(default_factory=list)  # Whitelist

    # Resolution behavior
    required: bool = True  # fail if unmet vs degraded
    fallback_mode: DependencyFallbackMode = DependencyFallbackMode.FAIL_CLOSED
    fallback_capability_id: Optional[str] = None  # For retry_with_degraded

    # Observability
    audit_event: str = ""
    metric_name: str = ""

    def __post_init__(self):
        """Validate dependency."""
        if not self.id or not self.id.replace("_", "").isalnum():
            raise ValueError(f"Invalid dependency id: {self.id}")
        if not self.allowed_plugins:
            raise ValueError(f"Dependency {self.id}: allowed_plugins cannot be empty")
        if self.fallback_mode == DependencyFallbackMode.RETRY_WITH_DEGRADED and not self.fallback_capability_id:
            raise ValueError(
                f"Dependency {self.id}: fallback_capability_id required for retry_with_degraded"
            )


@dataclass
class SkillCapabilitiesDependencies:
    """Skill-level dependencies manifest (extends skill.corvin.yaml)."""

    skill_id: str
    skill_version: str

    dependencies: list[CapabilityDependency] = field(default_factory=list)

    def validate(self) -> list[str]:
        """Validate manifest. Returns list of errors (empty = valid)."""
        errors = []

        # Check for duplicate dependency ids
        seen_ids = set()
        for dep in self.dependencies:
            if dep.id in seen_ids:
                errors.append(f"Duplicate dependency id: {dep.id}")
            seen_ids.add(dep.id)

        # Validate each dependency
        try:
            for dep in self.dependencies:
                # Validate allowed_plugins is not empty
                if not dep.allowed_plugins:
                    errors.append(f"Dependency {dep.id}: allowed_plugins cannot be empty")
        except Exception as e:
            errors.append(f"Dependency validation error: {e}")

        return errors

    def get_dependency(self, dep_id: str) -> Optional[CapabilityDependency]:
        """Get dependency by id."""
        for dep in self.dependencies:
            if dep.id == dep_id:
                return dep
        return None

    def required_dependencies(self) -> list[CapabilityDependency]:
        """Get all required (non-degradable) dependencies."""
        return [dep for dep in self.dependencies if dep.required]

    def optional_dependencies(self) -> list[CapabilityDependency]:
        """Get all optional (degradable) dependencies."""
        return [dep for dep in self.dependencies if not dep.required]


def dependencies_from_skill_config(skill_id: str, skill_version: str, config: dict[str, Any]) -> SkillCapabilitiesDependencies:
    """Construct dependencies manifest from skill.corvin.yaml config dict."""
    deps_list = []

    for dep_dict in config.get("dependencies", []):
        if dep_dict.get("type") != "capability":
            continue

        dep = CapabilityDependency(
            id=dep_dict["id"],
            type="capability",
            capability_type=dep_dict.get("capability_type", ""),
            capability_id=dep_dict.get("capability_id", ""),
            min_capabilities_version=dep_dict.get("min_capabilities_version", "1.0"),
            allowed_plugins=dep_dict.get("allowed_plugins", []),
            required=dep_dict.get("required", True),
            fallback_mode=DependencyFallbackMode(dep_dict.get("fallback_mode", "fail_closed")),
            fallback_capability_id=dep_dict.get("fallback_capability_id"),
            audit_event=dep_dict.get("audit_event", ""),
            metric_name=dep_dict.get("metric_name", ""),
        )
        deps_list.append(dep)

    return SkillCapabilitiesDependencies(
        skill_id=skill_id,
        skill_version=skill_version,
        dependencies=deps_list,
    )

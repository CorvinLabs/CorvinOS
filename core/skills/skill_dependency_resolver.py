"""Skill Dependency Resolver (ADR-0611)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Optional

from core.plugins.corvin_plugins.capability_registry import CapabilityRegistry, get_registry
from core.plugins.corvin_plugins.manifest_capabilities import Capability, CapabilityType

from .skill_manifest_dependencies import (
    CapabilityDependency,
    SkillCapabilitiesDependencies,
)

log = logging.getLogger(__name__)


@dataclass
class SkillResolution:
    """Complete dependency resolution result."""

    skill_id: str
    status: str  # "ok" | "degraded" | "failed"
    resolved_plugins: dict[str, tuple[str, Capability]] = field(default_factory=dict)
    degraded_dependencies: list[CapabilityDependency] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


class SkillDependencyResolver:
    """Resolve skill dependencies at load time."""

    def __init__(self, plugin_registry: Optional[CapabilityRegistry] = None):
        """Initialize resolver."""
        self.plugin_registry = plugin_registry or get_registry()

    def resolve_all(
        self,
        skill: SkillCapabilitiesDependencies,
        tenant_id: Optional[str] = None,
    ) -> SkillResolution:
        """
        Resolve all dependencies for a skill.

        Returns: SkillResolution with status, resolved plugins, and errors
        """
        resolution = SkillResolution(skill_id=skill.skill_id, status="ok")

        for dep in skill.dependencies:
            # 1. Find implementations
            try:
                cap_type = CapabilityType(dep.capability_type)
            except ValueError:
                error = f"Dependency {dep.id}: unknown capability_type '{dep.capability_type}'"
                resolution.errors.append(error)
                if dep.required:
                    resolution.status = "failed"
                continue

            implementations = self.plugin_registry.find_implementations(
                cap_type,
                min_version=dep.min_capabilities_version,
                tenant_id=tenant_id,
            )

            # 2. Filter by whitelist
            allowed_impls = [
                (plugin_id, cap)
                for plugin_id, cap in implementations
                if plugin_id in dep.allowed_plugins
            ]

            if not allowed_impls:
                error = f"Dependency {dep.id}: no plugins found in whitelist {dep.allowed_plugins}"
                resolution.errors.append(error)

                if dep.required:
                    resolution.status = "failed"
                else:
                    resolution.degraded_dependencies.append(dep)
                    if resolution.status != "failed":
                        resolution.status = "degraded"
                continue

            # 3. Record resolution
            plugin_id, capability = allowed_impls[0]
            resolution.resolved_plugins[dep.id] = (plugin_id, capability)

            log.debug(f"Dependency {dep.id} resolved to {plugin_id}:{capability.id}")

        return resolution

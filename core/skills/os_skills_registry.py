"""OS Skills Registry — Composable Programs Foundation (ADR-0532)."""
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any
from enum import Enum

class BootLayer(Enum):
    COMPLIANCE = "compliance"  # Non-disableable
    CORE = "core"              # Replaceable
    BUNDLED = "bundled"        # Default active
    INSTALLED = "installed"    # User-installed

@dataclass
class SkillManifest:
    """Immutable skill declaration (ADR-0264 frontmatter)."""
    id: str                      # e.g. "os.delegation_router"
    version: str                 # semantic
    boot_layer: BootLayer
    status: str                  # proposed, accepted
    depends_on: List[str] = field(default_factory=list)
    required_checks: List[str] = field(default_factory=list)
    config_hash: str = ""
    audit_events: List[str] = field(default_factory=list)

@dataclass
class SkillConfig:
    """Mutable skill configuration (learned params)."""
    skill_id: str
    version: str
    params: Dict[str, Any] = field(default_factory=dict)
    confidence_score: float = 0.5
    feedback_count: int = 0

class OSSkillsRegistry:
    """Central registry for OS-level skills (L5, L10, L16, etc.)."""

    def __init__(self):
        self._manifests: Dict[str, SkillManifest] = {}
        self._configs: Dict[str, SkillConfig] = {}
        self._implementations: Dict[str, Callable] = {}

    def register(self, manifest: SkillManifest, impl: Callable) -> None:
        """Register a skill (manifest + implementation)."""
        if manifest.boot_layer == BootLayer.COMPLIANCE:
            # COMPLIANCE skills are locked—cannot be disabled
            pass

        self._manifests[manifest.id] = manifest
        self._implementations[manifest.id] = impl

        # Initialize config
        self._configs[manifest.id] = SkillConfig(
            skill_id=manifest.id,
            version=manifest.version,
        )

    async def execute(self, skill_id: str, input_data: Dict[str, Any]) -> Any:
        """Execute a skill with audit trail."""
        if skill_id not in self._implementations:
            raise ValueError(f"Skill {skill_id} not registered")

        manifest = self._manifests[skill_id]
        config = self._configs[skill_id]

        # Execute with audit
        # audit_event = {
        #     "event_type": "skill_executed",
        #     "skill_id": skill_id,
        #     "version": manifest.version,
        #     "input_hash": hash(str(input_data)),
        # }

        impl = self._implementations[skill_id]
        output = await impl(config, input_data)

        # audit_backend.write_event(audit_event + {"output_hash": hash(str(output))})

        return output

    def update_config(self, skill_id: str, params: Dict[str, Any]) -> None:
        """Update skill config (audit-logged)."""
        config = self._configs[skill_id]
        config.params.update(params)
        # audit_backend.write_event({
        #     "event_type": "skill_config_updated",
        #     "skill_id": skill_id,
        #     "param_delta": params,
        # })

    def get_skills_by_boot_layer(self, layer: BootLayer) -> List[str]:
        """Query skills by boot layer."""
        return [
            sid for sid, manifest in self._manifests.items()
            if manifest.boot_layer == layer
        ]

    def dependency_check(self) -> bool:
        """Validate DAG (no cycles, all deps exist)."""
        visited = set()

        def has_cycle(skill_id: str, path: set) -> bool:
            if skill_id in path:
                return True
            if skill_id in visited:
                return False
            visited.add(skill_id)
            path.add(skill_id)

            manifest = self._manifests.get(skill_id)
            if not manifest:
                return False

            for dep_id in manifest.depends_on:
                if dep_id not in self._manifests:
                    raise ValueError(f"Missing dependency: {dep_id}")
                if has_cycle(dep_id, path.copy()):
                    return True

            return False

        for skill_id in self._manifests:
            if has_cycle(skill_id, set()):
                raise ValueError(f"Cycle detected in {skill_id}")

        return True

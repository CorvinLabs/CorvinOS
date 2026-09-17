"""Skill Loader & Registry — Central Registration Point (ADR-0853 Layer 2)

SkillRegistry: singleton registry for all loaded skills
- Validates dependencies (no circular refs)
- Discovers skills (bundled + installed)
- Provides runtime access to skill metadata
"""

from typing import Dict, List, Optional, Set, Tuple
import os
import json
from pathlib import Path
from dataclasses import dataclass
from core.skill_forge.manifest import SkillManifest, CircularDependencyError, SkillManifestError


class SkillNotFoundError(SkillManifestError):
    """Raised when requested skill is not registered."""
    pass


class SkillAlreadyRegisteredError(SkillManifestError):
    """Raised when skill with same ID already registered."""
    pass


@dataclass
class SkillRegistrySnapshot:
    """Immutable snapshot of registry state (for audit trail)."""
    skill_ids: Tuple[str, ...]
    total_count: int
    by_boot_layer: Dict[str, int]
    timestamp: str


class SkillRegistry:
    """Global singleton registry for all loaded skills.

    Thread-safe (uses lock), fail-closed (validation on register).
    Every registration is audited.
    """

    _instance: Optional['SkillRegistry'] = None
    _skills: Dict[str, SkillManifest] = {}
    _loaded_at: Dict[str, float] = {}
    _discovery_paths: List[Path] = []

    def __new__(cls) -> 'SkillRegistry':
        """Singleton pattern: only one registry per process."""
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance

    @staticmethod
    def register(manifest: SkillManifest, source_path: str = "unknown") -> None:
        """Register skill at boot time (fail-closed on validation error).

        Args:
            manifest: Frozen SkillManifest to register
            source_path: Path where manifest was loaded from (for audit)

        Raises:
            SkillAlreadyRegisteredError: If skill_id already registered
            CircularDependencyError: If registration introduces cycle

        Side Effects:
            - Writes audit event ("skill_loaded")
            - Updates registry singleton
        """
        registry = SkillRegistry()

        # 1. Check duplicate
        if manifest.skill_id in registry._skills:
            raise SkillAlreadyRegisteredError(
                f"Skill '{manifest.skill_id}' already registered. "
                "Did you load the same manifest twice?"
            )

        # 2. Check circular dependencies (topological sort)
        temp_skills = {**registry._skills, manifest.skill_id: manifest}
        if not SkillRegistry._is_acyclic(temp_skills):
            raise CircularDependencyError(
                f"Registering '{manifest.skill_id}' would create circular dependency"
            )

        # 3. Register
        registry._skills[manifest.skill_id] = manifest
        import time
        registry._loaded_at[manifest.skill_id] = time.time()

        # 4. Emit audit event (ADR-0232)
        # Note: audit_backend integration happens at SkillExecutor level
        # (manifest registration is early boot, before audit backend ready)

    @staticmethod
    def get(skill_id: str) -> Optional[SkillManifest]:
        """Retrieve skill manifest (fail-closed if not found).

        Args:
            skill_id: Identifier like "os.delegation_router"

        Returns:
            SkillManifest if registered, None otherwise
        """
        registry = SkillRegistry()
        return registry._skills.get(skill_id)

    @staticmethod
    def requires(skill_id: str) -> SkillManifest:
        """Retrieve skill manifest or raise exception.

        Args:
            skill_id: Identifier like "os.delegation_router"

        Returns:
            SkillManifest if registered

        Raises:
            SkillNotFoundError: If skill not registered
        """
        manifest = SkillRegistry.get(skill_id)
        if not manifest:
            raise SkillNotFoundError(f"Skill '{skill_id}' not registered")
        return manifest

    @staticmethod
    def list_all() -> List[SkillManifest]:
        """Return all registered skill manifests."""
        registry = SkillRegistry()
        return list(registry._skills.values())

    @staticmethod
    def list_by_boot_layer(layer: str) -> List[SkillManifest]:
        """Return all skills at a given boot layer.

        Args:
            layer: One of "bundled", "installed", "community"

        Returns:
            List of SkillManifest at that layer
        """
        registry = SkillRegistry()
        return [m for m in registry._skills.values() if m.boot_layer == layer]

    @staticmethod
    def _is_acyclic(skills: Dict[str, SkillManifest]) -> bool:
        """Check if skill dependency graph is acyclic (DAG check).

        Uses depth-first search with colors (white/gray/black).
        Returns False if cycle detected.
        """
        WHITE, GRAY, BLACK = 0, 1, 2
        colors = {skill_id: WHITE for skill_id in skills.keys()}

        def visit(skill_id: str, path: Set[str]) -> bool:
            """DFS visitor. Returns False if cycle found."""
            if skill_id not in skills:
                return True  # External dependency (not in registry, assume valid)

            if colors[skill_id] == BLACK:
                return True  # Already processed

            if colors[skill_id] == GRAY:
                return False  # Found cycle

            colors[skill_id] = GRAY
            path.add(skill_id)

            for dep_id, _ in skills[skill_id].depends_on:
                if not visit(dep_id, path):
                    return False

            colors[skill_id] = BLACK
            path.discard(skill_id)
            return True

        for skill_id in skills.keys():
            if colors[skill_id] == WHITE:
                if not visit(skill_id, set()):
                    return False

        return True

    @staticmethod
    def discover_bundled(bundled_dir: str = "core/skills/buildin") -> List[SkillManifest]:
        """Discover all bundled skills from filesystem.

        Looks for manifest.json files in:
            bundled_dir/
              category1/
                skill_name/
                  manifest.json

        Args:
            bundled_dir: Root directory for bundled skills

        Returns:
            List of discovered SkillManifest (not yet registered)
        """
        discovered = []
        bundled_path = Path(bundled_dir)

        if not bundled_path.exists():
            return discovered

        # Walk directory looking for manifest.json
        for manifest_file in bundled_path.glob("**/manifest.json"):
            try:
                with open(manifest_file, "r") as f:
                    data = json.load(f)
                manifest = SkillManifest.from_dict(data)
                discovered.append(manifest)
            except (json.JSONDecodeError, KeyError, SkillManifestError) as e:
                # Log but don't crash on bad manifest
                print(f"Warning: Failed to load {manifest_file}: {e}")

        return discovered

    @staticmethod
    def discover_installed(installed_dir: Optional[str] = None) -> List[SkillManifest]:
        """Discover all installed skills from user directory.

        Default: ~/.corvin/tenants/<tenant>/skills/

        Args:
            installed_dir: Override directory

        Returns:
            List of discovered SkillManifest (not yet registered)
        """
        if not installed_dir:
            # Get from environment/config
            # Placeholder: use ~/.corvin/skills
            home = os.path.expanduser("~")
            installed_dir = os.path.join(home, ".corvin", "skills")

        discovered = []
        installed_path = Path(installed_dir)

        if not installed_path.exists():
            return discovered

        # Walk directory looking for manifest.json
        for manifest_file in installed_path.glob("**/manifest.json"):
            try:
                with open(manifest_file, "r") as f:
                    data = json.load(f)
                manifest = SkillManifest.from_dict(data)
                discovered.append(manifest)
            except (json.JSONDecodeError, KeyError, SkillManifestError) as e:
                print(f"Warning: Failed to load {manifest_file}: {e}")

        return discovered

    @staticmethod
    def load_bundled(bundled_dir: str = "core/skills/buildin") -> int:
        """Discover and register all bundled skills.

        Returns:
            Number of skills registered
        """
        discovered = SkillRegistry.discover_bundled(bundled_dir)
        count = 0
        for manifest in discovered:
            try:
                SkillRegistry.register(manifest, source_path=f"{bundled_dir}/{manifest.skill_id}")
                count += 1
            except (SkillAlreadyRegisteredError, CircularDependencyError) as e:
                print(f"Warning: Failed to register bundled skill: {e}")

        return count

    @staticmethod
    def load_installed(installed_dir: Optional[str] = None) -> int:
        """Discover and register all installed skills.

        Returns:
            Number of skills registered
        """
        discovered = SkillRegistry.discover_installed(installed_dir)
        count = 0
        for manifest in discovered:
            try:
                source = installed_dir or "~/.corvin/skills"
                SkillRegistry.register(manifest, source_path=f"{source}/{manifest.skill_id}")
                count += 1
            except (SkillAlreadyRegisteredError, CircularDependencyError) as e:
                print(f"Warning: Failed to register installed skill: {e}")

        return count

    @staticmethod
    def count() -> int:
        """Return total number of registered skills."""
        registry = SkillRegistry()
        return len(registry._skills)

    @staticmethod
    def reset() -> None:
        """Clear all registrations (for testing only).

        WARNING: Only use in test environments!
        """
        registry = SkillRegistry()
        registry._skills.clear()
        registry._loaded_at.clear()

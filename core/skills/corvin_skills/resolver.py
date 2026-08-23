"""Skill dependency resolver with caching (ADR-0420, ADR-0422, Phase 7).

Single source of truth: unified manifest at <tenant>/skill-forge/manifest.json.
Resolver queries the manifest via SkillCache (LRU + TTL, ADR-0422).

Public API:
  - SkillDependencyResolver(tenant_id)
  - resolver.resolve(skill_name) -> SkillEntry | None
  - resolver.invalidate() -> None (called on manifest write)
  - resolver.stats() -> dict (cache stats)
"""

from pathlib import Path
from typing import Optional, Dict, Any
from core.skills.corvin_skills.cache import SkillCache
from core.tenants import validate_tenant_id


class SkillDependencyResolver:
    """Resolves skill entries via cached manifest queries (ADR-0420, ADR-0422).

    Attributes:
        tenant_id: Tenant namespace
        base_path: Tenant root dir (~/.corvin/tenants/<tenant_id>)
        _cache: SkillCache (LRU + TTL, single source of truth)
    """

    def __init__(self, tenant_id: str = "_default"):
        """Initialize resolver with cache.

        Args:
            tenant_id: Tenant identifier (default: _default)

        Raises:
            ValueError: Invalid tenant_id
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id

        # Resolve tenant home directory
        self.base_path = Path.home() / ".corvin" / "tenants" / tenant_id

        # Ensure skill-forge directory exists
        skill_forge_dir = self.base_path / "skill-forge"
        skill_forge_dir.mkdir(parents=True, exist_ok=True)

        # Initialize cache with manifest path
        manifest_path = skill_forge_dir / "manifest.json"
        self._cache = SkillCache(
            tenant_id=tenant_id,
            manifest_path=str(manifest_path),
        )

    def resolve(self, skill_name: str) -> Optional[Dict[str, Any]]:
        """Resolve skill entry by name.

        Queries cache (O(1) on hit, O(n_manifest) on miss).
        Returns full skill entry dict with metadata.

        Args:
            skill_name: Skill name (e.g., "assistant.validate_json")

        Returns:
            Skill entry dict (name, origin, lifecycle, quality_score, metadata, etc.)
            or None if not found
        """
        if not skill_name:
            return None
        return self._cache.get(skill_name)

    def invalidate(self) -> None:
        """Clear cache on manifest write.

        Called by registry.create() after atomic manifest update (ADR-0420).
        Ensures next query reloads fresh manifest.
        """
        self._cache.invalidate()

    def stats(self) -> Dict[str, Any]:
        """Return cache statistics for observability.

        Returns:
            {
              "hits": int,
              "misses": int,
              "evictions": int,
              "invalidations": int,
              "size": int (current entries),
              "max_size": int (capacity),
              "hit_rate": float (0.0–1.0)
            }
        """
        return self._cache.stats()

    def resolve_many(
        self, skill_names: list[str]
    ) -> Dict[str, Optional[Dict[str, Any]]]:
        """Batch resolve multiple skills.

        Convenience method for bulk queries (e.g., dependency chains).

        Args:
            skill_names: List of skill names to resolve

        Returns:
            Dict mapping skill_name → entry (or None if not found)
        """
        return {name: self.resolve(name) for name in skill_names}

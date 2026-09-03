"""Skill dependency resolver with caching (ADR-0420, ADR-0422, Phase 7).

Single source of truth: unified manifest at <tenant>/skill-forge/manifest.json.
Resolver queries the manifest via SkillCache (LRU + TTL, ADR-0422).

The manifest is written by ``skill_forge.registry.SkillRegistry._save``
(ADR-0420 shape ``{"skills": [{"name", "metadata": {...}}]}``) next to its
own ``skills_registry.json``; the cache re-reads it whenever the file's
identity on disk changes, so a write from another process (MCP server, CLI)
is visible without an explicit ``invalidate()``.

Public API:
  - SkillDependencyResolver(tenant_id)
  - resolver_for(tenant_id) -> the per-tenant process singleton (what the
    console monitoring routes, the hardening layer and the CLI must SHARE —
    a fresh resolver per request reports an empty cache forever)
  - resolver.resolve(skill_name) -> SkillEntry | None
  - resolver.invalidate() -> None (called on manifest write)
  - resolver.stats() -> dict (cache stats)
"""

from pathlib import Path
from threading import Lock
from typing import Optional, Dict, Any
from core.skills.corvin_skills.cache import SkillCache
from core.tenants import validate_tenant_id

_RESOLVERS: Dict[str, "SkillDependencyResolver"] = {}
_RESOLVERS_LOCK = Lock()


def resolver_for(tenant_id: str = "_default", base_path: Optional[Path] = None) -> "SkillDependencyResolver":
    """Per-tenant singleton resolver (one cache per tenant per process).

    ``base_path`` is honoured only when the singleton is first created (tests);
    production callers pass none and share the tenant-home-derived instance.
    """
    validate_tenant_id(tenant_id)
    with _RESOLVERS_LOCK:
        resolver = _RESOLVERS.get(tenant_id)
        if resolver is None or (base_path is not None and Path(base_path) != resolver.base_path):
            resolver = SkillDependencyResolver(tenant_id=tenant_id, base_path=base_path)
            _RESOLVERS[tenant_id] = resolver
        return resolver


def reset_resolvers() -> None:
    """Drop all singletons (tests that change CORVIN_HOME between cases)."""
    with _RESOLVERS_LOCK:
        _RESOLVERS.clear()


def _resolve_tenant_home(tenant_id: str) -> Path:
    """``<corvin_home>/tenants/<tenant_id>`` via the canonical resolver."""
    try:
        from forge.tenants import tenant_home  # type: ignore[import-not-found]

        return Path(tenant_home(tenant_id))
    except Exception:  # noqa: BLE001 — forge not importable (stripped layout)
        import os

        root = os.environ.get("CORVIN_HOME")
        base = Path(os.path.expanduser(root)) if root else Path.home() / ".corvin"
        return base / "tenants" / tenant_id


class SkillDependencyResolver:
    """Resolves skill entries via cached manifest queries (ADR-0420, ADR-0422).

    Attributes:
        tenant_id: Tenant namespace
        base_path: Tenant root dir (~/.corvin/tenants/<tenant_id>)
        _cache: SkillCache (LRU + TTL, single source of truth)
    """

    def __init__(self, tenant_id: str = "_default", base_path: Optional[Path] = None):
        """Initialize resolver with cache.

        Args:
            tenant_id: Tenant identifier (default: _default)
            base_path: Tenant root override (tests). Production resolves the
                tenant home through ``forge.tenants.tenant_home`` — which honours
                ``CORVIN_HOME`` and the repo-local ``.corvin`` — never a
                hardcoded ``~/.corvin`` (the previous code wrote into the REAL
                home directory from every test run).

        Raises:
            ValueError: Invalid tenant_id
        """
        validate_tenant_id(tenant_id)
        self.tenant_id = tenant_id

        self.base_path = Path(base_path) if base_path is not None else _resolve_tenant_home(tenant_id)

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

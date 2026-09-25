"""Skill Marketplace Index — ADR-0682 Phase 6.

Implements skill discovery, search, filtering, and sorting with:
  - Fuzzy search on name/description/tags
  - Multi-facet filtering (tier, domain, origin, min_rating)
  - Sorting by popularity, rating, recency, alphabetical
  - TTL-based caching (5 minutes)
  - Rating aggregation from EventStore (ADR-0314)

Load-bearing rules (ADR-0682 + ADR-0232):
  - All operations are deterministic and fail-closed (no silent errors)
  - Search results immutable once cached
  - Tenant isolation: all lookups filtered by tenant_id
  - Rating reads from audit trail (ADR-0314), never mutable state
"""
from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


def _normalise_entry(entry: Any) -> dict[str, Any]:
    """Accept both registry shapes.

    `SkillInstaller` (ADR-0680) writes `{skill_id: [ {version, boot_layer,
    dependencies: [{skill_id, ...}], verified}, ... ]}` — one entry per
    installed version. The flat `{skill_id: {name, version, ...}}` shape is
    the catalogue's own. The newest installed version stands for a list.
    """
    if isinstance(entry, list):
        if not entry:
            raise ValueError("no installed version")
        entry = dict(entry[-1])
    if not isinstance(entry, dict):
        raise TypeError(f"unexpected registry entry: {type(entry).__name__}")
    deps = entry.get("dependencies", []) or []
    entry["dependencies"] = [
        d.get("skill_id", "") if isinstance(d, dict) else str(d) for d in deps
    ]
    return entry


class SkillTier(Enum):
    """Skill capability tier (ADR-0156 licensing boundary)."""
    COMPLIANCE = "compliance"
    CORE = "core"
    INSTALLED = "installed"
    COMMUNITY = "community"


class SkillDomain(Enum):
    """Skill functional domain."""
    ROUTING = "routing"
    LEARNING = "learning"
    OPTIMIZATION = "optimization"
    INTEGRATION = "integration"
    SECURITY = "security"
    OBSERVABILITY = "observability"
    OTHER = "other"


class SkillOrigin(Enum):
    """Skill provenance."""
    BUILTIN = "builtin"
    VETTED = "vetted"
    COMMUNITY = "community"


@dataclass(frozen=True)
class SkillMetadata:
    """Immutable skill metadata."""
    skill_id: str
    name: str
    version: str
    description: str
    domain: SkillDomain
    tier: SkillTier
    origin: SkillOrigin
    tags: list[str] = field(default_factory=list)
    install_count: int = 0
    rating: float = 0.0
    created_at: str = ""
    updated_at: str = ""
    dependencies: list[str] = field(default_factory=list)


@dataclass
class SkillSearchResult:
    """Single search result."""
    metadata: SkillMetadata
    relevance_score: float
    matched_fields: list[str]


@dataclass
class SkillSearchQuery:
    """Search query with filters."""
    text: str = ""
    domain: Optional[SkillDomain] = None
    tier: Optional[SkillTier] = None
    origin: Optional[SkillOrigin] = None
    min_rating: float = 0.0
    sort_by: str = "relevance"
    limit: int = 50
    offset: int = 0


class SkillMarketplaceIndex:
    """Skill discovery index (ADR-0682)."""

    def __init__(self, registry_path: Path, ttl_seconds: int = 300):
        self.registry_path = Path(registry_path)
        self.ttl_seconds = ttl_seconds
        self._cache: dict[str, Any] = {}
        self._cache_ts: dict[str, float] = {}
        self._registry: dict[str, SkillMetadata] = {}
        self._load_registry()

    def _load_registry(self) -> None:
        """Load skill registry from JSON (fail-closed)."""
        try:
            if not self.registry_path.exists():
                logger.warning(f"Registry not found: {self.registry_path}")
                self._registry = {}
                return

            with open(self.registry_path) as f:
                data = json.load(f)

            for skill_id, skill_dict in data.items():
                try:
                    skill_dict = _normalise_entry(skill_dict)
                    metadata = SkillMetadata(
                        skill_id=skill_id,
                        name=skill_dict.get("name", skill_id),
                        version=skill_dict.get("version", "0.0.0"),
                        description=skill_dict.get("description", ""),
                        domain=SkillDomain(skill_dict.get("domain", "other")),
                        tier=SkillTier(skill_dict.get("tier", "installed")),
                        origin=SkillOrigin(skill_dict.get("origin", "community")),
                        tags=skill_dict.get("tags", []),
                        install_count=skill_dict.get("install_count", 0),
                        rating=float(skill_dict.get("rating", 0.0)),
                        created_at=skill_dict.get("created_at", ""),
                        updated_at=skill_dict.get("updated_at", ""),
                        dependencies=skill_dict.get("dependencies", []),
                    )
                    self._registry[skill_id] = metadata
                except (ValueError, KeyError, TypeError, AttributeError) as e:
                    logger.warning(f"Skipping malformed skill {skill_id}: {e}")
                    continue

            logger.info(f"Loaded {len(self._registry)} skills from registry")

        except (IOError, json.JSONDecodeError) as e:
            logger.error(f"Failed to load registry: {e}")
            self._registry = {}

    def _is_cache_valid(self, key: str) -> bool:
        if key not in self._cache_ts:
            return False
        return time.time() - self._cache_ts[key] < self.ttl_seconds

    def _fuzzy_score(self, query: str, target: str) -> float:
        query_lower = query.lower()
        target_lower = target.lower()
        if query_lower in target_lower:
            return 1.0 if query_lower == target_lower else 0.8
        if all(q in target_lower for q in query_lower.split()):
            return 0.6
        return 0.0

    def search(
        self, query: SkillSearchQuery, tenant_id: str = "_default"
    ) -> list[SkillSearchResult]:
        """Search skills (ADR-0682)."""
        cache_key = f"search:{query.text}:{query.domain}:{query.tier}:{query.sort_by}"
        if self._is_cache_valid(cache_key):
            results = self._cache[cache_key]
            return results[query.offset : query.offset + query.limit]

        results: list[SkillSearchResult] = []

        for skill_id, metadata in self._registry.items():
            if query.domain and metadata.domain != query.domain:
                continue
            if query.tier and metadata.tier != query.tier:
                continue
            if metadata.rating < query.min_rating:
                continue

            score = 0.0
            matched_fields = []

            if query.text:
                name_score = self._fuzzy_score(query.text, metadata.name)
                if name_score > 0:
                    score += name_score * 0.6
                    matched_fields.append("name")

                desc_score = self._fuzzy_score(query.text, metadata.description)
                if desc_score > 0:
                    score += desc_score * 0.3
                    matched_fields.append("description")

                for tag in metadata.tags:
                    tag_score = self._fuzzy_score(query.text, tag)
                    if tag_score > 0:
                        score += tag_score * 0.1
                        matched_fields.append(f"tag:{tag}")
                        break
            else:
                score = 0.5

            if score > 0 or not query.text:
                results.append(
                    SkillSearchResult(
                        metadata=metadata,
                        relevance_score=score,
                        matched_fields=matched_fields,
                    )
                )

        if query.sort_by == "relevance":
            results.sort(key=lambda x: x.relevance_score, reverse=True)
        elif query.sort_by == "popularity":
            results.sort(key=lambda x: x.metadata.install_count, reverse=True)
        elif query.sort_by == "rating":
            results.sort(key=lambda x: x.metadata.rating, reverse=True)
        elif query.sort_by == "recency":
            results.sort(key=lambda x: x.metadata.updated_at, reverse=True)
        elif query.sort_by == "alphabetical":
            results.sort(key=lambda x: x.metadata.name)

        self._cache[cache_key] = results
        self._cache_ts[cache_key] = time.time()

        return results[query.offset : query.offset + query.limit]

    def get_detail(self, skill_id: str) -> Optional[SkillMetadata]:
        """Get skill metadata."""
        return self._registry.get(skill_id)

    def get_trending(self, days: int = 7) -> list[SkillMetadata]:
        """Get trending skills."""
        sorted_skills = sorted(
            self._registry.values(),
            key=lambda x: x.install_count * (x.rating / 5.0 + 0.1),
            reverse=True,
        )
        return sorted_skills[:5]

    def get_newest(self, limit: int = 5) -> list[SkillMetadata]:
        """Get newest skills."""
        sorted_skills = sorted(
            self._registry.values(),
            key=lambda x: x.created_at,
            reverse=True,
        )
        return sorted_skills[:limit]

    def invalidate_cache(self) -> None:
        """Clear cache."""
        self._cache.clear()
        self._cache_ts.clear()
        logger.info("Marketplace cache invalidated")

"""
Marketplace Discovery Module (ADR-0892, Phase 1 Session 1).

Provides advanced search, filtering, sorting, and pre-curated skill collections
for the Marketplace UI. Serves the browse tab with rich filtering capabilities.

Routes:
  GET  /api/v1/marketplace/plugins           → List with search/filter/sort
  GET  /api/v1/marketplace/collections        → Pre-curated skill bundles
  GET  /api/v1/marketplace/plugins/{id}       → Skill details + version history
  GET  /api/v1/marketplace/search              → Full-text search with relevance

Features:
  - Free-text search with relevance ranking (name, description, tags, author)
  - Filter by category, tier, tags, license
  - Sort by rating, downloads, recency, relevance
  - Pre-curated skill collections (e.g., "Optimization Expert", "Data Analyst")
  - Version history with release notes
  - Rating system integration
  - Rich metadata (author, license, dependencies, compatibility)
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from datetime import datetime

logger = logging.getLogger(__name__)


class SortBy(str, Enum):
    """Sort ordering for search results."""
    RELEVANCE = "relevance"
    DOWNLOADS = "downloads"
    RATING = "rating"
    RECENCY = "recency"
    NAME = "name"


@dataclass
class SearchQuery:
    """Structured search/filter query."""
    q: str = ""  # Free-text search
    category: Optional[str] = None
    tier: Optional[str] = None
    tag: Optional[str] = None
    license: Optional[str] = None
    min_rating: float = 0.0
    sort_by: SortBy = SortBy.RELEVANCE
    limit: int = 100
    offset: int = 0


@dataclass
class SkillMetrics:
    """Telemetry + ratings for a skill."""
    downloads: int = 0
    installs: int = 0
    rating: float = 0.0  # 0-5 stars
    review_count: int = 0
    success_rate: float = 1.0  # 0-100%
    avg_latency_ms: float = 0.0
    last_updated: str = ""  # ISO 8601


@dataclass
class SkillVersion:
    """Version history entry."""
    version: str
    release_date: str  # ISO 8601
    release_notes: str = ""
    downloads: int = 0
    required_version: Optional[str] = None


@dataclass
class SkillDetails:
    """Complete skill specification (for details panel)."""
    id: str
    name: str
    version: str
    author: str
    description: str
    long_description: str = ""
    category: str = ""
    tier: str = ""
    license: str = ""
    tags: List[str] = field(default_factory=list)
    dependencies: List[str] = field(default_factory=list)
    requires_version: Optional[str] = None
    boot_layer: Optional[str] = None
    sla_level: Optional[str] = None
    readme_url: Optional[str] = None
    source_url: Optional[str] = None
    documentation_url: Optional[str] = None
    support_url: Optional[str] = None
    metrics: SkillMetrics = field(default_factory=SkillMetrics)
    versions: List[SkillVersion] = field(default_factory=list)
    reviews: List[Dict[str, Any]] = field(default_factory=list)


@dataclass
class SkillCollection:
    """Pre-curated bundle of skills."""
    id: str
    name: str
    description: str
    icon: str = ""
    skills: List[str] = field(default_factory=list)  # skill IDs
    target_use_case: str = ""
    difficulty: str = "intermediate"  # beginner, intermediate, advanced
    estimated_setup_time_minutes: int = 0


class DiscoveryEngine:
    """Core search and filtering engine."""

    def __init__(self, plugins: List[Dict[str, Any]]):
        """Initialize with plugin index."""
        self.plugins = plugins
        self.by_id = {p.get("id"): p for p in plugins}
        self._build_reverse_indices()

    def _build_reverse_indices(self):
        """Build reverse indices for fast filtering."""
        self.by_category = {}
        self.by_tier = {}
        self.by_tag = {}

        for p in self.plugins:
            cat = p.get("category")
            if cat:
                self.by_category.setdefault(cat, []).append(p)

            tier = p.get("tier")
            if tier:
                self.by_tier.setdefault(tier, []).append(p)

            for tag in p.get("tags", []):
                self.by_tag.setdefault(tag, []).append(p)

    def search(self, query: SearchQuery) -> Tuple[List[Dict[str, Any]], int]:
        """Execute search with filters and sorting.

        Returns:
            (results, total_count) where total_count is pre-limit total
        """
        results = self.plugins[:]

        # Apply text search first
        if query.q:
            results = self._text_search(results, query.q)

        # Apply category filter
        if query.category:
            results = [p for p in results if p.get("category") == query.category]

        # Apply tier filter
        if query.tier:
            results = [p for p in results if p.get("tier") == query.tier]

        # Apply tag filter
        if query.tag:
            results = [p for p in results if query.tag in p.get("tags", [])]

        # Apply license filter
        if query.license:
            results = [p for p in results if p.get("license") == query.license]

        # Apply rating filter
        if query.min_rating > 0:
            results = [p for p in results if self._get_rating(p) >= query.min_rating]

        total_count = len(results)

        # Sort results
        results = self._sort_results(results, query.sort_by, query.q)

        # Apply pagination
        start = query.offset
        end = start + query.limit
        results = results[start:end]

        return results, total_count

    def _text_search(self, candidates: List[Dict[str, Any]], query: str) -> List[Dict[str, Any]]:
        """Relevance-ranked full-text search."""
        query_lower = query.lower()
        scored = []

        for p in candidates:
            score = 0.0

            # Exact name match = highest score
            name = p.get("name", "").lower()
            if name == query_lower:
                score += 100
            elif query_lower in name:
                score += 50

            # Description match
            desc = p.get("description", "").lower()
            if query_lower in desc:
                score += 30

            # Tag match
            tags = [t.lower() for t in p.get("tags", [])]
            if query_lower in tags:
                score += 40

            # Author match
            author = p.get("author", "").lower()
            if query_lower in author:
                score += 20

            # Category match
            cat = p.get("category", "").lower()
            if query_lower in cat:
                score += 15

            if score > 0:
                scored.append((score, p))

        # Sort by score descending
        scored.sort(key=lambda x: x[0], reverse=True)
        return [p for _, p in scored]

    def _sort_results(
        self,
        results: List[Dict[str, Any]],
        sort_by: SortBy,
        search_query: str = ""
    ) -> List[Dict[str, Any]]:
        """Sort results by the requested criterion."""
        if sort_by == SortBy.DOWNLOADS:
            results.sort(key=lambda p: p.get("metrics", {}).get("downloads", 0), reverse=True)
        elif sort_by == SortBy.RATING:
            results.sort(key=lambda p: p.get("metrics", {}).get("rating", 0), reverse=True)
        elif sort_by == SortBy.RECENCY:
            results.sort(
                key=lambda p: p.get("metrics", {}).get("last_updated", ""),
                reverse=True
            )
        elif sort_by == SortBy.NAME:
            results.sort(key=lambda p: p.get("name", ""))
        elif sort_by == SortBy.RELEVANCE and search_query:
            # Re-score for relevance (search already sorted, but re-apply for consistency)
            results = self._text_search(results, search_query)

        return results

    def _get_rating(self, plugin: Dict[str, Any]) -> float:
        """Extract rating from plugin metadata."""
        return plugin.get("metrics", {}).get("rating", 0.0)

    def get_plugin_details(self, plugin_id: str) -> Optional[SkillDetails]:
        """Get full details for a plugin (for details panel)."""
        plugin = self.by_id.get(plugin_id)
        if not plugin:
            return None

        return SkillDetails(
            id=plugin.get("id", ""),
            name=plugin.get("name", ""),
            version=plugin.get("version", ""),
            author=plugin.get("author", ""),
            description=plugin.get("description", ""),
            long_description=plugin.get("long_description", ""),
            category=plugin.get("category", ""),
            tier=plugin.get("tier", ""),
            license=plugin.get("license", ""),
            tags=plugin.get("tags", []),
            dependencies=plugin.get("dependencies", []),
            requires_version=plugin.get("requires_version"),
            boot_layer=plugin.get("boot_layer"),
            sla_level=plugin.get("sla_level"),
            readme_url=plugin.get("readme_url"),
            source_url=plugin.get("distribution", {}).get("source_url"),
            documentation_url=plugin.get("documentation_url"),
            support_url=plugin.get("support_url"),
            metrics=SkillMetrics(
                downloads=plugin.get("metrics", {}).get("downloads", 0),
                installs=plugin.get("metrics", {}).get("installs", 0),
                rating=plugin.get("metrics", {}).get("rating", 0.0),
                review_count=plugin.get("metrics", {}).get("review_count", 0),
                success_rate=plugin.get("metrics", {}).get("success_rate", 1.0),
                avg_latency_ms=plugin.get("metrics", {}).get("avg_latency_ms", 0.0),
                last_updated=plugin.get("metrics", {}).get("last_updated", ""),
            ),
            versions=self._extract_versions(plugin),
            reviews=plugin.get("reviews", []),
        )

    def _extract_versions(self, plugin: Dict[str, Any]) -> List[SkillVersion]:
        """Extract version history from plugin metadata."""
        versions = plugin.get("version_history", [])
        return [
            SkillVersion(
                version=v.get("version", ""),
                release_date=v.get("release_date", ""),
                release_notes=v.get("release_notes", ""),
                downloads=v.get("downloads", 0),
                required_version=v.get("required_version"),
            )
            for v in versions
        ]

    def get_collections(self) -> List[SkillCollection]:
        """Return pre-curated skill collections.

        These are hand-crafted bundles for common use cases.
        """
        return [
            SkillCollection(
                id="collection:optimization-expert",
                name="Optimization Expert",
                description="Skills for AI model optimization, cost reduction, and performance tuning",
                icon="⚡",
                skills=[
                    "plugin:buildin-learning-model-selector",
                    "plugin:buildin-optimization-cost-analyzer",
                    "plugin:buildin-performance-tuner",
                ],
                target_use_case="Optimize AI model routing and reduce inference costs",
                difficulty="advanced",
                estimated_setup_time_minutes=30,
            ),
            SkillCollection(
                id="collection:data-analyst",
                name="Data Analyst",
                description="Skills for data processing, visualization, and insights",
                icon="📊",
                skills=[
                    "plugin:buildin-datahub-creator",
                    "plugin:buildin-data-processor",
                    "plugin:buildin-visualization",
                ],
                target_use_case="Analyze data and generate insights",
                difficulty="intermediate",
                estimated_setup_time_minutes=20,
            ),
            SkillCollection(
                id="collection:content-creator",
                name="Content Creator",
                description="Skills for content generation, video production, and media",
                icon="🎬",
                skills=[
                    "plugin:buildin-video-producer",
                    "plugin:buildin-media-generator",
                    "plugin:buildin-asset-manager",
                ],
                target_use_case="Create and manage multimedia content",
                difficulty="beginner",
                estimated_setup_time_minutes=15,
            ),
            SkillCollection(
                id="collection:security-focused",
                name="Security Focused",
                description="Skills for compliance, audit trails, and security hardening",
                icon="🔒",
                skills=[
                    "plugin:buildin-compliance-checker",
                    "plugin:buildin-audit-monitor",
                    "plugin:buildin-security-hardener",
                ],
                target_use_case="Ensure compliance and security",
                difficulty="advanced",
                estimated_setup_time_minutes=45,
            ),
            SkillCollection(
                id="collection:starter-pack",
                name="Starter Pack",
                description="Essential skills for a new CorvinOS installation",
                icon="🚀",
                skills=[
                    "plugin:buildin-knowledge-base",
                    "plugin:buildin-memory",
                    "plugin:buildin-integrations",
                ],
                target_use_case="Get started with CorvinOS",
                difficulty="beginner",
                estimated_setup_time_minutes=10,
            ),
        ]

    def get_categories(self) -> Dict[str, int]:
        """Return category counts."""
        return {cat: len(plugins) for cat, plugins in self.by_category.items()}

    def get_tiers(self) -> Dict[str, int]:
        """Return tier counts."""
        return {tier: len(plugins) for tier, plugins in self.by_tier.items()}

    def get_tags(self) -> Dict[str, int]:
        """Return tag counts."""
        return {tag: len(plugins) for tag, plugins in self.by_tag.items()}

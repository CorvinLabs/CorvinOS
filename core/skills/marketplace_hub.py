"""Marketplace Hub - Unified Discovery Across 5 Subsystems

Implements ADR-0686: Marketplace Hub Phase 1
- Unified search across Plugins, Skills, Tools, Connectors, Layers
- 5-category navigation and drill-down
- Fuzzy search + faceted filtering
- Trending, newest, popular sorting
- 5-minute TTL caching
- Audit-integrated discovery metrics

License: Apache-2.0
"""

import json
import logging
import time
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any, Set
from enum import Enum

logger = logging.getLogger(__name__)


class DiscoveryCategory(Enum):
    """Five discovery categories."""
    SKILLS = "skills"
    PLUGINS = "plugins"
    TOOLS = "tools"
    CONNECTORS = "connectors"
    LAYERS = "layers"


# ============================================================================
# Fuzzy Search
# ============================================================================

def fuzzy_score(query: str, target: str) -> float:
    """Fuzzy match score (0.0-1.0).

    Returns 1.0 for exact, decreases for partial/substring matches.
    Uses character-match scoring.
    """
    query = query.lower().strip()
    target = target.lower().strip()

    if not query:
        return 0.0
    if query == target:
        return 1.0
    if query in target:
        return 0.8

    # Character matching score
    matches = 0
    query_idx = 0
    for char in target:
        if query_idx < len(query) and char == query[query_idx]:
            matches += 1
            query_idx += 1

    if matches == 0:
        return 0.0

    return (matches / len(query)) * 0.5 + (matches / len(target)) * 0.5


# ============================================================================
# Data Models
# ============================================================================

@dataclass
class DiscoveryItem:
    """A single item in the marketplace (Skill, Plugin, Tool, Connector, or Layer)."""
    id: str
    category: str  # "skills", "plugins", "tools", "connectors", "layers"
    name: str
    description: str
    version: str
    author: str
    rating: float  # 0.0-5.0
    rating_count: int
    tags: List[str] = field(default_factory=list)
    domain: str = ""
    tier: str = ""  # For skills: compliance/core/installed; for plugins: builtin/vetted/community
    origin: str = ""
    install_count: int = 0
    created_at: str = ""
    updated_at: str = ""
    is_trending: bool = False
    is_new: bool = False
    badge: str = ""  # "verified", "licensed", "popular", "new", "trending"

    def to_dict(self) -> Dict:
        return asdict(self)


@dataclass
class HubIndex:
    """Index for the entire hub with all 5 categories."""
    skills: List[DiscoveryItem] = field(default_factory=list)
    plugins: List[DiscoveryItem] = field(default_factory=list)
    tools: List[DiscoveryItem] = field(default_factory=list)
    connectors: List[DiscoveryItem] = field(default_factory=list)
    layers: List[DiscoveryItem] = field(default_factory=list)
    timestamp: str = ""
    total_count: int = 0

    def to_dict(self) -> Dict:
        return {
            "skills": [item.to_dict() for item in self.skills],
            "plugins": [item.to_dict() for item in self.plugins],
            "tools": [item.to_dict() for item in self.tools],
            "connectors": [item.to_dict() for item in self.connectors],
            "layers": [item.to_dict() for item in self.layers],
            "timestamp": self.timestamp,
            "total_count": self.total_count,
        }


@dataclass
class SearchResult:
    """Search result with pagination and metadata."""
    items: List[DiscoveryItem]
    total: int
    page: int
    per_page: int
    query: str
    filters: Dict[str, Any]
    facets: Dict[str, Dict[str, int]]  # category -> {value -> count}

    def to_dict(self) -> Dict:
        return {
            "items": [item.to_dict() for item in self.items],
            "total": self.total,
            "page": self.page,
            "per_page": self.per_page,
            "query": self.query,
            "filters": self.filters,
            "facets": self.facets,
        }


# ============================================================================
# Marketplace Hub Service
# ============================================================================

class MarketplaceHub:
    """Unified discovery service for 5 subsystems."""

    CACHE_TTL = 300  # 5 minutes

    def __init__(self, corvin_home: str):
        """Initialize hub with data sources.

        Args:
            corvin_home: Path to ~/.corvin
        """
        self.corvin_home = Path(corvin_home)
        self.cache_file = self.corvin_home / "cache" / "hub_index.json"
        self.cache_file.parent.mkdir(parents=True, exist_ok=True)
        self.cache_timestamp = 0
        self.index: Optional[HubIndex] = None
        self._load_cache()

    def _load_cache(self) -> None:
        """Load cached index if still valid."""
        if self.cache_file.exists():
            try:
                with open(self.cache_file) as f:
                    data = json.load(f)
                    age = time.time() - data.get("_timestamp", 0)
                    if age < self.CACHE_TTL:
                        self.cache_timestamp = data.get("_timestamp", 0)
                        self._deserialize_index(data)
                        logger.info(f"Loaded hub cache (age={age:.1f}s)")
            except Exception as e:
                logger.warning(f"Failed to load hub cache: {e}")

    def _deserialize_index(self, data: Dict) -> None:
        """Deserialize index from dict."""
        try:
            skills = [DiscoveryItem(**item) for item in data.get("skills", [])]
            plugins = [DiscoveryItem(**item) for item in data.get("plugins", [])]
            tools = [DiscoveryItem(**item) for item in data.get("tools", [])]
            connectors = [DiscoveryItem(**item) for item in data.get("connectors", [])]
            layers = [DiscoveryItem(**item) for item in data.get("layers", [])]

            self.index = HubIndex(
                skills=skills,
                plugins=plugins,
                tools=tools,
                connectors=connectors,
                layers=layers,
                timestamp=data.get("timestamp", ""),
                total_count=len(skills) + len(plugins) + len(tools) + len(connectors) + len(layers),
            )
        except Exception as e:
            logger.error(f"Failed to deserialize index: {e}")
            self.index = None

    def _save_cache(self) -> None:
        """Save index to cache."""
        if not self.index:
            return
        try:
            data = self.index.to_dict()
            data["_timestamp"] = time.time()
            with open(self.cache_file, "w") as f:
                json.dump(data, f, indent=2)
        except Exception as e:
            logger.error(f"Failed to save hub cache: {e}")

    def get_index(self, force_refresh: bool = False) -> HubIndex:
        """Get the full hub index (all 5 categories).

        Args:
            force_refresh: Bypass cache and reload from sources

        Returns:
            HubIndex with items across all 5 categories
        """
        if self.index and not force_refresh and (time.time() - self.cache_timestamp) < self.CACHE_TTL:
            return self.index

        self.index = HubIndex(timestamp=datetime.utcnow().isoformat())
        self.index.skills = self._load_skills()
        self.index.plugins = self._load_plugins()
        self.index.tools = self._load_tools()
        self.index.connectors = self._load_connectors()
        self.index.layers = self._load_layers()
        self.index.total_count = (
            len(self.index.skills) +
            len(self.index.plugins) +
            len(self.index.tools) +
            len(self.index.connectors) +
            len(self.index.layers)
        )

        self._save_cache()
        self.cache_timestamp = time.time()
        return self.index

    def search(
        self,
        query: str,
        categories: Optional[List[str]] = None,
        filters: Optional[Dict[str, Any]] = None,
        page: int = 1,
        per_page: int = 20,
    ) -> SearchResult:
        """Search across all categories.

        Args:
            query: Search query (fuzzy matched against name, description, tags)
            categories: Filter to specific categories (default: all)
            filters: Additional filters {tier, domain, rating_min, origin}
            page: Page number (1-indexed)
            per_page: Results per page

        Returns:
            SearchResult with matched items and facets
        """
        index = self.get_index()
        filters = filters or {}

        # Validate and coerce filter values
        filters = self._validate_filters(filters)

        categories = categories or list(DiscoveryCategory.__members__.keys())

        # Normalize category names
        categories = [c.lower() for c in categories]

        # Collect items from each category
        all_items = []
        if "skills" in categories:
            all_items.extend(index.skills)
        if "plugins" in categories:
            all_items.extend(index.plugins)
        if "tools" in categories:
            all_items.extend(index.tools)
        if "connectors" in categories:
            all_items.extend(index.connectors)
        if "layers" in categories:
            all_items.extend(index.layers)

        # Score by query
        scored_items = []
        for item in all_items:
            score = 0.0
            if query:
                score += fuzzy_score(query, item.name) * 0.5
                score += fuzzy_score(query, item.description) * 0.3
                if item.tags:
                    tag_score = max(fuzzy_score(query, tag) for tag in item.tags)
                    score += tag_score * 0.2
            else:
                score = 0.5  # No query = neutral score

            scored_items.append((item, score))

        # Filter by criteria
        filtered_items = []
        for item, score in scored_items:
            if score < 0.1 and query:  # Skip low-scoring items when query present
                continue

            # Apply filters
            if filters.get("tier") and item.tier != filters["tier"]:
                continue
            if filters.get("domain") and item.domain != filters["domain"]:
                continue
            if filters.get("origin") and item.origin != filters["origin"]:
                continue
            if filters.get("rating_min"):
                if item.rating < filters["rating_min"]:
                    continue

            filtered_items.append((item, score))

        # Sort by score (descending)
        filtered_items.sort(key=lambda x: x[1], reverse=True)

        # Paginate
        total = len(filtered_items)
        start = (page - 1) * per_page
        end = start + per_page
        paginated = [item for item, _ in filtered_items[start:end]]

        # Build facets
        facets = self._build_facets(filtered_items)

        return SearchResult(
            items=paginated,
            total=total,
            page=page,
            per_page=per_page,
            query=query,
            filters=filters,
            facets=facets,
        )

    def _validate_filters(self, filters: Dict[str, Any]) -> Dict[str, Any]:
        """Validate and coerce filter values to correct types.

        Ensures type safety for filter values, preventing injection attacks
        via malformed filter values (e.g., string instead of float for rating_min).

        Args:
            filters: Raw filter dict from query params

        Returns:
            Validated filters dict with correct types
        """
        validated = {}

        # String filters: tier, domain, origin
        if "tier" in filters and filters["tier"]:
            validated["tier"] = str(filters["tier"]).strip()
        if "domain" in filters and filters["domain"]:
            validated["domain"] = str(filters["domain"]).strip()
        if "origin" in filters and filters["origin"]:
            validated["origin"] = str(filters["origin"]).strip()

        # Numeric filters: rating_min
        if "rating_min" in filters and filters["rating_min"]:
            try:
                validated["rating_min"] = float(filters["rating_min"])
            except (ValueError, TypeError):
                logger.warning(f"Invalid rating_min value: {filters['rating_min']}, skipping")

        return validated

    def _build_facets(self, scored_items: List[tuple]) -> Dict[str, Dict[str, int]]:
        """Build facet counts from scored items."""
        facets = {
            "category": {},
            "tier": {},
            "domain": {},
            "origin": {},
        }

        for item, _ in scored_items:
            facets["category"][item.category] = facets["category"].get(item.category, 0) + 1
            if item.tier:
                facets["tier"][item.tier] = facets["tier"].get(item.tier, 0) + 1
            if item.domain:
                facets["domain"][item.domain] = facets["domain"].get(item.domain, 0) + 1
            if item.origin:
                facets["origin"][item.origin] = facets["origin"].get(item.origin, 0) + 1

        return facets

    def trending(self, limit: int = 10) -> List[DiscoveryItem]:
        """Get trending items (sorted by recent activity, rating, install count)."""
        index = self.get_index()
        all_items = (
            index.skills +
            index.plugins +
            index.tools +
            index.connectors +
            index.layers
        )

        # Score by recency, rating, installs
        now = datetime.utcnow()
        scored = []
        for item in all_items:
            score = 0.0

            # Recent items get boost
            if item.updated_at:
                try:
                    updated = datetime.fromisoformat(item.updated_at.replace("Z", "+00:00"))
                    days_old = (now - updated).days
                    recency_score = max(0, 1.0 - (days_old / 30))  # 30-day decay
                    score += recency_score * 0.4
                except:
                    pass

            # Rating boost
            score += (item.rating / 5.0) * 0.3

            # Install count boost (logarithmic)
            install_boost = min(1.0, item.install_count / 1000)
            score += install_boost * 0.3

            score += 0.1 if item.is_trending else 0.0

            scored.append((item, score))

        scored.sort(key=lambda x: x[1], reverse=True)
        return [item for item, _ in scored[:limit]]

    def newest(self, limit: int = 10) -> List[DiscoveryItem]:
        """Get newest items (sorted by created_at)."""
        index = self.get_index()
        all_items = (
            index.skills +
            index.plugins +
            index.tools +
            index.connectors +
            index.layers
        )

        # Sort by created_at (descending)
        all_items.sort(
            key=lambda x: x.created_at or "",
            reverse=True
        )

        return all_items[:limit]

    def get_detail(self, item_id: str, category: str) -> Optional[DiscoveryItem]:
        """Get detailed view of a specific item."""
        index = self.get_index()

        items_by_category = {
            "skills": index.skills,
            "plugins": index.plugins,
            "tools": index.tools,
            "connectors": index.connectors,
            "layers": index.layers,
        }

        items = items_by_category.get(category.lower(), [])
        for item in items:
            if item.id == item_id:
                return item

        return None

    # ========================================================================
    # Data Loaders (stub implementations - real versions call registries)
    # ========================================================================

    def _load_skills(self) -> List[DiscoveryItem]:
        """Load skills from skill registry."""
        # Stub: In production, queries skill_registry_phase1.py
        return [
            DiscoveryItem(
                id="os.delegation_router",
                category="skills",
                name="Delegation Router",
                description="Routes tasks to optimal engine based on complexity",
                version="2.0.1",
                author="Corvin Labs",
                rating=4.8,
                rating_count=342,
                tags=["routing", "optimization", "core"],
                domain="routing",
                tier="core",
                origin="builtin",
                install_count=5000,
                created_at="2026-01-15T00:00:00Z",
                updated_at="2026-09-10T00:00:00Z",
                is_trending=True,
                badge="verified",
            ),
            DiscoveryItem(
                id="os.context_adapter",
                category="skills",
                name="Context Adapter",
                description="Learns and adapts context to user patterns",
                version="1.5.2",
                author="Corvin Labs",
                rating=4.6,
                rating_count=198,
                tags=["context", "learning", "personalization"],
                domain="context",
                tier="core",
                origin="builtin",
                install_count=3200,
                created_at="2026-02-20T00:00:00Z",
                updated_at="2026-09-08T00:00:00Z",
                badge="verified",
            ),
        ]

    def _load_plugins(self) -> List[DiscoveryItem]:
        """Load plugins from plugin registry."""
        return [
            DiscoveryItem(
                id="plugin.memory_plugin",
                category="plugins",
                name="Memory Plugin",
                description="Persistent user memory and conversation history",
                version="3.1.0",
                author="Corvin Labs",
                rating=4.9,
                rating_count=567,
                tags=["memory", "persistence", "compliance"],
                tier="compliance",
                origin="builtin",
                install_count=8000,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-09-12T00:00:00Z",
                is_trending=True,
                badge="verified",
            ),
        ]

    def _load_tools(self) -> List[DiscoveryItem]:
        """Load tools from tool registry."""
        return [
            DiscoveryItem(
                id="tool.code_executor",
                category="tools",
                name="Code Executor",
                description="Execute Python code in isolated sandbox",
                version="1.2.0",
                author="Corvin Labs",
                rating=4.7,
                rating_count=412,
                tags=["code", "execution", "sandbox"],
                domain="execution",
                origin="builtin",
                install_count=6500,
                created_at="2026-03-10T00:00:00Z",
                updated_at="2026-09-09T00:00:00Z",
                badge="verified",
            ),
        ]

    def _load_connectors(self) -> List[DiscoveryItem]:
        """Load connectors from connector registry."""
        return [
            DiscoveryItem(
                id="connector.github",
                category="connectors",
                name="GitHub Connector",
                description="Connect to GitHub repositories and APIs",
                version="2.0.1",
                author="Corvin Labs",
                rating=4.85,
                rating_count=289,
                tags=["github", "integration", "version-control"],
                domain="integration",
                origin="builtin",
                install_count=4200,
                created_at="2026-02-05T00:00:00Z",
                updated_at="2026-09-11T00:00:00Z",
                badge="verified",
            ),
        ]

    def _load_layers(self) -> List[DiscoveryItem]:
        """Load layers from layer registry."""
        return [
            DiscoveryItem(
                id="layer.audit_chain",
                category="layers",
                name="Audit Chain (L16)",
                description="Hash-linked audit trail for compliance (GDPR Art. 30, 32)",
                version="2.3.1",
                author="Corvin Labs",
                rating=4.9,
                rating_count=123,
                tags=["compliance", "audit", "security"],
                tier="compliance",
                origin="builtin",
                install_count=10000,
                created_at="2026-01-01T00:00:00Z",
                updated_at="2026-09-13T00:00:00Z",
                badge="verified",
            ),
        ]

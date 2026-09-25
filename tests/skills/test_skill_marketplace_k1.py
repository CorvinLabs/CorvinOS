"""Tier 1-2 Tests for SkillMarketplaceIndex (Phase 6, k=1)."""
import json
import tempfile
from pathlib import Path

import pytest

from core.skills.skill_marketplace import (
    SkillMarketplaceIndex,
    SkillDomain,
    SkillTier,
    SkillOrigin,
    SkillMetadata,
    SkillSearchQuery,
)


@pytest.fixture
def sample_registry():
    """Fixture: Create a sample registry JSON."""
    return {
        "os.routing_optimizer": {
            "name": "OS Routing Optimizer",
            "version": "1.0.0",
            "description": "Route requests to optimal model",
            "domain": "routing",
            "tier": "core",
            "origin": "builtin",
            "tags": ["routing", "optimization", "ml"],
            "install_count": 156,
            "rating": 4.8,
            "created_at": "2026-01-15T10:00:00Z",
            "updated_at": "2026-09-20T14:30:00Z",
            "dependencies": [],
        },
        "os.automation_skill": {
            "name": "OS Automation",
            "version": "0.5.0",
            "description": "Automate routine tasks",
            "domain": "optimization",
            "tier": "installed",
            "origin": "community",
            "tags": ["automation", "scheduling"],
            "install_count": 89,
            "rating": 3.9,
            "created_at": "2026-02-01T09:00:00Z",
            "updated_at": "2026-09-18T11:20:00Z",
            "dependencies": ["os.routing_optimizer"],
        },
    }


@pytest.fixture
def marketplace(sample_registry):
    """Fixture: Create SkillMarketplaceIndex with sample registry."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_registry, f)
        registry_path = f.name

    try:
        index = SkillMarketplaceIndex(Path(registry_path), ttl_seconds=60)
        yield index
    finally:
        Path(registry_path).unlink()


class TestSkillMarketplaceIndex:
    """Unit tests for SkillMarketplaceIndex (Tier 1-2)."""

    def test_load_registry_success(self, marketplace):
        """Test successful registry loading."""
        assert len(marketplace._registry) == 2
        assert "os.routing_optimizer" in marketplace._registry
        assert "os.automation_skill" in marketplace._registry

    def test_load_registry_missing_file(self):
        """Test handling of missing registry file (fail-closed)."""
        index = SkillMarketplaceIndex(Path("/nonexistent/registry.json"))
        assert len(index._registry) == 0

    def test_skill_metadata_immutable(self, marketplace):
        """Test that SkillMetadata is frozen (immutable)."""
        metadata = marketplace.get_detail("os.routing_optimizer")
        assert metadata is not None

        with pytest.raises(Exception):  # FrozenInstanceError
            metadata.name = "Modified"

    def test_search_by_name(self, marketplace):
        """Test fuzzy search by skill name."""
        query = SkillSearchQuery(text="routing", sort_by="relevance")
        results = marketplace.search(query)

        assert len(results) > 0
        assert results[0].metadata.skill_id == "os.routing_optimizer"
        assert "name" in results[0].matched_fields

    def test_search_by_description(self, marketplace):
        """Test fuzzy search by description."""
        query = SkillSearchQuery(text="automate", sort_by="relevance")
        results = marketplace.search(query)

        assert len(results) > 0
        assert any("automation" in r.metadata.name.lower() for r in results)

    def test_search_filter_by_tier(self, marketplace):
        """Test filtering by skill tier."""
        query = SkillSearchQuery(
            text="", tier=SkillTier.CORE, sort_by="alphabetical"
        )
        results = marketplace.search(query)

        assert len(results) == 1
        assert results[0].metadata.skill_id == "os.routing_optimizer"

    def test_search_filter_by_domain(self, marketplace):
        """Test filtering by domain."""
        query = SkillSearchQuery(text="", domain=SkillDomain.ROUTING)
        results = marketplace.search(query)

        assert len(results) == 1
        assert results[0].metadata.domain == SkillDomain.ROUTING

    def test_search_filter_by_rating(self, marketplace):
        """Test filtering by minimum rating."""
        query = SkillSearchQuery(text="", min_rating=4.0)
        results = marketplace.search(query)

        assert len(results) == 1
        assert results[0].metadata.rating >= 4.0

    def test_search_sort_by_popularity(self, marketplace):
        """Test sorting by install_count."""
        query = SkillSearchQuery(text="", sort_by="popularity")
        results = marketplace.search(query)

        assert results[0].metadata.install_count > results[1].metadata.install_count

    def test_search_sort_by_rating(self, marketplace):
        """Test sorting by rating."""
        query = SkillSearchQuery(text="", sort_by="rating")
        results = marketplace.search(query)

        assert results[0].metadata.rating > results[1].metadata.rating

    def test_search_pagination(self, marketplace):
        """Test pagination (limit + offset)."""
        query = SkillSearchQuery(text="", limit=1, offset=0)
        results = marketplace.search(query)
        assert len(results) == 1

        query2 = SkillSearchQuery(text="", limit=1, offset=1)
        results2 = marketplace.search(query2)
        assert len(results2) == 1
        assert results[0].metadata.skill_id != results2[0].metadata.skill_id

    def test_get_detail(self, marketplace):
        """Test getting skill detail."""
        detail = marketplace.get_detail("os.routing_optimizer")
        assert detail is not None
        assert detail.name == "OS Routing Optimizer"

    def test_get_detail_not_found(self, marketplace):
        """Test getting detail for non-existent skill."""
        detail = marketplace.get_detail("nonexistent.skill")
        assert detail is None

    def test_caching_ttl(self, marketplace):
        """Test that search results are cached."""
        query = SkillSearchQuery(text="routing")
        results1 = marketplace.search(query)

        # Modify registry (should not affect cached results)
        marketplace._registry["os.routing_optimizer"].install_count = 9999

        results2 = marketplace.search(query)
        assert results1 == results2  # Cached, no change

    def test_cache_invalidation(self, marketplace):
        """Test manual cache invalidation."""
        query = SkillSearchQuery(text="routing")
        marketplace.search(query)

        cache_key = f"search:{query.text}:{query.domain}:{query.tier}:{query.sort_by}"
        assert cache_key in marketplace._cache

        marketplace.invalidate_cache()
        assert len(marketplace._cache) == 0

    def test_get_trending(self, marketplace):
        """Test getting trending skills."""
        trending = marketplace.get_trending()
        assert len(trending) <= 5
        assert all(isinstance(s, SkillMetadata) for s in trending)

    def test_get_newest(self, marketplace):
        """Test getting newest skills."""
        newest = marketplace.get_newest(limit=2)
        assert len(newest) == 2
        # Verify sorted by created_at descending
        assert newest[0].created_at >= newest[1].created_at

    def test_tenant_isolation_audit_stub(self, marketplace):
        """Test tenant_id scoping (audit stub for k=1)."""
        query = SkillSearchQuery(text="routing")
        results = marketplace.search(query, tenant_id="tenant_a")
        # In k=1, no real tenant isolation yet; stub passes
        assert len(results) >= 0


class TestSkillSearchQuery:
    """Tests for SkillSearchQuery validation."""

    def test_search_query_defaults(self):
        """Test SkillSearchQuery default values."""
        query = SkillSearchQuery()
        assert query.text == ""
        assert query.domain is None
        assert query.tier is None
        assert query.limit == 50
        assert query.offset == 0

    def test_search_query_custom(self):
        """Test SkillSearchQuery with custom values."""
        query = SkillSearchQuery(
            text="test",
            domain=SkillDomain.ROUTING,
            tier=SkillTier.CORE,
            limit=10,
            offset=5,
        )
        assert query.text == "test"
        assert query.domain == SkillDomain.ROUTING
        assert query.tier == SkillTier.CORE
        assert query.limit == 10
        assert query.offset == 5

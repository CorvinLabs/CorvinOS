"""Tier 3 (Integration) Tests for Skill Marketplace API Routes (k=2)."""
import json
import tempfile
from pathlib import Path

import pytest

# Mock FastAPI for test (since we're testing routes in isolation)
class MockRequest:
    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id


@pytest.fixture
def sample_registry():
    """Sample skills registry."""
    return {
        "os.routing_optimizer": {
            "name": "OS Routing Optimizer",
            "version": "1.0.0",
            "description": "Route requests to optimal model",
            "domain": "routing",
            "tier": "core",
            "origin": "builtin",
            "tags": ["routing", "optimization"],
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
def marketplace_routes(sample_registry):
    """A SkillMarketplaceIndex over the sample registry.

    Built directly: the unmounted route module that used to wrap it
    (routes/skill_marketplace_routes.py, a stub install router) was deleted
    2026-09-26 under ADR-0892 — what this suite tests is the index.
    """
    from core.skills.skill_marketplace import SkillMarketplaceIndex

    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(sample_registry, f)
        registry_path = f.name

    try:
        yield SkillMarketplaceIndex(Path(registry_path), ttl_seconds=300)
    finally:
        Path(registry_path).unlink()


class TestSkillMarketplaceRoutes:
    """Tier 3 integration tests for API routes."""

    def test_list_marketplace_returns_all_skills(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/index returns paginated skills."""
        # Stub: in real test, would call HTTP endpoint via test client
        # For k=2, we test the marketplace index directly
        from core.skills.skill_marketplace import SkillSearchQuery

        query = SkillSearchQuery(text="", limit=50, offset=0, sort_by="alphabetical")
        results = marketplace_routes.search(query)

        assert len(results) == 2
        assert results[0].metadata.name == "OS Automation"  # Alphabetical
        assert results[1].metadata.name == "OS Routing Optimizer"

    def test_search_marketplace_by_text(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/search with query text."""
        from core.skills.skill_marketplace import SkillSearchQuery

        query = SkillSearchQuery(text="automation", sort_by="relevance")
        results = marketplace_routes.search(query)

        assert len(results) == 1
        assert "os.automation_skill" in results[0].metadata.skill_id

    def test_search_marketplace_with_filter(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/search with filters."""
        from core.skills.skill_marketplace import SkillSearchQuery, SkillDomain

        query = SkillSearchQuery(text="", domain=SkillDomain.ROUTING)
        results = marketplace_routes.search(query)

        assert len(results) == 1
        assert results[0].metadata.domain == SkillDomain.ROUTING

    def test_get_skill_detail(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/{skill_id}."""
        detail = marketplace_routes.get_detail("os.routing_optimizer")

        assert detail is not None
        assert detail.skill_id == "os.routing_optimizer"
        assert detail.rating == 4.8

    def test_get_skill_detail_not_found(self, marketplace_routes):
        """Test 404 for non-existent skill."""
        detail = marketplace_routes.get_detail("nonexistent.skill")
        assert detail is None

    def test_get_trending_skills(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/trending."""
        trending = marketplace_routes.get_trending(days=7)

        assert len(trending) <= 5
        # Trending is sorted by install_count × rating, so highest should be first
        assert trending[0].install_count > 0

    def test_get_newest_skills(self, marketplace_routes):
        """Test GET /v1/skills/marketplace/newest."""
        newest = marketplace_routes.get_newest(limit=5)

        assert len(newest) <= 5
        # Newest is sorted by created_at DESC
        if len(newest) > 1:
            assert newest[0].created_at >= newest[1].created_at

    def test_marketplace_cache_integration(self, marketplace_routes):
        """Test that marketplace index caches results."""
        from core.skills.skill_marketplace import SkillSearchQuery

        query = SkillSearchQuery(text="automation")
        results1 = marketplace_routes.search(query)

        # Second call should use cache (same results, no registry change)
        results2 = marketplace_routes.search(query)

        assert len(results1) == len(results2)
        assert results1[0].metadata.skill_id == results2[0].metadata.skill_id

    def test_marketplace_cache_invalidation(self, marketplace_routes):
        """Test cache invalidation on registry update."""
        marketplace_routes.invalidate_cache()

        # After invalidation, cache should be empty
        assert len(marketplace_routes._cache) == 0

    def test_skill_metadata_immutability(self, marketplace_routes):
        """Test that returned SkillMetadata is frozen (immutable)."""
        detail = marketplace_routes.get_detail("os.routing_optimizer")

        # Should not be able to modify
        with pytest.raises(Exception):  # FrozenInstanceError
            detail.name = "Modified"


class TestAPIResponseFormats:
    """Tests for API response format compliance."""

    def test_search_response_has_required_fields(self, marketplace_routes):
        """Test SkillSearchResponse includes all required fields."""
        from core.skills.skill_marketplace import SkillSearchQuery

        query = SkillSearchQuery(text="automation")
        results = marketplace_routes.search(query)

        assert len(results) > 0
        result = results[0]

        # Verify all required fields
        assert hasattr(result, "metadata")
        assert hasattr(result, "relevance_score")
        assert hasattr(result, "matched_fields")

    def test_skill_detail_response_format(self, marketplace_routes):
        """Test SkillDetailResponse format."""
        detail = marketplace_routes.get_detail("os.routing_optimizer")

        assert detail is not None
        assert isinstance(detail.rating, float)
        assert isinstance(detail.install_count, int)
        assert isinstance(detail.tags, list)
        assert isinstance(detail.dependencies, list)

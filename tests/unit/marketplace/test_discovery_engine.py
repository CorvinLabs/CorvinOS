"""
Unit tests for Marketplace Discovery Engine (Phase 1 Session 1).

Tests search, filtering, sorting, and collection functionality.
"""

import pytest
from core.console.corvin_console.routes.marketplace_discovery import (
    DiscoveryEngine,
    SearchQuery,
    SortBy,
)


@pytest.fixture
def sample_plugins():
    """Sample plugin data for testing."""
    return [
        {
            "id": "plugin:buildin-learning-model-selector",
            "name": "Model Selector",
            "version": "1.0.0",
            "author": "Corvin Labs",
            "description": "AI model routing and selection",
            "category": "learning",
            "tier": "buildin",
            "tags": ["ml", "routing", "optimization"],
            "license": "Apache-2.0",
            "dependencies": [],
            "metrics": {
                "downloads": 5000,
                "rating": 4.5,
                "review_count": 42,
                "success_rate": 0.98,
                "last_updated": "2026-09-20",
            },
        },
        {
            "id": "plugin:buildin-cost-analyzer",
            "name": "Cost Analyzer",
            "version": "2.0.1",
            "author": "Corvin Labs",
            "description": "Analyze and optimize AI infrastructure costs",
            "category": "optimization",
            "tier": "buildin",
            "tags": ["cost", "analysis", "optimization"],
            "license": "Apache-2.0",
            "dependencies": [],
            "metrics": {
                "downloads": 3000,
                "rating": 4.8,
                "review_count": 28,
                "success_rate": 0.99,
                "last_updated": "2026-09-21",
            },
        },
        {
            "id": "plugin:contributor-video-producer",
            "name": "Video Producer",
            "version": "3.1.0",
            "author": "Community",
            "description": "Generate and edit videos programmatically",
            "category": "media",
            "tier": "contributor",
            "tags": ["video", "media", "generation"],
            "license": "MIT",
            "dependencies": [],
            "metrics": {
                "downloads": 1200,
                "rating": 3.9,
                "review_count": 12,
                "success_rate": 0.92,
                "last_updated": "2026-09-15",
            },
        },
    ]


@pytest.fixture
def engine(sample_plugins):
    """Create a discovery engine with sample data."""
    return DiscoveryEngine(sample_plugins)


class TestDiscoverySearch:
    """Test free-text search functionality."""

    def test_search_by_name(self, engine):
        """Search should find plugins by name."""
        results, _ = engine.search(SearchQuery(q="model selector"))
        assert len(results) > 0
        assert results[0]["id"] == "plugin:buildin-learning-model-selector"

    def test_search_by_description(self, engine):
        """Search should find plugins by description."""
        results, _ = engine.search(SearchQuery(q="optimize"))
        names = [r["name"] for r in results]
        assert "Cost Analyzer" in names or "Model Selector" in names

    def test_search_by_tag(self, engine):
        """Search should find plugins by tags."""
        results, _ = engine.search(SearchQuery(q="optimization"))
        assert len(results) > 0

    def test_search_empty_returns_all(self, engine):
        """Empty search should return all plugins."""
        results, total = engine.search(SearchQuery(q=""))
        assert len(results) == 3
        assert total == 3

    def test_search_case_insensitive(self, engine):
        """Search should be case insensitive."""
        results1, _ = engine.search(SearchQuery(q="MODEL"))
        results2, _ = engine.search(SearchQuery(q="model"))
        assert len(results1) == len(results2)

    def test_search_partial_match(self, engine):
        """Search should support partial matches."""
        results, _ = engine.search(SearchQuery(q="cost"))
        assert len(results) > 0
        assert any(r["name"] == "Cost Analyzer" for r in results)


class TestDiscoveryFiltering:
    """Test filtering functionality."""

    def test_filter_by_category(self, engine):
        """Should filter by category."""
        results, _ = engine.search(
            SearchQuery(q="", category="learning")
        )
        assert all(r["category"] == "learning" for r in results)
        assert len(results) == 1

    def test_filter_by_tier(self, engine):
        """Should filter by tier."""
        results, _ = engine.search(
            SearchQuery(q="", tier="buildin")
        )
        assert all(r["tier"] == "buildin" for r in results)
        assert len(results) == 2

    def test_filter_by_tag(self, engine):
        """Should filter by tag."""
        results, _ = engine.search(
            SearchQuery(q="", tag="optimization")
        )
        assert all("optimization" in r.get("tags", []) for r in results)

    def test_filter_by_min_rating(self, engine):
        """Should filter by minimum rating."""
        results, _ = engine.search(
            SearchQuery(q="", min_rating=4.5)
        )
        for r in results:
            rating = r.get("metrics", {}).get("rating", 0)
            assert rating >= 4.5

    def test_combined_filters(self, engine):
        """Should support multiple filters at once."""
        results, _ = engine.search(
            SearchQuery(
                q="",
                category="learning",
                tier="buildin",
                min_rating=4.0
            )
        )
        assert len(results) > 0
        assert all(r["category"] == "learning" for r in results)
        assert all(r["tier"] == "buildin" for r in results)


class TestDiscoverySorting:
    """Test sorting functionality."""

    def test_sort_by_downloads(self, engine):
        """Should sort by downloads descending."""
        results, _ = engine.search(
            SearchQuery(q="", sort_by=SortBy.DOWNLOADS)
        )
        downloads = [r.get("metrics", {}).get("downloads", 0) for r in results]
        assert downloads == sorted(downloads, reverse=True)

    def test_sort_by_rating(self, engine):
        """Should sort by rating descending."""
        results, _ = engine.search(
            SearchQuery(q="", sort_by=SortBy.RATING)
        )
        ratings = [r.get("metrics", {}).get("rating", 0) for r in results]
        assert ratings == sorted(ratings, reverse=True)

    def test_sort_by_name(self, engine):
        """Should sort by name ascending."""
        results, _ = engine.search(
            SearchQuery(q="", sort_by=SortBy.NAME)
        )
        names = [r["name"] for r in results]
        assert names == sorted(names)

    def test_sort_by_relevance_with_query(self, engine):
        """Should rank by relevance when searching."""
        results, _ = engine.search(
            SearchQuery(q="cost", sort_by=SortBy.RELEVANCE)
        )
        assert results[0]["name"] == "Cost Analyzer"


class TestDiscoveryPagination:
    """Test pagination functionality."""

    def test_limit_results(self, engine):
        """Should respect limit parameter."""
        results, _ = engine.search(
            SearchQuery(q="", limit=2)
        )
        assert len(results) == 2

    def test_offset_results(self, engine):
        """Should respect offset parameter."""
        all_results, _ = engine.search(
            SearchQuery(q="", limit=100)
        )
        first_page, _ = engine.search(
            SearchQuery(q="", limit=2, offset=0)
        )
        second_page, _ = engine.search(
            SearchQuery(q="", limit=2, offset=2)
        )
        assert first_page[0] != second_page[0]

    def test_total_count_without_limit(self, engine):
        """Should return total count before pagination."""
        _, total = engine.search(
            SearchQuery(q="", limit=1)
        )
        assert total == 3


class TestDiscoveryCollections:
    """Test pre-curated collections."""

    def test_get_collections(self, engine):
        """Should return collections."""
        collections = engine.get_collections()
        assert len(collections) > 0
        assert all(hasattr(c, "id") for c in collections)
        assert all(hasattr(c, "name") for c in collections)
        assert all(hasattr(c, "skills") for c in collections)

    def test_collection_structure(self, engine):
        """Collections should have proper structure."""
        collections = engine.get_collections()
        for c in collections:
            assert c.id.startswith("collection:")
            assert len(c.name) > 0
            assert len(c.description) > 0
            assert c.difficulty in ["beginner", "intermediate", "advanced"]
            assert c.estimated_setup_time_minutes >= 0


class TestDiscoveryDetails:
    """Test plugin details retrieval."""

    def test_get_plugin_details(self, engine):
        """Should retrieve full plugin details."""
        details = engine.get_plugin_details("plugin:buildin-learning-model-selector")
        assert details is not None
        assert details.name == "Model Selector"
        assert details.author == "Corvin Labs"

    def test_nonexistent_plugin(self, engine):
        """Should return None for nonexistent plugin."""
        details = engine.get_plugin_details("plugin:nonexistent")
        assert details is None

    def test_details_include_metrics(self, engine):
        """Details should include metrics."""
        details = engine.get_plugin_details("plugin:buildin-learning-model-selector")
        assert details.metrics.downloads == 5000
        assert details.metrics.rating == 4.5


class TestDiscoveryCatalog:
    """Test catalog inspection methods."""

    def test_get_categories(self, engine):
        """Should return categories with counts."""
        categories = engine.get_categories()
        assert "learning" in categories
        assert "optimization" in categories
        assert categories["learning"] == 1

    def test_get_tiers(self, engine):
        """Should return tiers with counts."""
        tiers = engine.get_tiers()
        assert "buildin" in tiers
        assert "contributor" in tiers
        assert tiers["buildin"] == 2
        assert tiers["contributor"] == 1

    def test_get_tags(self, engine):
        """Should return tags with counts."""
        tags = engine.get_tags()
        assert "optimization" in tags
        assert tags["optimization"] == 2


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

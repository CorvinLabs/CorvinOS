"""
E2E tests for Marketplace Discovery API Routes (Phase 1 Session 1).

Tests search, filtering, collections, and details endpoints.
"""

import json
import pytest
from fastapi.testclient import TestClient


@pytest.fixture
def sample_index(tmp_path):
    """Create a temporary marketplace index for testing."""
    index_file = tmp_path / "plugins.json"
    index_data = {
        "version": "2.0",
        "schema": "ADR-0511",
        "plugins": [
            {
                "id": "plugin:buildin-model-selector",
                "name": "Model Selector",
                "version": "1.0.0",
                "author": "Corvin Labs",
                "description": "AI model routing and selection",
                "category": "learning",
                "tier": "buildin",
                "tags": ["ml", "routing"],
                "license": "Apache-2.0",
                "metrics": {
                    "downloads": 5000,
                    "rating": 4.5,
                    "review_count": 42,
                },
            },
            {
                "id": "plugin:buildin-cost-analyzer",
                "name": "Cost Analyzer",
                "version": "2.0.0",
                "author": "Corvin Labs",
                "description": "Cost optimization",
                "category": "optimization",
                "tier": "buildin",
                "tags": ["cost"],
                "license": "Apache-2.0",
                "metrics": {
                    "downloads": 3000,
                    "rating": 4.8,
                    "review_count": 28,
                },
            },
        ],
    }
    index_file.write_text(json.dumps(index_data))
    return index_file


@pytest.mark.asyncio
async def test_search_endpoint(client, sample_index):
    """Test /search endpoint."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/search?q=model",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total" in data
    assert len(data["results"]) > 0


@pytest.mark.asyncio
async def test_search_with_filters(client, sample_index):
    """Test search with category and tier filters."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/search?q=&category=learning&tier=buildin",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert all(r["category"] == "learning" for r in data["results"])
    assert all(r["tier"] == "buildin" for r in data["results"])


@pytest.mark.asyncio
async def test_search_with_sorting(client, sample_index):
    """Test search with sort options."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/search?q=&sort_by=rating",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    # Verify results are sorted by rating
    ratings = [r["metrics"]["rating"] for r in data["results"]]
    assert ratings == sorted(ratings, reverse=True)


@pytest.mark.asyncio
async def test_search_with_pagination(client, sample_index):
    """Test search pagination."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/search?limit=1&offset=0",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["total"] == 2


@pytest.mark.asyncio
async def test_collections_endpoint(client):
    """Test /collections endpoint."""
    response = client.get(
        "/api/v1/marketplace/collections",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "collections" in data
    assert len(data["collections"]) > 0
    assert all("id" in c for c in data["collections"])
    assert all("name" in c for c in data["collections"])
    assert all("skills" in c for c in data["collections"])


@pytest.mark.asyncio
async def test_categories_endpoint(client, sample_index):
    """Test /categories endpoint."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/categories",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], dict)


@pytest.mark.asyncio
async def test_tags_endpoint(client, sample_index):
    """Test /tags endpoint."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/tags",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "tags" in data
    assert isinstance(data["tags"], dict)


# /plugins/{id} details: removed from marketplace_discovery_routes (it was shadowed
# by marketplace.py's identical route and never dispatched). The canonical route
# is covered by tests/e2e/test_marketplace_single_install_route.py.


@pytest.mark.asyncio
async def test_search_facets(client, sample_index):
    """Test that search includes facets for filtering."""
    from core.console.corvin_console.routes.marketplace_discovery import set_marketplace_index_path
    set_marketplace_index_path(sample_index)

    response = client.get(
        "/api/v1/marketplace/search?q=",
        headers={"Authorization": "Bearer test_token"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "facets" in data
    assert "categories" in data["facets"]
    assert "tiers" in data["facets"]
    assert "tags" in data["facets"]


@pytest.mark.asyncio
async def test_search_requires_auth(client):
    """Test that search requires authentication."""
    response = client.get("/api/v1/marketplace/search?q=test")
    assert response.status_code in [401, 403]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
E2E tests for Marketplace Discovery API Routes (Phase 1 Session 1).

Tests search, filtering, collections, and details endpoints through the REAL
console router (``/v1/console/api/v1/marketplace/*``) with a real console
session against a scratch CORVIN_HOME.

Rewritten 2026-09-27 (adversarial review): the tests imported
``set_marketplace_index_path`` from the wrong module (it lives in
``routes/marketplace.py``), called un-prefixed paths and authenticated with a
Bearer header the console does not accept.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

P = "/v1/console/api/v1/marketplace"


@pytest.fixture
def client(tmp_path, monkeypatch):
    home = tmp_path / "corvin_home"
    for sub in ("auth", "forge", "console/sessions"):
        (home / "tenants" / "_default" / "global" / sub).mkdir(parents=True)
    monkeypatch.setenv("CORVIN_HOME", str(home))
    monkeypatch.setenv("CORVIN_TENANT_ID", "_default")

    from core.console.corvin_console import auth as _auth
    from core.console.corvin_console.app import router
    from core.console.corvin_console.routes import marketplace as mp

    rec = _auth.create_session(tenant_id="_default", token_fingerprint="test-fp")
    app = FastAPI()
    app.include_router(router, prefix="/v1/console")
    c = TestClient(app, raise_server_exceptions=False)
    c.cookies.set("corvin_console_sid", rec.sid)
    prev = (mp._index_manager._index_path, mp._index_manager._index)
    yield c
    mp._index_manager._index_path, mp._index_manager._index = prev


def set_marketplace_index_path(path):
    from core.console.corvin_console.routes.marketplace import set_marketplace_index_path as _set
    _set(path)


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


def test_search_endpoint(client, sample_index):
    """Test /search endpoint."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/search?q=model"
    )
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "total" in data
    assert len(data["results"]) > 0


def test_search_with_filters(client, sample_index):
    """Test search with category and tier filters."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/search?q=&category=learning&tier=buildin"
    )
    assert response.status_code == 200
    data = response.json()
    assert all(r["category"] == "learning" for r in data["results"])
    assert all(r["tier"] == "buildin" for r in data["results"])


def test_search_with_sorting(client, sample_index):
    """Test search with sort options."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/search?q=&sort_by=rating"
    )
    assert response.status_code == 200
    data = response.json()
    # Verify results are sorted by rating
    ratings = [r["metrics"]["rating"] for r in data["results"]]
    assert ratings == sorted(ratings, reverse=True)


def test_search_with_pagination(client, sample_index):
    """Test search pagination."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/search?limit=1&offset=0"
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["results"]) == 1
    assert data["total"] == 2


def test_collections_endpoint(client, sample_index):
    """Test /collections endpoint."""
    set_marketplace_index_path(sample_index)
    response = client.get(f"{P}/collections"
    )
    assert response.status_code == 200
    data = response.json()
    assert "collections" in data
    assert len(data["collections"]) > 0
    assert all("id" in c for c in data["collections"])
    assert all("name" in c for c in data["collections"])
    assert all("skills" in c for c in data["collections"])


def test_categories_endpoint(client, sample_index):
    """Test /categories endpoint."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/categories"
    )
    assert response.status_code == 200
    data = response.json()
    assert "categories" in data
    assert isinstance(data["categories"], dict)


def test_tags_endpoint(client, sample_index):
    """Test /tags endpoint."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/tags"
    )
    assert response.status_code == 200
    data = response.json()
    assert "tags" in data
    assert isinstance(data["tags"], dict)


# /plugins/{id} details: removed from marketplace_discovery_routes (it was shadowed
# by marketplace.py's identical route and never dispatched). The canonical route
# is covered by tests/e2e/test_marketplace_single_install_route.py.


def test_search_facets(client, sample_index):
    """Test that search includes facets for filtering."""
    set_marketplace_index_path(sample_index)

    response = client.get(f"{P}/search?q="
    )
    assert response.status_code == 200
    data = response.json()
    assert "facets" in data
    assert "categories" in data["facets"]
    assert "tiers" in data["facets"]
    assert "tags" in data["facets"]


def test_search_requires_auth(client):
    """Test that search requires authentication."""
    client.cookies.clear()
    response = client.get(f"{P}/search?q=test")
    assert response.status_code in [401, 403]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

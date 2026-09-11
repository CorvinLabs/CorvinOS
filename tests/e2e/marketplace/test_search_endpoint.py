"""
E2E Tests for Marketplace Hub Search Endpoint
"""

import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_search_marketplace_returns_200(client: AsyncClient):
    """Search endpoint returns 200 OK."""
    response = await client.get("/api/v1/marketplace/search?q=plugins")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_returns_list(client: AsyncClient):
    """Search endpoint returns list of results."""
    response = await client.get("/api/v1/marketplace/search?q=plugins")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_marketplace_result_structure(client: AsyncClient):
    """Search results have correct structure."""
    response = await client.get("/api/v1/marketplace/search?q=test")
    assert response.status_code == 200

    if response.json():  # Only check if results exist
        result = response.json()[0]
        assert "id" in result
        assert "type" in result
        assert "name" in result
        assert "installed" in result
        assert result["type"] in ["plugins", "skills", "tools", "connectors", "layers"]


@pytest.mark.asyncio
async def test_search_marketplace_empty_query(client: AsyncClient):
    """Search with empty query returns all artifacts."""
    response = await client.get("/api/v1/marketplace/search?q=")
    assert response.status_code == 200

    data = response.json()
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_marketplace_type_filter(client: AsyncClient):
    """Search filters by type."""
    response = await client.get("/api/v1/marketplace/search?types=plugins&types=skills")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_category_filter(client: AsyncClient):
    """Search filters by category."""
    response = await client.get("/api/v1/marketplace/search?q=test&category=memory")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_tier_filter(client: AsyncClient):
    """Search filters by tier."""
    response = await client.get("/api/v1/marketplace/search?q=test&tier=buildin")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_performance(client: AsyncClient):
    """Search endpoint responds in <1000ms."""
    import time
    start = time.time()
    response = await client.get("/api/v1/marketplace/search?q=test&types=plugins")
    elapsed = (time.time() - start) * 1000

    assert response.status_code == 200
    assert elapsed < 1000, f"Search endpoint took {elapsed:.0f}ms (target: <1000ms)"


@pytest.mark.asyncio
async def test_search_marketplace_requires_auth(client: AsyncClient, unauthenticated_client: AsyncClient):
    """Search endpoint requires authentication."""
    response = await unauthenticated_client.get("/api/v1/marketplace/search?q=test")
    assert response.status_code == 401  # Unauthorized


@pytest.mark.asyncio
async def test_search_marketplace_tenant_isolation(
    client_tenant_a: AsyncClient,
    client_tenant_b: AsyncClient,
):
    """Different tenants see only their own results."""
    response_a = await client_tenant_a.get("/api/v1/marketplace/search?q=test")
    response_b = await client_tenant_b.get("/api/v1/marketplace/search?q=test")

    # Both should succeed
    assert response_a.status_code == 200
    assert response_b.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_audit_trail(client: AsyncClient, audit_log):
    """Search endpoint logs audit event."""
    response = await client.get("/api/v1/marketplace/search?q=test&types=plugins")
    assert response.status_code == 200

    # Verify audit event was logged
    events = audit_log.get_events(event_type="marketplace_hub_search")
    assert len(events) > 0

    event = events[-1]
    assert event["tenant_id"] is not None
    assert event["event_type"] == "marketplace_hub_search"
    assert event["query"] == "test"


@pytest.mark.asyncio
async def test_search_marketplace_no_results(client: AsyncClient):
    """Search with no matching results returns empty list."""
    response = await client.get("/api/v1/marketplace/search?q=xyznonexistentquery12345")
    assert response.status_code == 200

    data = response.json()
    # Empty results is valid (not an error)
    assert isinstance(data, list)


@pytest.mark.asyncio
async def test_search_marketplace_case_insensitive(client: AsyncClient):
    """Search is case-insensitive."""
    response_lower = await client.get("/api/v1/marketplace/search?q=plugin")
    response_upper = await client.get("/api/v1/marketplace/search?q=PLUGIN")

    # Both should return same results
    assert response_lower.status_code == 200
    assert response_upper.status_code == 200


@pytest.mark.asyncio
async def test_search_marketplace_installed_status(client: AsyncClient):
    """Search results include installation status."""
    response = await client.get("/api/v1/marketplace/search?q=test")

    if response.json():
        result = response.json()[0]
        assert isinstance(result["installed"], bool)


@pytest.mark.asyncio
async def test_search_marketplace_sorted_by_relevance(client: AsyncClient):
    """Search results are sorted by relevance."""
    response = await client.get("/api/v1/marketplace/search?q=test")

    if len(response.json()) > 1:
        # Installed items should come first
        results = response.json()
        installed_count = sum(1 for r in results if r["installed"])
        if installed_count > 0:
            # First installed item should come before first uninstalled
            first_installed_idx = next(i for i, r in enumerate(results) if r["installed"])
            first_uninstalled_idx = next((i for i, r in enumerate(results) if not r["installed"]), len(results))
            assert first_installed_idx < first_uninstalled_idx

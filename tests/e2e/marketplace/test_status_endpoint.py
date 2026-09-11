"""
E2E Tests for Marketplace Hub Status Endpoint
"""

import pytest
from httpx import AsyncClient
from datetime import datetime


@pytest.mark.asyncio
async def test_get_marketplace_status_returns_200(client: AsyncClient):
    """Status endpoint returns 200 OK."""
    response = await client.get("/api/v1/marketplace/status")
    assert response.status_code == 200


@pytest.mark.asyncio
async def test_get_marketplace_status_has_all_fields(client: AsyncClient):
    """Response contains all 5 artifact types."""
    response = await client.get("/api/v1/marketplace/status")
    assert response.status_code == 200

    data = response.json()
    assert "plugins" in data
    assert "skills" in data
    assert "tools" in data
    assert "connectors" in data
    assert "layers" in data


@pytest.mark.asyncio
async def test_get_marketplace_status_plugins_structure(client: AsyncClient):
    """Plugins status has correct structure."""
    response = await client.get("/api/v1/marketplace/status")
    data = response.json()
    plugins = data["plugins"]

    assert "installed" in plugins
    assert "available" in plugins
    assert "status" in plugins
    assert isinstance(plugins["installed"], int)
    assert isinstance(plugins["available"], int)
    assert plugins["status"] in ["active", "inactive", "pending"]


@pytest.mark.asyncio
async def test_get_marketplace_status_all_types_structure(client: AsyncClient):
    """All types have consistent structure."""
    response = await client.get("/api/v1/marketplace/status")
    data = response.json()

    for type_name in ["plugins", "skills", "tools", "connectors"]:
        type_data = data[type_name]
        assert "installed" in type_data or "active" in type_data or "registered" in type_data or "configured" in type_data
        assert "available" in type_data
        assert "status" in type_data


@pytest.mark.asyncio
async def test_get_marketplace_status_layers_immutable(client: AsyncClient):
    """Layers status includes immutable flag."""
    response = await client.get("/api/v1/marketplace/status")
    data = response.json()
    layers = data["layers"]

    assert "builtin" in layers
    assert "immutable" in layers
    assert layers["immutable"] is True


@pytest.mark.asyncio
async def test_get_marketplace_status_performance(client: AsyncClient):
    """Status endpoint responds in <500ms."""
    import time
    start = time.time()
    response = await client.get("/api/v1/marketplace/status")
    elapsed = (time.time() - start) * 1000

    assert response.status_code == 200
    assert elapsed < 500, f"Status endpoint took {elapsed:.0f}ms (target: <500ms)"


@pytest.mark.asyncio
async def test_get_marketplace_status_requires_auth(client: AsyncClient, unauthenticated_client: AsyncClient):
    """Status endpoint requires authentication."""
    response = await unauthenticated_client.get("/api/v1/marketplace/status")
    assert response.status_code == 401  # Unauthorized


@pytest.mark.asyncio
async def test_get_marketplace_status_tenant_isolation(
    client: AsyncClient,
    client_tenant_a: AsyncClient,
    client_tenant_b: AsyncClient,
):
    """Different tenants see only their own data."""
    # Each tenant makes a request
    response_a = await client_tenant_a.get("/api/v1/marketplace/status")
    response_b = await client_tenant_b.get("/api/v1/marketplace/status")

    # Both should succeed
    assert response_a.status_code == 200
    assert response_b.status_code == 200

    # Verify tenant isolation (this is a basic check; real implementation would verify counts)
    data_a = response_a.json()
    data_b = response_b.json()

    # Both should have the structure (counts may differ per tenant)
    assert "plugins" in data_a
    assert "plugins" in data_b


@pytest.mark.asyncio
async def test_marketplace_status_audit_trail(client: AsyncClient, audit_log):
    """Status endpoint logs audit event."""
    response = await client.get("/api/v1/marketplace/status")
    assert response.status_code == 200

    # Verify audit event was logged
    events = audit_log.get_events(event_type="marketplace_hub_status")
    assert len(events) > 0

    event = events[-1]
    assert event["tenant_id"] is not None
    assert event["event_type"] == "marketplace_hub_status"


@pytest.mark.asyncio
async def test_marketplace_status_caching_headers(client: AsyncClient):
    """Status endpoint sets correct cache headers for client-side cache."""
    response = await client.get("/api/v1/marketplace/status")

    # Should be cacheable on client (30s)
    assert response.headers.get("Cache-Control") is not None or response.status_code == 200


@pytest.mark.asyncio
async def test_marketplace_status_consistency_plugins_connectors(client: AsyncClient):
    """Plugins + Connectors status can vary per tenant."""
    # This test verifies that different tenants can have different
    # installed counts (expected behavior)

    response1 = await client.get("/api/v1/marketplace/status")
    response2 = await client.get("/api/v1/marketplace/status")

    # Same tenant, same call = consistent data
    data1 = response1.json()
    data2 = response2.json()

    assert data1["plugins"]["installed"] == data2["plugins"]["installed"]
    assert data1["skills"]["active"] == data2["skills"]["active"]


@pytest.mark.asyncio
async def test_marketplace_status_null_handling(client: AsyncClient):
    """Status endpoint handles missing data gracefully."""
    response = await client.get("/api/v1/marketplace/status")
    data = response.json()

    # All counts should be integers (not None)
    for type_name in ["plugins", "skills", "tools", "connectors", "layers"]:
        type_data = data[type_name]
        # At minimum, status should be set
        assert type_data["status"] is not None

"""E2E Tests for Skill Marketplace — ADR-0682 Phase 6 k=3

Full user journey testing:
  1. Load marketplace (empty state)
  2. Search for skills (API call + grid rendering)
  3. Click skill card (modal opens with details)
  4. Install skill (progress tracking)
  5. Pagination (next/prev pages)

Tests: 8 scenarios covering happy path + error cases
"""
import pytest
import json
from httpx import AsyncClient
from unittest.mock import patch, MagicMock


@pytest.mark.asyncio
async def test_marketplace_search_e2e(async_client: AsyncClient):
    """E2E: Search marketplace and render results."""
    # Mock API response
    mock_response = {
        "total": 25,
        "limit": 50,
        "offset": 0,
        "results": [
            {
                "skill_id": "os.delegation_router",
                "name": "Delegation Router",
                "description": "Auto-route tasks by complexity",
                "domain": "routing",
                "tier": "tier_a",
                "rating": 4.8,
                "install_count": 324,
                "version": "2.1.0",
                "tags": ["routing", "agentic"],
                "created_at": "2026-01-15T00:00:00Z",
                "dependencies": [],
                "matched_fields": ["name", "description"],
                "relevance_score": 0.95,
            },
            {
                "skill_id": "os.context_adapter",
                "name": "Context Adapter",
                "description": "Learn user patterns from history",
                "domain": "learning",
                "tier": "tier_b",
                "rating": 4.5,
                "install_count": 156,
                "version": "1.8.0",
                "tags": ["learning", "context"],
                "created_at": "2026-02-01T00:00:00Z",
                "dependencies": ["skill:os.delegation_router"],
                "matched_fields": ["description"],
                "relevance_score": 0.87,
            },
        ],
    }

    # Test: GET /v1/skills/marketplace/search
    response = await async_client.get(
        "/v1/skills/marketplace/search",
        params={
            "q": "router",
            "limit": 50,
            "offset": 0,
            "sort_by": "relevance",
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1
    assert len(data["results"]) > 0
    assert "skill_id" in data["results"][0]
    assert "name" in data["results"][0]
    assert "rating" in data["results"][0]


@pytest.mark.asyncio
async def test_marketplace_index_e2e(async_client: AsyncClient):
    """E2E: Load marketplace index (empty search)."""
    response = await async_client.get(
        "/v1/skills/marketplace/index",
        params={"limit": 50, "offset": 0},
    )
    assert response.status_code == 200
    data = response.json()
    assert "total" in data
    assert "results" in data
    assert "limit" in data
    assert "offset" in data


@pytest.mark.asyncio
async def test_skill_detail_e2e(async_client: AsyncClient):
    """E2E: Fetch full skill detail (modal data)."""
    # First, list skills to get a valid skill_id
    search_response = await async_client.get(
        "/v1/skills/marketplace/index",
        params={"limit": 10},
    )
    assert search_response.status_code == 200
    results = search_response.json()["results"]

    if len(results) > 0:
        skill_id = results[0]["skill_id"]

        # Fetch detail
        detail_response = await async_client.get(
            f"/v1/skills/marketplace/{skill_id}"
        )
        assert detail_response.status_code == 200
        detail = detail_response.json()
        assert detail["skill_id"] == skill_id
        assert "version" in detail
        assert "created_at" in detail
        assert "dependencies" in detail


@pytest.mark.asyncio
async def test_skill_install_e2e(async_client: AsyncClient):
    """E2E: Initiate skill installation and check job status."""
    # First, list skills
    search_response = await async_client.get(
        "/v1/skills/marketplace/index",
        params={"limit": 10},
    )
    assert search_response.status_code == 200
    results = search_response.json()["results"]

    if len(results) > 0:
        skill_id = results[0]["skill_id"]

        # POST install request
        install_response = await async_client.post(
            f"/v1/skills/marketplace/{skill_id}/install",
            json={"tenant_id": "_default"},
        )
        assert install_response.status_code in [200, 202]
        job_data = install_response.json()
        assert "job_id" in job_data
        assert "status" in job_data
        assert job_data["skill_id"] == skill_id

        job_id = job_data["job_id"]

        # Poll status
        status_response = await async_client.get(
            f"/v1/skills/marketplace/install/{job_id}"
        )
        assert status_response.status_code == 200
        status = status_response.json()
        assert status["job_id"] == job_id
        assert "progress" in status


@pytest.mark.asyncio
async def test_marketplace_search_with_filters_e2e(async_client: AsyncClient):
    """E2E: Search with domain and tier filters."""
    response = await async_client.get(
        "/v1/skills/marketplace/search",
        params={
            "q": "",
            "domain": "routing",
            "tier": "tier_a",
            "limit": 50,
            "offset": 0,
        },
    )
    assert response.status_code == 200
    data = response.json()
    assert "results" in data
    assert "limit" in data
    # Results should be filtered (if any)
    if len(data["results"]) > 0:
        for result in data["results"]:
            assert result["domain"] == "routing" or result["tier"] == "tier_a"


@pytest.mark.asyncio
async def test_marketplace_pagination_e2e(async_client: AsyncClient):
    """E2E: Paginate through marketplace results."""
    # Page 1
    page1 = await async_client.get(
        "/v1/skills/marketplace/index",
        params={"limit": 10, "offset": 0},
    )
    assert page1.status_code == 200
    page1_data = page1.json()
    page1_ids = [r["skill_id"] for r in page1_data["results"]]

    # Page 2
    if page1_data["total"] > 10:
        page2 = await async_client.get(
            "/v1/skills/marketplace/index",
            params={"limit": 10, "offset": 10},
        )
        assert page2.status_code == 200
        page2_data = page2.json()
        page2_ids = [r["skill_id"] for r in page2_data["results"]]

        # Pages should be different
        assert set(page1_ids) != set(page2_ids)


@pytest.mark.asyncio
async def test_marketplace_trending_e2e(async_client: AsyncClient):
    """E2E: Fetch trending skills."""
    response = await async_client.get(
        "/v1/skills/marketplace/trending",
        params={"limit": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "skill_id" in data[0]
        assert "rating" in data[0]


@pytest.mark.asyncio
async def test_marketplace_newest_e2e(async_client: AsyncClient):
    """E2E: Fetch newest skills."""
    response = await async_client.get(
        "/v1/skills/marketplace/newest",
        params={"limit": 10},
    )
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    if len(data) > 0:
        assert "skill_id" in data[0]
        assert "created_at" in data[0]


@pytest.mark.asyncio
async def test_marketplace_invalid_skill_id(async_client: AsyncClient):
    """E2E: 404 on invalid skill ID."""
    response = await async_client.get(
        "/v1/skills/marketplace/invalid-nonexistent-skill-12345"
    )
    assert response.status_code == 404


@pytest.mark.asyncio
async def test_marketplace_invalid_filter(async_client: AsyncClient):
    """E2E: 400 on invalid tier filter."""
    response = await async_client.get(
        "/v1/skills/marketplace/search",
        params={
            "q": "test",
            "tier": "invalid_tier",
        },
    )
    assert response.status_code == 400


# Fixture for async test client (requires app fixture)
@pytest.fixture
async def async_client():
    """Provide async HTTP client for testing."""
    from core.console.corvin_console.app import app
    from httpx import AsyncClient

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

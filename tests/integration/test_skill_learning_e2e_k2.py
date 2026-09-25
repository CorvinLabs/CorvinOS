"""E2E Tests for Skill Learning Dashboard — ADR-0683 Phase 7 k=2"""
import pytest
from httpx import AsyncClient


@pytest.mark.asyncio
async def test_get_learning_metrics_e2e(async_client: AsyncClient):
    """E2E: Fetch learning metrics for a Skill."""
    response = await async_client.get(
        "/v1/console/skills/os.delegation_router/learning"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skill_id"] == "os.delegation_router"
    assert "accuracy" in data
    assert "confidence_score" in data
    assert 0 <= data["accuracy"] <= 1
    assert 0 <= data["confidence_score"] <= 1


@pytest.mark.asyncio
async def test_get_feedback_history_e2e(async_client: AsyncClient):
    """E2E: Fetch feedback history for a Skill."""
    response = await async_client.get(
        "/v1/console/skills/os.delegation_router/feedback/history?limit=20"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skill_id"] == "os.delegation_router"
    assert "recent" in data
    assert isinstance(data["recent"], list)
    
    if len(data["recent"]) > 0:
        item = data["recent"][0]
        assert "execution_id" in item
        assert "outcome_correct" in item
        assert "rating" in item


@pytest.mark.asyncio
async def test_get_optimization_proposals_e2e(async_client: AsyncClient):
    """E2E: Fetch optimization proposals for a Skill."""
    response = await async_client.get(
        "/v1/console/skills/os.delegation_router/optimization/proposals"
    )
    assert response.status_code == 200
    data = response.json()
    assert data["skill_id"] == "os.delegation_router"
    assert "proposals" in data
    assert isinstance(data["proposals"], list)
    
    if len(data["proposals"]) > 0:
        prop = data["proposals"][0]
        assert "parameter_name" in prop
        assert "old_value" in prop
        assert "new_value" in prop
        assert 0 <= prop["confidence"] <= 1


@pytest.mark.asyncio
async def test_learning_dashboard_full_flow_e2e(async_client: AsyncClient):
    """E2E: Full learning dashboard flow (metrics + feedback + proposals)."""
    skill_id = "os.delegation_router"
    
    # 1. Get metrics
    metrics_response = await async_client.get(
        f"/v1/console/skills/{skill_id}/learning"
    )
    assert metrics_response.status_code == 200
    metrics = metrics_response.json()
    
    # 2. Get feedback history
    feedback_response = await async_client.get(
        f"/v1/console/skills/{skill_id}/feedback/history?limit=10"
    )
    assert feedback_response.status_code == 200
    feedback = feedback_response.json()
    
    # 3. Get proposals
    proposals_response = await async_client.get(
        f"/v1/console/skills/{skill_id}/optimization/proposals"
    )
    assert proposals_response.status_code == 200
    proposals = proposals_response.json()
    
    # Verify dashboard data is coherent
    assert metrics["skill_id"] == feedback["skill_id"] == proposals["skill_id"]
    assert metrics["accuracy"] == (
        metrics["correct_outcomes"] / metrics["total_executions"]
        if metrics["total_executions"] > 0
        else 0
    )

"""E2E Tests for Skill Learning Dashboard routes — ADR-0683 Phase 7 k=2.

These routes used to be unauthenticated and to answer every skill id with the
same hard-coded sample numbers; the tests here asserted that sample data. They
now require a console session (this client has none → 401). The authenticated
behaviour — 404 "not available on this build" for metrics, empty lists with
``available: false`` for history/proposals, never sample data — is proven with a
real session in
``core/console/tests/test_backend_review_regressions_2026_09_27.py``.
"""
import pytest
from httpx import AsyncClient

_BASE = "/v1/console/skills/os.delegation_router"


@pytest.mark.asyncio
@pytest.mark.parametrize("path", ["learning", "feedback/history?limit=20", "optimization/proposals"])
async def test_skill_learning_routes_require_a_session(anon_async_client: AsyncClient, path: str):
    response = await anon_async_client.get(f"{_BASE}/{path}")
    assert response.status_code == 401

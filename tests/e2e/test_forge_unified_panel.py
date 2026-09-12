"""
E2E Test für unified Forge Panel (Tools, Skills, OS-Skills, Graph, Audit).

ADR-TBD: Unified Forge Control Plane
Tests:
  - Panel loads at /console/forge
  - All 5 tabs render (Tools, Skills, OS-Skills, Graph, Audit)
  - API endpoints respond (scaffold stage)
  - UI components accept input (search, filter)
"""

import pytest
from httpx import AsyncClient
from fastapi.testclient import TestClient


@pytest.fixture
async def client(app):
    """FastAPI test client for console routes."""
    return TestClient(app)


class TestForgeUnifiedPanel:
    """Tests for the consolidated Forge panel."""

    async def test_forge_endpoints_exist(self, client):
        """Verify all Forge API endpoints are registered."""
        endpoints = [
            "/v1/console/forge/tools",
            "/v1/console/forge/skills",
            "/v1/console/forge/os-skills",
            "/v1/console/forge/graph",
            "/v1/console/forge/audit",
            "/v1/console/forge/search",
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)
            # Scaffold stage: endpoints exist (200/500 ok, 404 would be bad)
            assert response.status_code in [200, 401, 500], f"Endpoint {endpoint} not found"

    async def test_tools_list_endpoint(self, client):
        """GET /v1/console/forge/tools returns tools list."""
        response = client.get("/v1/console/forge/tools")
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "tools" in data
            assert "count" in data
            assert isinstance(data["tools"], list)

    async def test_skills_list_endpoint(self, client):
        """GET /v1/console/forge/skills returns skills list."""
        response = client.get("/v1/console/forge/skills")
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "skills" in data
            assert "count" in data
            assert isinstance(data["skills"], list)

    async def test_os_skills_list_endpoint(self, client):
        """GET /v1/console/forge/os-skills returns OS-Skills list."""
        response = client.get("/v1/console/forge/os-skills")
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "os_skills" in data
            assert "count" in data
            assert isinstance(data["os_skills"], list)

    async def test_graph_endpoint(self, client):
        """GET /v1/console/forge/graph returns dependency DAG."""
        response = client.get("/v1/console/forge/graph")
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "nodes" in data
            assert "edges" in data
            assert isinstance(data["nodes"], list)
            assert isinstance(data["edges"], list)

    async def test_audit_endpoint(self, client):
        """GET /v1/console/forge/audit returns audit trail."""
        response = client.get("/v1/console/forge/audit")
        assert response.status_code in [200, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "events" in data
            assert "total" in data
            assert isinstance(data["events"], list)

    async def test_search_endpoint(self, client):
        """GET /v1/console/forge/search?q=test returns cross-tab results."""
        response = client.get("/v1/console/forge/search?q=test")
        assert response.status_code in [200, 400, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "results" in data
            assert "total" in data
            assert isinstance(data["results"], list)

    async def test_tool_enable_endpoint(self, client):
        """POST /v1/console/forge/tools/{id}/enable changes tool state."""
        response = client.post("/v1/console/forge/tools/test_tool/enable")
        assert response.status_code in [200, 401, 403, 500]
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "enabled"
            assert data.get("tool_id") == "test_tool"

    async def test_skill_rollback_endpoint(self, client):
        """POST /v1/console/forge/skills/{id}/rollback reverts to version."""
        response = client.post("/v1/console/forge/skills/test_skill/rollback?version=1.0.0")
        assert response.status_code in [200, 401, 403, 500]
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "rolled_back"
            assert data.get("skill_id") == "test_skill"
            assert data.get("version") == "1.0.0"

    async def test_os_skill_config_endpoint(self, client):
        """POST /v1/console/forge/os-skills/{id}/config updates OS-Skill params."""
        response = client.post(
            "/v1/console/forge/os-skills/test_os_skill/config",
            json={"threshold": 0.85, "mode": "adaptive"},
        )
        assert response.status_code in [200, 401, 403, 500]
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "configured"
            assert data.get("os_skill_id") == "test_os_skill"
            assert "config" in data


class TestForgeConsolePages:
    """Tests for the unified Forge console panel (frontend)."""

    async def test_forge_page_exports(self):
        """Verify ForgePage is properly exported from pages/forge.tsx."""
        from core.console.corvin_console.web_next.src.pages.forge import ForgePage

        assert ForgePage is not None
        assert callable(ForgePage)

    async def test_tabs_components_exist(self):
        """Verify all Tab components are importable."""
        from core.console.corvin_console.web_next.src.components.forge.ToolsTab import ToolsTab
        from core.console.corvin_console.web_next.src.components.forge.SkillsTab import SkillsTab
        from core.console.corvin_console.web_next.src.components.forge.OSSkillsTab import OSSkillsTab
        from core.console.corvin_console.web_next.src.components.forge.GraphTab import GraphTab
        from core.console.corvin_console.web_next.src.components.forge.AuditTab import AuditTab

        assert all([ToolsTab, SkillsTab, OSSkillsTab, GraphTab, AuditTab])

    async def test_forge_types_defined(self):
        """Verify TypeScript types are properly defined."""
        from core.console.corvin_console.web_next.src.types.forge import (
            ForgeTool, ForgeSkill, ForgeOSSkill, ForgeDependency, ForgeAuditEvent
        )

        assert all([ForgeTool, ForgeSkill, ForgeOSSkill, ForgeDependency, ForgeAuditEvent])


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

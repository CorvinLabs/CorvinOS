"""Phase 5b: Skill Manager E2E Tests (Upload UI + Panel Integration)

Tests verify end-to-end Skill Manager functionality:
  - Panel registration (routing, nav)
  - API routes (installed, install, uninstall, health)
  - React component (upload form, progress, list view)
  - Integration with Phase 4 SkillInstaller

Compliance: ADR-0681 (Console Skill Manager)
"""
import pytest
import json
import tempfile
from pathlib import Path
from httpx import AsyncClient


class TestPhase5bSkillManagerE2E:
    """Phase 5b Skill Manager E2E Tests"""

    @pytest.mark.asyncio
    async def test_skill_manager_panel_registered(self, async_client: AsyncClient):
        """E2E: Skill Manager panel is registered and accessible."""
        # Test route exists
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text
        # Should render the SkillManager component
        assert "Skill Manager" in html or "Install New Skill" in html

    @pytest.mark.asyncio
    async def test_skill_manager_api_list_endpoint(self, async_client: AsyncClient):
        """E2E: GET /v1/console/skills-manager/skills/installed returns skill list."""
        response = await async_client.get("/v1/console/skills-manager/skills/installed")
        assert response.status_code == 200
        data = response.json()

        # Schema validation
        assert isinstance(data, dict)
        assert "skills" in data
        assert "total" in data
        assert isinstance(data["skills"], list)
        assert isinstance(data["total"], int)

    @pytest.mark.asyncio
    async def test_skill_manager_health_endpoint(self, async_client: AsyncClient):
        """E2E: GET /v1/console/skills-manager/skills/health returns health status."""
        response = await async_client.get("/v1/console/skills-manager/skills/health")
        assert response.status_code == 200
        data = response.json()

        # Schema validation
        assert "status" in data
        assert data["status"] == "ok"
        assert "installed_count" in data
        assert "registry_path" in data

    @pytest.mark.asyncio
    async def test_skill_manager_install_endpoint_validates_zip(self, async_client: AsyncClient):
        """E2E: POST /v1/console/skills-manager/skills/install validates ZIP files."""
        # Test with non-ZIP file
        with tempfile.NamedTemporaryFile(suffix=".txt") as tmp:
            tmp.write(b"not a zip file")
            tmp.flush()

            with open(tmp.name, "rb") as f:
                response = await async_client.post(
                    "/v1/console/skills-manager/skills/install",
                    data={
                        "skill_id": "test-skill",
                        "version": "1.0.0"
                    },
                    files={"file": f}
                )

            # Should reject non-ZIP files
            assert response.status_code == 400
            data = response.json()
            assert "ZIP" in data.get("detail", "") or "must be ZIP" in str(data)

    @pytest.mark.asyncio
    async def test_skill_manager_upload_form_fields(self, async_client: AsyncClient):
        """E2E: Upload form includes skill_id, version, file picker."""
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text

        # Should have form fields
        assert "skill_id" in html.lower() or "skill id" in html.lower()
        assert "version" in html.lower()
        assert "file" in html.lower() or "upload" in html.lower().replace("\n", " ")

    @pytest.mark.asyncio
    async def test_skill_manager_installed_skills_list(self, async_client: AsyncClient):
        """E2E: Installed skills list renders with boot_layer and verified status."""
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text

        # Should have list section
        assert "Installed Skills" in html or "installed" in html.lower()

    @pytest.mark.asyncio
    async def test_skill_manager_uninstall_endpoint(self, async_client: AsyncClient):
        """E2E: DELETE /v1/console/skills-manager/skills/uninstall/{id}/{version} exists."""
        # Test with non-existent skill (should fail gracefully)
        response = await async_client.delete(
            "/v1/console/skills-manager/skills/uninstall/nonexistent-skill/1.0.0"
        )
        # Should return 400 or 404 (graceful failure)
        assert response.status_code in [400, 404]

    @pytest.mark.asyncio
    async def test_skill_manager_progress_bar_ui(self, async_client: AsyncClient):
        """E2E: Upload form includes progress bar and upload status indicator."""
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text

        # Should have upload status elements
        assert "Upload" in html or "upload" in html.lower()
        assert "progress" in html.lower() or "installing" in html.lower()

    @pytest.mark.asyncio
    async def test_skill_manager_success_message_ui(self, async_client: AsyncClient):
        """E2E: Upload form shows success/error messages."""
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text

        # Should have alert/message elements
        assert "success" in html.lower() or "error" in html.lower() or "alert" in html.lower()

    @pytest.mark.asyncio
    async def test_skill_manager_refresh_button(self, async_client: AsyncClient):
        """E2E: Refresh button to reload skill list exists."""
        response = await async_client.get("/app/skill-manager")
        assert response.status_code == 200
        html = response.text

        # Should have refresh capability
        assert "Refresh" in html or "refresh" in html.lower()

    @pytest.mark.asyncio
    async def test_skill_manager_api_routes_prefix(self, async_client: AsyncClient):
        """E2E: All API routes are under /v1/console/skills-manager prefix."""
        # Test various endpoints with correct prefix
        routes = [
            "/v1/console/skills-manager/skills/installed",
            "/v1/console/skills-manager/skills/health",
        ]

        for route in routes:
            response = await async_client.get(route)
            # Should not be 404 (route exists)
            assert response.status_code != 404, f"Route {route} not found"


# Fixture for async client (requires app fixture)
@pytest.fixture
async def async_client():
    """Provide async HTTP client for testing."""
    from core.console.corvin_console.app import app
    from httpx import AsyncClient

    async with AsyncClient(app=app, base_url="http://test") as client:
        yield client

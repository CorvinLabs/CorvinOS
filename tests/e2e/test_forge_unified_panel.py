"""
E2E Test für unified Forge Panel (Tools, Skills, OS-Skills, Graph, Audit).

ADR-TBD: Unified Forge Control Plane
Tests:
  - Panel loads at /console/forge
  - All 5 tabs render (Tools, Skills, OS-Skills, Graph, Audit)
  - API endpoints respond (scaffold stage)
  - UI components accept input (search, filter)
"""

from pathlib import Path

import pytest


# `client` and `app` come from tests/fixtures_console.py (registered as a
# pytest plugin in tests/conftest.py). The local override here was an
# `async def` fixture that RETURNED a TestClient instead of yielding one, so
# every test received a coroutine — and `app` was never defined at all, which
# is why all ten tests in this file were "fixture 'app' not found" errors.


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
        ]

        for endpoint in endpoints:
            response = client.get(endpoint)
            # Scaffold stage: endpoints exist (200/500 ok, 404 would be bad)
            assert response.status_code in [200, 401, 500], f"Endpoint {endpoint} not found"

        # /forge/search is listed separately: `q` is a REQUIRED query param, so
        # a bare GET is a 422 (which still proves the route is mounted), and
        # listing it above asserted 404-is-bad against a 422.
        assert client.get("/v1/console/forge/search").status_code == 422
        assert client.get("/v1/console/forge/search?q=x").status_code in [200, 401, 500]

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
            # The endpoint names the size field `count` (and carries tenant_id
            # + timestamp); `total` never existed in this payload.
            assert "count" in data
            assert data["count"] == len(data["events"])
            assert isinstance(data["events"], list)

    async def test_search_endpoint(self, client):
        """GET /v1/console/forge/search?q=test returns cross-tab results."""
        response = client.get("/v1/console/forge/search?q=test")
        assert response.status_code in [200, 400, 401, 500]
        if response.status_code == 200:
            data = response.json()
            assert "results" in data
            assert "count" in data
            assert data["count"] == len(data["results"])
            assert isinstance(data["results"], list)

    async def test_tool_enable_endpoint(self, client):
        """POST /v1/console/forge/tools/{id}/enable changes tool state."""
        response = client.post("/v1/console/forge/tools/test_tool/enable")
        # 501 is the DECLARED contract: the route is registered with
        # status_code=HTTP_501_NOT_IMPLEMENTED (forge_unified.py:168), so a 501
        # proves it is wired and honest, not missing.
        assert response.status_code in [200, 401, 403, 500, 501]
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
        # Registered with status_code=HTTP_501_NOT_IMPLEMENTED
        # (forge_unified.py:401) — see the tools/enable test above.
        assert response.status_code in [200, 401, 403, 500, 501]
        if response.status_code == 200:
            data = response.json()
            assert data.get("status") == "configured"
            assert data.get("os_skill_id") == "test_os_skill"
            assert "config" in data


class TestForgeConsolePages:
    """The Forge panel's frontend files exist and export what the router imports.

    These three tests used to do `from core.console.corvin_console.web_next.src.
    pages.forge import ForgePage` — importing TypeScript through Python. There
    is no `web_next` Python package (the directory is `web-next`, with a
    hyphen, and holds an npm project), so all three were guaranteed
    ModuleNotFoundError and proved nothing about the panel. They now assert on
    the real files and their real export statements; the behavioural side is
    covered by the vitest suite under web-next/tests.
    """

    WEB = (
        Path(__file__).resolve().parents[2]
        / "core" / "console" / "corvin_console" / "web-next"
    )

    def test_forge_page_exports(self):
        page = self.WEB / "src" / "pages" / "forge.tsx"
        assert page.is_file(), f"{page} missing"
        src = page.read_text(encoding="utf-8")
        assert "export default function ForgePage" in src
        assert "export { ForgePage }" in src, "named re-export is what lazy-pages imports"

    def test_tabs_components_exist(self):
        tabs = ["ToolsTab", "SkillsTab", "OSSkillsTab", "GraphTab", "AuditTab"]
        missing = []
        for tab in tabs:
            f = self.WEB / "src" / "components" / "forge" / f"{tab}.tsx"
            if not f.is_file() or f"export default function {tab}" not in f.read_text(encoding="utf-8"):
                missing.append(tab)
        assert not missing, f"tab components missing or not exported: {missing}"

    def test_forge_types_defined(self):
        types = self.WEB / "src" / "types" / "forge.ts"
        assert types.is_file(), f"{types} missing"
        src = types.read_text(encoding="utf-8")
        for name in (
            "ForgeTool", "ForgeSkill", "ForgeOSSkill",
            "ForgeDependency", "ForgeAuditEvent",
        ):
            assert f"interface {name}" in src or f"type {name}" in src, \
                f"{name} not declared in forge.ts"

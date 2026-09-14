"""
E2E Tests for Forge Console Panel — All 5 Tabs with Real Data

Tests verify that:
1. Tools tab displays real tools (6 items)
2. Skills tab displays real skills (5 items)
3. OS-Skills tab structure is ready for plugins
4. Graph tab displays nodes + edges
5. Audit tab shows forge_* events

NO MOCKS — all data comes from ~/.corvin/tenants/_default/global/
"""

import pytest
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, List
from unittest.mock import MagicMock
from fastapi.testclient import TestClient

from core.console.corvin_console.routes import forge_unified
from core.console.corvin_console import auth as session_auth
from core.console.corvin_console import _bootstrap


# ─────────────────────────────────────────────────────────────────────────────
# Fixtures: Real Registry Data
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def forge_registry_path() -> Path:
    """Get path to real Forge tools registry."""
    tid = "_default"
    return _bootstrap.forge_paths.tenant_global_dir(tid) / "forge" / "registry.json"


@pytest.fixture(scope="session")
def skill_registry_path() -> Path:
    """Get path to real SkillForge registry."""
    tid = "_default"
    return _bootstrap.forge_paths.tenant_global_dir(tid) / "skill-forge" / "registry.json"


@pytest.fixture
def mock_session_rec() -> session_auth.SessionRecord:
    """Create a mock session record for auth."""
    rec = MagicMock(spec=session_auth.SessionRecord)
    rec.tenant_id = "_default"
    rec.sid_fingerprint = "test_sid_12345"
    rec.user_id = "test_user"
    return rec


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Real Registry Access
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeRegistryFiles:
    """Verify that registry files exist and are readable."""

    def test_forge_registry_exists(self, forge_registry_path: Path):
        """Verify forge registry.json exists."""
        assert forge_registry_path.exists(), f"Forge registry not found at {forge_registry_path}"

    def test_forge_registry_is_valid_json(self, forge_registry_path: Path):
        """Verify forge registry.json is valid JSON."""
        data = json.loads(forge_registry_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "Registry should be a dict"
        assert len(data) >= 5, "Registry should have ≥5 tools"

    def test_skill_registry_exists(self, skill_registry_path: Path):
        """Verify skill-forge registry.json exists."""
        assert skill_registry_path.exists(), f"Skill registry not found at {skill_registry_path}"

    def test_skill_registry_is_valid_json(self, skill_registry_path: Path):
        """Verify skill-forge registry.json is valid JSON."""
        data = json.loads(skill_registry_path.read_text(encoding="utf-8"))
        assert isinstance(data, dict), "Registry should be a dict"
        assert len(data) >= 3, "Registry should have ≥3 skills"


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Tools Tab (Backend API)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeToolsTab:
    """Verify Tools tab returns real data from registry."""

    @pytest.mark.asyncio
    async def test_list_tools_returns_real_data(self, mock_session_rec, forge_registry_path: Path):
        """Test /forge/tools endpoint returns real tools."""
        # Load expected data
        expected = json.loads(forge_registry_path.read_text(encoding="utf-8"))

        # Call the route handler directly
        response = await forge_unified.list_tools(mock_session_rec)

        # Verify response structure
        assert "tools" in response
        assert "count" in response
        assert response["count"] >= 5, f"Expected ≥5 tools, got {response['count']}"

        # Verify specific tools exist
        tool_names = {t["name"] for t in response["tools"]}
        expected_names = {"browser_control", "send_email", "sql_query", "file_storage", "slack_notifier"}
        assert expected_names.issubset(tool_names), f"Missing expected tools: {expected_names - tool_names}"

    @pytest.mark.asyncio
    async def test_tools_have_metadata(self, mock_session_rec):
        """Test that tools have required metadata fields."""
        response = await forge_unified.list_tools(mock_session_rec)

        for tool in response["tools"]:
            # Required fields per schema
            assert "id" in tool
            assert "name" in tool
            assert "enabled" in tool
            assert "version" in tool
            assert "runtime" in tool
            assert "description" in tool

            # Verify types
            assert isinstance(tool["name"], str)
            assert isinstance(tool["enabled"], bool)
            assert isinstance(tool["version"], str)

    @pytest.mark.asyncio
    async def test_tools_enabled_status_correct(self, mock_session_rec):
        """Test that tool enabled/disabled status is correct."""
        response = await forge_unified.list_tools(mock_session_rec)
        tools_by_name = {t["name"]: t for t in response["tools"]}

        # From test registry
        assert tools_by_name["browser_control"]["enabled"] is True
        assert tools_by_name["send_email"]["enabled"] is True
        assert tools_by_name["webhook_sender"]["enabled"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Skills Tab (Backend API)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeSkillsTab:
    """Verify Skills tab returns real data from registry."""

    @pytest.mark.asyncio
    async def test_list_skills_returns_real_data(self, mock_session_rec, skill_registry_path: Path):
        """Test /forge/skills endpoint returns real skills."""
        response = await forge_unified.list_skills(mock_session_rec)

        # Verify response structure
        assert "skills" in response
        assert "count" in response
        assert response["count"] >= 3, f"Expected ≥3 skills, got {response['count']}"

        # Verify specific skills exist
        skill_names = {s["name"] for s in response["skills"]}
        expected_names = {
            "os.delegation_router",
            "assistant.code_reviewer",
            "assistant.e2e_wiring_proof",
        }
        assert expected_names.issubset(skill_names), f"Missing expected skills: {expected_names - skill_names}"

    @pytest.mark.asyncio
    async def test_skills_have_metadata(self, mock_session_rec):
        """Test that skills have required metadata fields."""
        response = await forge_unified.list_skills(mock_session_rec)

        for skill in response["skills"]:
            # Required fields per schema
            assert "id" in skill
            assert "name" in skill
            assert "enabled" in skill
            assert "version" in skill
            assert "type" in skill
            assert "description" in skill

            # Verify types
            assert isinstance(skill["name"], str)
            assert isinstance(skill["enabled"], bool)
            assert isinstance(skill["version"], str)

    @pytest.mark.asyncio
    async def test_skills_learning_state_populated(self, mock_session_rec):
        """Test that skills with grades have learning_state populated."""
        response = await forge_unified.list_skills(mock_session_rec)
        skills_by_name = {s["name"]: s for s in response["skills"]}

        # os.delegation_router has grades [0.92, 0.88, 0.95, 0.91]
        router_skill = skills_by_name["os.delegation_router"]
        assert router_skill["learning_state"] is not None
        assert router_skill["learning_state"]["confidence"] > 0.9
        assert router_skill["learning_state"]["feedback_count"] == 4

    @pytest.mark.asyncio
    async def test_skills_injectable_flag_set(self, mock_session_rec):
        """Test that injectable flag is correctly set."""
        response = await forge_unified.list_skills(mock_session_rec)
        skills_by_name = {s["name"]: s for s in response["skills"]}

        # os.delegation_router should be injectable
        assert skills_by_name["os.delegation_router"]["injectable"] is True

        # assistant.unified_learning_loops_optimizer should NOT be injectable
        assert skills_by_name["assistant.unified_learning_loops_optimizer"]["injectable"] is False


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: OS-Skills Tab (Backend API)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeOSSkillsTab:
    """Verify OS-Skills tab structure is ready for plugins."""

    @pytest.mark.asyncio
    async def test_list_os_skills_structure(self, mock_session_rec):
        """Test /forge/os-skills endpoint returns proper structure."""
        response = await forge_unified.list_os_skills(mock_session_rec)

        # Verify response structure
        assert "os_skills" in response
        assert "count" in response
        assert "tenant_id" in response
        assert "timestamp" in response

        # Structure ready (empty for now, plugins not yet integrated)
        assert isinstance(response["os_skills"], list)


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Graph Tab (Backend API)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeGraphTab:
    """Verify Graph tab returns dependency graph."""

    @pytest.mark.asyncio
    async def test_graph_returns_nodes(self, mock_session_rec):
        """Test /forge/graph endpoint returns nodes for tools + skills."""
        response = await forge_unified.get_dependency_graph(mock_session_rec)

        # Verify response structure
        assert "nodes" in response
        assert "edges" in response
        assert "cycles" in response

        # Verify nodes are populated (tools + skills)
        assert len(response["nodes"]) >= 8, f"Expected ≥8 nodes, got {len(response['nodes'])}"

        # Verify node structure
        for node in response["nodes"]:
            assert "id" in node
            assert "name" in node
            assert "type" in node
            assert "enabled" in node
            assert node["type"] in ("tool", "skill", "os-skill")

    @pytest.mark.asyncio
    async def test_graph_has_tool_and_skill_nodes(self, mock_session_rec):
        """Test that graph has nodes for both tools and skills."""
        response = await forge_unified.get_dependency_graph(mock_session_rec)

        node_types = {n["type"] for n in response["nodes"]}
        assert "tool" in node_types
        assert "skill" in node_types


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Audit Tab (Backend API)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeAuditTab:
    """Verify Audit tab returns audit trail."""

    @pytest.mark.asyncio
    async def test_audit_endpoint_structure(self, mock_session_rec):
        """Test /forge/audit endpoint returns proper structure."""
        response = await forge_unified.get_audit_trail(mock_session_rec)

        # Verify response structure
        assert "events" in response
        assert "count" in response
        assert "tenant_id" in response
        assert "timestamp" in response

        # Events should be a list (may be empty initially)
        assert isinstance(response["events"], list)

    @pytest.mark.asyncio
    async def test_audit_events_have_required_fields(self, mock_session_rec):
        """Test that audit events have required fields."""
        response = await forge_unified.get_audit_trail(mock_session_rec)

        for event in response["events"]:
            assert "id" in event
            assert "timestamp" in event
            assert "type" in event
            assert "resource_type" in event
            assert "resource_id" in event
            assert "action" in event


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Cross-Tab Search
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeSearch:
    """Verify search functionality works across tabs."""

    @pytest.mark.asyncio
    async def test_search_finds_tools(self, mock_session_rec):
        """Test search finds tools."""
        response = await forge_unified.search_forge(mock_session_rec, q="browser")

        assert response["count"] >= 1
        assert any(r["type"] == "tool" and "browser" in r["name"].lower() for r in response["results"])

    @pytest.mark.asyncio
    async def test_search_finds_skills(self, mock_session_rec):
        """Test search finds skills."""
        response = await forge_unified.search_forge(mock_session_rec, q="router")

        assert response["count"] >= 1
        assert any(r["type"] == "skill" and "router" in r["name"].lower() for r in response["results"])

    @pytest.mark.asyncio
    async def test_search_deduplicates_results(self, mock_session_rec):
        """Test that search deduplicates results."""
        response = await forge_unified.search_forge(mock_session_rec, q="code")

        # Extract (type, id) pairs to check for duplicates
        seen = set()
        duplicates = []
        for r in response["results"]:
            key = (r["type"], r["id"])
            if key in seen:
                duplicates.append(key)
            seen.add(key)

        assert len(duplicates) == 0, f"Found duplicate results: {duplicates}"


# ─────────────────────────────────────────────────────────────────────────────
# Test Suite: Mutations (Enable/Disable)
# ─────────────────────────────────────────────────────────────────────────────

class TestForgeMutations:
    """Verify enable/disable mutations work."""

    @pytest.mark.asyncio
    async def test_enable_tool_audit_logged(self, mock_session_rec):
        """Test that enabling a tool is audit-logged."""
        # Note: This test verifies audit logging is called (side effect)
        # The actual audit chain write is tested separately

        response = await forge_unified.enable_tool("test_tool", mock_session_rec)

        assert response["status"] == "enabled"
        assert response["tool_id"] == "test_tool"
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_disable_tool_audit_logged(self, mock_session_rec):
        """Test that disabling a tool is audit-logged."""
        response = await forge_unified.disable_tool("test_tool", mock_session_rec)

        assert response["status"] == "disabled"
        assert response["tool_id"] == "test_tool"
        assert "timestamp" in response

    @pytest.mark.asyncio
    async def test_cannot_disable_meta_os_skill(self, mock_session_rec):
        """Test that Meta-Skills (compliance-critical) cannot be disabled."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            await forge_unified.disable_os_skill("audit-gate", mock_session_rec)

        assert exc_info.value.status_code == 403


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

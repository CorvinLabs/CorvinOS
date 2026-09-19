"""
Unit tests for KG MCP tools.
Tests tool signatures, error handling, tenant isolation, and validation.
"""

import pytest
from unittest.mock import Mock, patch, MagicMock
import sys
import os

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from kg_client import KGClient, KGClientError, KGConnectionError, KGTimeoutError
from tool_registry import (
    QUERY_ENTITY_SCHEMA,
    SEARCH_SCHEMA,
    LIST_RELATIONS_SCHEMA,
    GET_SCHEMA_SCHEMA,
    ADR_DEPENDENCY_GRAPH_SCHEMA,
    AUDIT_TRAIL_LINKS_SCHEMA,
    get_tool_by_name,
    validate_tool_input,
)


class TestToolSignatures:
    """Test that tool schemas are correctly defined."""

    def test_query_entity_schema(self):
        """Query entity tool has correct schema."""
        assert QUERY_ENTITY_SCHEMA["name"] == "kg:query_entity"
        assert "inputSchema" in QUERY_ENTITY_SCHEMA
        assert "entity_id" in QUERY_ENTITY_SCHEMA["inputSchema"]["properties"]
        assert "entity_id" in QUERY_ENTITY_SCHEMA["inputSchema"]["required"]

    def test_search_schema(self):
        """Search tool has correct schema."""
        assert SEARCH_SCHEMA["name"] == "kg:search"
        assert "query" in SEARCH_SCHEMA["inputSchema"]["required"]
        assert SEARCH_SCHEMA["inputSchema"]["properties"]["limit"]["default"] == 10

    def test_all_tools_have_names(self):
        """All tools have valid names."""
        for tool_name in ["kg:query_entity", "kg:search", "kg:list_relations",
                         "kg:get_schema", "kg:adr_dependency_graph", "kg:audit_trail_links"]:
            tool = get_tool_by_name(tool_name)
            assert tool["name"] == tool_name


class TestErrorHandling:
    """Test error handling: KG unreachable, timeouts, etc."""

    @patch('kg_client.requests.Session.post')
    def test_kg_unreachable(self, mock_post):
        """KG unreachable returns error, not dummy data."""
        import requests
        mock_post.side_effect = requests.ConnectionError("Connection refused")

        client = KGClient(kg_url="http://localhost:8001")
        with pytest.raises(KGConnectionError) as exc:
            client.query_entity("ADR-0516")

        assert "Cannot reach KG server" in str(exc.value)

    @patch('kg_client.requests.Session.post')
    def test_kg_timeout(self, mock_post):
        """KG timeout returns error."""
        import requests
        mock_post.side_effect = requests.Timeout("5s timeout")

        client = KGClient(kg_url="http://localhost:8001", timeout_s=5.0)
        with pytest.raises(KGTimeoutError) as exc:
            client.query_entity("ADR-0516")

        assert "timeout" in str(exc.value).lower()

    @patch('kg_client.requests.Session.post')
    def test_kg_404_not_found(self, mock_post):
        """KG 404 returns structured error."""
        mock_response = Mock()
        mock_response.status_code = 404
        mock_response.text = "Entity not found"
        mock_post.return_value = mock_response

        client = KGClient(kg_url="http://localhost:8001")
        with pytest.raises(KGClientError) as exc:
            client.query_entity("INVALID-ID")

        assert "not found" in str(exc.value).lower()


class TestTenantIsolation:
    """Test tenant isolation in all queries."""

    @patch('kg_client.requests.Session.post')
    @patch('kg_client.current_tenant')
    def test_query_respects_tenant(self, mock_current_tenant, mock_post):
        """Query includes tenant_id in request."""
        mock_current_tenant.return_value = "tenant_a"
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"entity_id": "ADR-0516", "tenant_id": "tenant_a"}
        mock_post.return_value = mock_response

        client = KGClient()
        result = client.query_entity("ADR-0516")

        # Verify tenant_id was passed to the request
        call_args = mock_post.call_args
        assert call_args[1]["json"]["tenant_id"] == "tenant_a"

    @patch('kg_client.requests.Session.post')
    def test_query_with_explicit_tenant(self, mock_post):
        """Query can override tenant_id."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"entity_id": "ADR-0516"}
        mock_post.return_value = mock_response

        client = KGClient()
        client.query_entity("ADR-0516", tenant_id="custom_tenant")

        call_args = mock_post.call_args
        assert call_args[1]["json"]["tenant_id"] == "custom_tenant"


class TestInputValidation:
    """Test input validation for malformed queries."""

    def test_empty_search_query(self):
        """Empty search query raises error."""
        client = KGClient()
        with pytest.raises(KGClientError) as exc:
            client.search("")

        assert "empty" in str(exc.value).lower()

    def test_search_limit_bounds(self):
        """Search limit is clamped to [1, 100]."""
        with patch('kg_client.requests.Session.post') as mock_post:
            mock_response = Mock()
            mock_response.status_code = 200
            mock_response.json.return_value = {"results": []}
            mock_post.return_value = mock_response

            client = KGClient()
            client.search("test", limit=500)

            # Verify limit was clamped to 100
            call_args = mock_post.call_args
            assert call_args[1]["json"]["limit"] == 100

    def test_validate_tool_input_missing_required(self):
        """Validation rejects missing required fields."""
        with pytest.raises(ValueError) as exc:
            validate_tool_input("kg:query_entity", {})

        assert "entity_id" in str(exc.value)


class TestSchemaIntrospection:
    """Test schema introspection tool."""

    @patch('kg_client.requests.Session.post')
    def test_get_schema_success(self, mock_post):
        """Get schema returns correct field list."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "entity_type": "ADR",
            "fields": {"id": {"type": "string"}, "status": {"type": "string"}}
        }
        mock_post.return_value = mock_response

        client = KGClient()
        result = client.get_schema("ADR")

        assert result["entity_type"] == "ADR"
        assert "id" in result["fields"]


class TestADRDependencyGraph:
    """Test ADR dependency graph tracing."""

    @patch('kg_client.requests.Session.post')
    def test_adr_dependency_chain(self, mock_post):
        """ADR dependency graph traces depends_on chain."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "adr_id": "ADR-0516",
            "depends_on": ["ADR-0264"],
            "depended_by": [],
            "dag": {"ADR-0516": ["ADR-0264"]}
        }
        mock_post.return_value = mock_response

        client = KGClient()
        result = client.adr_dependency_graph("ADR-0516")

        assert result["adr_id"] == "ADR-0516"
        assert "ADR-0264" in result["depends_on"]


class TestAuditTrailLinks:
    """Test audit trail cross-reference."""

    @patch('kg_client.requests.Session.post')
    def test_audit_links_resolution(self, mock_post):
        """Audit trail links are resolved correctly."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "audit_links": [
                {"event_id": "ev-001", "event_type": "skill_executed", "timestamp": "2026-09-20T12:00:00Z"}
            ]
        }
        mock_post.return_value = mock_response

        client = KGClient()
        result = client.audit_trail_links("ADR-0516")

        assert len(result) == 1
        assert result[0]["event_type"] == "skill_executed"


class TestListRelations:
    """Test list relations (graph traversal)."""

    @patch('kg_client.requests.Session.post')
    def test_relations_returned_as_tuples(self, mock_post):
        """Relations are returned as (target, type, attrs) tuples."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "relations": [
                {"target_id": "ADR-0264", "relation_type": "depends_on", "attributes": {}}
            ]
        }
        mock_post.return_value = mock_response

        client = KGClient()
        result = client.list_relations("ADR-0516")

        assert len(result) == 1
        target_id, relation_type, attrs = result[0]
        assert target_id == "ADR-0264"
        assert relation_type == "depends_on"


class TestConcurrency:
    """Test concurrent query handling (basic)."""

    @patch('kg_client.requests.Session.post')
    def test_multiple_queries(self, mock_post):
        """Multiple queries can be issued sequentially."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"entity_id": "test"}
        mock_post.return_value = mock_response

        client = KGClient()
        for i in range(5):
            result = client.query_entity(f"ADR-{i:04d}")
            assert result is not None


class TestTimeoutHandling:
    """Test timeout behavior."""

    @patch('kg_client.requests.Session.post')
    def test_timeout_is_configurable(self, mock_post):
        """Timeout is passed to requests."""
        mock_response = Mock()
        mock_response.status_code = 200
        mock_response.json.return_value = {}
        mock_post.return_value = mock_response

        client = KGClient(timeout_s=3.0)
        client.query_entity("ADR-0516")

        # Verify timeout was passed
        call_args = mock_post.call_args
        assert call_args[1]["timeout"] == 3.0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

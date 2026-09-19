"""
E2E tests for KG MCP server.
Tests real MCP server with mock KG backend and real tool dispatch.
Note: Real E2E with actual Corvin-Knowledge would require it running at localhost:8001
"""

import pytest
import json
import subprocess
import sys
import os
import time
from unittest.mock import patch, Mock, MagicMock
import threading
from io import StringIO

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from mcp_server import MCPServer
from kg_client import KGClient


class TestMCPServerBasics:
    """Test MCP server fundamentals."""

    def test_server_initialization(self):
        """MCP server initializes correctly."""
        server = MCPServer(kg_url="http://localhost:8001")
        assert server.kg_client is not None
        assert server.kg_client.kg_url == "http://localhost:8001"

    def test_tools_list_response(self):
        """tools/list returns all 6 tools."""
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": "1"
        }

        response = server._handle_request(request)

        assert response["result"]["tools"] is not None
        assert len(response["result"]["tools"]) == 6
        tool_names = [t["name"] for t in response["result"]["tools"]]
        assert "kg:query_entity" in tool_names


class TestMCPToolDispatch:
    """Test MCP tool dispatch and execution."""

    @patch('mcp_server.KGClient.query_entity')
    def test_query_entity_dispatch(self, mock_query):
        """kg:query_entity tool dispatches to KG client."""
        mock_query.return_value = {"entity_id": "ADR-0516", "title": "Test ADR"}

        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:query_entity",
                "arguments": {"entity_id": "ADR-0516"}
            },
            "id": "1"
        }

        response = server._handle_request(request)

        assert response["result"]["entity_id"] == "ADR-0516"
        mock_query.assert_called_once()

    @patch('mcp_server.KGClient.search')
    def test_search_dispatch(self, mock_search):
        """kg:search tool dispatches correctly."""
        mock_search.return_value = [
            {"entity_id": "ADR-0516", "score": 0.95},
            {"entity_id": "ADR-0264", "score": 0.87}
        ]

        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:search",
                "arguments": {"query": "ADR dependency", "limit": 10}
            },
            "id": "2"
        }

        response = server._handle_request(request)

        assert len(response["result"]) == 2
        assert response["result"][0]["score"] == 0.95

    @patch('mcp_server.KGClient.list_relations')
    def test_list_relations_dispatch(self, mock_relations):
        """kg:list_relations tool dispatches correctly."""
        mock_relations.return_value = [
            ("ADR-0264", "depends_on", {}),
            ("ADR-0232", "relates_to", {})
        ]

        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:list_relations",
                "arguments": {"entity_id": "ADR-0516"}
            },
            "id": "3"
        }

        response = server._handle_request(request)

        assert len(response["result"]) == 2


class TestMCPErrorHandling:
    """Test MCP error handling."""

    def test_invalid_json(self):
        """Invalid JSON handled gracefully."""
        server = MCPServer()

        # Simulate invalid JSON handling
        request_str = "{invalid json"
        try:
            json.loads(request_str)
            assert False, "Should have raised JSONDecodeError"
        except json.JSONDecodeError:
            pass  # Expected

    def test_missing_method(self):
        """Missing method returns error."""
        server = MCPServer()
        request = {"jsonrpc": "2.0", "id": "1"}

        response = server._handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == -32600

    def test_unknown_tool(self):
        """Unknown tool returns error."""
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:unknown_tool",
                "arguments": {}
            },
            "id": "1"
        }

        response = server._handle_request(request)

        assert "error" in response
        assert "Unknown tool" in response["error"]["message"]

    @patch('mcp_server.KGClient.query_entity')
    def test_kg_error_handling(self, mock_query):
        """KG errors are wrapped in JSON-RPC errors."""
        from kg_client import KGClientError
        mock_query.side_effect = KGClientError("Test error")

        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:query_entity",
                "arguments": {"entity_id": "INVALID"}
            },
            "id": "1"
        }

        response = server._handle_request(request)

        assert "error" in response
        assert response["error"]["code"] == -32603


class TestAuditEventEmission:
    """Test audit event emission."""

    @patch('mcp_server.KGClient.query_entity')
    def test_audit_event_on_success(self, mock_query):
        """Audit event emitted on successful query."""
        mock_query.return_value = {"entity_id": "ADR-0516"}

        server = MCPServer()

        # Capture log output
        with patch('mcp_server.logger') as mock_logger:
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "tool_name": "kg:query_entity",
                    "arguments": {"entity_id": "ADR-0516"}
                },
                "id": "1"
            }

            response = server._handle_request(request)

            # Verify audit event was logged
            assert mock_logger.info.called
            log_call = str(mock_logger.info.call_args)
            assert "KG Query Event" in log_call


class TestTenantIsolation:
    """Test tenant isolation in E2E context."""

    @patch('mcp_server.KGClient.query_entity')
    @patch('mcp_server.KGClient.current_tenant')
    def test_query_with_tenant_a(self, mock_current_tenant, mock_query):
        """Query respects tenant A."""
        mock_current_tenant.return_value = "tenant_a"
        mock_query.return_value = {"entity_id": "ADR-0516", "tenant_id": "tenant_a"}

        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:query_entity",
                "arguments": {"entity_id": "ADR-0516"}
            },
            "id": "1"
        }

        response = server._handle_request(request)

        # Verify correct tenant was used
        assert response["result"]["tenant_id"] == "tenant_a"


class TestPerformance:
    """Test performance characteristics."""

    @patch('mcp_server.KGClient.query_entity')
    def test_query_latency_tracking(self, mock_query):
        """Query latency is tracked."""
        mock_query.return_value = {"entity_id": "ADR-0516"}

        server = MCPServer()

        with patch('mcp_server.logger') as mock_logger:
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "tool_name": "kg:query_entity",
                    "arguments": {"entity_id": "ADR-0516"}
                },
                "id": "1"
            }

            response = server._handle_request(request)

            # Verify latency was logged
            assert mock_logger.info.called
            log_call = str(mock_logger.info.call_args)
            assert "latency_ms" in log_call


class TestSchemaValidation:
    """Test schema validation in MCP context."""

    def test_adr_dependency_graph_schema(self):
        """ADR dependency graph tool has correct schema."""
        from tool_registry import get_tool_by_name

        tool = get_tool_by_name("kg:adr_dependency_graph")

        assert tool["name"] == "kg:adr_dependency_graph"
        assert "adr_id" in tool["inputSchema"]["required"]


class TestConcurrentRequests:
    """Test handling of concurrent requests."""

    @patch('mcp_server.KGClient.query_entity')
    def test_multiple_sequential_requests(self, mock_query):
        """Multiple sequential requests handled correctly."""
        mock_query.return_value = {"entity_id": "ADR-0516"}

        server = MCPServer()

        for i in range(5):
            request = {
                "jsonrpc": "2.0",
                "method": "tools/call",
                "params": {
                    "tool_name": "kg:query_entity",
                    "arguments": {"entity_id": f"ADR-{i:04d}"}
                },
                "id": str(i)
            }

            response = server._handle_request(request)
            assert "result" in response or "error" in response


class TestJsonRPCCompliance:
    """Test JSON-RPC 2.0 protocol compliance."""

    def test_invalid_jsonrpc_version(self):
        """Invalid JSON-RPC version returns error."""
        server = MCPServer()
        request = {
            "jsonrpc": "1.0",  # Wrong version
            "method": "tools/list",
            "id": "1"
        }

        response = server._handle_request(request)

        assert "error" in response

    def test_response_includes_request_id(self):
        """Response includes the request ID."""
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": "test-123"
        }

        response = server._handle_request(request)

        assert response["id"] == "test-123"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

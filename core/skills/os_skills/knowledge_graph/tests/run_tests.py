#!/usr/bin/env python3
"""
Standalone test runner for KG MCP integration.
Does not require pytest - runs all tests and reports results.
"""

import sys
import os
from pathlib import Path
import json
from io import StringIO
import traceback

# Add parent directory to path
parent_dir = Path(__file__).parent.parent
sys.path.insert(0, str(parent_dir))

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
    ALL_TOOLS
)
from mcp_server import MCPServer


class TestRunner:
    """Simple test runner without pytest."""

    def __init__(self):
        self.passed = 0
        self.failed = 0
        self.tests = []

    def test(self, name: str, func):
        """Register and run a test."""
        try:
            func()
            self.passed += 1
            self.tests.append(("✓", name, None))
            print(f"✓ {name}")
        except AssertionError as e:
            self.failed += 1
            self.tests.append(("✗", name, str(e)))
            print(f"✗ {name}: {e}")
        except Exception as e:
            self.failed += 1
            self.tests.append(("✗", name, f"{type(e).__name__}: {e}"))
            print(f"✗ {name}: {type(e).__name__}: {e}")

    def report(self):
        """Print test report."""
        print("\n" + "="*70)
        print(f"Test Results: {self.passed} passed, {self.failed} failed")
        print("="*70)

        if self.failed > 0:
            print("\nFailed tests:")
            for status, name, error in self.tests:
                if status == "✗":
                    print(f"  - {name}: {error}")

        return self.failed == 0


def run_unit_tests():
    """Run unit tests for KG MCP tools."""
    runner = TestRunner()

    print("\n" + "="*70)
    print("UNIT TESTS: KG MCP Tools")
    print("="*70)

    # Tool signatures
    def test_query_entity_schema():
        assert QUERY_ENTITY_SCHEMA["name"] == "kg:query_entity"
        assert "inputSchema" in QUERY_ENTITY_SCHEMA
        assert "entity_id" in QUERY_ENTITY_SCHEMA["inputSchema"]["properties"]

    def test_search_schema():
        assert SEARCH_SCHEMA["name"] == "kg:search"
        assert "query" in SEARCH_SCHEMA["inputSchema"]["required"]

    def test_list_relations_schema():
        assert LIST_RELATIONS_SCHEMA["name"] == "kg:list_relations"

    def test_get_schema_schema():
        assert GET_SCHEMA_SCHEMA["name"] == "kg:get_schema"

    def test_adr_dependency_schema():
        assert ADR_DEPENDENCY_GRAPH_SCHEMA["name"] == "kg:adr_dependency_graph"

    def test_audit_trail_links_schema():
        assert AUDIT_TRAIL_LINKS_SCHEMA["name"] == "kg:audit_trail_links"

    # Tool registry
    def test_all_tools_exist():
        assert len(ALL_TOOLS) == 6

    def test_get_tool_by_name():
        tool = get_tool_by_name("kg:query_entity")
        assert tool["name"] == "kg:query_entity"

    def test_get_unknown_tool():
        try:
            get_tool_by_name("kg:unknown")
            assert False, "Should have raised ValueError"
        except ValueError:
            pass

    # Input validation
    def test_validate_required_field():
        try:
            validate_tool_input("kg:query_entity", {})
            assert False, "Should have raised ValueError"
        except ValueError as e:
            assert "entity_id" in str(e)

    def test_validate_valid_input():
        result = validate_tool_input("kg:query_entity", {"entity_id": "ADR-0516"})
        assert result is True

    def test_validate_search_limit():
        result = validate_tool_input("kg:search", {"query": "test"})
        assert result is True

    # KG Client instantiation
    def test_kg_client_init():
        client = KGClient(kg_url="http://localhost:8001", timeout_s=5.0)
        assert client.kg_url == "http://localhost:8001"
        assert client.timeout_s == 5.0

    # Run all unit tests
    runner.test("Tool signature: query_entity", test_query_entity_schema)
    runner.test("Tool signature: search", test_search_schema)
    runner.test("Tool signature: list_relations", test_list_relations_schema)
    runner.test("Tool signature: get_schema", test_get_schema_schema)
    runner.test("Tool signature: adr_dependency_graph", test_adr_dependency_schema)
    runner.test("Tool signature: audit_trail_links", test_audit_trail_links_schema)
    runner.test("Tool registry: all 6 tools exist", test_all_tools_exist)
    runner.test("Tool registry: get_tool_by_name", test_get_tool_by_name)
    runner.test("Tool registry: unknown tool error", test_get_unknown_tool)
    runner.test("Input validation: missing required field", test_validate_required_field)
    runner.test("Input validation: valid input", test_validate_valid_input)
    runner.test("Input validation: search limit", test_validate_search_limit)
    runner.test("KGClient: initialization", test_kg_client_init)

    return runner.report()


def run_mcp_server_tests():
    """Run tests for MCP server basics."""
    runner = TestRunner()

    print("\n" + "="*70)
    print("MCP SERVER TESTS")
    print("="*70)

    # MCP Server basics
    def test_server_init():
        server = MCPServer(kg_url="http://localhost:8001")
        assert server.kg_client is not None

    def test_tools_list_request():
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/list",
            "id": "1"
        }
        response = server._handle_request(request)
        assert "result" in response
        assert "tools" in response["result"]
        assert len(response["result"]["tools"]) == 6

    def test_invalid_method():
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "unknown_method",
            "id": "1"
        }
        response = server._handle_request(request)
        assert "error" in response

    def test_missing_required_field():
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:query_entity",
                "arguments": {}
            },
            "id": "1"
        }
        response = server._handle_request(request)
        assert "error" in response

    def test_unknown_tool():
        server = MCPServer()
        request = {
            "jsonrpc": "2.0",
            "method": "tools/call",
            "params": {
                "tool_name": "kg:unknown",
                "arguments": {}
            },
            "id": "1"
        }
        response = server._handle_request(request)
        assert "error" in response
        assert "Unknown tool" in response["error"]["message"]

    def test_invalid_jsonrpc_version():
        server = MCPServer()
        request = {
            "jsonrpc": "1.0",
            "method": "tools/list",
            "id": "1"
        }
        response = server._handle_request(request)
        assert "error" in response

    # Run all MCP tests
    runner.test("MCP Server: initialization", test_server_init)
    runner.test("MCP Server: tools/list request", test_tools_list_request)
    runner.test("MCP Server: invalid method error", test_invalid_method)
    runner.test("MCP Server: missing required field", test_missing_required_field)
    runner.test("MCP Server: unknown tool error", test_unknown_tool)
    runner.test("MCP Server: invalid JSON-RPC version", test_invalid_jsonrpc_version)

    return runner.report()


def main():
    """Run all tests."""
    print("\n" + "="*70)
    print("KG MCP INTEGRATION TEST SUITE")
    print("="*70)

    unit_pass = run_unit_tests()
    mcp_pass = run_mcp_server_tests()

    print("\n" + "="*70)
    if unit_pass and mcp_pass:
        print("✓ ALL TESTS PASSED")
        print("="*70)
        return 0
    else:
        print("✗ SOME TESTS FAILED")
        print("="*70)
        return 1


if __name__ == "__main__":
    sys.exit(main())

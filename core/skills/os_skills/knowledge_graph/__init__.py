"""
Knowledge Graph MCP Integration

Provides MCP tools for querying the Corvin-Knowledge Graph.
- 6 MCP tools for entity/relation/ADR queries
- Tenant-scoped queries with fail-closed error handling
- Audit trail integration for all queries
"""

from kg_client import KGClient, KGClientError, KGConnectionError, KGTimeoutError
from mcp_server import MCPServer
from tool_registry import ALL_TOOLS, get_tool_by_name, validate_tool_input

__all__ = [
    "KGClient",
    "KGClientError",
    "KGConnectionError",
    "KGTimeoutError",
    "MCPServer",
    "ALL_TOOLS",
    "get_tool_by_name",
    "validate_tool_input",
]

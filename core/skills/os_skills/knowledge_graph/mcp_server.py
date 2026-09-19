"""
KG MCP Server — stdio-based MCP server for Knowledge Graph queries.
Implements JSON-RPC protocol, tool dispatch, audit trail integration.
"""

import json
import logging
import sys
import traceback
from typing import Dict, Any, Optional
from datetime import datetime
import time

from kg_client import KGClient, KGClientError, KGConnectionError, KGTimeoutError
from tool_registry import ALL_TOOLS, get_tool_by_name, validate_tool_input

logger = logging.getLogger(__name__)


class MCPServer:
    """
    MCP Server for KG queries.

    Transport: stdio (JSON-RPC 2.0)
    Tools: 6 KG query tools
    Audit: Every query logged to audit trail
    """

    def __init__(self, kg_url: str = "http://localhost:8001", kg_timeout_s: float = 5.0):
        """
        Initialize MCP server.

        Args:
            kg_url: Base URL of Corvin-Knowledge web_api
            kg_timeout_s: Request timeout
        """
        self.kg_client = KGClient(kg_url=kg_url, timeout_s=kg_timeout_s)
        self.request_id_counter = 0

    def run(self):
        """
        Main server loop: read JSON-RPC requests from stdin, dispatch to tools, write responses to stdout.
        """
        try:
            for line in sys.stdin:
                line = line.strip()
                if not line:
                    continue

                try:
                    request = json.loads(line)
                    response = self._handle_request(request)
                    sys.stdout.write(json.dumps(response) + '\n')
                    sys.stdout.flush()
                except json.JSONDecodeError as e:
                    error_response = {
                        "jsonrpc": "2.0",
                        "error": {
                            "code": -32700,
                            "message": "Parse error",
                            "data": str(e)
                        },
                        "id": None
                    }
                    sys.stdout.write(json.dumps(error_response) + '\n')
                    sys.stdout.flush()
        except KeyboardInterrupt:
            logger.info("MCP server interrupted")
        except Exception as e:
            logger.error(f"MCP server error: {e}", exc_info=True)

    def _handle_request(self, request: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle JSON-RPC 2.0 request.

        Args:
            request: JSON-RPC request dict

        Returns:
            JSON-RPC response dict
        """
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params", {})

        # Validate request
        if request.get("jsonrpc") != "2.0":
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Invalid Request"},
                "id": request_id
            }

        if not method:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32600, "message": "Missing method"},
                "id": request_id
            }

        # Dispatch to handler
        if method == "tools/list":
            return self._handle_tools_list(request_id)
        elif method == "tools/call":
            return self._handle_tools_call(request_id, params)
        else:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32601, "message": f"Method not found: {method}"},
                "id": request_id
            }

    def _handle_tools_list(self, request_id: Optional[str]) -> Dict[str, Any]:
        """
        Handle tools/list RPC call.
        Returns: list of all available KG tools
        """
        return {
            "jsonrpc": "2.0",
            "result": {"tools": ALL_TOOLS},
            "id": request_id
        }

    def _handle_tools_call(self, request_id: Optional[str], params: Dict[str, Any]) -> Dict[str, Any]:
        """
        Handle tools/call RPC call.

        Args:
            request_id: JSON-RPC request ID
            params: {tool_name, arguments}

        Returns:
            JSON-RPC response
        """
        tool_name = params.get("tool_name")
        arguments = params.get("arguments", {})

        if not tool_name:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": "Missing tool_name"},
                "id": request_id
            }

        # Validate tool name
        try:
            get_tool_by_name(tool_name)
        except ValueError:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Unknown tool: {tool_name}"},
                "id": request_id
            }

        # Validate inputs
        try:
            validate_tool_input(tool_name, arguments)
        except ValueError as e:
            return {
                "jsonrpc": "2.0",
                "error": {"code": -32602, "message": f"Invalid arguments: {str(e)}"},
                "id": request_id
            }

        # Call tool and emit audit event
        start_time = time.time()
        try:
            result = self._call_kg_tool(tool_name, arguments)
            latency_ms = int((time.time() - start_time) * 1000)

            # Emit audit event (success)
            self._emit_audit_event(
                tool_name=tool_name,
                input_params=arguments,
                result_count=self._count_results(result),
                latency_ms=latency_ms,
                status="success",
                error=None
            )

            return {
                "jsonrpc": "2.0",
                "result": result,
                "id": request_id
            }

        except (KGClientError, KGConnectionError, KGTimeoutError) as e:
            latency_ms = int((time.time() - start_time) * 1000)

            # Emit audit event (failure)
            self._emit_audit_event(
                tool_name=tool_name,
                input_params=arguments,
                result_count=0,
                latency_ms=latency_ms,
                status="error",
                error=str(e)
            )

            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": f"Tool error: {str(e)}",
                    "data": {"tool_name": tool_name, "error_type": type(e).__name__}
                },
                "id": request_id
            }

        except Exception as e:
            latency_ms = int((time.time() - start_time) * 1000)

            # Emit audit event (unexpected error)
            self._emit_audit_event(
                tool_name=tool_name,
                input_params=arguments,
                result_count=0,
                latency_ms=latency_ms,
                status="error",
                error=f"Unexpected error: {str(e)}"
            )

            logger.error(f"Unexpected error in tool {tool_name}: {e}", exc_info=True)
            return {
                "jsonrpc": "2.0",
                "error": {
                    "code": -32603,
                    "message": "Internal server error",
                    "data": {"error": str(e)}
                },
                "id": request_id
            }

    def _call_kg_tool(self, tool_name: str, arguments: Dict[str, Any]) -> Any:
        """
        Call KG client method based on tool name.

        Args:
            tool_name: MCP tool name (e.g., "kg:query_entity")
            arguments: Tool arguments

        Returns:
            Tool result

        Raises:
            KGClientError: On KG-level errors
        """
        if tool_name == "kg:query_entity":
            return self.kg_client.query_entity(
                entity_id=arguments["entity_id"],
                tenant_id=arguments.get("tenant_id")
            )

        elif tool_name == "kg:search":
            return self.kg_client.search(
                query=arguments["query"],
                limit=arguments.get("limit", 10),
                tenant_id=arguments.get("tenant_id")
            )

        elif tool_name == "kg:list_relations":
            return self.kg_client.list_relations(
                entity_id=arguments["entity_id"],
                tenant_id=arguments.get("tenant_id")
            )

        elif tool_name == "kg:get_schema":
            return self.kg_client.get_schema(
                entity_type=arguments["entity_type"],
                tenant_id=arguments.get("tenant_id")
            )

        elif tool_name == "kg:adr_dependency_graph":
            return self.kg_client.adr_dependency_graph(
                adr_id=arguments["adr_id"],
                tenant_id=arguments.get("tenant_id")
            )

        elif tool_name == "kg:audit_trail_links":
            return self.kg_client.audit_trail_links(
                entity_id=arguments["entity_id"],
                tenant_id=arguments.get("tenant_id")
            )

        else:
            raise KGClientError(f"Unknown tool: {tool_name}")

    def _emit_audit_event(
        self,
        tool_name: str,
        input_params: Dict[str, Any],
        result_count: int,
        latency_ms: int,
        status: str,
        error: Optional[str] = None
    ):
        """
        Emit audit event for KG query.

        This is a placeholder — in production, this would emit to the audit chain.
        For now, we log it locally.

        Args:
            tool_name: MCP tool name
            input_params: Input parameters
            result_count: Number of results returned
            latency_ms: Query latency in milliseconds
            status: "success" or "error"
            error: Error message (if status == "error")
        """
        event = {
            "event_type": "kg_query",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "tool_name": tool_name,
            "query_input": input_params,
            "result_count": result_count,
            "latency_ms": latency_ms,
            "status": status,
        }
        if error:
            event["error"] = error

        logger.info(f"KG Query Event: {json.dumps(event)}")

    def _count_results(self, result: Any) -> int:
        """
        Count number of results returned.

        Args:
            result: Tool result

        Returns:
            Number of results (0 for None/empty, 1 for dict, len for list)
        """
        if result is None:
            return 0
        elif isinstance(result, dict):
            # Dict with "results" key
            if "results" in result:
                return len(result["results"])
            return 1
        elif isinstance(result, list):
            return len(result)
        else:
            return 1


def main():
    """
    Entry point: start MCP server.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[logging.StreamHandler(sys.stderr)]
    )

    try:
        server = MCPServer(kg_url="http://localhost:8001", kg_timeout_s=5.0)
        logger.info("KG MCP Server starting on stdio transport")
        server.run()
    except Exception as e:
        logger.error(f"MCP Server failed to start: {e}", exc_info=True)
        sys.exit(1)


if __name__ == "__main__":
    main()

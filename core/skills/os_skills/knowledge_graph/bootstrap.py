"""
Bootstrap wiring for KG MCP integration.
Starts the KG MCP server at CorvinOS boot time.
"""

import subprocess
import sys
import os
import logging
from pathlib import Path

logger = logging.getLogger(__name__)


def ensure_knowledge_graph_mcp() -> bool:
    """
    Ensure KG MCP server is running.

    This is called during CorvinOS bootstrap (via seed_builtin).
    The MCP server runs as a subprocess and communicates via stdio.

    Returns:
        True if server started/running, False if failed
    """
    try:
        # Get path to MCP server script
        mcp_server_path = Path(__file__).parent / "mcp_server.py"

        if not mcp_server_path.exists():
            logger.error(f"MCP server script not found: {mcp_server_path}")
            return False

        # Verify imports work
        try:
            from kg_client import KGClient
            from mcp_server import MCPServer
            logger.info("KG MCP modules imported successfully")
        except ImportError as e:
            logger.error(f"Failed to import KG MCP modules: {e}")
            return False

        # Create a health check: try to instantiate the server
        try:
            server = MCPServer(kg_url="http://localhost:8001", kg_timeout_s=5.0)
            logger.info("KG MCP server instantiated successfully")
        except Exception as e:
            logger.error(f"Failed to instantiate KG MCP server: {e}")
            return False

        logger.info("KG MCP bootstrap: ready (server will be launched via corvin_operator)")
        return True

    except Exception as e:
        logger.error(f"KG MCP bootstrap failed: {e}", exc_info=True)
        return False


def start_mcp_server_subprocess() -> bool:
    """
    Start KG MCP server as a subprocess (for standalone testing).

    In production, this is managed by corvin_operator.mcp_manager.
    This function is for manual/debug startup.

    Returns:
        True if subprocess started, False if failed
    """
    try:
        mcp_server_path = Path(__file__).parent / "mcp_server.py"

        # Start subprocess
        process = subprocess.Popen(
            [sys.executable, str(mcp_server_path)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd=str(mcp_server_path.parent)
        )

        logger.info(f"KG MCP server subprocess started (PID: {process.pid})")
        return True

    except Exception as e:
        logger.error(f"Failed to start MCP server subprocess: {e}", exc_info=True)
        return False

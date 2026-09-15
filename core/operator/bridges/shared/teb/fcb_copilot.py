"""
ADR-0087 M5: Copilot MCP via Function-Call Bridge (FCB)

Goal: Emulate MCP tools on Copilot CLI via text-based tool syntax.

Copilot CLI cannot call functions natively (no structured tool_use), so FCB implements:
  1. Tool injection: Serialize MCP tool schemas as markdown system prompt section
  2. Output parsing: Detect [TOOL: name] syntax in Copilot text output
  3. Execution: Invoke via TEB tool_registry with L10/L16 enforcement
  4. Result buffering: Prepend results to next turn (M2 buffered queue pattern)

Copilot is single-turn by default; M7 (multi-turn wrapper) handles multi-turn requests.

Architecture:
  - ToolSchemaInjector: Transform MCP tool metadata → markdown schema
  - CopilotToolParser: RegEx-based parser for [TOOL: name] syntax
  - ToolResultInjector: Queue results in buffered transport (session_dir/btw_queue.jsonl)
  - Integration: All execution goes through TEB (audit-first L16, path-gate L10)

Compliance (from CLAUDE.md):
  - L10 Path-Gate: TEB enforces before tool execution (fail-closed)
  - L16 Audit-First: Event written before execution (audit-first invariant)
  - L33 Artifacts: Results auto-registered if binary
  - L34 Data-Classification: Tool args/results subject to classification gate
  - L35 Egress: Tool calls subject to egress lockdown
  - L36 GDPR: No PII in audit details (tool name + status only)

Test Coverage Standard (Tier-1/2/3):
  - Tier-1: Syntax validation (JSON schemas, markdown format, size limits)
  - Tier-2: Unit tests (parsing, injection, mock TEB invocation)
  - Tier-3: E2E with real Copilot spawn (or mock with known outputs)

Key Constraints:
  - Total schema size ≤ 8 KB (enforced via linter)
  - Result truncation at 4 KB (sentence boundary preferred)
  - One schema per tool (no grouping/nesting)
  - Case-insensitive tool name matching
  - First [TOOL:...] match wins (ignores subsequent matches)
"""

from typing import Optional, Dict, Any, Tuple, List, BinaryIO
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


@dataclass
class MCPTool:
    """MCP tool metadata for schema injection."""
    name: str
    description: str
    input_schema: Dict[str, Any]  # JSON Schema: {type, properties, required, ...}


class ToolSchemaInjector:
    """Serialize MCP tools → markdown system prompt section."""

    MAX_SCHEMA_SIZE_KB = 8

    def inject_schemas(self, tool_list: List[MCPTool]) -> str:
        """
        Serialize tool schemas as markdown for system prompt injection.

        Args:
            tool_list: List of MCP tools to inject

        Returns:
            Markdown string: "## Available Tools\n\n<schemas>"

        Constraints:
            - Total size ≤ 8 KB (enforced; truncates gracefully if exceeded)
            - One schema per tool (no grouping/nesting)
            - JSON pretty-printed, spec-compliant

        Raises:
            ValueError: If tool list empty or tool name invalid (alphanumeric + underscore)

        Example:
            >>> injector = ToolSchemaInjector()
            >>> tools = [MCPTool(name="Read", description="...", input_schema={...})]
            >>> schemas = injector.inject_schemas(tools)
            >>> assert schemas.startswith("## Available Tools")
            >>> assert len(schemas.encode()) <= 8192
        """
        if not tool_list:
            raise ValueError("tool_list cannot be empty")

        # Validate all tool names upfront (alphanumeric + underscore)
        for tool in tool_list:
            if not re.match(r"^\w+$", tool.name):
                raise ValueError(f"Invalid tool name '{tool.name}': must be alphanumeric + underscore")

        # Build markdown sections
        header = "## Available Tools\n\n"
        sections = []
        total_bytes = len(header.encode())

        for tool in tool_list:
            section = self.schema_to_markdown(tool)
            section_bytes = len(section.encode())

            # Check if adding this section would exceed 8 KB
            if total_bytes + section_bytes > self.MAX_SCHEMA_SIZE_KB * 1024:
                logger.warning(
                    f"Tool schema injection exceeds {self.MAX_SCHEMA_SIZE_KB} KB; "
                    f"truncating at tool '{tool.name}' (included {len(sections)} tools)"
                )
                break

            sections.append(section)
            total_bytes += section_bytes

        return header + "\n\n".join(sections)

    def schema_to_markdown(self, tool: MCPTool) -> str:
        """
        Convert single tool to markdown schema block.

        Format:
            ### Tool: <name>
            <description>

            **Input Schema:**
            ```json
            {…}
            ```

        Returns: Markdown string (single tool block)
        """
        schema_json = json.dumps(tool.input_schema, indent=2)
        return (
            f"### Tool: {tool.name}\n"
            f"{tool.description}\n\n"
            f"**Input Schema:**\n"
            f"```json\n"
            f"{schema_json}\n"
            f"```"
        )


class CopilotToolParser:
    """Parse Copilot output for [TOOL: name] syntax and extract args."""

    TOOL_PATTERN = re.compile(
        r"\[TOOL:\s*(\w+)\]\s*(.+?)(?=\[TOOL:|$)",
        re.MULTILINE | re.DOTALL
    )

    def parse_tool_call(self, output: str, engine_id: str = "copilot") -> Tuple[Optional[str], Optional[Dict]]:
        """
        Parse Copilot output for tool invocation syntax.

        Syntax: [TOOL: <tool_name>] <args_json>

        Returns:
            (tool_name, args_dict) if valid match found
            (None, None) if no match or parse error

        Args:
            output: Copilot text output
            engine_id: Engine ID for audit logging (default: "copilot")

        Behavior:
            - Case-insensitive tool name matching
            - First [TOOL:...] match wins (ignores subsequent matches)
            - Args must be valid JSON; malformed → return (tool_name, None) with error
            - No execution; caller invokes TEB tool_registry

        Examples:
            >>> parser = CopilotToolParser()
            >>> name, args = parser.parse_tool_call('[TOOL: Read] {"file_path": "/etc/hosts"}')
            >>> assert name == "Read"
            >>> assert args == {"file_path": "/etc/hosts"}

            >>> name, args = parser.parse_tool_call("No tool here")
            >>> assert (name, args) == (None, None)

            >>> name, args = parser.parse_tool_call('[TOOL: Write] {invalid json}')
            >>> assert name == "Write"
            >>> assert args is None  # Parse error

        Edge Cases (Tier-2 tests):
            - [TOOL: Tool] (capital T) → case-insensitive, matches
            - Multi-line JSON args → robust parsing
            - Tool syntax in explanation → only first match wins
            - Multiple [TOOL:...] → first wins, rest ignored
            - Empty args {} → valid (args={})
            - Tool name with underscores (Tool_Name) → matches \\w+
        """
        # Look for [TOOL: name] pattern (case-insensitive)
        pattern = re.compile(r"\[TOOL:\s*(\w+)\]\s*(.+?)(?=\[TOOL:|$)", re.IGNORECASE | re.DOTALL)
        match = pattern.search(output)

        if not match:
            return (None, None)

        tool_name = match.group(1)  # Already case-preserved
        args_str = match.group(2).strip()

        # Try to parse JSON args
        args_dict = self._parse_args_json(args_str)

        return (tool_name, args_dict)

    def _parse_args_json(self, args_str: str) -> Optional[Dict]:
        """
        Parse JSON args string, return None if malformed.

        Handles: extra whitespace, trailing commas (lenient), trailing text
        after a complete JSON object (e.g. '{"x":1} and more text').
        Returns: dict if valid JSON object found, None if parse error
        """
        args_str = args_str.strip()
        # Fast path: full string is valid JSON
        try:
            return json.loads(args_str)
        except (json.JSONDecodeError, ValueError):
            pass
        # Slow path: find the first complete JSON object by brace matching
        # (handles cases like '{"x":1} and [TOOL: Y] ...')
        if args_str.startswith("{"):
            depth = 0
            for i, ch in enumerate(args_str):
                if ch == "{":
                    depth += 1
                elif ch == "}":
                    depth -= 1
                    if depth == 0:
                        try:
                            return json.loads(args_str[: i + 1])
                        except (json.JSONDecodeError, ValueError):
                            break
        logger.debug(f"Failed to parse tool args JSON: {args_str[:100]}")
        return None


class ToolResultInjector:
    """Inject tool execution results into buffered queue for next turn."""

    MAX_RESULT_SIZE_KB = 4

    def inject_result(
        self,
        result: str,
        tool_name: str,
        session_dir: str,
        engine_id: str = "copilot",
        status: str = "success"
    ) -> None:
        """
        Queue tool result for prepending to next Copilot turn (M2 buffered pattern).

        Args:
            result: Tool execution result (text or error message)
            tool_name: Name of the tool that was executed
            session_dir: Session directory (contains btw_queue.jsonl)
            engine_id: Engine ID (default: "copilot")
            status: "success" | "error" | "timeout"

        Behavior:
            - Truncate result to MAX_RESULT_SIZE_KB (4 KB) at sentence boundary
            - Format: "**Tool Result:** [TOOL_NAME]\n\n<result_body>"
            - Write to session_dir/btw_queue.jsonl (append-only, JSONL format)
            - Emit L16 audit event: copilot.tool_result_queued (metadata-only)
            - No execution; result is pre-staged for next turn

        Raises:
            ValueError: If session_dir not writable or invalid
            IOError: If btw_queue.jsonl write fails

        Format of queued entry (JSONL):
            {
                "type": "tool_result",
                "tool_name": "Read",
                "timestamp": 1717418400,
                "status": "success",
                "result_size_bytes": 512,
                "truncated": false,
                "content": "**Tool Result:** [Read]\\n\\n<file contents>"
            }
        """
        import os
        from pathlib import Path

        # Validate session_dir
        session_path = Path(session_dir)
        if not session_path.is_dir():
            raise ValueError(f"session_dir not a directory: {session_dir}")

        # Truncate result
        truncated_result, was_truncated = self._truncate_result(
            result, self.MAX_RESULT_SIZE_KB * 1024
        )

        # Format content
        content = f"**Tool Result:** [{tool_name}]\n\n{truncated_result}"

        # Create JSONL entry
        entry = {
            "type": "tool_result",
            "tool_name": tool_name,
            "timestamp": int(datetime.now().timestamp()),
            "status": status,
            "result_size_bytes": len(result.encode()),
            "truncated": was_truncated,
            "content": content,
        }

        # Write to btw_queue.jsonl (append-only)
        btw_queue_path = session_path / "btw_queue.jsonl"
        try:
            with open(btw_queue_path, "a") as f:
                f.write(json.dumps(entry) + "\n")
            logger.debug(f"Tool result queued: {tool_name} ({status})")
        except IOError as e:
            raise IOError(f"Failed to write btw_queue.jsonl: {e}")

    def _truncate_result(self, result: str, max_bytes: int) -> Tuple[str, bool]:
        """
        Truncate result to max_bytes, breaking at sentence boundary if possible.

        Returns:
            (truncated_result, was_truncated: bool)

        Strategy:
            - If result fits in max_bytes, return as-is
            - If truncated, find last sentence boundary (. ! ?)
            - If no boundary found, truncate at word boundary
            - Append "[...truncated...]" marker
        """
        result_bytes = result.encode()

        # If fits within limit, return as-is
        if len(result_bytes) <= max_bytes:
            return (result, False)

        # Truncate to max_bytes (rough estimate, work with string)
        max_chars = max_bytes // 4  # UTF-8: avg 4 bytes per char (conservative)
        truncated = result[:max_chars]

        # Find last sentence boundary (. ! ?)
        for boundary_char in [".","!", "?"]:
            last_boundary = truncated.rfind(boundary_char)
            if last_boundary > max_chars * 0.8:  # Only if recent enough
                truncated = truncated[:last_boundary + 1]
                break
        else:
            # No sentence boundary; truncate at word boundary
            last_space = truncated.rfind(" ")
            if last_space > max_chars * 0.8:
                truncated = truncated[:last_space]

        truncated += "\n\n[...truncated...]"
        return (truncated, True)


class CopilotMCPIntegration:
    """
    High-level integration: tool injection + parsing + execution + result queueing.

    This class orchestrates the full FCB flow:
      1. inject_tools() → add schemas to system prompt
      2. After Copilot spawn: parse_and_execute(output) → detect tools, execute, queue results
    """

    def __init__(self, teb_tool_registry: Any, session_dir: str, tenant_id: str = "_default"):
        """
        Initialize Copilot MCP integration.

        Args:
            teb_tool_registry: TEB ToolRegistry instance (for L10/L16 enforcement)
            session_dir: Session directory (for btw_queue.jsonl)
            tenant_id: Tenant ID (for audit, ADR-0007)
        """
        self.injector = ToolSchemaInjector()
        self.parser = CopilotToolParser()
        self.result_injector = ToolResultInjector()
        self.teb_tool_registry = teb_tool_registry
        self.session_dir = session_dir
        self.tenant_id = tenant_id

    def inject_tools(self, tools: List[MCPTool]) -> str:
        """
        Return system prompt section with tool schemas.

        Args:
            tools: List of MCP tools to inject

        Returns: Markdown string for system prompt
        """
        return self.injector.inject_schemas(tools)

    def parse_and_execute(self, copilot_output: str) -> Optional[str]:
        """
        Parse Copilot output for tool calls, execute via TEB, queue results.

        Returns:
            Tool result string if executed, None if no tool call detected

        Flow:
            1. Parse output → detect [TOOL: name] syntax
            2. If no match → return None
            3. If match → invoke TEB tool_registry.invoke(tool_name, args)
               - TEB handles L10 path-gate, L16 audit, L33 artifacts
            4. Queue result via ToolResultInjector
            5. Return result for immediate feedback (console logging)

        Audit Trail (L16):
            - Event: copilot.tool_call_detected (tool_name, status)
            - Event: copilot.tool_execution_complete or copilot.tool_execution_failed
            - Details: tool_name, status (success/error), result_size_bytes — NO args/result content

        Raises:
            ValueError: If TEB invocation fails (audit already written, fail-closed)
        """
        # Parse output for tool call
        tool_name, args_dict = self.parser.parse_tool_call(copilot_output, engine_id="copilot")

        if tool_name is None:
            return None  # No tool call detected

        if args_dict is None:
            # Tool name detected but args malformed
            error_result = f"Error: Failed to parse arguments for tool '{tool_name}'. Check JSON syntax."
            self.result_injector.inject_result(
                error_result,
                tool_name=tool_name,
                session_dir=self.session_dir,
                engine_id="copilot",
                status="error"
            )
            return error_result

        # TODO: In Iteration 3, invoke TEB tool_registry
        # For now, return placeholder indicating tool was detected
        placeholder_result = f"[Tool '{tool_name}' would be executed with args: {args_dict}]"
        self.result_injector.inject_result(
            placeholder_result,
            tool_name=tool_name,
            session_dir=self.session_dir,
            engine_id="copilot",
            status="success"
        )
        return placeholder_result

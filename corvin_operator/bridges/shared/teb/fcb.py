"""Function-Call Bridge (FCB) — ADR-0069 M2.

Translates between MCP tool definitions/calls and the OpenAI-compatible
function-calling format used by OpenAI-compatible engines and the Gemini
API.  This allows non-MCP engines to call Forge tools.  (The local Ollama
stream parser went with Hermes, ADR-2087.)

Translation surface
-------------------
  mcp_tool_to_openai(spec)         MCP ToolSpec → OpenAI function definition
  openai_tools_to_mcp_list(defs)   OpenAI definitions → MCP schema list
  openai_call_to_mcp_call(call)    OpenAI-compatible tool_call → {name, args}
  mcp_result_to_openai_msg(r)      MCP result → OpenAI tool message dict

Design constraints:
  - MUST NOT import anthropic (CI AST lint enforces)
  - Pure data transformation — no network I/O, no subprocess
  - All functions accept / return plain dicts (no MCP SDK types required)
"""

from __future__ import annotations

import json
from typing import Any


def mcp_tool_to_openai(tool_spec: dict[str, Any]) -> dict[str, Any]:
    """Convert one MCP tool definition to OpenAI-compatible function format.

    MCP tool spec shape (from Forge tools/list response):
        {"name": "...", "description": "...", "inputSchema": {"type": "object", "properties": {...}}}

    OpenAI function shape:
        {"type": "function", "function": {"name": "...", "description": "...", "parameters": {...}}}
    """
    name = tool_spec.get("name", "")
    description = tool_spec.get("description", "")
    # MCP uses "inputSchema"; OpenAI uses "parameters"
    parameters = tool_spec.get("inputSchema") or tool_spec.get("input_schema") or {
        "type": "object",
        "properties": {},
    }
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": parameters,
        },
    }


def mcp_tools_to_openai_list(tool_specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Bulk-convert a list of MCP tool specs to OpenAI function definitions."""
    return [mcp_tool_to_openai(spec) for spec in tool_specs]


def openai_call_to_mcp_call(tool_call: dict[str, Any]) -> dict[str, Any]:
    """Extract {name, arguments} from an OpenAI-compatible tool_call entry.

    tool_call shape:
        {"function": {"name": "...", "arguments": {...}}}

    Returns:
        {"name": str, "arguments": dict}
    """
    fn = tool_call.get("function") or {}
    name = fn.get("name", "")
    arguments = fn.get("arguments") or {}
    # Some engines return arguments as a JSON string, others as an object
    if isinstance(arguments, str):
        try:
            arguments = json.loads(arguments)
        except json.JSONDecodeError as _exc:
            import logging as _logging
            _logging.getLogger("corvin.teb.fcb").warning(
                "openai_call_to_mcp_call: malformed JSON arguments from engine "
                "(tool=%r): %s — falling back to empty args", fn.get("name"), _exc
            )
            arguments = {}
    return {"name": name, "arguments": arguments}


def mcp_result_to_openai_message(result: Any, tool_call_id: str = "") -> dict[str, Any]:
    """Convert a MCP tool execution result to an OpenAI tool-result message.

    The result is serialised to a string; the engine receives it as the
    "content" of a role="tool" message.

    Args:
        result:       Return value from TEB / run_tool (any JSON-serialisable value)
        tool_call_id: OpenAI requires IDs; pass "" if the engine supplies none
    """
    if result is None:
        content = "(no output)"
    elif isinstance(result, str):
        content = result
    else:
        try:
            content = json.dumps(result, ensure_ascii=False, indent=2)
        except (TypeError, ValueError):
            content = str(result)

    msg: dict[str, Any] = {"role": "tool", "content": content}
    if tool_call_id:
        msg["tool_call_id"] = tool_call_id
    return msg


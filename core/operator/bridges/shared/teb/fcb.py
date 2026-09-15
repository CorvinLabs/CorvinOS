"""Function-Call Bridge (FCB) — ADR-0069 M2.

Translates between MCP tool definitions/calls and the OpenAI-compatible
function-calling format used by engines like Hermes (via Ollama) and
Gemini API.  This allows non-MCP engines to call Forge tools.

Translation surface
-------------------
  mcp_tool_to_openai(spec)         MCP ToolSpec → OpenAI function definition
  openai_tools_to_mcp_list(defs)   OpenAI definitions → MCP schema list
  openai_call_to_mcp_call(call)    Ollama/OpenAI tool_call → {name, args}
  mcp_result_to_openai_msg(r)      MCP result → OpenAI tool message dict

Ollama tool-calling wire format (API /api/chat with tools=[...]):
  Request:
    {"model": "...", "messages": [...], "tools": [<openai-format>], "stream": true}
  Response line when model wants a tool:
    {"message": {"role":"assistant","tool_calls":[{"function":{"name":"...","arguments":{...}}}]}, "done": false}
  Tool-result message to send back:
    {"role": "tool", "content": "<result string>"}

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
    """Extract {name, arguments} from an OpenAI/Ollama tool_call entry.

    Ollama tool_call shape:
        {"function": {"name": "...", "arguments": {...}}}

    Returns:
        {"name": str, "arguments": dict}
    """
    fn = tool_call.get("function") or {}
    name = fn.get("name", "")
    arguments = fn.get("arguments") or {}
    # Ollama may return arguments as a JSON string in some versions
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
        tool_call_id: Ollama does not require IDs but OpenAI does; pass "" if absent
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


def extract_tool_calls_from_ollama_chunk(chunk: dict[str, Any]) -> list[dict[str, Any]]:
    """Extract tool_calls list from an Ollama NDJSON response chunk.

    Ollama emits tool calls as:
        {"message": {"role": "assistant", "tool_calls": [...]}, "done": false}

    Returns the list of tool_calls (may be empty if this chunk has none).
    """
    message = chunk.get("message") or {}
    return message.get("tool_calls") or []


def is_tool_call_chunk(chunk: dict[str, Any]) -> bool:
    """Return True if this Ollama chunk contains a tool call request."""
    return bool(extract_tool_calls_from_ollama_chunk(chunk))

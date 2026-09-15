"""Tool Execution Broker (TEB) — ADR-0069 M1 skeleton.

The TEB intercepts every MCP tool call and runs Corvin hook logic
(path-gate, audit, artifact registration) before and after execution.
This gives non-ClaudeCode engines (Codex, OpenCode, Hermes) the same
tool-execution guarantees that ClaudeCode's native hook system provides.

M1 (this file): data structures + broker protocol.
M2: wire into the Forge MCP server.
M3: full Forge + SkillForge integration.

MUST NOT import anthropic (CI AST lint enforces).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class HookContext:
    """Shared context passed to pre- and post-hooks."""

    engine_id: str
    tool_name: str
    args: dict[str, Any]
    chat_key: str
    result: Any = None
    denied: bool = False
    denial_reason: str = ""


@dataclass
class BrokerResult:
    """Result of a TEB-brokered tool execution."""

    success: bool
    output: Any
    denied: bool = False
    denial_reason: str = ""
    audit_events: list[dict[str, Any]] = field(default_factory=list)

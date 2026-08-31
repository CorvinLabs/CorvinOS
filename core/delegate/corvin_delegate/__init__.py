"""corvin-delegate — Layer 29 (Delegation).

Claude Code stays the OS process that owns the bridge, the audit chain,
consent, disclosure, skills, voice, /btw and every other Layer 6-28
comfort feature. Other engines (Codex CLI, OpenCode, future GeminiCLI)
are reduced to pure swappable workers: prompt in, text out, no bridge
state, no audit, no skills.

This plugin gives Claude OS three MCP tools — ``delegate_claude_code``,
``delegate_codex`` and ``delegate_opencode`` — that wrap the existing
``WorkerEngine`` layer (Layer 22, ``operator/bridges/shared/agents/``)
and surface the result as a single structured envelope. The OS turn
decides whether to delegate; the worker turn executes; the OS turn
formats the answer and replies through the bridge.

See ``CLAUDE.md`` § "Layer 29 — Delegation" for the full design.
"""

from __future__ import annotations

from .delegation import (
    AVAILABLE_ENGINES,
    DelegateError,
    DelegateResult,
    run_delegate,
)

__all__ = [
    "AVAILABLE_ENGINES",
    "DelegateError",
    "DelegateResult",
    "run_delegate",
]

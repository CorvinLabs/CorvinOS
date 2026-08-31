"""TEB path-gate pre-hook — ADR-0069 M1.

Lightweight equivalent of the Claude Code PreToolUse path_gate for
non-CC engines.  Runs inside the ToolExecutionBroker before any tool
execution; denies any path argument that points at a structurally
protected Corvin location.

Protected patterns (mirror of operator/voice/hooks/path_gate.py):
  - **/audit.jsonl        — tamper-evident audit chain
  - **/policy.json        — operator-only forge policy
  - **/forge/**           — forge workspaces (may not be written directly)
  - **/skill-forge/**     — skill-forge workspaces
  - **/.claude/memory/**  — auto-memory tree
  - **/cowork/personas/** — persona definitions

Design:
  The hook inspects ALL string arguments for path-like values (str with
  "/") regardless of tool name.  This is intentionally fail-closed:
  unknown tool names must not bypass the gate (old whitelist approach
  missed any tool not in _WRITE_TOOL_NAMES).

  Well-known READ-ONLY tools (Read, Glob, Grep, etc.) are explicitly
  allowed to pass protected paths — they cannot modify the files.

  Exception handling is fail-CLOSED: if path normalisation throws, the
  path is treated as potentially protected and denied.  A warning is
  logged to aid debugging.

Design constraints:
  - MUST NOT import anthropic  (CI AST lint, same as L34+)
  - MUST NOT fail-open — unknown/unreadable paths → deny
  - Audit emission is best-effort (observability, not fail-closed)
"""

from __future__ import annotations

import logging
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from . import HookContext

_log = logging.getLogger("corvin.teb.path_gate")

# Protected leaf file names — always denied regardless of directory
_PROTECTED_LEAVES: frozenset[str] = frozenset({
    "audit.jsonl",
    "policy.json",
})

# Protected directory segments — any path containing these is denied
_PROTECTED_DIR_SEGMENTS: tuple[str, ...] = (
    "/forge/",
    "/skill-forge/",
    "/.claude/memory/",
    "/cowork/personas/",
)

# Read-only tools whose path arguments are allowed to point at protected
# locations — they can READ but not WRITE.
_READ_ONLY_TOOLS: frozenset[str] = frozenset({
    "Read", "Glob", "Grep", "LS",
    "read_file", "list_files", "search_files",
    # Artifact read tools (L33) — read-only by contract
    "artifact_list", "artifact_search", "artifact_get", "artifact_extract",
})


def _looks_like_path(value: object) -> bool:
    return isinstance(value, str) and "/" in value


def _is_protected(path_str: str) -> tuple[bool, str]:
    """Return (denied, reason) for a path string.

    Fails CLOSED on any exception: an unreadable path is treated as
    potentially protected so it is denied rather than silently allowed.
    """
    try:
        p = PurePosixPath(path_str)
    except Exception as exc:
        _log.warning("teb.path_gate: could not parse path %r: %s — denying", path_str, exc)
        return True, f"unparseable path (fail-closed): {exc}"

    # Check leaf name
    if p.name in _PROTECTED_LEAVES:
        return True, f"protected file: {p.name}"

    # Check directory segments (normalise backslashes for Windows paths)
    normalized = path_str.replace("\\", "/")
    for seg in _PROTECTED_DIR_SEGMENTS:
        if seg in normalized:
            return True, f"protected directory segment: {seg.strip('/')}"

    return False, ""


def path_gate_pre_hook(ctx: "HookContext") -> None:
    """Pre-hook: deny path arguments that point at protected Corvin paths.

    Scans ALL string-valued arguments regardless of tool name.
    Read-only tools (Read, Glob, Grep …) are exempted — they cannot
    modify the files they read.
    """
    tool = ctx.tool_name
    args = ctx.args

    # Read-only tools may see protected paths (they cannot write).
    if tool in _READ_ONLY_TOOLS:
        return

    # Scan all string arguments for path-like values.
    for key, val in args.items():
        if not _looks_like_path(val):
            continue
        denied, reason = _is_protected(val)
        if denied:
            ctx.denied = True
            ctx.denial_reason = f"teb.path_gate: {reason} (tool={tool!r}, arg={key!r})"
            return

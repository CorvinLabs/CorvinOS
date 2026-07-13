"""Worker-Engine layer (ADR-0001).

This module defines the abstract `WorkerEngine` Protocol and a normalised
`StreamEvent` shape that all backends emit. Concrete engines live in
sibling modules:

    agents/claude_code.py   — wraps `claude -p --output-format stream-json`
    agents/codex_cli.py     — wraps `codex exec --json`

The engines are intentionally *parallel to* the existing
`bridges/shared/adapter.py::call_claude_streaming` — they are NOT a
drop-in replacement yet. Phase 2 of the AWP migration (see ADR-0001) will
fold the adapter onto this protocol; Phase 1 only ships the engines and
their per-subtask E2E.

Design notes
------------

* `spawn()` returns an iterator of `StreamEvent` so the caller can react
  per-event (status callbacks, voice TTS, mid-stream injection). A
  helper `collect()` drains the iterator into a `SpawnResult` for tests
  and one-shot use.
* Capability flags live in `engine.capabilities`. Adapter logic gates
  features per capability — a missing capability is a degraded mode,
  never a crash.
* Engines own subprocess lifecycle: spawn, stream, cancel. The caller
  passes a `prompt` and gets back events; nothing else.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterator, Protocol, runtime_checkable


def terminate_process_tree(proc: "subprocess.Popen", *, grace: float = 5.0) -> None:
    """Terminate *proc* AND its whole process group, not just the direct
    child — every engine spawns with ``start_new_session=True`` specifically
    so a live tool-use grandchild (a Bash call, an MCP server, a long curl)
    shares the parent's pgid and can be reaped together via ``killpg``.

    Before this helper existed, ``cancel()``/``_cleanup_proc()`` across the
    engine modules called only ``proc.terminate()``/``proc.kill()`` on the
    direct child — the grandchild silently outlived the turn the adapter
    believed was cancelled. Mirrors the SIGTERM-then-SIGKILL-after-grace
    pattern ``adapter.py``'s ``_cancel_chat`` already uses correctly.
    Windows has no process groups; falls back to a single-process
    terminate()/kill() there.
    """
    if proc.poll() is not None:
        return
    try:
        if hasattr(os, "killpg"):
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        else:  # Windows — no process groups
            proc.terminate()
    except (ProcessLookupError, PermissionError, OSError):
        pass
    try:
        proc.wait(timeout=grace)
    except subprocess.TimeoutExpired:
        pass
    if proc.poll() is None:
        try:
            if hasattr(os, "killpg"):
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            else:
                proc.kill()
        except (ProcessLookupError, PermissionError, OSError):
            pass


# ---------------------------------------------------------------------------
# Normalised stream events
# ---------------------------------------------------------------------------


@dataclass
class StreamEvent:
    """Backend-agnostic stream event.

    `type` is one of:
      - "session_started"   — engine has session_id, model decided
      - "turn_started"      — model began generating
      - "text_delta"        — chunk of assistant text (may arrive in pieces)
      - "tool_call"         — engine emitted a tool/function call (raw in `raw`)
      - "tool_result"       — tool call completed
      - "turn_completed"    — turn done, final usage in `usage`
      - "error"             — non-fatal stream error (engine-specific in `raw`)
    """

    type: str
    text: str = ""
    raw: dict[str, Any] | None = None
    usage: dict[str, Any] | None = None
    error: str | None = None


@dataclass
class SpawnResult:
    """Drained final result of a spawn() call. Used by tests and one-shots."""

    final_text: str
    usage: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    events: list[StreamEvent] = field(default_factory=list)
    error: str | None = None


# ---------------------------------------------------------------------------
# WorkerEngine Protocol
# ---------------------------------------------------------------------------


@runtime_checkable
class WorkerEngine(Protocol):
    """Backend-agnostic worker contract.

    Concrete implementations live in sibling modules. Capabilities are
    a free-form dict so new flags can be added without breaking the
    protocol.

    Reserved capability keys (engines SHOULD declare these explicitly):

      - mid_stream_inject : bool   — supports inject() while streaming
      - hooks             : bool   — engine has filesystem-based pre/post hooks
      - skills_tool       : bool   — engine has a first-class Skill tool
      - mcp               : bool   — engine supports MCP servers natively
      - stream_json       : bool   — engine emits JSONL events on stdout
      - permission_modes  : list[str]  — supported permission/sandbox modes
      - add_system_prompt : bool   — supports --append-system-prompt or equivalent
    """

    name: str
    capabilities: dict[str, Any]

    def spawn(
        self,
        prompt: str,
        *,
        system: str | None = None,
        model: str | None = None,
        working_dir: Path | None = None,
        timeout: float = 120.0,
        extra_args: list[str] | None = None,
        env: dict[str, str] | None = None,
    ) -> Iterator[StreamEvent]:
        """Spawn a single turn and yield normalised events.

        The iterator MUST end with exactly one `turn_completed` event
        (or one `error` event if the spawn failed before the turn
        completed). The caller can use this as a termination signal.
        """

    def cancel(self) -> None:
        """Cancel an in-flight spawn() if one is running. Best-effort."""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def collect(events: Iterator[StreamEvent]) -> SpawnResult:
    """Drain an event iterator into a SpawnResult. Useful for tests."""

    result = SpawnResult(final_text="")
    text_chunks: list[str] = []
    usage: dict[str, Any] = {}
    error: str | None = None

    for ev in events:
        result.events.append(ev)
        if ev.type == "text_delta" and ev.text:
            text_chunks.append(ev.text)
        elif ev.type == "turn_completed":
            if ev.usage:
                usage = ev.usage
            if ev.text and not text_chunks:
                # Some engines (Codex) only deliver final text on completion
                text_chunks.append(ev.text)
        elif ev.type == "error":
            error = ev.error or "unknown engine error"

    result.final_text = "".join(text_chunks).strip()
    result.usage = usage
    result.error = error
    return result


def parse_jsonl_line(line: bytes | str) -> dict[str, Any] | None:
    """Parse a single JSONL line. Returns None on malformed input.

    Tolerant by design: JSONL streams in the wild include trailing
    whitespace, partial lines on early-exit, and BOM-prefixed first
    lines. Engines call this from their stdout reader loops.
    """

    if isinstance(line, bytes):
        try:
            line = line.decode("utf-8", errors="replace")
        except Exception:
            return None
    line = line.strip()
    if not line or not line.startswith("{"):
        return None
    try:
        obj = json.loads(line)
        if isinstance(obj, dict):
            return obj
        return None
    except json.JSONDecodeError:
        return None


class CapabilityError(Exception):
    """Raised when a caller requests a capability the engine does not support.

    ADR-0049: raised at spawn time when ``pin_session=True`` is passed to
    an engine whose ``capabilities["session_pinning"]`` is False. Never
    silently ignored — the caller must handle or re-raise.
    """


__all__ = [
    "StreamEvent",
    "SpawnResult",
    "WorkerEngine",
    "CapabilityError",
    "collect",
    "parse_jsonl_line",
]

"""ADR-0172 M1 — Worker-Engine Deep-Trace: per-worker tool-call observability.

Writes a JSONL trace file per worker invocation under the ACS run directory:
  <run_dir>/workers/<worker_id>.trace.jsonl

Metadata only (GDPR Art. 5): tool names, timing, decision — never tool input,
tool output, file content, prompt text, or generated code.

No hash-chain: the trace is an observability artifact with separate retention
and L36-erasure semantics (file-level delete, same run_dir that L36 already
targets). It is NOT a compliance record; engine.span.end carries the compliance
summary with trace_available=True as the correlation signal.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Path helper
# ---------------------------------------------------------------------------

def trace_path(run_dir: Path, worker_id: str) -> Path:
    """Canonical location for a worker's trace file."""
    return run_dir / "workers" / f"{worker_id}.trace.jsonl"


# ---------------------------------------------------------------------------
# Internal writer
# ---------------------------------------------------------------------------

def _write_trace(path: Path, events: list[dict[str, Any]]) -> bool:
    """Append-write events to path. Returns True on success."""
    if not events:
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as fh:
            for ev in events:
                fh.write(json.dumps(ev, separators=(",", ":")) + "\n")
        return True
    except OSError:
        return False


# ---------------------------------------------------------------------------
# ClaudeCode extraction (post-run, from tenant audit chain)
# ---------------------------------------------------------------------------

def extract_claudecode_trace(
    audit_path: Path,
    run_id: str,
    worker_id: str,
    span_id: str,
    run_dir: Path,
) -> int:
    """Extract forge.tool_executed events for this worker from the audit chain.

    ClaudeCode workers emit forge.tool_executed events to the tenant audit chain
    via the path-gate hook (L10). Each event carries run_id + worker_id injected
    by the ACS runtime into the worker subprocess environment. We scan once after
    the worker completes (zero hot-path overhead) and materialise a trace file.

    Returns the number of tool calls written (0 → no trace file created).
    """
    events: list[dict[str, Any]] = []
    if not audit_path.is_file():
        return 0
    try:
        with audit_path.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                # Fast pre-filter: skip lines that cannot match.
                if not raw or '"forge.tool_executed"' not in raw:
                    continue
                try:
                    obj = json.loads(raw)
                except json.JSONDecodeError:
                    continue
                if obj.get("event_type") != "forge.tool_executed":
                    continue
                d = obj.get("details") or {}
                if d.get("run_id") != run_id or d.get("worker_id") != worker_id:
                    continue
                events.append({
                    "ts":        float(obj.get("ts") or 0.0),
                    "seq":       len(events) + 1,
                    "event":     "tool.called",
                    "worker_id": worker_id,
                    "run_id":    run_id,
                    "span_id":   span_id,
                    "tool_name": str(d.get("tool_name") or ""),
                    "decision":  str(d.get("decision") or "allow"),
                })
    except OSError:
        return 0

    if not events:
        return 0
    _write_trace(trace_path(run_dir, worker_id), events)
    return len(events)


# ---------------------------------------------------------------------------
# Hermes extraction (post-run, from response JSON)
# ---------------------------------------------------------------------------

def extract_hermes_trace(
    raw_response: str,
    worker_id: str,
    run_id: str,
    span_id: str,
    run_dir: Path,
) -> int:
    """Extract tool_calls from a Hermes/Ollama response JSON (if present).

    Hermes may include a ``tool_calls`` array in its JSON response:
      [{"name": "Read", "duration_ms": 42, "exit_code": 0}, ...]

    Older or text-only Hermes responses omit the field — those return 0.
    Returns the number of tool calls written (0 → no trace file created).
    """
    try:
        resp: Any = json.loads(raw_response.strip() or "{}")
    except (json.JSONDecodeError, TypeError):
        return 0

    raw_calls = resp.get("tool_calls") if isinstance(resp, dict) else None
    if not isinstance(raw_calls, list) or not raw_calls:
        return 0

    events: list[dict[str, Any]] = []
    for tc in raw_calls:
        if not isinstance(tc, dict):
            continue
        tool_name = str(tc.get("name") or tc.get("tool") or "")
        if not tool_name:
            continue
        events.append({
            "ts":          time.time(),
            "seq":         len(events) + 1,
            "event":       "tool.called",
            "worker_id":   worker_id,
            "run_id":      run_id,
            "span_id":     span_id,
            "tool_name":   tool_name,
            "duration_ms": int(tc.get("duration_ms") or 0),
            "exit_code":   int(tc.get("exit_code") or 0),
        })

    if not events:
        return 0
    _write_trace(trace_path(run_dir, worker_id), events)
    return len(events)


# ---------------------------------------------------------------------------
# Reader (used by console endpoints)
# ---------------------------------------------------------------------------

def read_trace(trace_file: Path) -> list[dict[str, Any]]:
    """Return all events from a trace file; empty list if absent or corrupt."""
    if not trace_file.is_file():
        return []
    events: list[dict[str, Any]] = []
    try:
        with trace_file.open("r", encoding="utf-8") as fh:
            for raw in fh:
                raw = raw.strip()
                if not raw:
                    continue
                try:
                    events.append(json.loads(raw))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return sorted(events, key=lambda e: (e.get("seq") or 0, e.get("ts") or 0.0))


def summarize_trace(events: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate counts from a list of trace events."""
    tool_counts: dict[str, int] = {}
    denied = 0
    for ev in events:
        name = str(ev.get("tool_name") or "")
        if name:
            tool_counts[name] = tool_counts.get(name, 0) + 1
        if str(ev.get("decision") or "") == "deny":
            denied += 1
    return {
        "total_tool_calls": len(events),
        "denied_calls":     denied,
        "tools_used":       tool_counts,
    }

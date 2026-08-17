"""ACO event definitions — shared by Layer 1 writer and Layer 2/3 readers."""
from __future__ import annotations

from typing import Any

# All known event types emitted by Layer 1 (chat_runtime.py + adapter.py)
BACKEND_EVENTS: set[str] = {
    "turn.start",
    "turn.done",
    "delegation.decision",
    "delegation.skipped",
    "acs.run.start",
    "acs.run.done",
}

# Repair events written by Layer 5 (repair.py) — annotate repaired anomalies
REPAIR_EVENTS: set[str] = {
    "repair.turn_flushed",
    "repair.delegation_reset",
    "repair.acs_throttle_on",
    "repair.ws_reconnect_requested",
    "repair.stream_cleared",
}

# Events emitted by the frontend WS layer (chat-registry.ts)
FRONTEND_EVENTS: set[str] = {
    "ws.connecting",
    "ws.open",
    "ws.close",
    "ws.error",
    "ws.parse_error",
    "msg.send",
    "msg.cancel",
    "stream.delta",
    "stream.tool_use",
    "stream.artifact",
    "stream.error",
    "stream.result",
    "stream.done",
    "stream.session_title",
    "stream.ccc_action",
}

ALL_EVENTS = BACKEND_EVENTS | FRONTEND_EVENTS | REPAIR_EVENTS


def read_jsonl(path: Any) -> list[dict]:
    """Read a JSONL file into a list of dicts. Silently skips malformed lines."""
    import json
    from pathlib import Path

    p = Path(path)
    if not p.exists():
        return []
    events: list[dict] = []
    try:
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                events.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    except OSError:
        pass
    return events


def read_session_log(workdir: Any) -> list[dict]:
    """Read chat_debug.jsonl + rotated files from oldest to newest."""
    from pathlib import Path

    base = Path(workdir) / "chat_debug.jsonl"
    all_events: list[dict] = []
    for suffix in [".jsonl.2", ".jsonl.1", ""]:
        p = base.with_suffix(suffix) if suffix else base
        all_events.extend(read_jsonl(p))
    # Sort by ts (ISO-8601 lexicographic)
    all_events.sort(key=lambda e: e.get("ts", ""))
    return all_events

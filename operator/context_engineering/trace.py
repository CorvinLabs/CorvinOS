"""CEL trace persistence (Vibe Engineering P1, ADR-0275).

``build_brief`` returns a per-turn trace; this persists it per session so the
console can render the pipeline read-only. Stored as JSONL under the session
workdir, dot-prefixed (``.corvin-cel-traces.jsonl``) so it is NOT picked up as a
chat artifact (every artifact-scan site in the codebase skips ``name.startswith
(".")``). Tenant isolation is by workdir — a session belongs to exactly one
tenant; the route layer re-checks ``rec.tenant_id`` on top. Bounded to the last
``_MAX_TRACES`` lines so the file never grows without limit. Best-effort: a
persistence error never breaks the turn.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

_TRACE_FILE = ".corvin-cel-traces.jsonl"
_MAX_TRACES = 200
_SCHEMA_VERSION = 1


def persist_trace(trace: dict, workdir: Any, turn_id: str) -> None:
    """Append one per-turn CEL trace to the session workdir. Never raises."""
    try:
        p = Path(workdir) / _TRACE_FILE
        rec = {"v": _SCHEMA_VERSION, "turn_id": turn_id,
               "ts": time.time(), "trace": trace}
        lines: list[str] = []
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(rec, default=str))
        p.write_text("\n".join(lines[-_MAX_TRACES:]) + "\n", encoding="utf-8")
    except Exception:  # noqa: BLE001 — best-effort; a bad write never breaks a turn
        pass


def read_recent_traces(workdir: Any, n: int = 20) -> list[dict]:
    """Return the last ``n`` persisted traces, most recent FIRST. Empty on any
    error or when no turn has been context-engineered yet (the P1 empty-state)."""
    try:
        p = Path(workdir) / _TRACE_FILE
        if not p.exists():
            return []
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        out: list[dict] = []
        for ln in reversed(lines[-max(0, n):]):
            try:
                out.append(json.loads(ln))
            except (json.JSONDecodeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001
        return []

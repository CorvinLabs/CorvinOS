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

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

_TRACE_FILE = ".corvin-cel-traces.jsonl"
_MAX_TRACES = 200
_SCHEMA_VERSION = 1


def _scrub_trace(trace: dict) -> dict:
    """Content-free copy of the trace for on-disk persistence (review R3 finding
    C1): the cache lived at the workdir root, OUTSIDE the GDPR Art. 17 erasure
    handler's target list, so a raw ``task_preview`` (task[:120]) survived subject
    erasure. Persist NOTHING that carries user/task content — drop task_preview +
    raw exception strings, and hash the task-derived forge/egress source ids
    (mirrors decision_record's content-free discipline)."""
    def _h(v: str) -> str:
        return hashlib.sha256(str(v).encode("utf-8")).hexdigest()[:16]
    # Content-free PARITY with decision_record.build_record (review R5 finding #1):
    # forge/egress source ids are ALWAYS hashed (task-derived); conceptual ids are
    # scrubbed in place (raw unless PII-shaped, then hashed) — so a memory filename
    # carrying a PII shape never reaches this cache (which lives OUTSIDE the GDPR
    # erasure handler) raw. gate denials + stage reasons are scrubbed the same way.
    # SINGLE-SOURCE partition (review R6): derive "is this a task-derived egress/
    # forge stage → hash the id" from the SAME _CONCEPTUAL_STAGES set build_record
    # uses (its complement), so a FUTURE egress/forge stage can never auto-hash in
    # the Layer-A record but silently fall to raw here.
    from .decision_record import _scrub_str, _CONCEPTUAL_STAGES  # noqa: PLC0415
    # Drop the top-level keys that carry RAW task-derived forged/dropped names
    # (review R4 #1). Keep forged_rolled_back as counts.
    _DROP = {"task_preview", "tools_dropped"}
    out = {k: v for k, v in trace.items() if k not in _DROP}
    if isinstance(trace.get("forged_rolled_back"), dict):
        rb = trace["forged_rolled_back"]
        out["forged_rolled_back"] = {"tools": len(rb.get("tools") or []),
                                     "skills": len(rb.get("skills") or [])}
    for k in ("gate1_denied", "gate2_denied"):
        if out.get(k):
            out[k] = _scrub_str(out[k])
    stages = []
    for s in trace.get("stages", []):
        if not isinstance(s, dict):
            continue
        s2 = {k: v for k, v in s.items() if k != "error"}  # raw exception dropped
        if s.get("sources"):
            hash_all = s.get("stage") not in _CONCEPTUAL_STAGES
            s2["sources"] = [
                {"id": (_h(x.get("id", "")) if hash_all else _scrub_str(x.get("id", ""))),
                 "score": x.get("score")}
                for x in s.get("sources", []) if isinstance(x, dict)]
        if s.get("reason"):
            s2["reason"] = _scrub_str(s["reason"])
        stages.append(s2)
    out["stages"] = stages
    return out


def persist_trace(trace: dict, workdir: Any, turn_id: str) -> None:
    """Append one CONTENT-FREE per-turn CEL trace to the session workdir. Never
    raises. Atomic write (temp + os.replace) so a crash / concurrent turn cannot
    truncate the file (review R3 finding C3)."""
    try:
        p = Path(workdir) / _TRACE_FILE
        rec = {"v": _SCHEMA_VERSION, "turn_id": turn_id,
               "ts": time.time(), "trace": _scrub_trace(trace)}
        lines: list[str] = []
        if p.exists():
            lines = p.read_text(encoding="utf-8").splitlines()
        lines.append(json.dumps(rec, default=str))
        tmp = p.with_suffix(f".tmp.{os.getpid()}")
        tmp.write_text("\n".join(lines[-_MAX_TRACES:]) + "\n", encoding="utf-8")
        os.replace(tmp, p)
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

"""L5 routing ledger — the one persisted record of routing decisions and their outcomes.

ADR-2092 G0/G1. Every OS turn on every surface writes exactly one ``decision`` record
(bundled rule's engine, the Skill's advice, the engine actually used) and, when the
turn finishes, one ``outcome`` record joined to it by ``turn_id``. The rollback judge
(``rollback_detector``) and the Phase 2 readiness check (``readiness``) read only this
file, so bridge and console — two processes — are judged on one shared history.

The ledger is content-free by construction: engines, phases and sources are closed
enums, features are a closed vocabulary of enums/bools (see ``FEATURE_VOCAB``), and
every string value additionally passes the ADR-0297 fail-closed gate
(``core.pii.sensitive.has_sensitive``) — a field that is sensitive, or whose scan
raises, is dropped, never written. Prompt text never reaches this module.

Writes are best-effort: a ledger failure returns ``False`` and never costs the turn.
"""
from __future__ import annotations

import fcntl
import json
import os
import time
import uuid
from pathlib import Path
from typing import Any, Iterable, Optional

ENGINES = frozenset({"native", "acs", "tde"})
SURFACES = frozenset({"bridge", "console"})
PHASES = frozenset({"shadow", "dual_write"})
SOURCES = frozenset({"bundled", "skill"})

#: The only feature keys a decision may carry, and their admissible values.
#: ``bool`` means a real bool; a frozenset is a closed string vocabulary.
FEATURE_VOCAB: dict[str, Any] = {
    "complexity": frozenset({"simple", "medium", "complex", "unknown"}),
    "len_bucket": frozenset({"xs", "s", "m", "l", "xl"}),
    "has_table": bool,
    "has_code": bool,
    "force_delegate": bool,
    "is_big_data": bool,
    "mode": frozenset({"native", "acs", "tde"}),
    "tde_available": bool,
    "quota_ok": bool,
}

LEDGER_FILENAME = "routing_ledger.jsonl"
#: Reads look at the tail only; the file is append-only and never rewritten.
_READ_TAIL_BYTES = 8 * 1024 * 1024


def new_turn_id() -> str:
    return uuid.uuid4().hex[:20]


def routing_dir(tenant_id: str) -> Path:
    from core.paths.tenant import tenant_learning_dir  # noqa: PLC0415

    return tenant_learning_dir(tenant_id) / "routing"


def ledger_path(tenant_id: str) -> Path:
    return routing_dir(tenant_id) / LEDGER_FILENAME


def _string_is_safe(value: str) -> bool:
    """ADR-0297 gate: a sensitive value, or a scan that raises, is unsafe."""
    try:
        from core.pii.sensitive import has_sensitive  # noqa: PLC0415

        return not has_sensitive(value)
    except Exception:  # noqa: BLE001 — PIIDetectionFailedClosed or import failure → drop
        return False


def clean_features(features: Optional[dict]) -> dict:
    """Keep only vocabulary keys with admissible, PII-gated values."""
    out: dict = {}
    if not isinstance(features, dict):
        return out
    for key, value in features.items():
        allowed = FEATURE_VOCAB.get(key)
        if allowed is None:
            continue
        if allowed is bool:
            if isinstance(value, bool):
                out[key] = value
            continue
        if isinstance(value, str) and value in allowed and _string_is_safe(value):
            out[key] = value
    return out


def _append(tenant_id: str, record: dict) -> bool:
    try:
        path = ledger_path(tenant_id)
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        line = json.dumps(record, separators=(",", ":"), sort_keys=True) + "\n"
        fd = os.open(str(path), os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            os.write(fd, line.encode("utf-8"))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)
        return True
    except Exception:  # noqa: BLE001 — the ledger never costs the turn
        return False


def record_decision(
    *,
    tenant_id: str,
    turn_id: str,
    surface: str,
    phase: str,
    bundled: str,
    used: str,
    source: str,
    skill: Optional[str] = None,
    skill_conf: Optional[float] = None,
    clamped: bool = False,
    features: Optional[dict] = None,
) -> bool:
    if surface not in SURFACES or phase not in PHASES or source not in SOURCES:
        return False
    if bundled not in ENGINES or used not in ENGINES:
        return False
    if skill is not None and skill not in ENGINES:
        skill = None
    if not isinstance(turn_id, str) or not turn_id.isalnum() or len(turn_id) > 64:
        return False
    conf = None
    if isinstance(skill_conf, (int, float)) and not isinstance(skill_conf, bool):
        conf = round(max(0.0, min(1.0, float(skill_conf))), 4)
    return _append(tenant_id, {
        "v": 1,
        "kind": "decision",
        "ts": round(time.time(), 3),
        "turn_id": turn_id,
        "surface": surface,
        "phase": phase,
        "bundled": bundled,
        "skill": skill,
        "skill_conf": conf,
        "used": used,
        "source": source,
        "clamped": bool(clamped),
        "features": clean_features(features),
    })


def record_outcome(
    *,
    tenant_id: str,
    turn_id: str,
    surface: str,
    used: str,
    ok: bool,
    latency_ms: float,
) -> bool:
    if surface not in SURFACES or used not in ENGINES:
        return False
    if not isinstance(turn_id, str) or not turn_id.isalnum() or len(turn_id) > 64:
        return False
    return _append(tenant_id, {
        "v": 1,
        "kind": "outcome",
        "ts": round(time.time(), 3),
        "turn_id": turn_id,
        "surface": surface,
        "used": used,
        "ok": bool(ok),
        "latency_ms": int(max(0.0, float(latency_ms or 0.0))),
    })


def read_records(tenant_id: str, *, since_ts: float = 0.0) -> list[dict]:
    path = ledger_path(tenant_id)
    try:
        size = path.stat().st_size
    except OSError:
        return []
    try:
        with open(path, "rb") as fh:
            if size > _READ_TAIL_BYTES:
                fh.seek(size - _READ_TAIL_BYTES)
                fh.readline()  # drop the partial first line
            raw = fh.read()
    except OSError:
        return []
    out: list[dict] = []
    for line in raw.splitlines():
        try:
            rec = json.loads(line)
        except (ValueError, UnicodeDecodeError):
            continue
        if _well_formed(rec) and rec["ts"] >= since_ts:
            out.append(rec)
    return out


def _well_formed(rec: Any) -> bool:
    """A hostile or torn line is skipped, never allowed to break the readers."""
    if not isinstance(rec, dict) or rec.get("v") != 1:
        return False
    ts = rec.get("ts")
    if isinstance(ts, bool) or not isinstance(ts, (int, float)):
        return False
    tid = rec.get("turn_id")
    if not isinstance(tid, str) or not tid.isalnum():
        return False
    if rec.get("surface") not in SURFACES or rec.get("used") not in ENGINES:
        return False
    kind = rec.get("kind")
    if kind == "decision":
        return (rec.get("bundled") in ENGINES and rec.get("source") in SOURCES
                and isinstance(rec.get("features"), dict))
    if kind == "outcome":
        return isinstance(rec.get("ok"), bool)
    return False


def join(records: Iterable[dict], *, surface: Optional[str] = None) -> list[dict]:
    """Decisions paired with their outcome: ``{**decision, "outcome": outcome|None}``."""
    decisions: dict[str, dict] = {}
    outcomes: dict[str, dict] = {}
    for rec in records:
        if surface is not None and rec.get("surface") != surface:
            continue
        tid = rec.get("turn_id")
        if rec.get("kind") == "decision":
            decisions.setdefault(tid, rec)
        elif rec.get("kind") == "outcome":
            outcomes.setdefault(tid, rec)
    return [dict(d, outcome=outcomes.get(tid)) for tid, d in decisions.items()]


def summarize(records: Iterable[dict], *, surface: Optional[str] = None) -> dict:
    """Join rate, agreement and per-source success — the numbers every gate reads."""
    joined = join(records, surface=surface)
    with_outcome = [j for j in joined if j["outcome"] is not None]
    by_source: dict[str, dict] = {}
    for src in SOURCES:
        rows = [j for j in with_outcome if j.get("source") == src]
        ok = sum(1 for j in rows if j["outcome"].get("ok"))
        by_source[src] = {"n": len(rows), "ok": ok, "success_rate": (ok / len(rows)) if rows else None}
    advised = [j for j in joined if j.get("skill") is not None]
    agree = sum(1 for j in advised if j["skill"] == j["bundled"])
    return {
        "decisions": len(joined),
        "outcomes": len(with_outcome),
        "join_rate": (len(with_outcome) / len(joined)) if joined else None,
        "advised": len(advised),
        "agreement_rate": (agree / len(advised)) if advised else None,
        "by_source": by_source,
    }

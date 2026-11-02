"""L5 routing ledger — the one persisted record of routing decisions and their outcomes.

ADR-2092 G0/G1. Every OS turn on every surface writes exactly one ``decision`` record
(bundled rule's engine, the Skill's advice, the engine actually used) and, when the
turn finishes, one ``outcome`` record joined to it by ``turn_id``.

An outcome has three kinds: ``ok: true``, ``ok: false`` and NOT-JUDGED. A turn that
was cancelled (client disconnect, task cancel), killed by a restart (reaped at boot)
or in which no engine ran (gate/budget refusal, debug engine, empty directive) is
recorded with ``"ok": null`` and ``"excluded": <reason from EXCLUDED_REASONS>``. It is
counted and shown (``summarize()["not_judged"]``) but excluded from the join-rate
numerator AND denominator, from every success rate and from the rollback judge
(ADR-2092 G1 as amended 2026-10-27). The rollback judge
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
import hashlib
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
#: How a decision's phase came about: the gate answered (``verdict``), no verdict
#: existed yet and the turn ran shadow (``pending``, the readiness placeholder), or
#: the engine had already run and the record is pinned to shadow (``post_hoc``).
#: Lines written before 2026-10-28 carry no ``gate`` and read as ``verdict``.
GATES = frozenset({"verdict", "pending", "post_hoc"})
#: Why an outcome is not judged. Closed: a reader rejects any other value.
EXCLUDED_REASONS = frozenset({"cancelled", "orphaned", "no_engine"})

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
    gate: str = "verdict",
) -> bool:
    if surface not in SURFACES or phase not in PHASES or source not in SOURCES:
        return False
    if gate not in GATES:
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
        "gate": gate,
    })


def record_outcome(
    *,
    tenant_id: str,
    turn_id: str,
    surface: str,
    used: str,
    ok: bool,
    latency_ms: float,
    excluded: Optional[str] = None,
) -> bool:
    """``excluded`` (a reason from :data:`EXCLUDED_REASONS`) records a NOT-JUDGED
    outcome: ``ok`` is then stored as ``null`` whatever was passed."""
    if surface not in SURFACES or used not in ENGINES:
        return False
    if not isinstance(turn_id, str) or not turn_id.isalnum() or len(turn_id) > 64:
        return False
    if excluded is not None and excluded not in EXCLUDED_REASONS:
        return False
    rec = {
        "v": 1,
        "kind": "outcome",
        "ts": round(time.time(), 3),
        "turn_id": turn_id,
        "surface": surface,
        "used": used,
        "ok": None if excluded is not None else bool(ok),
        "latency_ms": int(max(0.0, float(latency_ms or 0.0))),
    }
    if excluded is not None:
        rec["excluded"] = excluded
    return _append(tenant_id, rec)


#: Bytes before a position whose hash identifies the ledger content at that position.
_FP_BYTES = 256


def _fingerprint(fh, offset: int) -> str:
    """sha256 of the (up to) :data:`_FP_BYTES` bytes that END at ``offset``."""
    begin = max(0, offset - _FP_BYTES)
    fh.seek(begin)
    return hashlib.sha256(fh.read(offset - begin)).hexdigest()


def ledger_position(tenant_id: str) -> Optional[dict]:
    """The ledger's current end as a POSITION, not a time (review c120 r6_s2-2):
    ``{"dev": int | None, "ino": int | None, "offset": int, "fp": str}`` — ``fp`` is the
    sha256 of the last :data:`_FP_BYTES` bytes before ``offset`` (review c120
    r7b-2/-3: an inode number alone is reused by a deleted and re-created ledger, and
    an in-place truncation keeps it). Read under a shared ``flock`` — every append
    holds the exclusive one — so the offset is always a line boundary. A missing
    ledger is ``{"dev": None, "ino": None, "offset": 0, "fp": <hash of no bytes>}``;
    ``None`` when it cannot be read."""
    path = ledger_path(tenant_id)
    try:
        fh = open(path, "rb")
    except FileNotFoundError:
        return {"dev": None, "ino": None, "offset": 0,
                "fp": hashlib.sha256(b"").hexdigest()}
    except OSError:
        return None
    with fh:
        fd = fh.fileno()
        try:
            fcntl.flock(fd, fcntl.LOCK_SH)
        except OSError:
            return None
        try:
            st = os.fstat(fd)
            size = int(st.st_size)
            return {"dev": int(st.st_dev), "ino": int(st.st_ino), "offset": size,
                    "fp": _fingerprint(fh, size)}
        except OSError:
            return None
        finally:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass


def _opt_int(v: Any) -> bool:
    return v is None or (isinstance(v, int) and not isinstance(v, bool))


def valid_position(pos: Any) -> bool:
    if not isinstance(pos, dict):
        return False
    if not _opt_int(pos.get("ino")) or not _opt_int(pos.get("dev")):
        return False
    fp = pos.get("fp")
    if fp is not None and not isinstance(fp, str):
        return False
    off = pos.get("offset")
    return isinstance(off, int) and not isinstance(off, bool) and off >= 0


def _position_holds(fh, st, pos: dict) -> bool:
    """Whether ``pos`` still names this file's content: same device and inode, the
    file at least as long as the offset, and the bytes before the offset unchanged
    (fingerprint). A position without a fingerprint cannot be verified (unless it
    points at byte 0). Any mismatch → False: the caller reads the ledger whole."""
    off = pos["offset"]
    if off == 0:
        return True
    if pos.get("ino") is None or pos["ino"] != st.st_ino:
        return False
    if pos.get("dev") is not None and pos["dev"] != st.st_dev:
        return False
    if off > st.st_size or not isinstance(pos.get("fp"), str):
        return False
    return _fingerprint(fh, off) == pos["fp"]


def read_records(tenant_id: str, *, since_ts: float = 0.0,
                 since_pos: Optional[dict] = None) -> list[dict]:
    """Well-formed records with ``ts >= since_ts``; with ``since_pos`` (a
    :func:`ledger_position`) only the records appended AFTER that position — the
    ledger is append-only, so the position bounds a window independently of the wall
    clock. The position is trusted only while it still names the same content (same
    device + inode, file not shorter, the fingerprint of the bytes before the offset
    unchanged); a ledger that was replaced, re-created (even under a reused inode
    number) or truncated — also one that regrew past the offset — is read WHOLE, no
    row skipped: the restrictive direction for the rollback judge (review c120
    r7b-2/-3)."""
    path = ledger_path(tenant_id)
    start = 0
    try:
        with open(path, "rb") as fh:
            st = os.fstat(fh.fileno())
            size = st.st_size
            if valid_position(since_pos) and _position_holds(fh, st, since_pos):
                start = since_pos["offset"]
            begin = max(start, size - _READ_TAIL_BYTES)
            fh.seek(begin)
            if begin > start:
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
                and isinstance(rec.get("features"), dict)
                and rec.get("gate", "verdict") in GATES)
    if kind == "outcome":
        # Backward compatible: every line written before 2026-10-27 is a judged
        # outcome (ok bool, no "excluded" key).
        if "excluded" in rec:
            return rec.get("excluded") in EXCLUDED_REASONS and rec.get("ok") is None
        return isinstance(rec.get("ok"), bool)
    return False


def is_not_judged(row: dict) -> bool:
    """A joined row whose outcome is NOT-JUDGED (cancelled / orphaned / no engine)."""
    outcome = row.get("outcome")
    return isinstance(outcome, dict) and outcome.get("excluded") in EXCLUDED_REASONS


def judged(rows: Iterable[dict]) -> list[dict]:
    """Joined rows that count: an outcome that is ok/not-ok, or no outcome at all."""
    return [r for r in rows if not is_not_judged(r)]


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
    """Join rate, agreement and per-source success — the numbers every gate reads.

    ``decisions`` counts only turns that are judged or still missing their outcome;
    NOT-JUDGED turns are reported apart in ``not_judged`` / ``not_judged_by_reason``.
    """
    all_joined = join(records, surface=surface)
    joined = judged(all_joined)
    not_judged: dict[str, int] = {}
    for row in all_joined:
        if is_not_judged(row):
            reason = row["outcome"]["excluded"]
            not_judged[reason] = not_judged.get(reason, 0) + 1
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
        "not_judged": sum(not_judged.values()),
        "not_judged_by_reason": not_judged,
        "join_rate": (len(with_outcome) / len(joined)) if joined else None,
        "advised": len(advised),
        "agreement_rate": (agree / len(advised)) if advised else None,
        "by_source": by_source,
    }

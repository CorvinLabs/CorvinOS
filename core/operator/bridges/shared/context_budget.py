"""context_budget.py — Layer 20 context memory manager (Phase-1 MVP).

Treats the LLM context window as a managed resource: per-session
quotas, OOM policies, and a working-set tracker that supports eviction
when the budget is exceeded.

This module is **pure bookkeeping** — token counting is the caller's
responsibility (use ``tiktoken`` or the Anthropic API's usage field).
The module accepts ``(session_id, turn_id, tokens)`` and decides what
to do.

Three OOM policies:

  - **evict** — drop the oldest turns until usage falls below a
    target. Lossy, but fast and predictable.
  - **compress** — like evict, but the caller is expected to summarise
    the dropped turns into one new compact turn (tracked here, but
    the summarisation itself is the agent's job).
  - **reject** — refuse the new turn. Operator must intervene
    (raise quota, evict manually, or accept the failure).

Storage in ``<corvin_home>/run/budgets.json`` — single file, atomic
write via tmp+rename. fcntl.flock serialises writers; reads are
mtime-cached for cheap polling. Cold-storage / vector-store paging
(the optional second tier) is intentionally NOT in the MVP — it needs
embedding-API integration that isn't safely testable in isolation.
"""
from __future__ import annotations

import contextlib
from _compat_fcntl import fcntl  # portable: real fcntl on POSIX, no-op flock on Windows
import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional

from paths import corvin_home  # type: ignore


# --------------------------------------------------------------------- consts

OOM_POLICIES = ("evict", "compress", "reject")
DEFAULT_QUOTA = 100_000   # tokens
WARN_THRESHOLD = 0.9      # used / quota at which we set action=warn


# --------------------------------------------------------------------- paths

def _run_dir() -> Path:
    d = corvin_home() / "run"
    d.mkdir(parents=True, exist_ok=True)
    return d


def budgets_file() -> Path:
    return _run_dir() / "budgets.json"


def _lock_file() -> Path:
    return _run_dir() / "budgets.json.lock"


# --------------------------------------------------------------------- locks

@contextlib.contextmanager
def _exclusive_lock() -> Iterator[None]:
    fd = os.open(str(_lock_file()), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fd, fcntl.LOCK_UN)
        finally:
            os.close(fd)


# --------------------------------------------------------------------- io

_read_cache: Dict[str, Any] = {"mtime": -1.0, "data": {}}


def _now_iso() -> str:
    return (
        datetime.now(timezone.utc)
        .isoformat(timespec="seconds")
        .replace("+00:00", "Z")
    )


def _load_all() -> Dict[str, Dict[str, Any]]:
    f = budgets_file()
    try:
        mt = f.stat().st_mtime
    except FileNotFoundError:
        _read_cache.update(mtime=-1.0, data={})
        return {}
    if mt == _read_cache["mtime"]:
        # deep-ish copy: top-level dict + nested record dicts
        return {k: dict(v, turns=list(v.get("turns", [])))
                for k, v in _read_cache["data"].items()}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    _read_cache.update(mtime=mt, data=data)
    return {k: dict(v, turns=list(v.get("turns", [])))
            for k, v in data.items()}


def _save_all(data: Dict[str, Dict[str, Any]]) -> None:
    f = budgets_file()
    tmp = f.with_suffix(f.suffix + ".tmp")
    tmp.write_text(
        json.dumps(data, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    os.replace(tmp, f)
    _read_cache["mtime"] = -1.0


def _recompute_used(record: Dict[str, Any]) -> int:
    return sum(int(t.get("tokens", 0)) for t in record.get("turns", []))


# --------------------------------------------------------------------- api

def register_session_budget(
    session_id: str,
    quota: int = DEFAULT_QUOTA,
    *,
    oom_policy: str = "compress",
) -> Dict[str, Any]:
    """Register a session with a token quota.

    Re-registering an existing session resets ``turns`` and ``used``
    but preserves the session's identity. Use ``unregister`` if you
    want it gone.
    """
    if not session_id:
        raise ValueError("session_id must be non-empty")
    if quota <= 0:
        raise ValueError(f"quota must be positive, got {quota}")
    if oom_policy not in OOM_POLICIES:
        raise ValueError(
            f"oom_policy must be one of {OOM_POLICIES}, got {oom_policy!r}"
        )
    with _exclusive_lock():
        data = _load_all()
        record = {
            "session_id": session_id,
            "quota": quota,
            "used": 0,
            "oom_policy": oom_policy,
            "turns": [],
            "registered_at": _now_iso(),
            "evictions": 0,
        }
        data[session_id] = record
        _save_all(data)
    return dict(record, turns=list(record["turns"]))


def account_turn(
    session_id: str,
    turn_id: str,
    tokens: int,
    *,
    content_hash: Optional[str] = None,
) -> Dict[str, Any]:
    """Append a turn to the session's working set.

    Returns the updated budget record. Raises if the session isn't
    registered or tokens is negative.
    """
    if tokens < 0:
        raise ValueError(f"tokens must be non-negative, got {tokens}")
    with _exclusive_lock():
        data = _load_all()
        rec = data.get(session_id)
        if rec is None:
            raise KeyError(f"session {session_id!r} has no budget registered")
        rec["turns"].append(
            {
                "turn_id": turn_id,
                "tokens": int(tokens),
                "content_hash": content_hash,
                "ts": _now_iso(),
            }
        )
        rec["used"] = _recompute_used(rec)
        data[session_id] = rec
        _save_all(data)
        return dict(rec, turns=list(rec["turns"]))


def check_budget(
    session_id: str,
    *,
    pending_tokens: int = 0,
) -> Dict[str, Any]:
    """Evaluate budget state for a session.

    Returns a decision envelope:

      {
        "allowed":   bool,         # caller may proceed?
        "action":    str,          # "ok" | "warn" | "<oom_policy>"
        "used":      int,          # current tokens (incl. pending)
        "quota":     int,
        "headroom":  int,          # quota - used
        "headroom_pct": float,     # 0..1
        "policy":    str,          # configured oom_policy
      }

    ``pending_tokens`` lets the caller check whether the *next* turn
    would fit before committing to it.
    """
    data = _load_all()
    rec = data.get(session_id)
    if rec is None:
        raise KeyError(f"session {session_id!r} has no budget registered")
    quota = int(rec["quota"])
    used = int(rec.get("used", 0)) + max(0, int(pending_tokens))
    headroom = quota - used
    pct = used / quota if quota else 1.0
    if used > quota:
        action = rec.get("oom_policy", "compress")
        allowed = action != "reject"
    elif pct >= WARN_THRESHOLD:
        action = "warn"
        allowed = True
    else:
        action = "ok"
        allowed = True
    return {
        "session_id": session_id,
        "allowed": allowed,
        "action": action,
        "used": used,
        "quota": quota,
        "headroom": headroom,
        "headroom_pct": round(pct, 4),
        "policy": rec.get("oom_policy", "compress"),
    }


def working_set(session_id: str) -> List[Dict[str, Any]]:
    """Return the recorded turns in chronological (oldest-first) order."""
    data = _load_all()
    rec = data.get(session_id)
    if rec is None:
        raise KeyError(f"session {session_id!r} has no budget registered")
    return list(rec.get("turns", []))


def evict(
    session_id: str,
    *,
    target_used: Optional[int] = None,
    target_pct: float = 0.7,
) -> List[str]:
    """Drop oldest turns until ``used`` falls to/below the target.

    If ``target_used`` is given (absolute tokens), stop there. Else
    stop at ``target_pct * quota`` (default 70%). Returns the list of
    evicted turn_ids in chronological order.
    """
    if target_pct <= 0 or target_pct >= 1:
        raise ValueError("target_pct must be in (0, 1)")
    with _exclusive_lock():
        data = _load_all()
        rec = data.get(session_id)
        if rec is None:
            raise KeyError(f"session {session_id!r} has no budget registered")
        quota = int(rec["quota"])
        if target_used is None:
            target_used = int(quota * target_pct)
        evicted: List[str] = []
        turns = list(rec.get("turns", []))
        while turns and _recompute_used({"turns": turns}) > target_used:
            dropped = turns.pop(0)
            evicted.append(dropped["turn_id"])
        rec["turns"] = turns
        rec["used"] = _recompute_used(rec)
        rec["evictions"] = rec.get("evictions", 0) + len(evicted)
        data[session_id] = rec
        _save_all(data)
        return evicted


def set_oom_policy(session_id: str, policy: str) -> Dict[str, Any]:
    """Change the OOM policy for a session."""
    if policy not in OOM_POLICIES:
        raise ValueError(
            f"oom_policy must be one of {OOM_POLICIES}, got {policy!r}"
        )
    with _exclusive_lock():
        data = _load_all()
        rec = data.get(session_id)
        if rec is None:
            raise KeyError(f"session {session_id!r} has no budget registered")
        rec["oom_policy"] = policy
        data[session_id] = rec
        _save_all(data)
        return dict(rec, turns=list(rec["turns"]))


def set_quota(session_id: str, quota: int) -> Dict[str, Any]:
    """Change the quota for a session."""
    if quota <= 0:
        raise ValueError(f"quota must be positive, got {quota}")
    with _exclusive_lock():
        data = _load_all()
        rec = data.get(session_id)
        if rec is None:
            raise KeyError(f"session {session_id!r} has no budget registered")
        rec["quota"] = quota
        data[session_id] = rec
        _save_all(data)
        return dict(rec, turns=list(rec["turns"]))


def get_budget(session_id: str) -> Optional[Dict[str, Any]]:
    """Inspect a session's budget record. None if not registered."""
    data = _load_all()
    rec = data.get(session_id)
    if rec is None:
        return None
    return dict(rec, turns=list(rec.get("turns", [])))


def list_budgets() -> List[Dict[str, Any]]:
    """List all registered session budgets, sorted by usage descending."""
    data = _load_all()
    out = [dict(v, turns=list(v.get("turns", []))) for v in data.values()]
    out.sort(key=lambda r: -r.get("used", 0))
    return out


def unregister_session_budget(session_id: str) -> bool:
    """Remove a session's budget. Returns True if it existed."""
    with _exclusive_lock():
        data = _load_all()
        if session_id in data:
            del data[session_id]
            _save_all(data)
            return True
        return False


def format_budget_table(records: List[Dict[str, Any]]) -> str:
    """Render /budget show output as a fixed-width table."""
    if not records:
        return "(no session budgets registered)"
    rows = [("SESSION", "USED", "QUOTA", "PCT", "POLICY", "TURNS")]
    for r in records:
        used = r.get("used", 0)
        quota = r.get("quota", 0)
        pct = (used / quota * 100) if quota else 0.0
        rows.append(
            (
                r["session_id"],
                _humanize(used),
                _humanize(quota),
                f"{pct:.0f}%",
                r.get("oom_policy", "?"),
                str(len(r.get("turns", []))),
            )
        )
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    out = []
    for i, r in enumerate(rows):
        out.append("  ".join(c.ljust(widths[j]) for j, c in enumerate(r)))
        if i == 0:
            out.append("  ".join("-" * w for w in widths))
    return "\n".join(out)


def _humanize(n: int) -> str:
    if n < 1000:
        return str(n)
    if n < 1_000_000:
        return f"{n / 1000:.1f}k".rstrip("0").rstrip(".")
    return f"{n / 1_000_000:.1f}M".rstrip("0").rstrip(".")

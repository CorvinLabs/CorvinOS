"""L5 Phase 2 rollback — a persisted, tenant-scoped trip (ADR-2092 G4).

Once the correctness judge trips, Phase 2 is off for the tenant on every surface and
across restarts until an operator clears it with :func:`reset_rollback`
(``POST /v1/console/learning/routing/rollback/reset``). The old in-memory detector
forgot its state on restart and, lacking outcomes, never tripped.

Trip order (review r3_s2-3) — restrictive first, unlocking never before its record:

1. the RESTRICTIVE-ONLY pending marker (``rollback_pending.json``) and the in-process
   pending entry are written FIRST: Phase 2 is off for the tenant in every process and
   across restarts from this moment on;
2. then ``routing.rollback_triggered`` is chained;
3. then the authoritative trip state (``rollback_state.json``) is written, and only
   after it is on disk is the marker removed.

A failure at any step leaves the marker (Phase 2 stays off) and the trip is retried —
by the next phase decision of any turn (:func:`maybe_retry_pending`, throttled) and by
the next readiness refresh. A retry after a committed record (step 3 failed) only
writes the trip state; it does not chain a second record. A reset is audit-first too:
``routing.rollback_reset`` commits before the state is written.

Every authoritative state write carries a ``generation`` one higher than the one it
replaces (review r3_s2-4/-5). Other processes compare it: the readiness cache drops a
verdict computed under another generation, and an in-process pending trip created
under an older generation than the persisted one is dropped — a reset (or trip) by
another process came after it and wins.
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Optional

from core.skills.os_skills.monitoring import correctness_tracker, routing_ledger

STATE_FILENAME = "rollback_state.json"
PENDING_FILENAME = "rollback_pending.json"
#: Minimum seconds between two background retries of a pending trip per tenant.
PENDING_RETRY_S = 60.0
#: Returns True only when the record committed to the hash-chained audit trail.
AuditFn = Callable[[str, dict], bool]
#: Tenants whose judge tripped but whose trip state is not on disk yet (process-local):
#: ``{"generation": int | None, "committed": bool, "details": dict}``.
_pending_trips: dict[str, dict] = {}
_pending_lock = threading.Lock()
_retry_last: dict[str, float] = {}


def state_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / STATE_FILENAME


def pending_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / PENDING_FILENAME


def _read_json(path: Path) -> tuple[str, Optional[dict]]:
    """``("missing", None)`` | ``("ok", dict)`` | ``("bad", None)``."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return "bad", None
    try:
        data = json.loads(raw)
    except ValueError:
        return "bad", None
    return ("ok", data) if isinstance(data, dict) else ("bad", None)


def _gen_of(data: Optional[dict]) -> Optional[int]:
    g = (data or {}).get("generation")
    return g if isinstance(g, int) and not isinstance(g, bool) and g >= 0 else None


def persisted_generation(tenant_id: str) -> Optional[int]:
    """Generation of the authoritative rollback state: 0 when it never changed (or a
    pre-generation file), None when the file cannot be read."""
    status, data = _read_json(state_path(tenant_id))
    if status == "missing":
        return 0
    if status == "bad":
        return None
    return _gen_of(data) or 0


def state_stamp(tenant_id: str) -> tuple:
    """Cheap identity of the authoritative rollback state, for cross-process cache
    invalidation: (generation, inode, mtime). Every write is a fresh ``os.replace``."""
    path = state_path(tenant_id)
    try:
        st = path.stat()
    except FileNotFoundError:
        return ("missing",)
    except OSError:
        return ("error",)
    return (persisted_generation(tenant_id), st.st_ino, st.st_mtime_ns)


def _drop_stale_pending(tenant_id: str) -> None:
    """Review r3_s2-5: a pending trip created under an OLDER generation than the
    persisted one was overtaken by another process's reset or trip — drop it here
    instead of re-chaining over an operator's reset. A marker without a generation,
    or an unreadable state, keeps restricting."""
    current = persisted_generation(tenant_id)
    if current is None:
        return
    with _pending_lock:
        entry = _pending_trips.get(tenant_id)
        if entry is not None and entry.get("generation") is not None \
                and current > entry["generation"]:
            _pending_trips.pop(tenant_id, None)
    status, data = _read_json(pending_path(tenant_id))
    marker_gen = _gen_of(data) if status == "ok" else None
    if marker_gen is not None and current > marker_gen:
        try:
            pending_path(tenant_id).unlink()
        except OSError:
            pass   # an undeletable marker keeps restricting: the safe direction


def _pending_entry(tenant_id: str) -> Optional[dict]:
    """The pending trip (in-process first, else the persisted marker), or None.
    Fail-closed: an unreadable marker is a pending trip without details."""
    _drop_stale_pending(tenant_id)
    with _pending_lock:
        entry = _pending_trips.get(tenant_id)
        if entry is not None:
            return dict(entry)
    status, data = _read_json(pending_path(tenant_id))
    if status == "missing":
        return None
    if status == "bad":
        return {"generation": None, "committed": False, "details": None}
    details = data.get("details")
    return {"generation": _gen_of(data),
            "committed": data.get("committed") is True,
            "details": details if isinstance(details, dict) else None}


def _is_pending(tenant_id: str) -> bool:
    return _pending_entry(tenant_id) is not None


def _default_audit(tenant_id: str) -> AuditFn:
    def _emit(event_type: str, details: dict) -> bool:
        try:
            from audit import audit_event  # noqa: PLC0415

            return bool(audit_event(event_type, details=details, tenant_id=tenant_id))
        except Exception:  # noqa: BLE001 — no writer: the trip stays pending
            return False
    return _emit


def _invalidate_readiness() -> None:
    try:
        from core.skills.os_skills.monitoring import readiness  # noqa: PLC0415 — cycle

        readiness.clear_cache()
    except Exception:  # noqa: BLE001
        pass


def last_change_ts(tenant_id: str) -> tuple[Optional[float], bool]:
    """``(ts, readable)`` of the last trip or reset. ``(None, True)``: never changed.

    The state file holds the latest change only, and a reset always follows the trip
    it clears, so its ``at`` IS the later of the last trip and the last reset.
    """
    status, data = _read_json(state_path(tenant_id))
    if status == "missing":
        return None, True
    if status == "bad":
        return None, False
    at = data.get("at")
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None, False
    return float(at), True


def rollback_active(tenant_id: str) -> bool:
    """Fail-closed: only a MISSING state file, or a readable one saying
    ``tripped: false``, means inactive. A torn or unreadable file keeps Phase 2 off,
    and so does a trip whose trip state is not on disk yet (in-process or the
    persisted pending marker of any process)."""
    if _is_pending(tenant_id):
        return True
    status, data = _read_json(state_path(tenant_id))
    if status == "missing":
        return False
    if status == "bad" or not isinstance(data.get("tripped"), bool):
        return True
    return data["tripped"]


def _write_state(tenant_id: str, data: dict, *, path: Optional[Path] = None) -> None:
    """Atomic write. The authoritative state (``path`` None) gets the next generation."""
    if path is None:
        path = state_path(tenant_id)
        data = {**data, "generation": (persisted_generation(tenant_id) or 0) + 1}
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    try:
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _invalidate_readiness()


def _hold_pending(tenant_id: str, entry: dict) -> None:
    """Restrictive: the in-process entry always, the marker when it can be written."""
    with _pending_lock:
        _pending_trips[tenant_id] = dict(entry)
    try:
        _write_state(tenant_id, {"pending": True, "at": time.time(), **entry},
                     path=pending_path(tenant_id))
    except OSError:
        pass   # the in-process pending trip still holds this process
    _invalidate_readiness()


def _commit_trip_state(tenant_id: str, details: dict) -> bool:
    """Step 3: the authoritative trip, then the marker goes. A failing write keeps
    the marker (Phase 2 stays off) and is retried."""
    try:
        _write_state(tenant_id, {"tripped": True, "at": time.time(), **details})
    except OSError:
        return True
    _clear_pending(tenant_id)
    _invalidate_readiness()
    return True


def evaluate(tenant_id: str, *, audit: Optional[AuditFn] = None,
             since_ts: float = 0.0) -> bool:
    """Judge the ledger; trip (marker, record, state) when the Skill trails.
    Returns the active state."""
    pending = _pending_entry(tenant_id)
    if pending is None and rollback_active(tenant_id):
        return True
    if pending is not None and _state_says_tripped(tenant_id):
        # Another process committed the trip meanwhile: the marker is obsolete.
        _clear_pending(tenant_id)
        return True
    if pending is not None and pending["committed"] and pending["details"] is not None:
        # The record committed earlier; only the trip state is outstanding.
        return _commit_trip_state(tenant_id, pending["details"])
    details = pending["details"] if pending is not None else None
    if details is None:
        # NOT-JUDGED turns (cancelled, orphaned, no engine) never reach the judge.
        rows = routing_ledger.judged(
            routing_ledger.join(routing_ledger.read_records(tenant_id, since_ts=since_ts)))
        metrics = correctness_tracker.compute_metrics(rows)
        # A pending trip is retried even if the window moved on: once tripped, only
        # an operator reset clears it.
        if pending is None and not correctness_tracker.should_rollback(metrics):
            return False
        details = {
            "tenant_id": tenant_id,
            "skill_n": metrics.skill_n,
            "bundled_n": metrics.bundled_n,
            "skill_success_rate": round(metrics.skill_success_rate or 0.0, 4),
            "bundled_success_rate": round(metrics.bundled_success_rate or 0.0, 4),
        }
    generation = (pending or {}).get("generation") if pending is not None \
        else persisted_generation(tenant_id)
    entry = {"generation": generation, "committed": False, "details": details}
    if pending is None:
        _hold_pending(tenant_id, entry)            # step 1: restrictive first
    committed = False
    if audit is not None:
        try:
            committed = bool(audit("routing.rollback_triggered", details))  # step 2
        except Exception:  # noqa: BLE001 — an audit that raises did not commit
            committed = False
    if not committed:
        if pending is not None:
            with _pending_lock:
                _pending_trips.setdefault(tenant_id, entry)
        return True
    _hold_pending(tenant_id, {**entry, "committed": True})   # never re-chained
    return _commit_trip_state(tenant_id, details)   # step 3


def _state_says_tripped(tenant_id: str) -> bool:
    status, data = _read_json(state_path(tenant_id))
    return status == "ok" and data.get("tripped") is True


def _clear_pending(tenant_id: str) -> None:
    """Drop the pending marker — only ever AFTER the state that replaces it is on disk."""
    try:
        pending_path(tenant_id).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return   # an undeletable marker keeps restricting: the safe direction
    with _pending_lock:
        _pending_trips.pop(tenant_id, None)


def maybe_retry_pending(tenant_id: str, *, audit: Optional[AuditFn] = None,
                        background: bool = True) -> None:
    """Retry a pending trip, at most once per ``PENDING_RETRY_S``.

    Called by every phase decision that finds Phase 2 held off by a rollback and by
    every readiness refresh. A no-op without a pending trip."""
    if not _is_pending(tenant_id):
        return
    now = time.monotonic()
    with _pending_lock:
        last = _retry_last.get(tenant_id)
        if last is not None and now - last < PENDING_RETRY_S:
            return
        _retry_last[tenant_id] = now
    emit = audit or _default_audit(tenant_id)

    def _run() -> None:
        try:
            evaluate(tenant_id, audit=emit)
        except Exception:  # noqa: BLE001 — still pending: the marker keeps restricting
            pass
    if background:
        threading.Thread(target=_run, daemon=True, name="l5-rollback-retry").start()
    else:
        _run()


def reset_rollback(tenant_id: str, *, audit: Callable[[str, dict], bool]) -> None:
    """Operator action: clear a trip. Audit-first: no committed record → no reset."""
    if not audit("routing.rollback_reset", {"tenant_id": tenant_id}):
        raise RuntimeError("routing.rollback_reset did not commit — rollback stays active")
    _write_state(tenant_id, {"tripped": False, "at": time.time()})
    _clear_pending(tenant_id)
    _invalidate_readiness()


def reset_for_tests() -> None:
    with _pending_lock:
        _pending_trips.clear()
        _retry_last.clear()

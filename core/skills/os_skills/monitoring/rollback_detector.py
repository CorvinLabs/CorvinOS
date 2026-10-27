"""L5 Phase 2 rollback — a persisted, tenant-scoped trip (ADR-2092 G4).

Once the correctness judge trips, Phase 2 is off for the tenant on every surface and
across restarts until an operator clears it with :func:`reset_rollback`. The old
in-memory detector forgot its state on restart and, lacking outcomes, never tripped.

Audit-first, both ways: the state file is written only after the
``routing.rollback_triggered`` (trip) or ``routing.rollback_reset`` (reset) record
committed. A trip whose record did not commit persists nothing; it keeps Phase 2 off
in THIS process (an in-memory pending trip, fail-safe) and is retried by the next
judgement. Every state change invalidates the readiness cache.
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
#: Returns True only when the record committed to the hash-chained audit trail.
AuditFn = Callable[[str, dict], bool]
#: Tenants whose judge tripped but whose trip record did not commit (process-local).
_pending_trips: set[str] = set()
_pending_lock = threading.Lock()


def state_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / STATE_FILENAME


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
    path = state_path(tenant_id)
    try:
        data = json.loads(path.read_text())
    except FileNotFoundError:
        return None, True
    except (OSError, ValueError):
        return None, False
    at = data.get("at") if isinstance(data, dict) else None
    if isinstance(at, bool) or not isinstance(at, (int, float)):
        return None, False
    return float(at), True


def rollback_active(tenant_id: str) -> bool:
    """Fail-closed: only a MISSING state file, or a readable one saying
    ``tripped: false``, means inactive. A torn or unreadable file keeps Phase 2 off,
    and so does a trip of this process whose audit record has not committed yet."""
    with _pending_lock:
        if tenant_id in _pending_trips:
            return True
    path = state_path(tenant_id)
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return False
    except OSError:
        return True
    try:
        data = json.loads(raw)
    except ValueError:
        return True
    if not isinstance(data, dict) or not isinstance(data.get("tripped"), bool):
        return True
    return data["tripped"]


def _write_state(tenant_id: str, data: dict) -> None:
    path = state_path(tenant_id)
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        json.dump(data, fh)
    os.replace(tmp, path)
    _invalidate_readiness()


def evaluate(tenant_id: str, *, audit: Optional[AuditFn] = None,
             since_ts: float = 0.0) -> bool:
    """Judge the ledger; trip (audit-first) when the Skill trails. Returns active state."""
    with _pending_lock:
        pending = tenant_id in _pending_trips
    if not pending and rollback_active(tenant_id):
        return True
    # NOT-JUDGED turns (cancelled, orphaned, no engine) never reach the judge.
    rows = routing_ledger.judged(
        routing_ledger.join(routing_ledger.read_records(tenant_id, since_ts=since_ts)))
    metrics = correctness_tracker.compute_metrics(rows)
    # A pending trip is retried even if the window moved on: once tripped, only an
    # operator reset clears it.
    if not pending and not correctness_tracker.should_rollback(metrics):
        return False
    details = {
        "tenant_id": tenant_id,
        "skill_n": metrics.skill_n,
        "bundled_n": metrics.bundled_n,
        "skill_success_rate": round(metrics.skill_success_rate or 0.0, 4),
        "bundled_success_rate": round(metrics.bundled_success_rate or 0.0, 4),
    }
    committed = False
    if audit is not None:
        try:
            committed = bool(audit("routing.rollback_triggered", details))
        except Exception:  # noqa: BLE001 — an audit that raises did not commit
            committed = False
    if not committed:
        # Audit-first: no persisted trip without its record. Fail-safe: Phase 2
        # stays off in this process until the next judgement commits the trip.
        with _pending_lock:
            _pending_trips.add(tenant_id)
        _invalidate_readiness()
        return True
    _write_state(tenant_id, {"tripped": True, "at": time.time(), **details})
    with _pending_lock:
        _pending_trips.discard(tenant_id)
    _invalidate_readiness()
    return True


def reset_rollback(tenant_id: str, *, audit: Callable[[str, dict], bool]) -> None:
    """Operator action: clear a trip. Audit-first: no committed record → no reset."""
    if not audit("routing.rollback_reset", {"tenant_id": tenant_id}):
        raise RuntimeError("routing.rollback_reset did not commit — rollback stays active")
    _write_state(tenant_id, {"tripped": False, "at": time.time()})
    with _pending_lock:
        _pending_trips.discard(tenant_id)
    _invalidate_readiness()


def reset_for_tests() -> None:
    with _pending_lock:
        _pending_trips.clear()

"""L5 Phase 2 rollback — a persisted, tenant-scoped trip (ADR-2092 G4).

Once the correctness judge trips, Phase 2 is off for the tenant on every surface and
across restarts until an operator clears it with :func:`reset_rollback`. The old
in-memory detector forgot its state on restart and, lacking outcomes, never tripped.

Audit-first, both ways: the state file is written only after the
``routing.rollback_triggered`` (trip) or ``routing.rollback_reset`` (reset) record
committed. A trip whose record did not commit writes no trip state; it writes a
RESTRICTIVE-ONLY pending marker (``rollback_pending.json``) that keeps Phase 2 off for
the tenant in every process and across restarts, and it is retried — by the next
phase decision of any turn (:func:`maybe_retry_pending`, throttled) and by the next
readiness refresh — until the ``routing.rollback_triggered`` record commits. Then the
trip state is written (the authoritative trip) and only after that the marker is
removed. A state that only restricts may be written before its audit record; a state
that unlocks never is (review r2_s2-4: the pending trip lived in process memory only,
was retried only on a Skill-served outcome — which a pending trip makes impossible —
and vanished on restart). Every state change invalidates the readiness cache.
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
#: Tenants whose judge tripped but whose trip record did not commit (process-local).
_pending_trips: set[str] = set()
_pending_lock = threading.Lock()
_retry_last: dict[str, float] = {}


def state_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / STATE_FILENAME


def pending_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / PENDING_FILENAME


def _pending_marker(tenant_id: str) -> bool:
    """Fail-closed: anything but a MISSING marker counts as a pending trip."""
    try:
        pending_path(tenant_id).stat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


def _is_pending(tenant_id: str) -> bool:
    with _pending_lock:
        if tenant_id in _pending_trips:
            return True
    return _pending_marker(tenant_id)


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
    and so does a trip whose audit record has not committed yet (in-process or the
    persisted pending marker of any process)."""
    if _is_pending(tenant_id):
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


def _write_state(tenant_id: str, data: dict, *, path: Optional[Path] = None) -> None:
    path = path or state_path(tenant_id)
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
    pending = _is_pending(tenant_id)
    if not pending and rollback_active(tenant_id):
        return True
    if pending and _state_says_tripped(tenant_id):
        # Another process committed the trip meanwhile: the marker is obsolete.
        _clear_pending(tenant_id)
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
        # Audit-first: no trip state without its record. Fail-safe: the pending
        # marker (restrictive only) keeps Phase 2 off everywhere until a retry
        # commits the trip.
        with _pending_lock:
            _pending_trips.add(tenant_id)
        try:
            _write_state(tenant_id, {"pending": True, "at": time.time()},
                         path=pending_path(tenant_id))
        except OSError:
            pass   # the in-process pending trip still holds this process
        _invalidate_readiness()
        return True
    _write_state(tenant_id, {"tripped": True, "at": time.time(), **details})
    _clear_pending(tenant_id)
    _invalidate_readiness()
    return True


def _state_says_tripped(tenant_id: str) -> bool:
    try:
        data = json.loads(state_path(tenant_id).read_text())
    except (OSError, ValueError):
        return False
    return isinstance(data, dict) and data.get("tripped") is True


def _clear_pending(tenant_id: str) -> None:
    """Drop the pending marker — only ever AFTER the state that replaces it is on disk."""
    try:
        pending_path(tenant_id).unlink()
    except FileNotFoundError:
        pass
    except OSError:
        return   # an undeletable marker keeps restricting: the safe direction
    with _pending_lock:
        _pending_trips.discard(tenant_id)


def maybe_retry_pending(tenant_id: str, *, audit: Optional[AuditFn] = None,
                        background: bool = True) -> None:
    """Retry a pending trip's audit record, at most once per ``PENDING_RETRY_S``.

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

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
``routing.rollback_reset`` commits before the state is written. A trip (steps 1-3) and
a reset run under one per-tenant rollback lock (``rollback.lock``, ``flock``) and so
never interleave in any process (review r4_s2-3); a trip or reset invalidates only its
own tenant's readiness verdicts (review r4_s2-2). A lock that cannot be taken never
reads as "not tripped" (review r5_s2-1): the judge holds the tenant (in-process
entry always, the marker where none exists and the dir is writable) and chains
``routing.rollback_unverified``; the reset route answers 503 ``lock_unavailable``.
Such an UNVERIFIED hold is released automatically and audited (review c120 r6_s2-1):
the next judge that gets the lock re-judges it, trips on a trailing window (the hold
is replaced by the judged trip — entry and marker — before the record is chained, so
a record that fails to commit leaves a judged trip, review c120 r7), and on a
judgeable window that finds no trip chains ``routing.rollback_hold_released`` and only
then removes the marker. A marker that cannot be read inside the lock holds without
judging (never a trip over an unread window). A JUDGED trip still needs the operator.

A reset opens a fresh window (reviews r5_s2-2, c120 r6_s2-2): the reset stores the
ledger's position (device + inode + byte offset + a fingerprint of the bytes before
the offset, ``ledger_pos``) and the judge evaluates only rows appended after it — a
ledger position, not a wall-clock time, so a clock step cannot blind the judge. A
ledger that no longer matches the position (re-created, also under a reused inode
number, or truncated and regrown) is read whole (review c120 r7b-2/-3). The pre-reset failures cannot re-trip the first Skill-served
turn after a reset, and the minimum sample size per side must be met again on
post-reset turns.

Every authoritative state write carries a ``generation`` one higher than the one it
replaces (review r3_s2-4/-5). Other processes compare it: the readiness cache drops a
verdict computed under another generation, and an in-process pending trip created
under an older generation than the persisted one is dropped — a reset (or trip) by
another process came after it and wins.
"""
from __future__ import annotations

import contextlib
import json
import os
import threading
import time
from pathlib import Path
from typing import Callable, Iterator, Optional

try:
    import fcntl as _fcntl
except ImportError:  # pragma: no cover — Windows
    _fcntl = None

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
#: Why a tenant is held WITHOUT a judged trip (review c120 r6_s2-1). Such a hold is
#: re-judged once the lock works and released (``routing.rollback_hold_released``)
#: when a judgeable window finds no trip; a judged trip (``cause`` None, details from
#: a judged window) is never released automatically.
HOLD_CAUSES = frozenset({"lock_unavailable", "marker_unreadable", "marker_torn"})


def state_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / STATE_FILENAME


def pending_path(tenant_id: str) -> Path:
    return routing_ledger.routing_dir(tenant_id) / PENDING_FILENAME


def _read_json(path: Path, *, io_status: str = "bad") -> tuple[str, Optional[dict]]:
    """``("missing", None)`` | ``("ok", dict)`` | ``("bad", None)``. A read that
    fails with an ``OSError`` (EMFILE, EACCES, ...) reports ``io_status`` — "bad" for
    every caller except the pending marker, which tells a transient read failure from
    a torn file."""
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return "missing", None
    except OSError:
        return io_status, None
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
    instead of re-chaining over an operator's reset.

    Staleness is decided by GENERATIONS ONLY (review c120 r7b-1): every pending entry
    and marker carries the generation of the state it was created under, and only a
    persisted generation strictly NEWER than the entry's drops it. No wall-clock
    reading is compared — a reset taken while the clock was ahead used to erase every
    later hold and every judged-but-unrecorded trip. A marker without a generation, a
    state without one (it reads as generation 0) or an unreadable state keeps
    restricting."""
    current = persisted_generation(tenant_id)
    if current is None:
        return

    def _stale(gen) -> bool:
        return gen is not None and current > gen
    with _pending_lock:
        entry = _pending_trips.get(tenant_id)
        if entry is not None and _stale(entry.get("generation")):
            _pending_trips.pop(tenant_id, None)
    status, data = _read_json(pending_path(tenant_id))
    if status == "ok" and _stale(_gen_of(data)):
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
    return _marker_entry(tenant_id)


def _marker_entry(tenant_id: str) -> Optional[dict]:
    """The persisted pending marker as an entry, or None when there is none."""
    status, data = _read_json(pending_path(tenant_id), io_status="io")
    if status == "missing":
        return None
    if status == "io":
        # Review c120 r6_s2-1: the marker exists in some form but cannot be read right
        # now (EMFILE, EACCES): restrictive, and the judge holds without judging.
        return {"generation": None, "committed": False, "details": None,
                "cause": "marker_unreadable"}
    if status == "bad":
        # Review c120 r7b-4: an EMPTY or torn marker (cut by a crash) says nothing
        # about a judged window. It is a HOLD, never a trip: re-judged, tripped only
        # when the window trips, released audited on a judgeable window that does not.
        return {"generation": None, "committed": False, "details": None,
                "cause": "marker_torn"}
    details = data.get("details")
    cause = data.get("cause")
    return {"generation": _gen_of(data),
            "committed": data.get("committed") is True,
            "details": details if isinstance(details, dict) else None,
            "created": data.get("created"),
            "cause": cause if cause in HOLD_CAUSES else None}


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


def _invalidate_readiness(tenant_id: str) -> None:
    """Drop only ``tenant_id``'s cached readiness verdicts (review r4_s2-2)."""
    try:
        from core.skills.os_skills.monitoring import readiness  # noqa: PLC0415 — cycle

        readiness.clear_cache(tenant_id)
    except Exception:  # noqa: BLE001
        pass


LOCK_FILENAME = "rollback.lock"


class LockUnavailable(RuntimeError):
    """The per-tenant rollback lock could not be taken (``rollback.lock`` or the
    routing dir cannot be opened/created, or ``flock`` failed). The judge treats this
    as "cannot judge safely" and holds Phase 2 off (review r5_s2-1); the reset route
    answers 503 ``lock_unavailable``."""


@contextlib.contextmanager
def _rollback_lock(tenant_id: str) -> Iterator[None]:
    """The per-tenant rollback lock (review r4_s2-3): a trip (marker, record, state)
    and a reset (re-read, record, state) never interleave, in one process or across
    processes. ``flock`` on a lock file in the tenant's routing dir — each holder opens
    its own descriptor, so two threads of one process exclude each other too. Where
    ``fcntl`` does not exist (Windows) ``msvcrt.locking`` on the first byte is used."""
    path = routing_ledger.routing_dir(tenant_id) / LOCK_FILENAME
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(path), os.O_RDWR | os.O_CREAT, 0o600)
    except OSError as exc:
        raise LockUnavailable(tenant_id) from exc
    try:
        if _fcntl is not None:
            _fcntl.flock(fd, _fcntl.LOCK_EX)
        else:  # pragma: no cover — Windows
            import msvcrt  # noqa: PLC0415

            while True:
                try:
                    msvcrt.locking(fd, msvcrt.LK_LOCK, 1)
                    break
                except OSError:
                    continue
    except OSError as exc:
        os.close(fd)
        raise LockUnavailable(tenant_id) from exc
    try:
        yield
    finally:
        try:
            if _fcntl is not None:
                _fcntl.flock(fd, _fcntl.LOCK_UN)
            else:  # pragma: no cover — Windows
                import msvcrt  # noqa: PLC0415

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
        finally:
            os.close(fd)


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
            fh.flush()
            os.fsync(fh.fileno())      # review c120 r7b-4: never a torn file after a crash
        os.replace(tmp, path)
    except BaseException:
        try:
            tmp.unlink()
        except OSError:
            pass
        raise
    _invalidate_readiness(tenant_id)


def _hold_pending(tenant_id: str, entry: dict) -> None:
    """Restrictive: the in-process entry always, the marker when it can be written."""
    with _pending_lock:
        _pending_trips[tenant_id] = dict(entry)
    try:
        _write_state(tenant_id, {"pending": True, "at": time.time(), **entry},
                     path=pending_path(tenant_id))
    except OSError:
        pass   # the in-process pending trip still holds this process
    _invalidate_readiness(tenant_id)


def _create_marker_if_absent(tenant_id: str, entry: dict) -> bool:
    """Write the pending marker for a hold only where NO marker exists: a fresh temp
    file is hard-linked to the marker path (``FileExistsError`` when one exists), so a
    judged trip's marker is never replaced by a hold. False when nothing was written
    (a marker exists, or no descriptor / no writable dir — the in-process entry holds)."""
    path = pending_path(tenant_id)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.{time.time_ns()}.hold.tmp")
    try:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as fh:
            json.dump({"pending": True, "at": time.time(), **entry}, fh)
            fh.flush()
            os.fsync(fh.fileno())      # review c120 r7b-4: the linked marker is complete
        os.link(tmp, path)
        return True
    except OSError:
        return False
    finally:
        try:
            tmp.unlink()
        except OSError:
            pass


def _commit_trip_state(tenant_id: str, details: dict) -> bool:
    """Step 3: the authoritative trip, then the marker goes. A failing write keeps
    the marker (Phase 2 stays off) and is retried."""
    try:
        _write_state(tenant_id, {"tripped": True, "at": time.time(), **details})
    except OSError:
        return True
    _clear_pending(tenant_id)
    _invalidate_readiness(tenant_id)
    return True


def evaluate(tenant_id: str, *, audit: Optional[AuditFn] = None,
             since_ts: float = 0.0) -> bool:
    """Judge the ledger; trip (marker, record, state) when the Skill trails.
    Returns the active state. Runs under the per-tenant rollback lock: a reset can
    never land between the trip's record and its state (review r4_s2-3).

    Fail-closed on the lock (review r5_s2-1): when the lock cannot be taken the
    tenant is held tripped (in-process pending entry, the pending marker if it can be
    written) and ``routing.rollback_unverified`` is chained; it never answers "not
    tripped" for want of a lock. The next judge or retry that gets the lock re-judges
    the hold (review c120 r6_s2-1): a window that trips completes it as a real trip;
    a judgeable window that finds no trip releases it, audited
    (``routing.rollback_hold_released`` before the marker goes); too few samples →
    still held. A judged trip is never released automatically: only an operator reset
    clears it."""
    with contextlib.ExitStack() as stack:
        try:
            stack.enter_context(_rollback_lock(tenant_id))
        except LockUnavailable:
            return _hold_unverified(tenant_id, audit=audit)
        return _evaluate_locked(tenant_id, audit=audit, since_ts=since_ts)


#: Tenants whose lock-unavailable hold has a committed ``routing.rollback_unverified``
#: record in this process (one record per hold, not one per retry).
_unverified_recorded: set[str] = set()


def _hold_unverified(tenant_id: str, *, audit: Optional[AuditFn]) -> bool:
    """The judge could not take the lock: hold Phase 2 off, chain the fact.

    Review c120 r6_s2-3: the in-process entry is written whenever no readable trip
    or pending trip already holds the tenant — also when the guard cannot read the
    marker (EMFILE): process memory needs no descriptor. The marker is written only
    where there is none yet (a judged marker is never overwritten by a hold)."""
    with _pending_lock:
        have_entry = tenant_id in _pending_trips
    if not have_entry and not _state_says_tripped(tenant_id):
        status, _ = _read_json(pending_path(tenant_id), io_status="io")
        entry = {"generation": persisted_generation(tenant_id), "committed": False,
                 "details": None, "created": time.time(), "cause": "lock_unavailable"}
        if status != "ok":
            with _pending_lock:
                _pending_trips.setdefault(tenant_id, entry)
            if status in ("missing", "io"):
                _create_marker_if_absent(tenant_id, entry)
            _invalidate_readiness(tenant_id)
    with _pending_lock:
        recorded = tenant_id in _unverified_recorded
    if audit is not None and not recorded:
        try:
            ok = bool(audit("routing.rollback_unverified",
                            {"tenant_id": tenant_id, "cause": "lock_unavailable"}))
        except Exception:  # noqa: BLE001 — an audit that raises did not commit
            ok = False
        if ok:
            with _pending_lock:
                _unverified_recorded.add(tenant_id)
    return True


def _window_rows(tenant_id: str, since_ts: float) -> list[dict]:
    """The judged rows of the judge's window (reviews c120 r5_s2-2, r6_s2-2).

    Reached only while no trip is active, so the state is the last RESET (or there is
    none). A reset stores the ledger's POSITION (``ledger_pos``: device + inode + byte
    offset + a fingerprint of the bytes before the offset) and the window is every row
    appended after it — never a comparison of two wall-clock readings, so a clock step
    before or after the reset neither hides post-reset rows nor admits pre-reset ones.
    A ledger that no longer matches the position (replaced, re-created under a reused
    inode number, truncated — also truncated and regrown past the offset) is read
    WHOLE, no row skipped (review c120 r7b-2/-3). The minimum sample size per side must
    be met again on post-reset turns before the judge can trip. A state written before
    positions existed (no ``ledger_pos``) still bounds the window by its ``at``.
    NOT-JUDGED turns (cancelled, orphaned, no engine) never reach the judge."""
    status, data = _read_json(state_path(tenant_id))
    pos = (data or {}).get("ledger_pos") if status == "ok" else None
    if routing_ledger.valid_position(pos):
        records = routing_ledger.read_records(tenant_id, since_ts=float(since_ts),
                                              since_pos=pos)
    else:
        changed_at, _readable = last_change_ts(tenant_id)
        since = max(float(since_ts), changed_at) if changed_at is not None else float(since_ts)
        records = routing_ledger.read_records(tenant_id, since_ts=since)
    return routing_ledger.judged(routing_ledger.join(records))


def _release_hold(tenant_id: str, pending: dict, metrics, *,
                  audit: Optional[AuditFn]) -> bool:
    """Review c120 r6_s2-1: an unverified hold (``cause`` in :data:`HOLD_CAUSES`, no
    judged details) whose window is now judgeable and finds no trip is released —
    audit-first: ``routing.rollback_hold_released`` commits BEFORE the marker and the
    in-process entry go. No committed record (or no audit writer) → still held.
    A judged trip's marker written meanwhile by another process is never removed here:
    the hold yields to it. Returns the active state."""
    status, data = _read_json(pending_path(tenant_id), io_status="io")
    if status == "io":
        return True                     # cannot see what the marker says: hold
    # A torn marker ("bad", review c120 r7b-4) is a hold like any other: it carries no
    # judged details, and the window just judged finds no trip.
    if status == "ok" and (data.get("cause") not in HOLD_CAUSES
                           or isinstance(data.get("details"), dict)):
        with _pending_lock:             # a judged trip is pending: it wins
            _pending_trips.pop(tenant_id, None)
        return True
    if audit is None:
        return True
    with _pending_lock:
        owe_unverified = (tenant_id in _pending_trips
                          and tenant_id not in _unverified_recorded)
    if owe_unverified:
        # This process held the tenant but could not chain the hold then (EMFILE):
        # the chain names the hold before it names its release.
        try:
            if not audit("routing.rollback_unverified",
                         {"tenant_id": tenant_id, "cause": pending.get("cause")}):
                return True
        except Exception:  # noqa: BLE001
            return True
        with _pending_lock:
            _unverified_recorded.add(tenant_id)
    try:
        ok = bool(audit("routing.rollback_hold_released", {
            "tenant_id": tenant_id, "cause": pending.get("cause"),
            "skill_n": int(metrics.skill_n), "bundled_n": int(metrics.bundled_n)}))
    except Exception:  # noqa: BLE001 — an audit that raises did not commit
        ok = False
    if not ok:
        return True
    _clear_pending(tenant_id)
    _invalidate_readiness(tenant_id)
    return rollback_active(tenant_id)


def _evaluate_locked(tenant_id: str, *, audit: Optional[AuditFn],
                     since_ts: float) -> bool:
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
    hold_cause = pending.get("cause") if pending is not None else None
    if hold_cause == "marker_unreadable":
        # Review c120 r6_s2-1 (EMFILE variant): the marker cannot be read inside the
        # lock (only ever the marker itself: an in-process entry is read first). Hold
        # — never a trip recorded over an unread window — and let the next run judge
        # what the marker says.
        return True
    details = pending["details"] if pending is not None else None
    if details is None:
        metrics = correctness_tracker.compute_metrics(_window_rows(tenant_id, since_ts))
        tripping = correctness_tracker.should_rollback(metrics)
        if pending is None and not tripping:
            return False
        if pending is not None and hold_cause in HOLD_CAUSES and not tripping:
            # An unverified hold, re-judged now that the lock works: released only on
            # a judgeable window; too few samples on either side → still held.
            if metrics.judgeable:
                return _release_hold(tenant_id, pending, metrics, audit=audit)
            return True
        # A pending JUDGED trip (or a hold whose window now trips) is completed even
        # if the window moved on: once tripped, only an operator reset clears it.
        details = {
            "tenant_id": tenant_id,
            "skill_n": metrics.skill_n,
            "bundled_n": metrics.bundled_n,
            "skill_success_rate": round(metrics.skill_success_rate or 0.0, 4),
            "bundled_success_rate": round(metrics.bundled_success_rate or 0.0, 4),
        }
    # The entry carries the generation it was created under — staleness is decided by
    # generations only (review c120 r7b-1). A torn marker has none: it is the current one.
    generation = (pending or {}).get("generation")
    if generation is None:
        generation = persisted_generation(tenant_id)
    created = (pending or {}).get("created")
    entry = {"generation": generation, "committed": False, "details": details,
             "created": created if isinstance(created, (int, float)) else time.time()}
    if pending is None or pending["details"] is None:
        # Step 1: restrictive first. A hold (or a torn marker) judged trailing just now
        # is REPLACED — in-process entry and marker — by the judged trip BEFORE its
        # record is chained (review c120 r7, J1): a record that fails to commit must
        # leave a judged trip, which no later window can release, not the hold.
        _hold_pending(tenant_id, entry)
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
        _unverified_recorded.discard(tenant_id)


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


class ResetNotApplied(RuntimeError):
    """The reset's state could not be written: the trip stays active. Either
    ``routing.rollback_reset`` committed and ``routing.rollback_reset_not_applied``
    follows it, or the outstanding trip state could not be written first and no reset
    record was chained."""


class NotTripped(LookupError):
    """``reset_rollback(require_active=True)``: no trip is active (read under the lock)."""


def reset_rollback(tenant_id: str, *, audit: Callable[[str, dict], bool],
                   require_active: bool = False) -> None:
    """Operator action: clear a trip. Audit-first: no committed record → no reset
    (``RuntimeError``). A state write that fails after the record committed chains
    the compensating ``routing.rollback_reset_not_applied`` and raises
    :class:`ResetNotApplied`; the rollback stays active.

    Review r4_s2-3: runs under the same per-tenant rollback lock as a trip, so it
    never lands between a trip's record and its state. Under the lock it re-reads the
    persisted state: ``require_active`` raises :class:`NotTripped` when no trip is
    active; a trip whose record committed but whose state is not on disk yet (its
    state write failed) gets that state written FIRST, so the persisted sequence
    follows the chain (trip, then reset). If that write fails the reset is refused
    with :class:`ResetNotApplied` before its record — the chain's last word stays the
    trip, which is still active."""
    with _rollback_lock(tenant_id):
        _reset_locked(tenant_id, audit=audit, require_active=require_active)


def _reset_locked(tenant_id: str, *, audit: Callable[[str, dict], bool],
                  require_active: bool) -> None:
    if require_active and not rollback_active(tenant_id):
        raise NotTripped(tenant_id)
    pending = _pending_entry(tenant_id)
    if (pending is not None and pending["committed"] and pending["details"] is not None
            and not _state_says_tripped(tenant_id)):
        try:
            _write_state(tenant_id, {"tripped": True, "at": time.time(), **pending["details"]})
        except OSError as exc:
            raise ResetNotApplied("trip state not written — rollback stays active") from exc
    # Review c120 r6_s2-2: the fresh window starts at a ledger POSITION, taken under
    # the lock before the record; without one the reset is refused (no record).
    pos = routing_ledger.ledger_position(tenant_id)
    if pos is None:
        raise ResetNotApplied("ledger position not readable — rollback stays active")
    if not audit("routing.rollback_reset", {"tenant_id": tenant_id}):
        raise RuntimeError("routing.rollback_reset did not commit — rollback stays active")
    try:
        _write_state(tenant_id, {"tripped": False, "at": time.time(), "ledger_pos": pos})
    except OSError as exc:
        try:
            audit("routing.rollback_reset_not_applied", {"tenant_id": tenant_id})
        except Exception:  # noqa: BLE001
            pass
        raise ResetNotApplied("rollback state not written — rollback stays active") from exc
    _clear_pending(tenant_id)
    _invalidate_readiness(tenant_id)


def reset_for_tests() -> None:
    with _pending_lock:
        _pending_trips.clear()
        _retry_last.clear()
        _unverified_recorded.clear()

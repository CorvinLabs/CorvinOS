"""PLAN-0948 D3/D4: the supervised host of the Data Hub learning daemon.

``DataHubLearningDaemon`` is a set of components plus an event loop that, run as written, woke ten
times a second with nothing to read and kept everything it learned in memory. This host is what makes it
a service:

* **Persisted.** The daemon's state (weights, consensus, causal edges, queue, cooldowns, source hashes)
  is snapshotted to ``<tenant>/learning/daemon/state.json`` (atomic, 0600) after any tick that changed
  it, together with the inbox cursor, and restored at start. A crash replays at most the unconfirmed
  inbox tail.
* **Fed.** Events arrive through the durable inbox (``learning_inbox``) written by the producers in
  other processes; the host also checks the watched data sources for change (a content hash, every
  ``SOURCE_CHECK_S``).
* **Idle-friendly.** One tick drains the inbox, runs the source check and the regeneration batch; the
  sleep between ticks backs off from ``MIN_INTERVAL_S`` to ``MAX_INTERVAL_S`` while nothing happens
  and snaps back on activity. No subject, no wakeups worth a name.
* **Does real work.** A ready regeneration batch goes to the ``regenerate`` callable (default: the
  Data Hub regenerator, audit-first). Its outcome is audited; the daemon's cooldown starts.
* **Supervised.** A tick that raises is logged and counted; the loop continues. ``start`` is
  idempotent per tenant and ``stop`` joins the thread.
"""
from __future__ import annotations

import asyncio
import atexit
import json
import logging
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

from core.background import learning_inbox
from core.background.learning_daemon import DaemonEvent, DataHubLearningDaemon

logger = logging.getLogger(__name__)

from core.deployment import audit_sink  # noqa: E402

audit_sink.register_events({
    # A persisted state the host would not load (other tenant / other version): recorded, and the
    # file is kept aside instead of being overwritten by the next save.
    "learning.state_refused": ("reason",),
    # Inbox lines tagged with another tenant (never written by a producer of this tenant).
    "learning.inbox_foreign_dropped": ("dropped_count",),
    # Daemon state of sources whose artifact no longer exists (deleted) was removed.
    "learning.sources_evicted": ("evicted_count",),
})

MIN_INTERVAL_S = 2.0
MAX_INTERVAL_S = 30.0
SOURCE_CHECK_S = 300.0
SAVE_EVERY_TICKS_WHEN_IDLE = 10
RETRY_FAILED_S = 3600.0          # a failed/partial regeneration is retried after an hour
RETRY_REFUSED_S = 24 * 3600.0    # a refused one (secret in the source, ...) after a day


def state_path(tenant_id: str) -> Path:
    return learning_inbox.daemon_dir(tenant_id) / "state.json"


def _atomic_write(path: Path, data: dict) -> None:
    import tempfile

    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".state.", suffix=".tmp", dir=str(path.parent))
    try:
        os.fchmod(fd, 0o600) if hasattr(os, "fchmod") else None
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, separators=(",", ":"))
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


class _Lease:
    """One host per tenant ACROSS processes: an exclusive, non-blocking lock on
    ``daemon/host.lock`` held for the host's lifetime. Both shipped hosts start a learning host;
    without this, two processes consumed the same inbox and regenerated twice (review DH-4)."""

    def __init__(self, tenant_id: str):
        self.path = learning_inbox.daemon_dir(tenant_id) / "host.lock"
        self._fh = None

    @property
    def held(self) -> bool:
        return self._fh is not None

    def try_acquire(self) -> bool:
        if self._fh is not None:
            return True
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fh = open(self.path, "a+")
        try:
            try:
                import fcntl  # noqa: PLC0415

                fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            except ImportError:  # Windows
                import msvcrt  # noqa: PLC0415

                fh.seek(0)
                msvcrt.locking(fh.fileno(), msvcrt.LK_NBLCK, 1)
        except OSError:
            fh.close()
            return False
        self._fh = fh
        if not self.still_valid():
            # The lock is on a file that is no longer ``host.lock`` (unlinked or replaced between
            # open and flock): a second process opening the new file would get its own "exclusive"
            # lock - two hosts again (review R2-DH-8).
            self.release()
            return False
        return True

    def still_valid(self) -> bool:
        """The locked file is still the one at ``path`` (same device + inode)."""
        if self._fh is None:
            return False
        try:
            a = os.fstat(self._fh.fileno())
            b = os.stat(self.path)
        except OSError:
            return False
        return (a.st_dev, a.st_ino) == (b.st_dev, b.st_ino)

    def release(self) -> None:
        if self._fh is not None:
            try:
                self._fh.close()   # closing drops flock / msvcrt lock
            finally:
                self._fh = None


class LearningHost:
    """One tenant's daemon, its persistence and its tick."""

    def __init__(self, tenant_id: str = "_default", *,
                 sources: Optional[Callable[[str], list]] = None,
                 regenerate: Optional[Callable[..., Any]] = None):
        self.tenant_id = tenant_id
        self.daemon = DataHubLearningDaemon(tenant_id)
        self.cursor: dict[str, Any] = {}
        self._sources = sources
        self._regenerate = regenerate
        self._last_source_check = 0.0
        self._dirty = False
        self._idle_ticks = 0
        self.ticks = 0
        self.errors = 0
        self.last_error = ""
        self.regenerations: list[dict[str, Any]] = []
        self.lease = _Lease(tenant_id)
        self._restored = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> bool:
        path = state_path(self.tenant_id)
        if not path.exists():
            return False
        try:
            raw = json.loads(path.read_text())
            refused_reason = None if isinstance(raw, dict) else "not_an_object"
        except (OSError, ValueError):
            raw, refused_reason = {}, "unreadable"
        if refused_reason is None and not self.daemon.restore(raw.get("daemon") or {}):
            d = raw.get("daemon") or {}
            refused_reason = ("tenant_mismatch" if d.get("tenant_id") not in (None, self.tenant_id)
                              else "version_mismatch")
        if refused_reason is not None:
            # Recorded and kept aside: the next save must not silently overwrite what was refused
            # (review 2026-10-12, DH-7, ADR-0562 "refused AND chained").
            try:
                audit_sink.emit("learning.state_refused", {"reason": refused_reason},
                                tenant_id=self.tenant_id)
            except audit_sink.AuditWriteFailed:
                logger.warning("learning.state_refused did not commit (%s)", refused_reason)
            try:
                path.rename(path.with_name(f"state.json.refused-{int(time.time())}"))
            except OSError:
                pass
            return False
        self.cursor = dict(raw.get("cursor") or {})
        self.regenerations = list(raw.get("regenerations") or [])[-50:]
        return True

    def save(self) -> None:
        _atomic_write(state_path(self.tenant_id), {
            "tenant_id": self.tenant_id, "saved_at": datetime.utcnow().isoformat(),
            "cursor": self.cursor, "daemon": self.daemon.snapshot(),
            "regenerations": self.regenerations[-50:],
        })
        self._dirty = False

    # ── one tick ─────────────────────────────────────────────────────────────

    async def tick(self, *, now: Optional[float] = None) -> int:
        """Do all due work once; returns how much happened (0 = idle)."""
        now = time.time() if now is None else now
        work = 0
        events, new_cursor = learning_inbox.read_from(self.tenant_id, self.cursor)
        for ev in events:
            await self.daemon.on_event(DaemonEvent(
                event_type=ev["event_type"], timestamp=ev.get("timestamp", ""),
                payload=ev.get("payload") or {}, tenant_id=self.tenant_id))
            work += 1
        foreign = int(new_cursor.pop("foreign", 0) or 0)
        if foreign:
            try:
                audit_sink.emit("learning.inbox_foreign_dropped", {"dropped_count": foreign},
                                tenant_id=self.tenant_id)
            except audit_sink.AuditWriteFailed:
                logger.warning("%d foreign inbox lines dropped; record did not commit", foreign)
        if new_cursor.get("files") and new_cursor.get("files") != (self.cursor or {}).get("files"):
            self.cursor = new_cursor
            self._dirty = True

        if now - self._last_source_check >= SOURCE_CHECK_S:
            self._last_source_check = now
            work += await self._check_sources()

        batch = await self.daemon.regeneration_scheduler.get_ready_batch()
        for i, skill_id in enumerate(batch):
            if self._stop_requested():
                # Shutting down: start no further regeneration; the rest stay queued for the next
                # holder instead of being dropped from the batch (review R2-DH-6).
                sched = self.daemon.regeneration_scheduler
                for rest in batch[i:]:
                    if rest not in sched.queued_skills:
                        sched.regeneration_queue.append(rest)
                        sched.queued_skills.add(rest)
                self._dirty = True
                break
            work += 1
            await self._regenerate_one(skill_id)

        if work:
            self._dirty = True
        if self._dirty:
            self.save()
        learning_inbox.prune(self.tenant_id, self.cursor)
        return work

    async def _check_sources(self) -> int:
        """A hash change of a watched source is a ``data_changed`` event for the skills built on it."""
        provider = self._sources or _default_sources
        try:
            refs = provider(self.tenant_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("source provider failed (%s)", type(exc).__name__)
            return 0
        work = self._evict_dead_sources({r.source_id for r in refs}, {r.skill_id for r in refs})
        for ref in refs:
            sid = ref.source_id
            if sid not in self.daemon.change_detector.last_manifest_hash and ref.sha256:
                # first sight: the hash the artifact was built from is the baseline
                await self.daemon.change_detector.check_for_changes(
                    {"manifest_id": sid, "sha256": ref.sha256})
            current = _default_hash(ref.path, self.tenant_id)
            if not current:
                continue
            before = self.daemon.change_detector.last_manifest_hash.get(sid)
            await self.daemon.on_event(DaemonEvent(
                event_type="data_changed", timestamp=datetime.utcnow().isoformat(),
                payload={"manifest": {"manifest_id": sid, "sha256": current},
                         "affected_skills": [ref.skill_id], f"quality_{ref.skill_id}": 0.0},
                tenant_id=self.tenant_id))
            if self.daemon.change_detector.last_manifest_hash.get(sid) != before:
                work += 1
        return work

    def _evict_dead_sources(self, live_sources: set, live_skills: set) -> int:
        """Forget the daemon state of Data Hub sources/skills whose artifact is gone: weights,
        hashes, edges, cooldowns and queue entries of deleted artifacts filled the source cap
        until no new skill could learn (review 2026-10-12, DH-9)."""
        d = self.daemon
        dead = [k for k in list(d.weight_learner.weights) if k.startswith("datahub:") and k not in live_sources]
        dead += [k for k in list(d.change_detector.last_manifest_hash)
                 if k.startswith("datahub:") and k not in live_sources and k not in dead]
        for k in dead:
            d.weight_learner.weights.pop(k, None)
            d.change_detector.last_manifest_hash.pop(k, None)
            d.causal_graph.edges.pop(k, None)
        sched = d.regeneration_scheduler
        dead_skills = [k for k in set(sched.last_regenerated) | sched.deferred | set(sched.regeneration_queue)
                       if k.startswith("assistant.datahub_") and k not in live_skills]
        for k in dead_skills:
            sched.last_regenerated.pop(k, None)
            sched.deferred.discard(k)
            sched.queued_skills.discard(k)
            if k in sched.regeneration_queue:
                sched.regeneration_queue.remove(k)
        n = len(dead) + len(dead_skills)
        if n:
            self._dirty = True
            try:
                audit_sink.emit("learning.sources_evicted", {"evicted_count": n}, tenant_id=self.tenant_id)
            except audit_sink.AuditWriteFailed:
                logger.warning("source eviction record did not commit")
        return n

    async def _regenerate_one(self, skill_id: str) -> None:
        fn = self._regenerate or _default_regenerate
        try:
            outcome = await asyncio.get_running_loop().run_in_executor(
                None, lambda: fn(self.tenant_id, skill_id, reason=self._reason_for(skill_id)))
        except Exception as exc:  # noqa: BLE001 - the host outlives a failed regeneration
            outcome = type("O", (), {"skill_id": skill_id, "outcome": "failed",
                                     "detail": type(exc).__name__, "generation": 0})()
        sched = self.daemon.regeneration_scheduler
        kind = getattr(outcome, "outcome", "failed")
        if kind == "completed":
            sched.mark_regenerated(skill_id)
        elif kind != "no_artifact":
            # Not done: try again later instead of counting a failure as a regeneration - the
            # change that asked for it is already consumed (review 2026-10-12, DH-5).
            sched.defer_retry(skill_id, RETRY_REFUSED_S if kind == "refused" else RETRY_FAILED_S)
        self.regenerations.append({
            "skill_id": skill_id, "outcome": getattr(outcome, "outcome", "failed"),
            "detail": str(getattr(outcome, "detail", ""))[:120],
            "at": datetime.utcnow().isoformat(), "generation": getattr(outcome, "generation", 0)})

    def _reason_for(self, skill_id: str) -> str:
        for rec in reversed(self.daemon.audit_trail):
            d = rec.get("decision") or {}
            if rec.get("event_type") == "regeneration_queued" and d.get("skill_id") == skill_id:
                return "negative_feedback_consensus" if d.get("reason") else "source_changed"
        return "manual"

    # ── loop ─────────────────────────────────────────────────────────────────

    def status(self) -> dict[str, Any]:
        d = self.daemon
        return {
            "tenant_id": self.tenant_id, "restored": self._restored, "ticks": self.ticks,
            "errors": self.errors, "last_error": self.last_error,
            "weights": dict(d.weight_learner.weights),
            "queue": list(d.regeneration_scheduler.regeneration_queue),
            "regenerations": self.regenerations[-10:], "cursor": dict(self.cursor),
            "standby": bool(getattr(self, "standby", False)), "lease": self.lease.held,
            "decisions": len(d.audit_trail),
        }

    def _stop_requested(self) -> bool:
        ev = getattr(self, "_stop_ev", None)
        return bool(ev is not None and ev.is_set())

    async def run(self, stop: threading.Event) -> None:
        interval = MIN_INTERVAL_S
        self._stop_ev = stop
        while not stop.is_set():
            if self.lease.held and not self.lease.still_valid():
                # Our lock file was replaced/removed: we no longer exclude anyone (R2-DH-8).
                self.lease.release()
                logger.warning("learning host lease file changed; re-acquiring")
            if not self.lease.try_acquire():
                # Another process hosts this tenant: stand by, try again later (takes over when
                # that process exits and the OS drops its lock).
                self.standby = True
                stop.wait(MAX_INTERVAL_S)
                continue
            if getattr(self, "standby", False):
                self.standby = False
                self._restored = self._load()   # pick up what the previous holder saved
            try:
                work = await self.tick()
            except Exception as exc:  # noqa: BLE001 - supervised: log, count, continue
                self.errors += 1
                self.last_error = type(exc).__name__
                logger.error("learning host tick failed: %s", exc, exc_info=True)
                work = 0
            self.ticks += 1
            interval = MIN_INTERVAL_S if work else min(MAX_INTERVAL_S, interval * 2)
            await asyncio.sleep(0)  # let waiting tasks run
            stop.wait(interval)
        # Persist on the way out - only if this host HOLDS the lease now. Acquiring it here just to
        # save let a host that never ran (or stood by) overwrite the newer state another process
        # had saved with its own stale snapshot from start-up (review R2-DH-2).
        if self.lease.held and self.lease.still_valid():
            try:
                self.save()
            finally:
                self.lease.release()
        else:
            self.lease.release()


def _default_sources(tenant_id: str) -> list:
    from core.skills.os_skills.datahub_unified.regenerate import sources

    return sources(tenant_id)


def _default_hash(path: str, tenant_id: str) -> str:
    from core.skills.os_skills.datahub_unified.regenerate import current_sha256

    return current_sha256(path, tenant_id) if path else ""


def _default_regenerate(tenant_id: str, skill_id: str, *, reason: str):
    from core.skills.os_skills.datahub_unified.regenerate import regenerate_skill

    return regenerate_skill(tenant_id, skill_id, reason=reason)


def read_state_summary(tenant_id: str) -> dict[str, Any]:
    """The daemon's persisted state as a content-free summary - readable from ANY process.

    The host runs in the console process; the bridge, the registry Skill and the dashboard route
    read what it last saved. ``running`` is only knowable in the process that hosts it
    (see :func:`status`)."""
    try:
        raw = json.loads(state_path(tenant_id).read_text())
    except (OSError, ValueError):
        return {"tenant_id": tenant_id, "state": "none", "saved_at": None, "weights": {},
                "queue": [], "regenerations": [], "decisions": 0, "pending_inbox_files": len(
                    learning_inbox.day_files(tenant_id))}
    daemon = raw.get("daemon") or {}
    decisions = daemon.get("decisions") or []
    return {
        "tenant_id": tenant_id, "state": "saved", "saved_at": raw.get("saved_at"),
        "weights": {str(k): round(float(v), 4) for k, v in (daemon.get("weights") or {}).items()},
        "queue": list(daemon.get("queue") or []),
        "deferred": list(daemon.get("deferred") or []),
        "regenerations": list(raw.get("regenerations") or [])[-10:],
        "decisions": len(decisions),
        "last_decision": (decisions[-1].get("event_type") if decisions else None),
        "sources_watched": len(daemon.get("manifest_hashes") or {}),
        "causal_edges": sum(len(v) for v in (daemon.get("edges") or {}).values()),
        "cursor": raw.get("cursor") or {},
        "pending_inbox_files": len(learning_inbox.day_files(tenant_id)),
    }


# ── process-level supervision ────────────────────────────────────────────────

_hosts: dict[str, tuple[LearningHost, threading.Event, threading.Thread]] = {}
_lock = threading.Lock()


def start(tenant_id: str = "_default") -> bool:
    """Start the host thread once per tenant per process. False when already running (or under pytest
    without ``CORVIN_LEARNING_HOST_IN_TESTS``: a test run must not leave a background writer behind)."""
    if os.environ.get("PYTEST_CURRENT_TEST") and not os.environ.get("CORVIN_LEARNING_HOST_IN_TESTS"):
        return False
    with _lock:
        if tenant_id in _hosts:
            return False
        host = LearningHost(tenant_id)
        stop = threading.Event()

        def _main() -> None:
            asyncio.run(host.run(stop))

        thread = threading.Thread(target=_main, name=f"learning-host-{tenant_id}", daemon=True)
        _hosts[tenant_id] = (host, stop, thread)
        thread.start()
        logger.info("learning host started for tenant %s (restored=%s)", tenant_id, host._restored)
        return True


#: Upper bound for waiting on an in-flight regeneration at stop/exit. The host starts no new
#: regeneration once stop is set, so this only covers the one already running.
STOP_WAIT_S = 120.0


def stop(tenant_id: str = "_default", *, timeout: Optional[float] = STOP_WAIT_S) -> bool:
    """Stop the host and WAIT for its in-flight tick (review R2-DH-6: a 5 s join returned while a
    regeneration was still writing, and interpreter exit then killed the daemon thread between the
    registry write and the artifact write). Returns False if it did not finish in ``timeout``."""
    with _lock:
        entry = _hosts.pop(tenant_id, None)
    if not entry:
        return True
    host, ev, thread = entry
    ev.set()
    thread.join(timeout)   # the host thread saves and releases its lease on the way out
    if thread.is_alive():
        logger.error("learning host %s did not stop within %ss (regeneration in flight)", tenant_id, timeout)
        return False
    return True


def _stop_all_at_exit() -> None:
    # atexit runs BEFORE daemon threads are torn down: stop every host that a lifespan did not.
    for tid in list(_hosts):
        stop(tid)


atexit.register(_stop_all_at_exit)


def status(tenant_id: str = "_default") -> Optional[dict[str, Any]]:
    entry = _hosts.get(tenant_id)
    if not entry:
        return None
    host, _ev, thread = entry
    return {**host.status(), "alive": thread.is_alive()}


__all__ = ["LearningHost", "start", "stop", "status", "state_path", "read_state_summary"]

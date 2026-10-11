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

MIN_INTERVAL_S = 2.0
MAX_INTERVAL_S = 30.0
SOURCE_CHECK_S = 300.0
SAVE_EVERY_TICKS_WHEN_IDLE = 10


def state_path(tenant_id: str) -> Path:
    return learning_inbox.daemon_dir(tenant_id) / "state.json"


def _atomic_write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    fd = os.open(str(tmp), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f, separators=(",", ":"))
    os.replace(tmp, path)


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
        self._restored = self._load()

    # ── persistence ──────────────────────────────────────────────────────────

    def _load(self) -> bool:
        try:
            raw = json.loads(state_path(self.tenant_id).read_text())
        except (OSError, ValueError):
            return False
        if not self.daemon.restore(raw.get("daemon") or {}):
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
        if new_cursor.get("file") and new_cursor != self.cursor:
            self.cursor = new_cursor
            self._dirty = True

        if now - self._last_source_check >= SOURCE_CHECK_S:
            self._last_source_check = now
            work += await self._check_sources()

        batch = await self.daemon.regeneration_scheduler.get_ready_batch()
        for skill_id in batch:
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
        work = 0
        for ref in refs:
            sid = ref.source_id
            if sid not in self.daemon.change_detector.last_manifest_hash and ref.sha256:
                # first sight: the hash the artifact was built from is the baseline
                await self.daemon.change_detector.check_for_changes(
                    {"manifest_id": sid, "sha256": ref.sha256})
            current = _default_hash(ref.path)
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

    async def _regenerate_one(self, skill_id: str) -> None:
        fn = self._regenerate or _default_regenerate
        try:
            outcome = await asyncio.get_running_loop().run_in_executor(
                None, lambda: fn(self.tenant_id, skill_id, reason=self._reason_for(skill_id)))
        except Exception as exc:  # noqa: BLE001 - the host outlives a failed regeneration
            outcome = type("O", (), {"skill_id": skill_id, "outcome": "failed",
                                     "detail": type(exc).__name__, "generation": 0})()
        self.daemon.regeneration_scheduler.mark_regenerated(skill_id)
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
            "decisions": len(d.audit_trail),
        }

    async def run(self, stop: threading.Event) -> None:
        interval = MIN_INTERVAL_S
        while not stop.is_set():
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


def _default_sources(tenant_id: str) -> list:
    from core.skills.os_skills.datahub_unified.regenerate import sources

    return sources(tenant_id)


def _default_hash(path: str) -> str:
    from core.skills.os_skills.datahub_unified.regenerate import current_sha256

    return current_sha256(path) if path else ""


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


def stop(tenant_id: str = "_default", *, timeout: float = 5.0) -> None:
    with _lock:
        entry = _hosts.pop(tenant_id, None)
    if entry:
        host, ev, thread = entry
        ev.set()
        thread.join(timeout)
        try:
            host.save()
        except OSError:
            pass


def status(tenant_id: str = "_default") -> Optional[dict[str, Any]]:
    entry = _hosts.get(tenant_id)
    if not entry:
        return None
    host, _ev, thread = entry
    return {**host.status(), "alive": thread.is_alive()}


__all__ = ["LearningHost", "start", "stop", "status", "state_path", "read_state_summary"]

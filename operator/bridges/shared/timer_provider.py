"""ADR-0159 M3 — TimerProvider: platform-independent scheduled-job abstraction.

Replaces direct ``systemctl --user enable --now`` calls with a provider that
either delegates to systemd (Linux + systemd present) or runs equivalent jobs
in-process via ``threading.Timer`` (macOS, Windows, Docker, no systemd).

Shipped implementations:
  SystemdTimerProvider  — Linux + systemd present; current behaviour, unchanged.
  ThreadTimerProvider   — any platform; Python ``threading.Timer``, in-process.

Factory:
  get_timer_provider()  — auto-selects based on platform + systemd availability.

ThreadTimerProvider notes:
  * Jobs are re-registered on bridge boot (they don't persist across restarts).
  * Jobs fire with a threading.Timer that rearms itself on completion (daily)
    or on a fixed interval.
  * A missed run (bridge was down at the scheduled time) is NOT back-filled.
    For audit-verify / session-TTL this is safe — idempotent on next boot.

No ``import anthropic`` in this module (CI AST lint enforces).
"""
from __future__ import annotations

import datetime
import logging
import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Dict, Optional

_log = logging.getLogger("corvin.timer")


# ── Protocol / base ──────────────────────────────────────────────────────────

class TimerProvider:
    """Abstract base for scheduled job backends.

    Concrete subclasses override ``schedule_daily`` and ``schedule_interval``.
    ``cancel`` uses the shared ``_handles`` registry by default.
    """

    def __init__(self) -> None:
        self._handles: Dict[str, object] = {}

    def schedule_daily(self, job_id: str, hour: int, minute: int,
                       fn: Callable[[], None]) -> None:
        raise NotImplementedError

    def schedule_interval(self, job_id: str, seconds: int,
                          fn: Callable[[], None]) -> None:
        raise NotImplementedError

    def cancel(self, job_id: str) -> None:
        handle = self._handles.pop(job_id, None)
        if handle is None:
            return
        if isinstance(handle, threading.Timer):
            handle.cancel()

    def cancel_all(self) -> None:
        for job_id in list(self._handles):
            self.cancel(job_id)


# ── SystemdTimerProvider ─────────────────────────────────────────────────────

class SystemdTimerProvider(TimerProvider):
    """Delegates to pre-installed systemd user-unit timers.

    bridge.sh install already writes the unit files and the enabled units
    survive reboots without re-registration.  schedule_daily / schedule_interval
    are no-ops here (units already enabled by bridge.sh up); cancel disables
    the systemd unit.
    """

    def schedule_daily(self, job_id: str, hour: int, minute: int,
                       fn: Callable[[], None]) -> None:
        # systemd units are registered by bridge.sh install; no runtime action.
        _log.debug("SystemdTimerProvider: %s managed by systemd unit", job_id)

    def schedule_interval(self, job_id: str, seconds: int,
                          fn: Callable[[], None]) -> None:
        _log.debug("SystemdTimerProvider: %s managed by systemd unit", job_id)

    def cancel(self, job_id: str) -> None:
        unit = f"corvin-{job_id}.timer"
        try:
            subprocess.run(
                ["systemctl", "--user", "disable", "--now", unit],
                capture_output=True,
                timeout=10,
            )
            _log.info("SystemdTimerProvider: disabled %s", unit)
        except Exception as e:
            _log.warning("SystemdTimerProvider: disable %s failed: %s", unit, e)


# ── ThreadTimerProvider ───────────────────────────────────────────────────────

def _seconds_until(hour: int, minute: int) -> float:
    """Seconds until the next wall-clock HH:MM (local time)."""
    now = datetime.datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += datetime.timedelta(days=1)
    return (target - now).total_seconds()


class ThreadTimerProvider(TimerProvider):
    """In-process timer backend for platforms without systemd.

    Each job is a threading.Timer that re-arms itself after firing.
    Daily jobs fire at the given HH:MM local time; interval jobs fire
    every ``seconds`` seconds.

    Cancel safety: ``_cancelled`` is checked at the top of every ``_fire()``
    callback before re-arming.  This closes a GIL-level race where cancel()
    pops the already-fired timer handle while a concurrent _fire() is between
    fn() and the re-arm dict-write, which would otherwise orphan the new timer.
    """

    def __init__(self) -> None:
        super().__init__()
        self._cancelled: set = set()

    def cancel(self, job_id: str) -> None:
        # Mark cancelled BEFORE popping so any in-flight _fire() sees the flag.
        self._cancelled.add(job_id)
        super().cancel(job_id)

    def cancel_all(self) -> None:
        self._cancelled.update(list(self._handles))
        super().cancel_all()

    def schedule_daily(self, job_id: str, hour: int, minute: int,
                       fn: Callable[[], None]) -> None:
        self.cancel(job_id)
        self._cancelled.discard(job_id)  # re-arm allowed for new registration
        delay = _seconds_until(hour, minute)
        _log.info(
            "ThreadTimerProvider: %s scheduled daily at %02d:%02d "
            "(first fire in %.0fs)",
            job_id, hour, minute, delay,
        )

        def _fire() -> None:
            if job_id in self._cancelled:
                return
            try:
                fn()
            except Exception as e:
                _log.error("ThreadTimerProvider: job %s raised: %s", job_id, e)
            if job_id in self._cancelled:
                return
            # Re-arm for the next day
            t = threading.Timer(_seconds_until(hour, minute), _fire)
            t.daemon = True
            t.name = f"corvin-timer-{job_id}"
            self._handles[job_id] = t
            t.start()

        t = threading.Timer(delay, _fire)
        t.daemon = True
        t.name = f"corvin-timer-{job_id}"
        self._handles[job_id] = t
        t.start()

    def schedule_interval(self, job_id: str, seconds: int,
                          fn: Callable[[], None]) -> None:
        if seconds <= 0:
            raise ValueError(
                f"schedule_interval: seconds must be positive, got {seconds!r}"
            )
        self.cancel(job_id)
        self._cancelled.discard(job_id)  # re-arm allowed for new registration
        _log.info(
            "ThreadTimerProvider: %s scheduled every %ds", job_id, seconds
        )

        def _fire() -> None:
            if job_id in self._cancelled:
                return
            try:
                fn()
            except Exception as e:
                _log.error("ThreadTimerProvider: job %s raised: %s", job_id, e)
            if job_id in self._cancelled:
                return
            # Re-arm
            t = threading.Timer(seconds, _fire)
            t.daemon = True
            t.name = f"corvin-timer-{job_id}"
            self._handles[job_id] = t
            t.start()

        t = threading.Timer(seconds, _fire)
        t.daemon = True
        t.name = f"corvin-timer-{job_id}"
        self._handles[job_id] = t
        t.start()


# ── Factory ───────────────────────────────────────────────────────────────────

def _have_systemd() -> bool:
    if sys.platform != "linux":
        return False
    if shutil.which("systemctl") is None:
        return False
    try:
        r = subprocess.run(
            ["systemctl", "--user", "is-system-running"],
            capture_output=True,
            timeout=5,
        )
        # is-system-running returns 0 (running) or 1 (degraded) — both mean systemd is alive
        return r.returncode in (0, 1)
    except Exception:
        return False


_PROVIDER_INSTANCE: Optional[TimerProvider] = None


def get_timer_provider(*, force_thread: bool = False) -> TimerProvider:
    """Return (and cache) the best available TimerProvider.

    pass ``force_thread=True`` to always get ThreadTimerProvider (e.g. in tests).
    """
    global _PROVIDER_INSTANCE
    if _PROVIDER_INSTANCE is not None:
        return _PROVIDER_INSTANCE

    env_override = os.environ.get("CORVIN_TIMER_PROVIDER", "").strip().lower()
    if env_override == "thread" or force_thread:
        _PROVIDER_INSTANCE = ThreadTimerProvider()
        _log.info("TimerProvider: ThreadTimerProvider (forced)")
        return _PROVIDER_INSTANCE

    if env_override == "systemd":
        _PROVIDER_INSTANCE = SystemdTimerProvider()
        _log.info("TimerProvider: SystemdTimerProvider (forced via env)")
        return _PROVIDER_INSTANCE

    if _have_systemd():
        _PROVIDER_INSTANCE = SystemdTimerProvider()
        _log.debug("TimerProvider: SystemdTimerProvider")
        return _PROVIDER_INSTANCE

    _PROVIDER_INSTANCE = ThreadTimerProvider()
    _log.info(
        "TimerProvider: ThreadTimerProvider (systemd unavailable on %s)",
        sys.platform,
    )
    return _PROVIDER_INSTANCE

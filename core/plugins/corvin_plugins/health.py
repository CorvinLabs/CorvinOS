"""Plugin health collection + Prometheus projection (ADR-0231 Stage 2).

Two pieces:

* :class:`HealthCollector` — polls ``registry.health_check_all()`` on an interval
  and keeps the latest snapshot. Started only when the
  ``plugin_health_monitoring`` flag is on, so a fresh install polls nothing.
* :func:`render_prometheus` — projects the snapshot into the 0.0.4 text format,
  following ``gateway/audit_metrics.py``'s conventions: no SDK dependency, a
  bounded label set, no PII, read-only.

Design decisions worth stating:

* **Alerting emits an audit event, not a notification.** ADR-0231 asks for
  "threshold alerting"; routing it to email/Slack would be a second delivery path
  next to ADR-0033's notification provider. The collector records
  ``plugin.health_alert`` in the chain — the notification backend can fan that out
  if the operator installed one.
* **A poll never raises into the loop.** One sick plugin must not stop the
  collector, and the collector must not stop the platform.
* **plugin_id as a label is safe but bounded.** It is operator-supplied and already
  charset-validated, so it carries no PII, but an unbounded set would blow up
  cardinality: past ``MAX_LABELLED_PLUGINS`` the id collapses to ``other``.
"""
from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Optional

from . import circuit_breaker as _breakers
from .registry import get_registry

log = logging.getLogger("corvin.plugins.health")

#: Default poll interval. ADR-0231 leaves 5-30 s open; 30 s is the lazy end, which
#: is the right default for a mechanism whose failure mode is "extra load".
DEFAULT_INTERVAL_S = 30.0
#: Consecutive unhealthy polls before an alert event is written.
DEFAULT_ALERT_AFTER = 3
#: Label-cardinality cap (see module docstring).
MAX_LABELLED_PLUGINS = 64


@dataclass
class PluginHealthSample:
    plugin_id: str
    ok: bool
    message: str = ""
    duration_ms: float = 0.0
    consecutive_failures: int = 0
    breaker_state: str = "closed"
    details: dict = field(default_factory=dict)


@dataclass
class HealthSnapshot:
    taken_at: float
    samples: Dict[str, PluginHealthSample] = field(default_factory=dict)

    def unhealthy(self) -> list[str]:
        return sorted(pid for pid, s in self.samples.items() if not s.ok)

    def to_dict(self) -> dict:
        return {
            "taken_at": self.taken_at,
            "plugins": {
                pid: {
                    "ok": s.ok,
                    "message": s.message,
                    "duration_ms": s.duration_ms,
                    "consecutive_failures": s.consecutive_failures,
                    "breaker_state": s.breaker_state,
                }
                for pid, s in sorted(self.samples.items())
            },
        }


class HealthCollector:
    """Polls plugin health on an interval.  Pull-only when not started."""

    def __init__(
        self,
        *,
        interval_s: float = DEFAULT_INTERVAL_S,
        alert_after: int = DEFAULT_ALERT_AFTER,
        audit_emit: Optional[Callable[[str, dict], None]] = None,
        healer: Optional[Any] = None,
    ):
        if interval_s <= 0:
            raise ValueError("interval_s must be > 0")
        self.interval_s = interval_s
        self.alert_after = alert_after
        self._audit_emit = audit_emit
        #: Optional HealingOrchestrator (ADR-0231 Stage 3). The collector is the
        #: only poller in the system, so healing is driven from here rather than
        #: from a second timer that would double the health-check load.
        self._healer = healer
        self._snapshot = HealthSnapshot(taken_at=0.0)
        self._failure_runs: Dict[str, int] = {}
        self._alerted: set[str] = set()
        self._task: Optional[asyncio.Task] = None
        # Created in start(): an Event bound at __init__ would attach to whatever
        # loop happened to be current, which is not necessarily the one that polls.
        self._stop: Optional[asyncio.Event] = None

    # ── polling ──────────────────────────────────────────────────────────────

    def poll_once(self) -> HealthSnapshot:
        """Run one collection pass.  Never raises."""
        samples: Dict[str, PluginHealthSample] = {}
        try:
            started = time.monotonic()
            statuses = get_registry().health_check_all()
            elapsed_ms = (time.monotonic() - started) * 1000.0
        except Exception as exc:  # noqa: BLE001
            log.error("health poll failed (%s)", type(exc).__name__)
            return self._snapshot

        per_plugin_ms = elapsed_ms / max(1, len(statuses))
        for pid, status in statuses.items():
            breaker = (status.details or {}).get("breaker") or {}
            run = self._failure_runs.get(pid, 0)
            run = 0 if status.ok else run + 1
            self._failure_runs[pid] = run
            samples[pid] = PluginHealthSample(
                plugin_id=pid,
                ok=status.ok,
                message=status.message,
                duration_ms=round(per_plugin_ms, 3),
                consecutive_failures=run,
                breaker_state=str(breaker.get("state", "closed")),
                details=status.details or {},
            )
            self._maybe_alert(pid, run, samples[pid])
            self._maybe_heal(pid, run, samples[pid])

        # A plugin that vanished stops counting toward an alert.
        for gone in set(self._failure_runs) - set(statuses):
            self._failure_runs.pop(gone, None)
            self._alerted.discard(gone)

        self._snapshot = HealthSnapshot(taken_at=time.time(), samples=samples)
        return self._snapshot

    def _maybe_alert(self, plugin_id: str, run: int, sample: PluginHealthSample) -> None:
        """Emit one alert per unhealthy streak, and one recovery event after it."""
        if run >= self.alert_after and plugin_id not in self._alerted:
            self._alerted.add(plugin_id)
            self._emit_audit(
                "plugin.health_alert",
                {
                    "plugin_id": plugin_id,
                    "consecutive_failures": run,
                    "breaker_state": sample.breaker_state,
                    # message is already class-name-only from health_check_all
                    "reason": sample.message,
                },
            )
            log.error(
                "plugin %r unhealthy for %d consecutive checks (breaker=%s)",
                plugin_id, run, sample.breaker_state,
            )
        elif run == 0 and plugin_id in self._alerted:
            self._alerted.discard(plugin_id)
            self._emit_audit("plugin.health_recovered", {"plugin_id": plugin_id})
            log.info("plugin %r recovered", plugin_id)

    def _maybe_heal(self, plugin_id: str, run: int, sample: PluginHealthSample) -> None:
        """Hand an unhealthy plugin to the orchestrator, if one is installed.

        The orchestrator decides everything (policy, budget, whether healing is
        enabled at all); the collector only reports. Never raises into the poll.
        """
        if self._healer is None or run == 0:
            return
        try:
            plugin_type = ""
            try:
                from .registry import get_registry

                plugin_type = getattr(get_registry().get(plugin_id), "plugin_type", "")
            except Exception:  # noqa: BLE001 - a vanished plugin needs no healing
                return
            self._healer.consider(
                plugin_id,
                plugin_type=plugin_type,
                consecutive_failures=run,
                error_code=sample.message,
            )
        except Exception as exc:  # noqa: BLE001
            log.error("healing consideration failed (%s)", type(exc).__name__)

    def _emit_audit(self, event_type: str, details: dict) -> None:
        if self._audit_emit is None:
            return
        try:
            self._audit_emit(event_type, details)
        except Exception as exc:  # noqa: BLE001
            log.error("health audit emit failed (%s)", type(exc).__name__)

    # ── lifecycle ────────────────────────────────────────────────────────────

    async def _loop(self) -> None:
        assert self._stop is not None
        while not self._stop.is_set():
            self.poll_once()
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=self.interval_s)
            except asyncio.TimeoutError:
                continue

    def start(self) -> asyncio.Task:
        """Start polling.  Idempotent; returns the running task."""
        if self._task is not None and not self._task.done():
            return self._task
        self._stop = asyncio.Event()
        self._task = asyncio.get_running_loop().create_task(self._loop())
        log.info("plugin health collector started (interval=%.1fs)", self.interval_s)
        return self._task

    async def stop(self) -> None:
        if self._stop is not None:
            self._stop.set()
        task, self._task = self._task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    def snapshot(self) -> HealthSnapshot:
        return self._snapshot


# ── Prometheus projection ─────────────────────────────────────────────────────

_FAMILIES = (
    ("corvin_plugin_health_ok", "gauge", "1 when the plugin's last health_check passed"),
    ("corvin_plugin_health_consecutive_failures", "gauge",
     "Consecutive failed health checks for this plugin"),
    ("corvin_plugin_health_check_duration_ms", "gauge",
     "Average duration of the last health collection pass, per plugin"),
    ("corvin_plugin_breaker_open", "gauge",
     "1 when the plugin's circuit breaker is open or half-open"),
    ("corvin_plugin_breaker_failures_total", "counter",
     "Total circuit-breaker failures recorded for this plugin"),
    ("corvin_plugin_breaker_refused_total", "counter",
     "Calls refused by an open circuit breaker for this plugin"),
)


def _escape(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")


def _label_for(plugin_id: str, allowed: set[str]) -> str:
    return plugin_id if plugin_id in allowed else "other"


def render_prometheus(snapshot: Optional[HealthSnapshot] = None) -> str:
    """Render plugin health + breaker state as Prometheus 0.0.4 text.

    Breaker numbers come straight from the breaker registry, so they are present
    even when polling is off — the breakers run regardless of the flag.
    """
    snap = snapshot or HealthSnapshot(taken_at=0.0)
    breakers = _breakers.snapshot()

    ids = sorted(set(snap.samples) | set(breakers))
    allowed = set(ids[:MAX_LABELLED_PLUGINS])

    lines: list[str] = []
    for name, mtype, helptext in _FAMILIES:
        lines.append(f"# HELP {name} {helptext}")
        lines.append(f"# TYPE {name} {mtype}")
        if not ids:
            # Zero-sample so dashboards show "0", not "no data".
            lines.append(f'{name}{{plugin_id="none"}} 0')
            continue
        for pid in ids:
            label = f'{{plugin_id="{_escape(_label_for(pid, allowed))}"}}'
            sample = snap.samples.get(pid)
            bstats = breakers.get(pid, {})
            if name == "corvin_plugin_health_ok":
                value = 1 if (sample and sample.ok) else 0
            elif name == "corvin_plugin_health_consecutive_failures":
                value = sample.consecutive_failures if sample else 0
            elif name == "corvin_plugin_health_check_duration_ms":
                value = round(sample.duration_ms, 3) if sample else 0
            elif name == "corvin_plugin_breaker_open":
                value = 0 if bstats.get("state", "closed") == "closed" else 1
            elif name == "corvin_plugin_breaker_failures_total":
                value = int(bstats.get("total_failures", 0))
            else:
                value = int(bstats.get("total_refused", 0))
            lines.append(f"{name}{label} {value}")
    return "\n".join(lines) + "\n"


__all__ = [
    "DEFAULT_ALERT_AFTER",
    "DEFAULT_INTERVAL_S",
    "HealthCollector",
    "HealthSnapshot",
    "MAX_LABELLED_PLUGINS",
    "PluginHealthSample",
    "render_prometheus",
]

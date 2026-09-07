#!/usr/bin/env python3
"""
LIVE EXPERIMENT COLLECTOR — Continuous Measurement System

Runs in background (systemd or supervisor) and appends ONE measurement per
minute to::

    <CORVIN_HOME>/tenants/<tenant>/experiments/live_measurements/measurements_YYYYMMDD.jsonl

Every value in a measurement is READ from a real source:

  * ``learning``      — the ADR-0314 ``EventStore`` (OUTCOME / FEEDBACK /
                        SKILL_EXECUTED / CONFIG_UPDATED counts, success rate of
                        the recent outcomes, the derived outcome loss)
  * ``system``        — this process (``resource.getrusage``) and the host
                        (``os.getloadavg``), plus the size of the core audit
                        chain file
  * ``user_actions``  — event counts in the last hour, again from the store
  * ``component_health`` — which learning event types have been seen at all

Until 2026-09-07 every one of these was ``random.gauss(...)`` — synthetic
numbers written to disk labelled as measurements, under ``~/.corvin`` (F-L5).
A collector that cannot read a real source records ``None`` for that field and
says so in ``sources``; it never invents a value.

**Provenance (round-4 review, F4).** Every record now carries ``schema`` and
``provenance``, so a measured record is distinguishable from a fabricated one
by inspection — the pre-fix files carry neither field and nothing else told
them apart, even though the documented purpose of this data is "export for
papers". Readers MUST filter on ``schema == MEASUREMENT_SCHEMA``;
:mod:`core.learning.live_collection_dashboard` does.

**Entry point.** Run it as ``corvin-live-collector start`` (a
``[project.scripts]`` console script) or ``python -m
core.learning.live_experiment_collector start``. It is NOT an executable
script: the file has no +x bit and its shebang is the system interpreter,
which has no ``core`` package on its path — a systemd unit that exec'd the
file directly failed with 203/EXEC, and with PYTHONPATH patched in it then
failed with ``ModuleNotFoundError: core``.
"""

from __future__ import annotations

import json
import math
import os
import resource
import sys
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

from core.paths.tenant import corvin_home, tenant_home

#: Window over which "recent outcomes" are judged (matches the console status endpoint).
RECENT_OUTCOME_WINDOW = 50

#: Identifies a record as a MEASURED sample of this schema. Records without it
#: predate 2026-09-07 and were generated with ``random.gauss`` — fabricated
#: data that must be quarantined, never aggregated or published.
MEASUREMENT_SCHEMA = "corvin.live_measurement/1"


class LiveExperimentCollector:
    """
    Continuously collects metrics from the running CorvinOS system.
    Persists to disk every minute, archives daily.
    """

    def __init__(self, tenant_id: str = "_default", interval_s: int = 60):
        self.tenant_id = tenant_id
        self.interval_s = int(interval_s)
        self.base_dir = tenant_home(tenant_id) / "experiments" / "live_measurements"
        self.base_dir.mkdir(parents=True, exist_ok=True)

        self.current_date = datetime.now().date()
        self.measurements_buffer: List[Dict[str, Any]] = []
        self.running = False
        self._thread: Optional[threading.Thread] = None

    # ── storage ────────────────────────────────────────────────────────────

    def get_today_file(self) -> Path:
        """Get the file path for today's measurements."""
        date_str = self.current_date.strftime("%Y%m%d")
        return self.base_dir / f"measurements_{date_str}.jsonl"

    def rotate_if_needed(self) -> None:
        """Archive yesterday's file if we've crossed into a new day."""
        today = datetime.now().date()
        if today != self.current_date:
            yesterday_date = self.current_date.strftime("%Y%m%d")
            yesterday_file = self.base_dir / f"measurements_{yesterday_date}.jsonl"
            if yesterday_file.exists():
                archive_dir = self.base_dir / "archive"
                archive_dir.mkdir(exist_ok=True)
                yesterday_file.replace(archive_dir / yesterday_file.name)
            self.current_date = today

    # ── real sources ───────────────────────────────────────────────────────

    def _event_store(self):
        from core.learning.event_store import EventStore  # noqa: PLC0415

        return EventStore(tenant_home(self.tenant_id), tenant_id=self.tenant_id)

    def collect_learning_metrics(self) -> Dict[str, Any]:
        """Learning-loop metrics from the ADR-0314 event store (real events only)."""
        from core.learning.learning_events import EventType  # noqa: PLC0415

        store = self._event_store()
        counts = {et.value: store.count_events(self.tenant_id, et) for et in EventType}
        since = (datetime.utcnow() - timedelta(days=7)).strftime("%Y-%m-%d")
        # ``limit`` windows from the NEWEST end (event_store contract), so this
        # is literally "the last RECENT_OUTCOME_WINDOW outcomes" — it used to ask
        # for the oldest 100 000 and slice the tail of that (round-4 review, F3).
        recent = store.query_events(
            self.tenant_id, event_type=EventType.OUTCOME, since=since, limit=RECENT_OUTCOME_WINDOW
        )
        successes = sum(1 for e in recent if (e.signal or {}).get("success") is True)
        total = len(recent)
        success_rate = (successes / total) if total else None
        return {
            "event_counts": counts,
            "recent_outcomes": {"window": RECENT_OUTCOME_WINDOW, "total": total, "successes": successes},
            "success_rate": success_rate,
            # the delegation router's real loss: share of recent tasks that did not succeed
            "outcome_loss": (1.0 - success_rate) if success_rate is not None else None,
        }

    def collect_system_metrics(self) -> Dict[str, Any]:
        """Process + host resource metrics (measured, not simulated)."""
        usage = resource.getrusage(resource.RUSAGE_SELF)
        try:
            load_1m, load_5m, _ = os.getloadavg()
        except OSError:
            load_1m = load_5m = None
        chain = Path(os.environ.get("VOICE_AUDIT_PATH") or (corvin_home() / "audit.jsonl"))
        try:
            chain_bytes: Optional[int] = chain.stat().st_size
        except OSError:
            chain_bytes = None
        return {
            "process_max_rss_mb": float(usage.ru_maxrss) / 1024.0,
            "process_cpu_user_s": float(usage.ru_utime),
            "process_cpu_system_s": float(usage.ru_stime),
            "host_load_1m": load_1m,
            "host_load_5m": load_5m,
            "audit_chain_bytes": chain_bytes,
        }

    def collect_user_actions(self) -> Dict[str, Any]:
        """Learning events recorded in the last hour, per type (from the store)."""
        from core.learning.learning_events import EventType  # noqa: PLC0415

        store = self._event_store()
        cutoff = (datetime.utcnow() - timedelta(hours=1)).isoformat()
        today = datetime.utcnow().strftime("%Y-%m-%d")
        since = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")
        counts: Dict[str, int] = {}
        for et in EventType:
            events = store.query_events(self.tenant_id, event_type=et, since=since, until=today, limit=100000)
            counts[et.value] = sum(1 for e in events if e.timestamp >= cutoff)
        return {
            "outcomes_last_hour": counts.get("outcome", 0),
            "feedback_last_hour": counts.get("feedback", 0),
            "skill_executions_last_hour": counts.get("skill_executed", 0),
            "config_updates_last_hour": counts.get("config_updated", 0),
            "events_last_hour": counts,
        }

    def collect_component_health(self, learning: Dict[str, Any]) -> Dict[str, Any]:
        """Which learning signals are actually flowing (derived from real counts)."""
        counts = learning.get("event_counts", {})
        return {
            name: {"active": counts.get(name, 0) > 0, "events": counts.get(name, 0)}
            for name in ("outcome", "feedback", "skill_executed", "config_updated", "preference", "confidence")
        }

    def collect_all_metrics(self) -> Dict[str, Any]:
        """Collect all available metrics; a source that cannot be read records None."""
        timestamp = datetime.now().isoformat()
        sources: Dict[str, str] = {}
        learning: Dict[str, Any] = {}
        try:
            learning = self.collect_learning_metrics()
            sources["learning"] = "event_store"
        except Exception as exc:  # noqa: BLE001 — record the gap, never a made-up number
            sources["learning"] = f"unavailable: {type(exc).__name__}"
        try:
            system = self.collect_system_metrics()
            sources["system"] = "rusage+loadavg"
        except Exception as exc:  # noqa: BLE001
            system = {}
            sources["system"] = f"unavailable: {type(exc).__name__}"
        try:
            user_actions = self.collect_user_actions()
            sources["user_actions"] = "event_store"
        except Exception as exc:  # noqa: BLE001
            user_actions = {}
            sources["user_actions"] = f"unavailable: {type(exc).__name__}"

        return {
            "schema": MEASUREMENT_SCHEMA,
            "provenance": {
                "generator": "core.learning.live_experiment_collector",
                "measured": True,
                "host_pid": os.getpid(),
            },
            "timestamp": timestamp,
            "unix_time": int(time.time()),
            "tenant_id": self.tenant_id,
            "sources": sources,
            "learning": learning,
            "system": system,
            "user_actions": user_actions,
            "component_health": self.collect_component_health(learning),
        }

    def save_measurement(self, measurement: Dict[str, Any]) -> None:
        """Append measurement to today's file."""
        self.rotate_if_needed()
        with open(self.get_today_file(), "a", encoding="utf-8") as f:
            f.write(json.dumps(measurement) + "\n")
        self.measurements_buffer.append(measurement)
        if len(self.measurements_buffer) > 1440:  # Keep ~24h in memory
            self.measurements_buffer = self.measurements_buffer[-1440:]

    # ── aggregation ────────────────────────────────────────────────────────

    @staticmethod
    def _series(measurements: List[Dict[str, Any]], section: str, key: str) -> List[float]:
        values = []
        for m in measurements:
            v = (m.get(section) or {}).get(key)
            if isinstance(v, (int, float)) and math.isfinite(v):
                values.append(float(v))
        return values

    @staticmethod
    def _stats(values: List[float]) -> Optional[Dict[str, float]]:
        if not values:
            return None
        mean = sum(values) / len(values)
        return {
            "mean": mean,
            "min": min(values),
            "max": max(values),
            "std": math.sqrt(sum((x - mean) ** 2 for x in values) / len(values)),
            "n": len(values),
        }

    def generate_hourly_summary(self) -> Optional[Dict[str, Any]]:
        """Hourly aggregation (mean/min/max) of the measured series."""
        one_hour_ago = time.time() - 3600
        recent = [m for m in self.measurements_buffer if m.get("unix_time", 0) > one_hour_ago]
        if not recent:
            return None
        return {
            "timestamp": datetime.now().isoformat(),
            "period": "1h",
            "num_samples": len(recent),
            "outcome_loss": self._stats(self._series(recent, "learning", "outcome_loss")),
            "success_rate": self._stats(self._series(recent, "learning", "success_rate")),
            "host_load_1m": self._stats(self._series(recent, "system", "host_load_1m")),
        }

    def save_summary(self, summary: Dict[str, Any]) -> None:
        summary_dir = self.base_dir / "summaries"
        summary_dir.mkdir(exist_ok=True)
        with open(summary_dir / "hourly_summaries.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(summary) + "\n")

    def get_statistics(self, days: int = 7) -> Optional[Dict[str, Any]]:
        """Rolling statistics over the last N days of persisted measurements."""
        all_measurements: List[Dict[str, Any]] = []
        for date_offset in range(days):
            date = (datetime.now() - timedelta(days=date_offset)).date()
            for candidate in (
                self.base_dir / f"measurements_{date.strftime('%Y%m%d')}.jsonl",
                self.base_dir / "archive" / f"measurements_{date.strftime('%Y%m%d')}.jsonl",
            ):
                if candidate.exists():
                    with open(candidate, "r", encoding="utf-8") as f:
                        for line in f:
                            try:
                                all_measurements.append(json.loads(line))
                            except json.JSONDecodeError:
                                pass
        if not all_measurements:
            return None
        all_measurements.sort(key=lambda m: m.get("unix_time", 0))
        losses = self._series(all_measurements, "learning", "outcome_loss")
        trend = None
        if len(losses) >= 20:
            head = sum(losses[:10]) / 10
            tail = sum(losses[-10:]) / 10
            trend = "improving" if tail < head else ("degrading" if tail > head else "flat")
        return {
            "period_days": days,
            "num_measurements": len(all_measurements),
            "outcome_loss": self._stats(losses),
            "success_rate": self._stats(self._series(all_measurements, "learning", "success_rate")),
            "host_load_1m": self._stats(self._series(all_measurements, "system", "host_load_1m")),
            "trend": trend,
        }

    # ── loop ───────────────────────────────────────────────────────────────

    def collection_loop(self) -> None:
        """Main collection loop — one measurement per ``interval_s``."""
        iteration = 0
        while self.running:
            iteration += 1
            measurement = self.collect_all_metrics()
            self.save_measurement(measurement)
            if iteration % 60 == 0:
                summary = self.generate_hourly_summary()
                if summary:
                    self.save_summary(summary)
            time.sleep(self.interval_s)

    def start(self) -> None:
        """Start the collection loop in a background thread."""
        if self.running:
            return
        self.running = True
        self._thread = threading.Thread(target=self.collection_loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self.running = False


# ============================================================================
# ENTRY POINT
# ============================================================================

def run_collector_daemon(tenant_id: str = "_default", interval_s: int = 60) -> None:
    """Run the collection loop until interrupted (systemd ``Type=simple``)."""
    collector = LiveExperimentCollector(tenant_id, interval_s=interval_s)
    collector.start()
    try:
        while True:
            time.sleep(60)
    except KeyboardInterrupt:
        collector.stop()


def main(argv: Optional[List[str]] = None) -> int:
    """Console entry point (``corvin-live-collector``).

    ``start``  — run the collection loop forever (the systemd unit's command).
    ``sample`` — take exactly ONE measurement, print it, exit. Use this to check
                 the unit's environment before enabling it: it exercises the
                 same code path and writes nothing.
    """
    import argparse  # noqa: PLC0415

    parser = argparse.ArgumentParser(prog="corvin-live-collector")
    parser.add_argument("command", choices=("start", "sample"), nargs="?", default="sample")
    parser.add_argument("--tenant", default=os.environ.get("CORVIN_TENANT_ID", "_default"))
    parser.add_argument("--interval", type=int, default=60, help="seconds between measurements")
    args = parser.parse_args(argv)

    if args.command == "start":
        run_collector_daemon(args.tenant, interval_s=args.interval)
        return 0

    collector = LiveExperimentCollector(args.tenant, interval_s=args.interval)
    print(json.dumps(collector.collect_all_metrics(), indent=2))
    print(f"\nData directory: {collector.base_dir}", file=sys.stderr)
    return 0


if __name__ == "__main__":  # `python -m core.learning.live_experiment_collector`
    raise SystemExit(main())

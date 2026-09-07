#!/usr/bin/env python3
"""
LIVE COLLECTION DASHBOARD — Real-time Monitoring UI

Reads the measurement files written by
:class:`core.learning.live_experiment_collector.LiveExperimentCollector` and
renders them. Shows: recent outcome-loss trend, success rate, host load,
component health, data-collection status.

**Schema (round-4 review, F5).** This reader used to index
``m["learning"]["loss_total"]``, ``m["learning"]["accuracy_routing"]`` and
``m["system"]["latency_p99_ms"]`` — keys of the PRE-2026-09-07 collector, which
fabricated every value with ``random.gauss``. When the collector was rewritten
to record only measured values the reader was not, so it raised
``KeyError: 'loss_total'`` on every honest record and worked only on the
synthetic ones. It now reads the real schema and treats a missing value as
missing (``None``), never as zero:

    learning: {event_counts, recent_outcomes, success_rate, outcome_loss}
    system:   {process_max_rss_mb, process_cpu_user_s, process_cpu_system_s,
               host_load_1m, host_load_5m, audit_chain_bytes}
    component_health: {<signal>: {active, events}}
    sources:  per-section provenance ("event_store" / "unavailable: <Error>")

Records written by the pre-fix collector carry no ``schema`` field and are
IGNORED with a warning (:data:`MEASUREMENT_SCHEMA`) — they are fabricated data
and must never reach an aggregate that is described as a measurement.
"""

import json
import logging
import math
from datetime import datetime, timedelta
from pathlib import Path
from core.paths.tenant import corvin_home
from core.learning.live_experiment_collector import MEASUREMENT_SCHEMA
from typing import List, Dict, Any

logger = logging.getLogger(__name__)


class LiveCollectionDashboard:
    """
    Reads live measurement files and generates human-readable reports.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.live_measurements_dir = (
            corvin_home() / "tenants" / tenant_id / "experiments" / "live_measurements"
        )
        self.live_events_dir = (
            corvin_home() / "tenants" / tenant_id / "experiments" / "live_events"
        )

    def load_recent_measurements(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Load measurements from the last N hours."""

        cutoff = datetime.now() - timedelta(hours=hours)
        measurements = []

        # Find today's file
        today_file = self.live_measurements_dir / f"measurements_{datetime.now().strftime('%Y%m%d')}.jsonl"

        legacy = 0
        if today_file.exists():
            with open(today_file, "r") as f:
                for line in f:
                    try:
                        m = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    # Provenance gate (round-4 review, F4/F5): a record without
                    # the schema marker was written by the pre-2026-09-07
                    # collector, whose values came from ``random.gauss``. It is
                    # fabricated data; aggregating it would launder it into a
                    # "measurement".
                    if m.get("schema") != MEASUREMENT_SCHEMA:
                        legacy += 1
                        continue
                    try:
                        if datetime.fromisoformat(m["timestamp"]) > cutoff:
                            measurements.append(m)
                    except (KeyError, TypeError, ValueError):
                        continue

        if legacy:
            logger.warning(
                "%s: ignored %d record(s) without schema %r — pre-2026-09-07 "
                "SYNTHETIC data, quarantine the file rather than publishing it",
                today_file, legacy, MEASUREMENT_SCHEMA,
            )
        return measurements

    def load_recent_events(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Load events from the last N hours."""

        cutoff = datetime.now().timestamp() - (hours * 3600)
        events = []

        event_file = self.live_events_dir / "events.jsonl"
        if event_file.exists():
            with open(event_file, "r") as f:
                for line in f:
                    try:
                        e = json.loads(line)
                        if e["unix_time"] > cutoff:
                            events.append(e)
                    except json.JSONDecodeError:
                        pass

        return events

    @staticmethod
    def _series(measurements: List[Dict[str, Any]], section: str, key: str) -> List[float]:
        """Finite numeric values of ``section.key``; missing stays missing."""
        out: List[float] = []
        for m in measurements:
            v = (m.get(section) or {}).get(key)
            if isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v):
                out.append(float(v))
        return out

    def generate_text_report(self, hours: int = 1) -> str:
        """Generate a text-based dashboard report from MEASURED values only."""

        measurements = self.load_recent_measurements(hours=hours)
        events = self.load_recent_events(hours=hours)

        if not measurements:
            return "❌ No measurements collected yet. Collector may not be running."

        losses = self._series(measurements, "learning", "outcome_loss")
        success = self._series(measurements, "learning", "success_rate")
        load = self._series(measurements, "system", "host_load_1m")
        rss = self._series(measurements, "system", "process_max_rss_mb")

        def _fmt(values: List[float], spec: str = ".4f", scale: float = 1.0) -> str:
            if not values:
                return "n/a"
            return format(sum(values) / len(values) * scale, spec)

        latest = measurements[-1]
        sources = latest.get("sources") or {}
        component_health = latest.get("component_health") or {}

        if losses:
            mean_loss = sum(losses) / len(losses)
            loss_range = f"{min(losses):.4f} ↔ {max(losses):.4f}"
            variance = f"{self._variance(losses):.6f}"
            trend = "📈 degrading" if losses[-1] > mean_loss else "📉 improving"
        else:
            loss_range = variance = trend = "n/a (no outcome recorded in window)"

        event_types: Dict[str, int] = {}
        anomalies = []
        for e in events:
            et = e.get("event_type", "unknown")
            event_types[et] = event_types.get(et, 0) + 1
            if et == "anomaly_detected":
                anomalies.append(f"  • {e.get('anomaly_type')} (details: {e.get('details', {})})")

        report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                   LIVE COLLECTION DASHBOARD                                ║
║                   Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                        ║
╚════════════════════════════════════════════════════════════════════════════╝

📊 OUTCOME LOSS (Last {hours}h) — share of recent tasks that did not succeed
  Mean:             {_fmt(losses)}
  Range:            {loss_range}
  Variance:         {variance}
  Trend:            {trend}
  Samples:          {len(measurements)} measurement(s), {len(losses)} with a loss value

🎯 SUCCESS RATE & HOST
  Mean success rate:  {_fmt(success, ".1f", 100.0)}{'%' if success else ''}
  Mean host load 1m:  {_fmt(load, ".2f")}
  Mean process RSS:   {_fmt(rss, ".1f")}{' MB' if rss else ''}

🧾 SOURCES (latest measurement)
"""
        for section, origin in sorted(sources.items()):
            report += f"  {section:14} {origin}\n"
        if not sources:
            report += "  (none recorded)\n"

        report += "\n🔧 COMPONENT HEALTH (learning signals actually seen)\n"
        for component, health in sorted(component_health.items()):
            status = "✅ ACTIVE" if health.get("active") else "⚠️  INACTIVE"
            report += f"  {component:16} {status}  | events: {health.get('events', 0)}\n"
        if not component_health:
            report += "  (none recorded)\n"

        report += f"""
📡 EVENTS (Last {hours}h)
  Total Events:     {len(events)}
  Event Types:      {event_types}
"""

        if anomalies:
            report += "\n⚠️  ANOMALIES DETECTED\n" + chr(10).join(anomalies) + "\n"

        report += f"""
✅ DATA PERSISTENCE
  Location: {self.live_measurements_dir}
  Format:   JSON Lines, one measurement per line, schema "{MEASUREMENT_SCHEMA}"
  Rotation: Daily (old files archived)
  Retention: Indefinite (you control cleanup)

📁 HOW TO ANALYZE THE DATA
  1. Load measurements:
     ```python
     import json
     from pathlib import Path
     src = Path("{self.live_measurements_dir}")
     data = [json.loads(l) for f in sorted(src.glob("measurements_*.jsonl"))
             for l in f.read_text().splitlines() if l.strip()]
     ```

  2. Plot the measured loss over time:
     ```python
     import matplotlib.pyplot as plt
     pts = [(m["timestamp"], m["learning"]["outcome_loss"]) for m in data
            if (m.get("learning") or {{}}).get("outcome_loss") is not None]
     plt.plot(*zip(*pts)); plt.show()
     ```

  3. Export for papers — ONLY records carrying schema "{MEASUREMENT_SCHEMA}" are
     real measurements. Anything without a ``schema`` field predates
     2026-09-07 and was generated with ``random.gauss``; it must be quarantined,
     not published:
     ```bash
     jq -c 'select(.schema == "{MEASUREMENT_SCHEMA}")' measurements_*.jsonl > all_measurements.jsonl
     ```

════════════════════════════════════════════════════════════════════════════
"""

        return report

    def _variance(self, values: List[float]) -> float:
        """Compute variance."""
        if len(values) < 2:
            return 0
        mean = sum(values) / len(values)
        return sum((x - mean) ** 2 for x in values) / len(values)

    def export_for_analysis(self, output_file: str = None) -> str:
        """
        Export all measurements to a single file for analysis.

        Returns path to the exported file.
        """

        if output_file is None:
            output_file = (
                corvin_home() / "tenants" / self.tenant_id / "experiments" /
                f"all_measurements_{datetime.now().strftime('%Y%m%d_%H%M%S')}.jsonl"
            )
        else:
            output_file = Path(output_file)

        # Collect all measurement files
        all_measurements = []
        for f in self.live_measurements_dir.glob("measurements_*.jsonl"):
            if "archive" not in str(f):
                with open(f, "r") as file:
                    for line in file:
                        try:
                            m = json.loads(line)
                            all_measurements.append(m)
                        except json.JSONDecodeError:
                            pass

        # Sort by timestamp
        all_measurements.sort(key=lambda m: m.get("unix_time", 0))

        # Write to output file
        with open(output_file, "w") as f:
            for m in all_measurements:
                f.write(json.dumps(m) + "\n")

        return str(output_file)

    def get_trend_analysis(self) -> Dict[str, Any]:
        """Analyze trends in the data."""

        measurements = self.load_recent_measurements(hours=24)  # Last 24 hours

        if not measurements:
            return {"status": "no_data"}

        losses = self._series(measurements, "learning", "outcome_loss")
        if not losses:
            return {"status": "no_loss_samples", "period_hours": 24, "samples": 0}

        # Early vs. late comparison
        n = len(losses)
        early = losses[:n//4]
        late = losses[3*n//4:]

        early_mean = sum(early) / len(early) if early else 0
        late_mean = sum(late) / len(late) if late else 0

        improvement = ((early_mean - late_mean) / early_mean * 100) if early_mean > 0 else 0

        return {
            "status": "analyzing",
            "period_hours": 24,
            "samples": len(losses),
            "early_mean_loss": float(early_mean),
            "late_mean_loss": float(late_mean),
            "improvement_percent": float(improvement),
            "convergence_verdict": "improving" if improvement > 5 else "stable" if improvement > 0 else "degrading"
        }


# ============================================================================
# CLI INTERFACE
# ============================================================================

def main():
    import sys
    import time

    dashboard = LiveCollectionDashboard()

    if len(sys.argv) > 1 and sys.argv[1] == "watch":
        # Watch mode — refresh every 30 seconds
        try:
            while True:
                print("\033[2J")  # Clear screen
                print(dashboard.generate_text_report(hours=1))
                time.sleep(30)
        except KeyboardInterrupt:
            print("\n[Dashboard] Stopped")

    elif len(sys.argv) > 1 and sys.argv[1] == "export":
        # Export mode
        output = dashboard.export_for_analysis()
        print(f"✅ Exported {len(dashboard.load_recent_measurements(hours=24*7))} measurements to: {output}")

    elif len(sys.argv) > 1 and sys.argv[1] == "trend":
        # Trend analysis
        analysis = dashboard.get_trend_analysis()
        print("\n📊 Trend Analysis (Last 24h)")
        print(json.dumps(analysis, indent=2))

    else:
        # Default: Show report once
        print(dashboard.generate_text_report(hours=1))


if __name__ == "__main__":
    main()

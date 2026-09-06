#!/usr/bin/env python3
"""
LIVE COLLECTION DASHBOARD — Real-time Monitoring UI

Shows current state of ongoing experiments:
- Recent loss trends
- Component health
- Anomalies detected
- Data collection status
- Time-series graphs (requires matplotlib/plotly)
"""

import json
import math
from datetime import datetime, timedelta
from pathlib import Path
from typing import List, Dict, Any


class LiveCollectionDashboard:
    """
    Reads live measurement files and generates human-readable reports.
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.live_measurements_dir = (
            Path.home() / ".corvin" / "tenants" / tenant_id / "experiments" / "live_measurements"
        )
        self.live_events_dir = (
            Path.home() / ".corvin" / "tenants" / tenant_id / "experiments" / "live_events"
        )

    def load_recent_measurements(self, hours: int = 1) -> List[Dict[str, Any]]:
        """Load measurements from the last N hours."""

        cutoff = datetime.now() - timedelta(hours=hours)
        measurements = []

        # Find today's file
        today_file = self.live_measurements_dir / f"measurements_{datetime.now().strftime('%Y%m%d')}.jsonl"

        if today_file.exists():
            with open(today_file, "r") as f:
                for line in f:
                    try:
                        m = json.loads(line)
                        if datetime.fromisoformat(m["timestamp"]) > cutoff:
                            measurements.append(m)
                    except json.JSONDecodeError:
                        pass

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

    def generate_text_report(self, hours: int = 1) -> str:
        """Generate a text-based dashboard report."""

        measurements = self.load_recent_measurements(hours=hours)
        events = self.load_recent_events(hours=hours)

        if not measurements:
            return "❌ No measurements collected yet. Collector may not be running."

        # Extract statistics
        losses = [m["learning"]["loss_total"] for m in measurements]
        accuracies = [m["learning"]["accuracy_routing"] for m in measurements]
        latencies = [m["system"]["latency_p99_ms"] for m in measurements]

        mean_loss = sum(losses) / len(losses)
        mean_accuracy = sum(accuracies) / len(accuracies)
        mean_latency = sum(latencies) / len(latencies)

        min_loss = min(losses)
        max_loss = max(losses)

        # Trend
        loss_trend = "📈 degrading" if losses[-1] > mean_loss else "📉 improving"

        # Component health from latest measurement
        latest = measurements[-1]
        component_health = latest.get("component_health", {})

        # Event summary
        event_types = {}
        anomalies = []
        for e in events:
            et = e["event_type"]
            event_types[et] = event_types.get(et, 0) + 1
            if et == "anomaly_detected":
                anomalies.append(f"  • {e['anomaly_type']} (details: {e.get('details', {})})")

        # Generate report
        report = f"""
╔════════════════════════════════════════════════════════════════════════════╗
║                   LIVE COLLECTION DASHBOARD                                ║
║                   Updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}                        ║
╚════════════════════════════════════════════════════════════════════════════╝

📊 LOSS METRICS (Last {hours}h)
  Mean Loss:        {mean_loss:.4f}
  Range:            {min_loss:.4f} ↔ {max_loss:.4f}
  Variance:         {self._variance(losses):.6f}
  Trend:            {loss_trend}
  Samples:          {len(measurements)}

🎯 ACCURACY & LATENCY
  Mean Routing Acc: {mean_accuracy:.1%}
  Mean Latency P99: {mean_latency:.1f}ms

🔧 COMPONENT HEALTH
"""

        # Add component health
        for component, health in component_health.items():
            status = "✅ ACTIVE" if health.get("active", False) else "⚠️  INACTIVE"
            contribution = health.get("contribution", 0) * 100
            drift = health.get("drift", 0)
            report += f"  {component:12} {status}  | Contribution: {contribution:5.1f}% | Drift: {drift:+.3f}\n"

        report += f"""
📡 EVENTS (Last {hours}h)
  Total Events:     {len(events)}
  Event Types:      {event_types}
"""

        if anomalies:
            report += f"""
⚠️  ANOMALIES DETECTED
{chr(10).join(anomalies)}
"""

        report += """
✅ DATA PERSISTENCE
  Location: ~/.corvin/tenants/_default/experiments/live_measurements/
  Format:   JSON Lines (one measurement per line)
  Rotation: Daily (old files archived)
  Retention: Indefinite (you control cleanup)

📁 HOW TO ANALYZE THE DATA
  1. Load measurements:
     ```python
     import json
     with open('~/.corvin/tenants/_default/experiments/live_measurements/measurements_20260906.jsonl') as f:
         data = [json.loads(line) for line in f]
     ```

  2. Plot trends over time:
     ```python
     import matplotlib.pyplot as plt
     times = [m['timestamp'] for m in data]
     losses = [m['learning']['loss_total'] for m in data]
     plt.plot(times, losses)
     plt.show()
     ```

  3. Export for papers:
     ```bash
     cat measurements_*.jsonl > all_measurements.jsonl
     # Share this file with collaborators for peer review
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
                Path.home() / ".corvin" / "tenants" / self.tenant_id / "experiments" /
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
        all_measurements.sort(key=lambda m: m["unix_time"])

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

        losses = [m["learning"]["loss_total"] for m in measurements]

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

"""Measurement instrumentation (ADR-0222): Autonomy success rate, latencies."""

import json
import os
from datetime import datetime
from dataclasses import dataclass, asdict
from typing import Optional
from pathlib import Path


@dataclass
class MeasurementSample:
    """One measurement datapoint."""
    timestamp: str
    task_id: str
    phase_id: str
    metric_type: str  # "session_renewal_latency", "notification_latency", "error_rate"
    value: float
    unit: str  # "ms", "percent", "count"


class MeasurementCollector:
    """Collects autonomy metrics for ADR-0222 decision gate."""

    def __init__(self, measurement_dir: Optional[str] = None):
        if measurement_dir is None:
            corvin_home = os.environ.get("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            measurement_dir = os.path.join(corvin_home, "measurement-week")

        self.measurement_dir = Path(measurement_dir)
        self.measurement_dir.mkdir(parents=True, exist_ok=True)
        self.measurement_file = self.measurement_dir / "autonomy_metrics.jsonl"

    def record_sample(self, sample: MeasurementSample):
        """Record one measurement to JSONL."""
        record = asdict(sample)
        with open(self.measurement_file, "a") as f:
            f.write(json.dumps(record) + "\n")

    def record_session_renewal_latency(self, task_id: str, latency_ms: float):
        """Record session renewal latency (goal: <10s = 10000ms)."""
        sample = MeasurementSample(
            timestamp=datetime.utcnow().isoformat(),
            task_id=task_id,
            phase_id="session_renewal",
            metric_type="session_renewal_latency",
            value=latency_ms,
            unit="ms",
        )
        self.record_sample(sample)

    def record_notification_latency(self, task_id: str, phase_id: str, latency_ms: float):
        """Record notification latency (goal: <5s = 5000ms)."""
        sample = MeasurementSample(
            timestamp=datetime.utcnow().isoformat(),
            task_id=task_id,
            phase_id=phase_id,
            metric_type="notification_latency",
            value=latency_ms,
            unit="ms",
        )
        self.record_sample(sample)

    def record_error(self, task_id: str, phase_id: str, error_type: str):
        """Record error for error_rate calculation."""
        sample = MeasurementSample(
            timestamp=datetime.utcnow().isoformat(),
            task_id=task_id,
            phase_id=phase_id,
            metric_type="error_rate",
            value=1.0,
            unit="count",
        )
        self.record_sample(sample)

    def get_metrics(self):
        """Calculate aggregated metrics from samples."""
        if not self.measurement_file.exists():
            return {}

        samples = []
        with open(self.measurement_file, "r") as f:
            for line in f:
                samples.append(json.loads(line))

        # Aggregate by metric type
        metrics = {}
        for sample in samples:
            metric_type = sample["metric_type"]
            if metric_type not in metrics:
                metrics[metric_type] = []
            metrics[metric_type].append(sample["value"])

        # Calculate statistics
        stats = {}
        for metric_type, values in metrics.items():
            if values:
                stats[metric_type] = {
                    "count": len(values),
                    "mean": sum(values) / len(values),
                    "min": min(values),
                    "max": max(values),
                    "p95": sorted(values)[int(len(values) * 0.95)] if len(values) > 1 else values[0],
                }

        return stats


# Singleton
_collector = None


def get_measurement_collector() -> MeasurementCollector:
    global _collector
    if _collector is None:
        _collector = MeasurementCollector()
    return _collector

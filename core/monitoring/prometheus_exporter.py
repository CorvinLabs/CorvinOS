"""Prometheus Exporter — Export OTEL metrics to Prometheus"""

from typing import Dict, List
import json
from datetime import datetime


class PrometheusExporter:
    """Export metrics to Prometheus format"""

    def __init__(self):
        self.metrics: Dict[str, float] = {}

    def record_metric(self, name: str, value: float, labels: dict | None = None):
        """Record a metric"""
        self.metrics[name] = value

    def export_text_format(self) -> str:
        """Export metrics in Prometheus text format"""
        lines = [f"# HELP corvinOS metrics"]
        for name, value in self.metrics.items():
            lines.append(f"corvinOS_{name} {value}")
        return "\n".join(lines)

    def to_json(self) -> str:
        """Export metrics as JSON"""
        return json.dumps({
            "timestamp": datetime.utcnow().isoformat(),
            "metrics": self.metrics
        })

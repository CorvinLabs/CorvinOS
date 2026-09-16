"""Phase 9: Anomaly Detection — detect metric deviations from baseline."""

from statistics import mean, stdev
from typing import List, Dict, Optional


class AnomalyDetector:
    """Detect anomalies in skill metrics using statistical baselines."""

    def __init__(self, baseline_window: int = 100):
        self.baseline_window = baseline_window

    def detect_anomalies(self, metrics: List[Dict]) -> List[str]:
        """Detect anomalies in recent metrics.

        Returns list of anomaly messages, empty if none.
        """
        if len(metrics) < 10:
            return []  # Need enough data

        recent = metrics[-self.baseline_window:]
        baseline = metrics[:-self.baseline_window] if len(metrics) > self.baseline_window else metrics[:-10]

        anomalies = []

        # Latency anomaly: >2σ above mean
        baseline_latencies = [m.get('latency_ms', 100) for m in baseline]
        recent_latencies = [m.get('latency_ms', 100) for m in recent]

        if len(baseline_latencies) > 1:
            mean_lat = mean(baseline_latencies)
            std_lat = stdev(baseline_latencies) if stdev(baseline_latencies) > 0 else 1
            recent_mean = mean(recent_latencies)

            if recent_mean > mean_lat + 2 * std_lat:
                anomalies.append(f"Latency spike: {recent_mean:.0f}ms (baseline: {mean_lat:.0f}ms)")

        # Error rate anomaly: >20% increase
        baseline_errors = [m.get('error_rate', 0.0) for m in baseline]
        recent_errors = [m.get('error_rate', 0.0) for m in recent]

        baseline_error_mean = mean(baseline_errors)
        recent_error_mean = mean(recent_errors)

        if recent_error_mean > baseline_error_mean * 1.2:
            anomalies.append(f"Error rate spike: {recent_error_mean*100:.1f}% (baseline: {baseline_error_mean*100:.1f}%)")

        # Convergence anomaly: >10% drop
        baseline_conv = [m.get('convergence', 0.5) for m in baseline]
        recent_conv = [m.get('convergence', 0.5) for m in recent]

        baseline_conv_mean = mean(baseline_conv)
        recent_conv_mean = mean(recent_conv)

        if recent_conv_mean < baseline_conv_mean * 0.9:
            anomalies.append(f"Convergence drop: {recent_conv_mean:.2f} (baseline: {baseline_conv_mean:.2f})")

        return anomalies

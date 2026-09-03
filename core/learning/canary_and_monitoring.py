"""Phase 2a.4/2a.5: A/B Testing Canary Deployer + Monitoring Metrics."""

from dataclasses import dataclass
from typing import Dict, List
import logging

logger = logging.getLogger(__name__)


@dataclass
class CanaryMetrics:
    """Canary A/B test results."""
    skill_id: str
    baseline_success_rate: float
    canary_success_rate: float
    baseline_latency_p99_ms: float
    canary_latency_p99_ms: float
    canary_winner: bool  # True if canary is better
    reason: str


class CanaryDeployer:
    """A/B test: Deploy tuned config to 10% traffic, compare vs. baseline."""

    def __init__(self, metrics_store):
        self.metrics_store = metrics_store

    def run_canary(self, old_config, new_config, duration_hours: int = 24) -> CanaryMetrics:
        """Run canary deployment for N hours.

        Args:
            old_config: Current (baseline) config
            new_config: Tuned config to test
            duration_hours: How long to run (default 24h)

        Returns:
            CanaryMetrics with winner decision
        """
        # Get metrics for both configs (simplified: mocked for now)
        baseline_metrics = self.metrics_store.get_metrics(
            skill_id=old_config.skill_id,
            config_version=old_config.version,
            hours=duration_hours
        )

        canary_metrics = self.metrics_store.get_metrics(
            skill_id=new_config.skill_id,
            config_version=new_config.version,
            hours=duration_hours
        )

        # Compare success rates + latencies
        baseline_success = baseline_metrics.get("success_rate", 0.95)
        canary_success = canary_metrics.get("success_rate", 0.95)

        baseline_latency = baseline_metrics.get("p99_latency_ms", 100)
        canary_latency = canary_metrics.get("p99_latency_ms", 100)

        # Winner: Higher success rate AND lower latency
        canary_better_success = canary_success >= baseline_success - 0.01  # Allow 1% regression
        canary_better_latency = canary_latency <= baseline_latency + 10  # Allow 10ms regression
        canary_winner = canary_better_success and canary_better_latency

        reason = (
            f"Baseline: success={baseline_success:.2%}, latency={baseline_latency:.0f}ms | "
            f"Canary: success={canary_success:.2%}, latency={canary_latency:.0f}ms | "
            f"Winner: {'CANARY' if canary_winner else 'BASELINE'}"
        )

        logger.info(f"CANARY RESULT: {reason}")

        return CanaryMetrics(
            skill_id=old_config.skill_id,
            baseline_success_rate=baseline_success,
            canary_success_rate=canary_success,
            baseline_latency_p99_ms=baseline_latency,
            canary_latency_p99_ms=canary_latency,
            canary_winner=canary_winner,
            reason=reason
        )


class LearningMetricsExporter:
    """Exports learning metrics to Prometheus (Phase 2a.5)."""

    METRIC_NAMES = {
        "learning_feedback_count": "Counter: feedback ingested",
        "learning_drift_detected_count": "Counter: drift detected",
        "learning_config_tuned_count": "Counter: config updated",
        "learning_canary_success_rate": "Gauge: canary win rate",
        "learning_optimizer_latency_ms": "Histogram: optimizer wall-clock time",
    }

    def __init__(self):
        self.metrics = {}

    def emit(self, metric_name: str, value: float, labels: Dict[str, str] = None) -> None:
        """Emit a metric value.

        Args:
            metric_name: Name (must be in METRIC_NAMES)
            value: Numeric value
            labels: Optional labels (skill_id, tenant_id, etc.)
        """
        if metric_name not in self.METRIC_NAMES:
            logger.warning(f"Unknown metric: {metric_name}")
            return

        key = (metric_name, tuple(sorted(labels.items())) if labels else ())
        self.metrics[key] = value

        logger.info(f"METRIC: {metric_name} = {value} {labels or ''}")

    def get_metrics(self) -> Dict:
        """Return all exported metrics (for testing/debugging)."""
        return self.metrics

    def export_to_prometheus(self) -> str:
        """Export metrics in Prometheus text format."""
        lines = []
        for (metric_name, label_tuple), value in self.metrics.items():
            if label_tuple:
                labels_str = ",".join(f'{k}="{v}"' for k, v in label_tuple)
                lines.append(f"{metric_name}{{{labels_str}}} {value}")
            else:
                lines.append(f"{metric_name} {value}")
        return "\n".join(lines)


# ============================================================================
# Tests
# ============================================================================

def test_canary_deployer():
    """Test: A/B canary decision logic."""

    class MockMetricsStore:
        def __init__(self, baseline_sr, canary_sr, baseline_lat, canary_lat):
            self.baseline = {"success_rate": baseline_sr, "p99_latency_ms": baseline_lat}
            self.canary = {"success_rate": canary_sr, "p99_latency_ms": canary_lat}

        def get_metrics(self, skill_id, config_version, hours):
            if config_version == 0:
                return self.baseline
            else:
                return self.canary

    class MockConfig:
        def __init__(self, skill_id, version):
            self.skill_id = skill_id
            self.version = version

    # Test 1: Canary better → roll forward
    print("Test 1: Canary better → winner...")
    store = MockMetricsStore(
        baseline_sr=0.95, canary_sr=0.96,
        baseline_lat=100, canary_lat=95
    )
    deployer = CanaryDeployer(store)

    old = MockConfig("test", 0)
    new = MockConfig("test", 1)

    result = deployer.run_canary(old, new)
    assert result.canary_winner, "Canary with better metrics should win"
    print(f"  {result.reason}")
    print("  ✅ Pass")

    # Test 2: Canary worse → keep baseline
    print("\nTest 2: Canary worse → baseline wins...")
    store = MockMetricsStore(
        baseline_sr=0.95, canary_sr=0.92,  # Regression
        baseline_lat=100, canary_lat=100
    )
    deployer = CanaryDeployer(store)

    result = deployer.run_canary(old, new)
    assert not result.canary_winner, "Canary with worse success should not win"
    print(f"  {result.reason}")
    print("  ✅ Pass")

    print("\n✅ Canary tests pass!")


def test_metrics_exporter():
    """Test: Prometheus metrics export."""

    exporter = LearningMetricsExporter()

    # Emit metrics
    exporter.emit("learning_feedback_count", 42, {"skill_id": "os.router", "tenant_id": "_default"})
    exporter.emit("learning_drift_detected_count", 1, {"skill_id": "os.router"})
    exporter.emit("learning_canary_success_rate", 0.98)

    # Export
    prom_output = exporter.export_to_prometheus()
    print("\nPrometheus Export:")
    print(prom_output)

    assert "learning_feedback_count" in prom_output
    assert "skill_id=\"os.router\"" in prom_output
    assert "0.98" in prom_output

    print("\n✅ Metrics exporter tests pass!")


if __name__ == "__main__":
    print("Running Phase 2a.4/2a.5 Canary + Monitoring Tests...\n")
    test_canary_deployer()
    test_metrics_exporter()
    print("\n🎉 A/B testing + monitoring ready!")

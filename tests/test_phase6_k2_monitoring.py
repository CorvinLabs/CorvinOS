"""
Phase 6 k=2 Validation — Monitoring Integration + Health Checks + APIs

Validates:
1. MetricsCollector pulls metrics from multiple sources
2. HealthCheckEvaluator aggregates metrics into health status
3. Monitoring APIs expose status/metrics/decisions to dashboard
4. Health checks integrate with Orchestrator

Total: 12 validations (4 collector, 4 evaluator, 4 API tests)
"""

import sys
import asyncio
import time
import json
from pathlib import Path
from datetime import datetime, timedelta
from typing import Dict, Any, Tuple, List
from dataclasses import dataclass

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.phase6_rollout.orchestrator import (
    RolloutOrchestrator,
    RolloutStage,
    HealthStatus,
    HealthMetrics,
)
from core.phase6_rollout.monitoring import (
    MetricsCollector,
    HealthCheckEvaluator,
    MetricSource,
    RawMetricSample,
)


# ============================================================================
# k=2 VALIDATION HARNESS
# ============================================================================

@dataclass
class ValidationResult:
    """Result of a k=2 validation scenario."""
    name: str
    passed: bool
    category: str
    error_message: str
    duration_seconds: float
    metrics: Dict[str, Any]


class Phase6K2Validator:
    """Harness for validating Phase 6 k=2 (monitoring integration)."""

    def __init__(self):
        self.results: List[ValidationResult] = []

    def run_validation(
        self,
        name: str,
        category: str,
        validation_fn,
    ) -> ValidationResult:
        """Run a validation scenario."""
        start_time = time.time()
        passed = False
        error = ""
        metrics = {}

        try:
            loop = asyncio.new_event_loop()
            try:
                passed, error, metrics = loop.run_until_complete(validation_fn())
            finally:
                loop.close()
        except Exception as e:
            passed = False
            error = f"Exception: {type(e).__name__}: {str(e)}"

        duration = time.time() - start_time

        result = ValidationResult(
            name=name,
            passed=passed,
            category=category,
            error_message=error,
            duration_seconds=duration,
            metrics=metrics,
        )

        self.results.append(result)
        return result

    def print_summary(self):
        """Print validation summary."""
        passed_count = sum(1 for r in self.results if r.passed)
        total_count = len(self.results)

        print("\n" + "=" * 80)
        print("PHASE 6 k=2 VALIDATION SUMMARY")
        print("=" * 80)
        print(f"Total scenarios: {total_count}")
        print(f"Passed: {passed_count}/{total_count}")
        print(f"Failed: {total_count - passed_count}/{total_count}")
        print()

        # Group by category
        by_category = {}
        for result in self.results:
            if result.category not in by_category:
                by_category[result.category] = []
            by_category[result.category].append(result)

        for category in ["collector", "evaluator", "api", "orchestrator"]:
            if category in by_category:
                results = by_category[category]
                passed = sum(1 for r in results if r.passed)
                print(f"{category.upper()}: {passed}/{len(results)} passed")

                for result in results:
                    status = "✓" if result.passed else "✗"
                    print(f"  {status} {result.name}")
                    if not result.passed:
                        print(f"      Error: {result.error_message}")

        print("=" * 80)


# ============================================================================
# COLLECTOR VALIDATIONS
# ============================================================================

async def validate_collector_initialization() -> Tuple[bool, str, Dict]:
    """Validate metrics collector initializes correctly."""
    collector = MetricsCollector("_default")

    if collector.tenant_id != "_default":
        return False, "Tenant ID not set correctly", {}
    if collector.buffer.max_size <= 0:
        return False, "Buffer size invalid", {}

    return True, "", {"buffer_max_size": collector.buffer.max_size}


async def validate_collector_collects_metrics() -> Tuple[bool, str, Dict]:
    """Validate collector can collect metrics from all sources."""
    collector = MetricsCollector()
    samples = await collector.collect_metrics()

    if len(samples) == 0:
        return False, "No metrics collected", {}

    # Should have at least throughput, latency, error rate, audit integrity
    labels = {s.label for s in samples}
    required = {"throughput", "latency_p99", "error_rate_percent", "audit_integrity_percent"}
    missing = required - labels
    if missing:
        return False, f"Missing labels: {missing}", {}

    return True, "", {"samples_collected": len(samples), "labels": list(labels)}


async def validate_collector_buffer_lifecycle() -> Tuple[bool, str, Dict]:
    """Validate buffer adds/retrieves samples correctly."""
    collector = MetricsCollector()

    # Add 10 samples
    for i in range(10):
        sample = RawMetricSample(
            source=MetricSource.CONTEXT_BUS,
            timestamp=datetime.now() - timedelta(minutes=i),
            label="throughput",
            value=250.0 + i,
            unit="workflows/sec",
        )
        collector.buffer.add(sample)

    # Retrieve recent samples
    recent = collector.buffer.recent(minutes=10)
    if len(recent) != 10:
        return False, f"Expected 10 samples, got {len(recent)}", {}

    # Retrieve with narrow window (5-minute window should have 5+ samples)
    recent_5 = collector.buffer.recent(minutes=5)
    if len(recent_5) < 5:  # At least 5 samples in 5-min window
        return False, f"Expected 5+ samples in 5-min window, got {len(recent_5)}", {}

    return True, "", {"total_samples": len(recent)}


async def validate_collector_error_spike_simulation() -> Tuple[bool, str, Dict]:
    """Validate collector can simulate and track error spikes."""
    collector = MetricsCollector()

    # Baseline collection
    await collector.collect_metrics()
    baseline_error = collector._error_count

    # Simulate error spike
    collector.simulate_error_spike()
    await collector.collect_metrics()

    if collector._error_count <= baseline_error:
        return False, "Error spike not recorded", {}

    # Simulate recovery
    collector.simulate_error_recovery()
    await collector.collect_metrics()

    if collector._error_count >= 100:
        return False, "Error recovery not working", {}

    return True, "", {"spike_recorded": True}


# ============================================================================
# EVALUATOR VALIDATIONS
# ============================================================================

async def validate_evaluator_healthy_metrics() -> Tuple[bool, str, Dict]:
    """Validate evaluator recognizes healthy metrics."""
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    # Collect and evaluate
    health = await evaluator.evaluate_health()

    if health is None:
        return False, "Health evaluation returned None", {}
    if not health.is_healthy():
        return False, f"Metrics not healthy: {health}", {}

    return True, "", {"status": "HEALTHY"}


async def validate_evaluator_degraded_metrics() -> Tuple[bool, str, Dict]:
    """Validate evaluator recognizes degraded metrics."""
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    # Inject degraded metric (high latency)
    collector.buffer.add(
        RawMetricSample(
            source=MetricSource.EXECUTION_CONTEXT,
            timestamp=datetime.now(),
            label="latency_p99",
            value=300.0,  # Degraded: 200-500ms range
            unit="ms",
        )
    )

    health = await evaluator.evaluate_health()

    if health is None:
        return False, "Health evaluation returned None", {}
    if health.is_healthy():
        return False, "Should be degraded, not healthy", {}
    if not health.is_degraded():
        return False, "Should be degraded", {}

    return True, "", {"status": "DEGRADED"}


async def validate_evaluator_critical_metrics() -> Tuple[bool, str, Dict]:
    """Validate evaluator recognizes critical metrics."""
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    # Inject critical metrics
    collector.buffer.add(
        RawMetricSample(
            source=MetricSource.ERROR_COUNTERS,
            timestamp=datetime.now(),
            label="error_rate_percent",
            value=2.0,  # Critical: >0.5%
            unit="%",
        )
    )
    collector.buffer.add(
        RawMetricSample(
            source=MetricSource.AUDIT_TRAIL,
            timestamp=datetime.now(),
            label="audit_integrity_percent",
            value=98.0,  # Critical: <99.9%
            unit="%",
        )
    )

    health = await evaluator.evaluate_health()

    if health is None:
        return False, "Health evaluation returned None", {}
    if health.is_healthy():
        return False, "Should be critical, not healthy", {}
    if health.is_degraded():
        return False, "Should be critical, not degraded", {}

    return True, "", {"status": "CRITICAL"}


async def validate_evaluator_dashboard_metrics_export() -> Tuple[bool, str, Dict]:
    """Validate evaluator can export metrics for dashboard."""
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    # Add some samples
    await collector.collect_metrics()

    dashboard_data = evaluator.get_recent_metrics_for_dashboard(minutes=60)

    if not isinstance(dashboard_data, list):
        return False, "Dashboard data should be list", {}
    if len(dashboard_data) == 0:
        return False, "Dashboard data should have metrics", {}

    # Check structure
    for metric in dashboard_data:
        if "label" not in metric or "samples" not in metric:
            return False, f"Missing fields in metric: {metric}", {}

    return True, "", {"metrics_exported": len(dashboard_data)}


# ============================================================================
# API VALIDATIONS
# ============================================================================

async def validate_canary_status_api_contract() -> Tuple[bool, str, Dict]:
    """Validate canary/status API contract."""
    orch = RolloutOrchestrator()
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    await orch.start()
    health = await evaluator.evaluate_health()

    # Build API response (what /v1/canary/status would return)
    status_response = {
        "stage": orch.state.stage.value,
        "canary_traffic_percent": orch.state.canary_traffic_percent,
        "health_status": orch.state.health_status.value,
        "go_no_go": "GO" if health.is_healthy() else "NO_GO",
        "age_hours": orch.state.age().total_seconds() / 3600,
        "metrics": {
            "throughput_per_sec": health.throughput_per_sec,
            "latency_p99_ms": health.latency_p99_ms,
            "error_rate_percent": health.error_rate_percent,
            "audit_integrity_percent": health.audit_integrity_percent,
        },
    }

    # Validate response structure
    required_fields = {"stage", "canary_traffic_percent", "health_status", "go_no_go", "metrics"}
    missing = required_fields - set(status_response.keys())
    if missing:
        return False, f"Missing fields: {missing}", {}

    # Validate metrics substructure
    required_metrics = {"throughput_per_sec", "latency_p99_ms", "error_rate_percent"}
    missing_metrics = required_metrics - set(status_response["metrics"].keys())
    if missing_metrics:
        return False, f"Missing metrics: {missing_metrics}", {}

    return True, "", {"response_valid": True, "go_no_go": status_response["go_no_go"]}


async def validate_canary_metrics_api_contract() -> Tuple[bool, str, Dict]:
    """Validate canary/metrics API contract for time-series data."""
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    # Generate metrics
    await collector.collect_metrics()

    # Get dashboard metrics (what /v1/canary/metrics would return)
    metrics_response = {
        "window_minutes": 360,
        "metrics": evaluator.get_recent_metrics_for_dashboard(minutes=360),
        "updated_at": datetime.now().isoformat(),
    }

    # Validate response structure
    if "metrics" not in metrics_response:
        return False, "Missing metrics field", {}
    if not isinstance(metrics_response["metrics"], list):
        return False, "Metrics should be list", {}

    # Check sample structure
    for metric in metrics_response["metrics"]:
        if "label" not in metric or "samples" not in metric or "unit" not in metric:
            return False, f"Invalid metric structure: {metric.keys()}", {}
        if not isinstance(metric["samples"], list):
            return False, f"Samples should be list for {metric['label']}", {}

    return True, "", {"metrics_count": len(metrics_response["metrics"])}


async def validate_canary_decisions_api_contract() -> Tuple[bool, str, Dict]:
    """Validate canary/decisions API contract for audit trail."""
    orch = RolloutOrchestrator()

    await orch.start()

    # Make some transitions
    stage1 = await orch.next_stage()

    # Build decisions response (what /v1/canary/decisions would return)
    decisions_response = {
        "total_decisions": len(orch.get_decision_history()),
        "decisions": [
            {
                "gate": d.gate.value,
                "pass_gate": d.pass_gate,
                "timestamp": d.timestamp.isoformat(),
                "reason": d.reason,
                "recommended_action": d.recommended_action,
                "confidence_percent": d.confidence_percent,
            }
            for d in orch.get_decision_history()[-10:]  # Last 10
        ],
    }

    # Validate response
    if "decisions" not in decisions_response:
        return False, "Missing decisions field", {}
    if not isinstance(decisions_response["decisions"], list):
        return False, "Decisions should be list", {}

    # Check decision structure
    for decision in decisions_response["decisions"]:
        required_fields = {"gate", "pass_gate", "timestamp", "reason", "confidence_percent"}
        missing = required_fields - set(decision.keys())
        if missing:
            return False, f"Decision missing fields: {missing}", {}

    return True, "", {"decisions_count": len(decisions_response["decisions"])}


# ============================================================================
# ORCHESTRATOR + MONITORING INTEGRATION
# ============================================================================

async def validate_orchestrator_monitoring_integration() -> Tuple[bool, str, Dict]:
    """Validate orchestrator integrates with monitoring for health checks."""
    orch = RolloutOrchestrator()
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    await orch.start()

    # Transition to canary
    stage = await orch.next_stage()
    if stage != RolloutStage.CANARY_10:
        return False, "Failed to transition to CANARY_10", {}

    # Get health metrics
    health = await evaluator.evaluate_health()
    if health is None:
        return False, "Evaluator returned no health", {}

    # Feed health to orchestrator
    orch.record_metrics(health)

    if orch.state.health_status == HealthStatus.UNKNOWN:
        return False, "Orchestrator should have recorded health", {}

    return True, "", {"health_status": orch.state.health_status.value}


async def validate_orchestrator_responds_to_degraded_health() -> Tuple[bool, str, Dict]:
    """Validate orchestrator recognizes degraded health from monitoring."""
    orch = RolloutOrchestrator()
    collector = MetricsCollector()
    evaluator = HealthCheckEvaluator(collector)

    await orch.start()
    await orch.next_stage()  # Go to CANARY_10

    # Simulate degraded health
    collector.buffer.add(
        RawMetricSample(
            source=MetricSource.EXECUTION_CONTEXT,
            timestamp=datetime.now(),
            label="latency_p99",
            value=300.0,
            unit="ms",
        )
    )

    health = await evaluator.evaluate_health()
    orch.record_metrics(health)

    if orch.state.health_status != HealthStatus.DEGRADED:
        return False, f"Status should be DEGRADED, got {orch.state.health_status}", {}

    return True, "", {"detected_degradation": True}


async def validate_orchestrator_initiates_rollback_on_critical() -> Tuple[bool, str, Dict]:
    """Validate orchestrator detects critical health and prepares rollback."""
    orch = RolloutOrchestrator()

    await orch.start()
    await orch.next_stage()  # Go to CANARY_10

    # Simulate critical health
    critical_health = HealthMetrics(
        timestamp=datetime.now(),
        throughput_per_sec=50,
        latency_p99_ms=1000,
        error_rate_percent=2.0,
        audit_integrity_percent=98.0,
        feature_promotion_count=0,
        features_stuck_alpha_count=1,
    )
    orch.record_metrics(critical_health)

    if not orch._health_is_critical():
        return False, "Should detect critical health", {}

    if orch.state.health_status != HealthStatus.CRITICAL:
        return False, f"Status should be CRITICAL, got {orch.state.health_status}", {}

    return True, "", {"detected_critical": True}


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run all Phase 6 k=2 validations."""
    validator = Phase6K2Validator()

    # Collector validations (4)
    validator.run_validation(
        "Collector initialization",
        "collector",
        validate_collector_initialization,
    )
    validator.run_validation(
        "Collector collects metrics from all sources",
        "collector",
        validate_collector_collects_metrics,
    )
    validator.run_validation(
        "Collector buffer lifecycle",
        "collector",
        validate_collector_buffer_lifecycle,
    )
    validator.run_validation(
        "Collector error spike simulation",
        "collector",
        validate_collector_error_spike_simulation,
    )

    # Evaluator validations (4)
    validator.run_validation(
        "Evaluator recognizes healthy metrics",
        "evaluator",
        validate_evaluator_healthy_metrics,
    )
    validator.run_validation(
        "Evaluator recognizes degraded metrics",
        "evaluator",
        validate_evaluator_degraded_metrics,
    )
    validator.run_validation(
        "Evaluator recognizes critical metrics",
        "evaluator",
        validate_evaluator_critical_metrics,
    )
    validator.run_validation(
        "Evaluator exports dashboard metrics",
        "evaluator",
        validate_evaluator_dashboard_metrics_export,
    )

    # API validations (4)
    validator.run_validation(
        "Canary status API contract",
        "api",
        validate_canary_status_api_contract,
    )
    validator.run_validation(
        "Canary metrics API contract",
        "api",
        validate_canary_metrics_api_contract,
    )
    validator.run_validation(
        "Canary decisions API contract",
        "api",
        validate_canary_decisions_api_contract,
    )

    # Orchestrator + Monitoring integration (3)
    validator.run_validation(
        "Orchestrator + Monitoring integration",
        "orchestrator",
        validate_orchestrator_monitoring_integration,
    )
    validator.run_validation(
        "Orchestrator responds to degraded health",
        "orchestrator",
        validate_orchestrator_responds_to_degraded_health,
    )
    validator.run_validation(
        "Orchestrator detects critical health",
        "orchestrator",
        validate_orchestrator_initiates_rollback_on_critical,
    )

    # Print summary
    validator.print_summary()

    # Exit with appropriate code
    passed_count = sum(1 for r in validator.results if r.passed)
    total_count = len(validator.results)

    if passed_count == total_count:
        print(f"\n✅ ALL {total_count} VALIDATIONS PASSED!")
        sys.exit(0)
    else:
        print(f"\n❌ {total_count - passed_count} VALIDATIONS FAILED!")
        sys.exit(1)


if __name__ == "__main__":
    main()

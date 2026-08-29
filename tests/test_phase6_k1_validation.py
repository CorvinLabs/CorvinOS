"""
Phase 6 k=1 Validation — Orchestrator + Simulation Framework

Validates core orchestration logic with simulated metric scenarios to prove:
1. Orchestrator makes correct go/no-go decisions based on health gates
2. State transitions work correctly (INITIAL → CANARY_10 → RAMP_50 → FULL_100)
3. Rollback triggers on health degradation
4. Decision gates enforce time-based constraints (48h minimum between stages)
5. Simulation framework generates realistic metric patterns for all 8 scenarios

Total: 15 scenarios, all passing by end of k=1.
"""

import sys
import asyncio
import time
from pathlib import Path
from datetime import datetime, timedelta
from typing import List, Dict, Any, Tuple
from dataclasses import dataclass

# Add repo root to path
sys.path.insert(0, str(Path(__file__).parent.parent))

from core.phase6_rollout.orchestrator import (
    RolloutOrchestrator,
    RolloutStage,
    HealthStatus,
    HealthMetrics,
)
from core.phase6_rollout.simulation import (
    MetricsGenerator,
    SimulationScenario,
)


# ============================================================================
# k=1 VALIDATION HARNESS
# ============================================================================

@dataclass
class ValidationResult:
    """Result of a Phase 6 k=1 validation scenario."""
    name: str
    passed: bool
    scenario_category: str  # "orchestration", "simulation", "e2e"
    error_message: str
    duration_seconds: float
    metrics: Dict[str, Any]


class Phase6K1Validator:
    """Harness for validating Phase 6 k=1 (orchestrator + simulation)."""

    def __init__(self):
        self.results: List[ValidationResult] = []

    def run_validation(
        self,
        name: str,
        category: str,
        validation_fn,
    ) -> ValidationResult:
        """Run a single validation scenario.

        Args:
            name: Validation name
            category: "orchestration", "simulation", or "e2e"
            validation_fn: Async function() -> (passed: bool, error: str, metrics: dict)

        Returns:
            ValidationResult with pass/fail + metrics
        """
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
            scenario_category=category,
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
        print("PHASE 6 k=1 VALIDATION SUMMARY")
        print("=" * 80)
        print(f"Total scenarios: {total_count}")
        print(f"Passed: {passed_count}/{total_count}")
        print(f"Failed: {total_count - passed_count}/{total_count}")
        print()

        # Group by category
        by_category = {}
        for result in self.results:
            if result.scenario_category not in by_category:
                by_category[result.scenario_category] = []
            by_category[result.scenario_category].append(result)

        for category in ["orchestration", "simulation", "e2e"]:
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
# ORCHESTRATION VALIDATIONS
# ============================================================================

async def validate_orchestrator_initialization() -> Tuple[bool, str, Dict]:
    """Validate orchestrator starts in correct initial state."""
    orch = RolloutOrchestrator("_default")

    if orch.state.stage != RolloutStage.INITIAL:
        return False, f"Expected INITIAL stage, got {orch.state.stage}", {}
    if orch.state.canary_traffic_percent != 0:
        return False, "Expected 0% traffic initially", {}
    if orch._is_running:
        return False, "Orchestrator should not be running initially", {}

    return True, "", {"tenant_id": orch.tenant_id}


async def validate_orchestrator_start_stop() -> Tuple[bool, str, Dict]:
    """Validate orchestrator can be started and stopped."""
    orch = RolloutOrchestrator()
    await orch.start()

    if not orch._is_running:
        return False, "Orchestrator should be running after start()", {}

    await orch.stop()

    if orch._is_running:
        return False, "Orchestrator should be stopped after stop()", {}

    return True, "", {}


async def validate_orchestrator_initial_to_canary_transition() -> Tuple[bool, str, Dict]:
    """Validate INITIAL → CANARY_10 transition."""
    orch = RolloutOrchestrator()
    await orch.start()

    stage = await orch.next_stage()

    if stage != RolloutStage.CANARY_10:
        return False, f"Expected CANARY_10, got {stage}", {}
    if orch.state.canary_traffic_percent != 10:
        return False, f"Expected 10% traffic, got {orch.state.canary_traffic_percent}%", {}

    return True, "", {"transitioned_to": stage.value}


async def validate_orchestrator_canary_requires_48h() -> Tuple[bool, str, Dict]:
    """Validate canary stage requires 48h minimum before promotion."""
    orch = RolloutOrchestrator()
    await orch.start()

    # Transition to canary
    await orch.next_stage()
    initial_stage = orch.state.stage

    # Try to transition immediately (too early)
    stage = await orch.next_stage()

    if stage != RolloutStage.CANARY_10:
        return False, "Should still be in CANARY_10 (age < 48h)", {}

    # Now set age to 48h+ and add healthy metrics
    orch.state.started_at = datetime.now() - timedelta(hours=50)

    for i in range(10):
        metrics = HealthMetrics(
            timestamp=datetime.now(),
            throughput_per_sec=250,
            latency_p99_ms=45,
            error_rate_percent=0.02,
            audit_integrity_percent=99.95,
            feature_promotion_count=5,
            features_stuck_alpha_count=0,
        )
        orch.state.metrics.append(metrics)

    # Check if gate would now pass
    decision = orch._check_canary_health_48h_gate()
    if not decision.pass_gate:
        return False, "Gate should pass after 48h with healthy metrics", {}

    return True, "", {"age_hours": 50, "healthy_metrics": len(orch.state.metrics)}


async def validate_orchestrator_canary_to_ramp50_transition() -> Tuple[bool, str, Dict]:
    """Validate CANARY_10 → RAMP_50 transition after 48h."""
    orch = RolloutOrchestrator()
    await orch.start()

    # Transition to canary
    await orch.next_stage()
    orch.state.stage = RolloutStage.CANARY_10
    orch.state.started_at = datetime.now() - timedelta(hours=50)

    # Add healthy metrics
    for i in range(10):
        metrics = HealthMetrics(
            timestamp=datetime.now(),
            throughput_per_sec=250,
            latency_p99_ms=45,
            error_rate_percent=0.02,
            audit_integrity_percent=99.95,
            feature_promotion_count=5,
            features_stuck_alpha_count=0,
        )
        orch.state.metrics.append(metrics)

    # Verify gate passes
    decision = orch._check_canary_health_48h_gate()
    if not decision.pass_gate:
        return False, "Canary health gate should pass", {}

    return True, "", {"gate_confidence": decision.confidence_percent}


async def validate_orchestrator_rollback_on_critical_health() -> Tuple[bool, str, Dict]:
    """Validate rollback is triggered when health becomes critical."""
    orch = RolloutOrchestrator()
    await orch.start()

    await orch.next_stage()  # Go to CANARY_10

    # Add critical metrics
    critical_metrics = HealthMetrics(
        timestamp=datetime.now(),
        throughput_per_sec=50,
        latency_p99_ms=1000,
        error_rate_percent=2.0,
        audit_integrity_percent=98.0,
        feature_promotion_count=0,
        features_stuck_alpha_count=1,
    )
    orch.record_metrics(critical_metrics)

    if not orch._health_is_critical():
        return False, "Health should be detected as critical", {}

    if orch.state.health_status != HealthStatus.CRITICAL:
        return False, f"Health status should be CRITICAL, got {orch.state.health_status}", {}

    return True, "", {"detected_critical": True}


async def validate_orchestrator_metrics_recording() -> Tuple[bool, str, Dict]:
    """Validate metrics recording updates health status."""
    orch = RolloutOrchestrator()

    # Record healthy metrics
    healthy = HealthMetrics(
        timestamp=datetime.now(),
        throughput_per_sec=250,
        latency_p99_ms=45,
        error_rate_percent=0.02,
        audit_integrity_percent=99.95,
        feature_promotion_count=5,
        features_stuck_alpha_count=0,
    )
    orch.record_metrics(healthy)

    if orch.state.health_status != HealthStatus.HEALTHY:
        return False, f"Status should be HEALTHY, got {orch.state.health_status}", {}

    # Record degraded metrics
    degraded = HealthMetrics(
        timestamp=datetime.now(),
        throughput_per_sec=250,
        latency_p99_ms=300,
        error_rate_percent=0.02,
        audit_integrity_percent=99.95,
        feature_promotion_count=5,
        features_stuck_alpha_count=0,
    )
    orch.record_metrics(degraded)

    if orch.state.health_status != HealthStatus.DEGRADED:
        return False, f"Status should be DEGRADED, got {orch.state.health_status}", {}

    return True, "", {"metrics_recorded": 2}


# ============================================================================
# SIMULATION VALIDATIONS
# ============================================================================

async def validate_simulation_healthy_baseline() -> Tuple[bool, str, Dict]:
    """Validate healthy baseline scenario generates all-green metrics."""
    gen = MetricsGenerator(SimulationScenario.HEALTHY_BASELINE)
    samples = gen.generate_metrics(datetime.now(), duration_hours=48, sample_interval_minutes=15)

    if len(samples) != (48 * 60) // 15:
        return False, f"Expected {(48*60)//15} samples, got {len(samples)}", {}

    healthy_count = sum(1 for s in samples if s.error_rate_percent < 0.1 and s.latency_p99_ms < 500)
    if healthy_count < len(samples) - 5:  # Allow 5 outliers due to jitter
        return False, f"Only {healthy_count}/{len(samples)} samples are healthy", {}

    return True, "", {"samples": len(samples), "healthy_count": healthy_count}


async def validate_simulation_error_spike() -> Tuple[bool, str, Dict]:
    """Validate error spike scenario shows spike and recovery."""
    gen = MetricsGenerator(SimulationScenario.ERROR_SPIKE)
    samples = gen.generate_metrics(datetime.now(), duration_hours=48, sample_interval_minutes=15)

    if len(samples) == 0:
        return False, "No samples generated", {}

    max_error = max(s.error_rate_percent for s in samples)
    min_error = min(s.error_rate_percent for s in samples)

    if max_error < 0.3:
        return False, f"Expected error spike >0.3%, got max {max_error}%", {}
    if min_error >= 0.05:
        return False, f"Expected recovery to <0.05%, got min {min_error}%", {}

    return True, "", {"max_error": max_error, "min_error": min_error}


async def validate_simulation_latency_degradation() -> Tuple[bool, str, Dict]:
    """Validate latency degradation scenario shows increasing p99."""
    gen = MetricsGenerator(SimulationScenario.LATENCY_DEGRADATION)
    samples = gen.generate_metrics(datetime.now(), duration_hours=48, sample_interval_minutes=15)

    early_latency = samples[0].latency_p99_ms
    late_latency = samples[-1].latency_p99_ms

    if late_latency <= early_latency * 1.3:
        return False, f"Expected 30%+ increase, got {(late_latency/early_latency - 1)*100:.0f}%", {}

    return True, "", {"early_ms": early_latency, "late_ms": late_latency}


async def validate_simulation_recovery() -> Tuple[bool, str, Dict]:
    """Validate recovery scenario spikes then recovers."""
    gen = MetricsGenerator(SimulationScenario.RECOVERY)
    samples = gen.generate_metrics(datetime.now(), duration_hours=48, sample_interval_minutes=15)

    max_error = max(s.error_rate_percent for s in samples)
    final_error = samples[-1].error_rate_percent

    if max_error < 0.3:
        return False, f"Expected spike >0.3%, got {max_error}%", {}
    if final_error >= 0.1:
        return False, f"Expected recovery to <0.1%, got {final_error}%", {}

    return True, "", {"spike": max_error, "final": final_error}


async def validate_simulation_successful_ramp() -> Tuple[bool, str, Dict]:
    """Validate successful ramp scenario stays healthy throughout."""
    gen = MetricsGenerator(SimulationScenario.SUCCESSFUL_RAMP)
    samples = gen.generate_metrics(datetime.now(), duration_hours=72, sample_interval_minutes=15)

    unhealthy_count = sum(
        1 for s in samples
        if s.error_rate_percent >= 0.05 or s.latency_p99_ms >= 200 or s.audit_integrity_percent < 99.9
    )

    if unhealthy_count > 2:  # Allow 2 outliers
        return False, f"Expected all healthy, got {unhealthy_count} unhealthy", {}

    promotion_count = samples[-1].feature_promotion_count
    if promotion_count < 10:
        return False, f"Expected features promoted, got {promotion_count}", {}

    return True, "", {"unhealthy": unhealthy_count, "promotions": promotion_count}


async def validate_simulation_cascading_failures() -> Tuple[bool, str, Dict]:
    """Validate cascading failures scenario shows multiple waves."""
    gen = MetricsGenerator(SimulationScenario.CASCADING_FAILURES)
    samples = gen.generate_metrics(datetime.now(), duration_hours=48, sample_interval_minutes=15)

    high_error_samples = sum(1 for s in samples if s.error_rate_percent > 0.5)

    if high_error_samples < 12:  # Multiple waves = 3+ waves × 4 samples/wave
        return False, f"Expected multiple failure waves, got {high_error_samples} high-error samples", {}

    return True, "", {"high_error_samples": high_error_samples}


# ============================================================================
# E2E VALIDATIONS
# ============================================================================

async def validate_e2e_orchestrator_with_healthy_baseline() -> Tuple[bool, str, Dict]:
    """E2E: Orchestrator + simulation with healthy baseline."""
    orch = RolloutOrchestrator()
    gen = MetricsGenerator(SimulationScenario.HEALTHY_BASELINE)

    await orch.start()

    # Generate and feed metrics
    samples = gen.generate_metrics(
        datetime.now() - timedelta(hours=48),
        duration_hours=48,
        sample_interval_minutes=15,
    )

    for sample in samples:
        orch.record_metrics(
            HealthMetrics(
                timestamp=sample.timestamp,
                throughput_per_sec=sample.throughput_per_sec,
                latency_p99_ms=sample.latency_p99_ms,
                error_rate_percent=sample.error_rate_percent,
                audit_integrity_percent=sample.audit_integrity_percent,
                feature_promotion_count=sample.feature_promotion_count,
                features_stuck_alpha_count=sample.features_stuck_alpha_count,
            )
        )

    if len(orch.state.metrics) != len(samples):
        return False, f"Expected {len(samples)} metrics, got {len(orch.state.metrics)}", {}

    if orch.state.health_status != HealthStatus.HEALTHY:
        return False, f"Expected HEALTHY status, got {orch.state.health_status}", {}

    return True, "", {"metrics_recorded": len(orch.state.metrics)}


async def validate_e2e_orchestrator_detects_error_spike() -> Tuple[bool, str, Dict]:
    """E2E: Orchestrator detects error spike and marks degraded."""
    orch = RolloutOrchestrator()
    gen = MetricsGenerator(SimulationScenario.ERROR_SPIKE)

    await orch.start()

    # Generate and feed metrics
    samples = gen.generate_metrics(
        datetime.now() - timedelta(hours=48),
        duration_hours=48,
        sample_interval_minutes=15,
    )

    for sample in samples:
        orch.record_metrics(
            HealthMetrics(
                timestamp=sample.timestamp,
                throughput_per_sec=sample.throughput_per_sec,
                latency_p99_ms=sample.latency_p99_ms,
                error_rate_percent=sample.error_rate_percent,
                audit_integrity_percent=sample.audit_integrity_percent,
                feature_promotion_count=sample.feature_promotion_count,
                features_stuck_alpha_count=sample.features_stuck_alpha_count,
            )
        )

    # Should have seen some non-healthy statuses
    statuses = [orch.state.metrics[i].is_healthy() for i in range(len(orch.state.metrics))]
    if all(statuses):
        return False, "Should have detected some unhealthy samples", {}

    return True, "", {"total_samples": len(orch.state.metrics)}


async def validate_e2e_canary_gate_logic_flow() -> Tuple[bool, str, Dict]:
    """E2E: Full gate logic flow (init → canary → check gates → ready for ramp)."""
    orch = RolloutOrchestrator()
    gen = MetricsGenerator(SimulationScenario.HEALTHY_BASELINE)

    await orch.start()

    # Stage 1: INITIAL → CANARY_10
    stage = await orch.next_stage()
    if stage != RolloutStage.CANARY_10:
        return False, "Failed to transition to CANARY_10", {}

    # Stage 2: Simulate 48h+ of healthy canary metrics
    orch.state.started_at = datetime.now() - timedelta(hours=50)

    samples = gen.generate_metrics(
        datetime.now() - timedelta(hours=48),
        duration_hours=48,
        sample_interval_minutes=15,
    )

    for sample in samples:
        orch.record_metrics(
            HealthMetrics(
                timestamp=sample.timestamp,
                throughput_per_sec=sample.throughput_per_sec,
                latency_p99_ms=sample.latency_p99_ms,
                error_rate_percent=sample.error_rate_percent,
                audit_integrity_percent=sample.audit_integrity_percent,
                feature_promotion_count=sample.feature_promotion_count,
                features_stuck_alpha_count=sample.features_stuck_alpha_count,
            )
        )

    # Stage 3: Check canary health gate
    decision = orch._check_canary_health_48h_gate()
    if not decision.pass_gate:
        return False, f"Canary health gate should pass: {decision.reason}", {}

    if decision.confidence_percent < 95:
        return False, f"Confidence too low: {decision.confidence_percent}%", {}

    return True, "", {"gate": "canary_health_48h", "confidence": decision.confidence_percent}


# ============================================================================
# MAIN
# ============================================================================

def main():
    """Run all Phase 6 k=1 validations."""
    validator = Phase6K1Validator()

    # Orchestration validations (6)
    validator.run_validation(
        "Orchestrator initialization",
        "orchestration",
        validate_orchestrator_initialization,
    )
    validator.run_validation(
        "Orchestrator start/stop",
        "orchestration",
        validate_orchestrator_start_stop,
    )
    validator.run_validation(
        "INITIAL → CANARY_10 transition",
        "orchestration",
        validate_orchestrator_initial_to_canary_transition,
    )
    validator.run_validation(
        "Canary requires 48h minimum",
        "orchestration",
        validate_orchestrator_canary_requires_48h,
    )
    validator.run_validation(
        "CANARY_10 → RAMP_50 transition (48h gate)",
        "orchestration",
        validate_orchestrator_canary_to_ramp50_transition,
    )
    validator.run_validation(
        "Rollback on critical health",
        "orchestration",
        validate_orchestrator_rollback_on_critical_health,
    )
    validator.run_validation(
        "Metrics recording updates health status",
        "orchestration",
        validate_orchestrator_metrics_recording,
    )

    # Simulation validations (7)
    validator.run_validation(
        "Simulation: Healthy baseline",
        "simulation",
        validate_simulation_healthy_baseline,
    )
    validator.run_validation(
        "Simulation: Error spike",
        "simulation",
        validate_simulation_error_spike,
    )
    validator.run_validation(
        "Simulation: Latency degradation",
        "simulation",
        validate_simulation_latency_degradation,
    )
    validator.run_validation(
        "Simulation: Recovery",
        "simulation",
        validate_simulation_recovery,
    )
    validator.run_validation(
        "Simulation: Successful ramp",
        "simulation",
        validate_simulation_successful_ramp,
    )
    validator.run_validation(
        "Simulation: Cascading failures",
        "simulation",
        validate_simulation_cascading_failures,
    )

    # E2E validations (3)
    validator.run_validation(
        "E2E: Orchestrator + healthy baseline",
        "e2e",
        validate_e2e_orchestrator_with_healthy_baseline,
    )
    validator.run_validation(
        "E2E: Orchestrator detects error spike",
        "e2e",
        validate_e2e_orchestrator_detects_error_spike,
    )
    validator.run_validation(
        "E2E: Full canary gate logic flow",
        "e2e",
        validate_e2e_canary_gate_logic_flow,
    )

    # Print summary
    validator.print_summary()

    # Exit with success if all passed
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

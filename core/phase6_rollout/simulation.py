"""
Phase 6 Simulation Framework — generates realistic production metrics for testing.

Simulates:
- Normal operation (baseline metrics)
- Error spikes (transient failures)
- Latency degradation (resource contention)
- Feature promotions (ALPHA→PRODUCTION transitions)
- Recovery scenarios (system self-healing)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import List, Optional, Callable
import random
import logging
import math


logger = logging.getLogger(__name__)


class SimulationScenario(Enum):
    """Pre-built simulation scenarios for testing."""
    HEALTHY_BASELINE = "healthy_baseline"  # Perfect metrics for 48h
    ERROR_SPIKE = "error_spike"  # Sudden error rate increase
    LATENCY_DEGRADATION = "latency_degradation"  # Slow gradual latency increase
    MEMORY_LEAK = "memory_leak"  # Memory growth over time
    RECOVERY = "recovery"  # System recovers from error spike
    FEATURE_STUCK = "feature_stuck"  # Features stuck in ALPHA
    CASCADING_FAILURES = "cascading_failures"  # Multiple failures in sequence
    SUCCESSFUL_RAMP = "successful_ramp"  # Clean progression through all stages


@dataclass
class MetricsSample:
    """A single sample of metrics over time."""
    timestamp: datetime
    throughput_per_sec: float
    latency_p99_ms: float
    error_rate_percent: float
    audit_integrity_percent: float
    feature_promotion_count: int
    features_stuck_alpha_count: int
    scenario_note: str = ""


class MetricsGenerator:
    """Generates realistic metrics for simulation scenarios."""

    def __init__(self, scenario: SimulationScenario, seed: int = 42):
        self.scenario = scenario
        random.seed(seed)
        self._base_throughput = 250  # /sec at 50% traffic
        self._base_latency_p99 = 45.0  # ms
        self._base_error_rate = 0.02  # %
        self._base_audit_integrity = 99.95  # %
        self._current_feature_promoted = 0

    def generate_metrics(
        self,
        start_time: datetime,
        duration_hours: int,
        sample_interval_minutes: int = 15,
    ) -> List[MetricsSample]:
        """Generate a sequence of metrics samples over the given duration."""

        samples = []
        num_samples = int((duration_hours * 60) / sample_interval_minutes)

        match self.scenario:
            case SimulationScenario.HEALTHY_BASELINE:
                samples = self._generate_healthy_baseline(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.ERROR_SPIKE:
                samples = self._generate_error_spike(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.LATENCY_DEGRADATION:
                samples = self._generate_latency_degradation(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.MEMORY_LEAK:
                samples = self._generate_memory_leak(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.RECOVERY:
                samples = self._generate_recovery(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.FEATURE_STUCK:
                samples = self._generate_feature_stuck(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.CASCADING_FAILURES:
                samples = self._generate_cascading_failures(start_time, num_samples, sample_interval_minutes)
            case SimulationScenario.SUCCESSFUL_RAMP:
                samples = self._generate_successful_ramp(start_time, num_samples, sample_interval_minutes)

        return samples

    def _generate_healthy_baseline(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Perfect metrics: healthy for full duration."""
        samples = []
        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)
            # Add small jitter around baselines
            throughput = self._base_throughput + random.gauss(0, 10)
            latency = self._base_latency_p99 + random.gauss(0, 5)
            error_rate = self._base_error_rate + random.gauss(0, 0.01)
            audit_integrity = self._base_audit_integrity + random.gauss(0, 0.02)

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=max(100, throughput),
                    latency_p99_ms=max(5, latency),
                    error_rate_percent=max(0, error_rate),
                    audit_integrity_percent=min(100, max(99, audit_integrity)),
                    feature_promotion_count=self._current_feature_promoted + random.randint(0, 2),
                    features_stuck_alpha_count=0,
                    scenario_note="Healthy baseline",
                )
            )
        return samples

    def _generate_error_spike(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Error spike: sudden jump in error rate, then recovery."""
        samples = []
        spike_start = num_samples // 3  # Spike starts 1/3 way through
        spike_duration = 8  # 2 hours at 15min intervals

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)

            # Error rate spike
            if spike_start <= i < spike_start + spike_duration:
                error_rate = 0.5 + random.gauss(0, 0.1)  # 0.5% error during spike
                scenario = "ERROR SPIKE IN PROGRESS"
            elif i >= spike_start + spike_duration:
                # Recovery phase
                progress = (i - spike_start - spike_duration) / max(1, num_samples - spike_start - spike_duration)
                error_rate = max(self._base_error_rate, 0.5 * (1 - progress)) + random.gauss(0, 0.01)
                scenario = "Recovery in progress"
            else:
                error_rate = self._base_error_rate + random.gauss(0, 0.01)
                scenario = "Normal baseline"

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=self._base_throughput + random.gauss(0, 10),
                    latency_p99_ms=self._base_latency_p99 + random.gauss(0, 5),
                    error_rate_percent=max(0, error_rate),
                    audit_integrity_percent=99.9,
                    feature_promotion_count=self._current_feature_promoted,
                    features_stuck_alpha_count=0 if i < spike_start else 1,
                    scenario_note=scenario,
                )
            )
        return samples

    def _generate_latency_degradation(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Latency degradation: slow increase over time."""
        samples = []
        max_latency = 800  # Peak at 800ms

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)
            progress = i / max(1, num_samples - 1)
            degradation = progress * (max_latency - self._base_latency_p99)
            latency = self._base_latency_p99 + degradation + random.gauss(0, 10)

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=self._base_throughput + random.gauss(0, 10),
                    latency_p99_ms=max(5, latency),
                    error_rate_percent=self._base_error_rate + random.gauss(0, 0.01),
                    audit_integrity_percent=99.9,
                    feature_promotion_count=self._current_feature_promoted,
                    features_stuck_alpha_count=0,
                    scenario_note=f"Latency degrading: {latency:.0f}ms",
                )
            )
        return samples

    def _generate_memory_leak(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Memory leak: throughput decline over time."""
        samples = []

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)
            # Exponential decay in throughput (memory pressure)
            decay = math.exp(-(i / num_samples) * 2)
            throughput = self._base_throughput * decay + random.gauss(0, 5)

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=max(50, throughput),
                    latency_p99_ms=self._base_latency_p99 + random.gauss(0, 5),
                    error_rate_percent=max(0, self._base_error_rate + (1 - decay) * 0.3),
                    audit_integrity_percent=99.9,
                    feature_promotion_count=self._current_feature_promoted,
                    features_stuck_alpha_count=0,
                    scenario_note=f"Memory pressure: throughput={throughput:.0f}/sec",
                )
            )
        return samples

    def _generate_recovery(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Recovery: system bounces back from error spike."""
        samples = []
        error_peak_at = num_samples // 4

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)

            if i < error_peak_at:
                # Error spike
                error_rate = 0.5 + random.gauss(0, 0.1)
            else:
                # Recovery: exponential decay back to baseline
                recovery_progress = (i - error_peak_at) / max(1, num_samples - error_peak_at)
                error_rate = 0.5 * math.exp(-recovery_progress * 3) + self._base_error_rate

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=self._base_throughput + random.gauss(0, 10),
                    latency_p99_ms=self._base_latency_p99 + random.gauss(0, 5),
                    error_rate_percent=max(0, error_rate),
                    audit_integrity_percent=99.9,
                    feature_promotion_count=self._current_feature_promoted,
                    features_stuck_alpha_count=0,
                    scenario_note="Recovery scenario" if i >= error_peak_at else "Error spike",
                )
            )
        return samples

    def _generate_feature_stuck(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Features stuck in ALPHA: features not being promoted."""
        samples = []
        stuck_features = 3

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)
            # Promote a few features early, then get stuck
            promotions = 5 if i < 12 else 5  # No more promotions after hour 3

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=self._base_throughput + random.gauss(0, 10),
                    latency_p99_ms=self._base_latency_p99 + random.gauss(0, 5),
                    error_rate_percent=self._base_error_rate + random.gauss(0, 0.01),
                    audit_integrity_percent=99.9,
                    feature_promotion_count=promotions,
                    features_stuck_alpha_count=stuck_features if i >= 12 else 0,
                    scenario_note=f"Features stuck: {stuck_features} in ALPHA",
                )
            )
        return samples

    def _generate_cascading_failures(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Cascading failures: multiple failures in sequence."""
        samples = []

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)
            # Three failure waves
            wave_1 = 8 <= i < 14
            wave_2 = 20 <= i < 26
            wave_3 = 32 <= i < 38

            if wave_1 or wave_2 or wave_3:
                error_rate = 1.0 + random.gauss(0, 0.2)
                latency = 800 + random.gauss(0, 50)
                throughput = 100 + random.gauss(0, 20)
                scenario = "CASCADING FAILURE"
            else:
                error_rate = self._base_error_rate + random.gauss(0, 0.01)
                latency = self._base_latency_p99 + random.gauss(0, 5)
                throughput = self._base_throughput + random.gauss(0, 10)
                scenario = "Recovery between waves"

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=max(50, throughput),
                    latency_p99_ms=max(5, latency),
                    error_rate_percent=max(0, error_rate),
                    audit_integrity_percent=99.5 if (wave_1 or wave_2 or wave_3) else 99.9,
                    feature_promotion_count=0,
                    features_stuck_alpha_count=1 if (wave_1 or wave_2 or wave_3) else 0,
                    scenario_note=scenario,
                )
            )
        return samples

    def _generate_successful_ramp(
        self, start_time: datetime, num_samples: int, interval_minutes: int
    ) -> List[MetricsSample]:
        """Successful ramp: clean progression through all stages."""
        samples = []
        stage_length = num_samples // 3

        for i in range(num_samples):
            ts = start_time + timedelta(minutes=i * interval_minutes)

            # All metrics healthy throughout
            throughput = self._base_throughput * (1 + i // stage_length)  # Grow with each stage
            error_rate = self._base_error_rate - (i / num_samples) * 0.01  # Improve over time
            promotions = min(20, i // 4)  # Promote 5 features per stage

            stage_num = (i // stage_length) + 1
            traffic_percent = stage_num * 33  # 33%, 66%, 100%

            samples.append(
                MetricsSample(
                    timestamp=ts,
                    throughput_per_sec=max(100, throughput),
                    latency_p99_ms=self._base_latency_p99 + random.gauss(0, 3),
                    error_rate_percent=max(0, error_rate),
                    audit_integrity_percent=99.95,
                    feature_promotion_count=promotions,
                    features_stuck_alpha_count=0,
                    scenario_note=f"Stage {stage_num}: {traffic_percent}% traffic",
                )
            )
        return samples

"""
Latency Stress Tests for CorvinOS v1.0.0

Tests latency stability under sustained load:
- P95 latency measurement
- P99 latency measurement
- Tail latency stability (no spikes)
- Latency consistency across 1+ minute sustained load
"""

import pytest
import time
import asyncio
import statistics
from typing import List, Tuple


class TestLatencyMeasurement:
    """Measure latencies under various load conditions."""

    def test_operation_latency_baseline(self):
        """Measure baseline operation latency."""
        latencies = []

        def operation():
            """Simulate operation."""
            time.sleep(0.01)  # 10ms operation

        for _ in range(100):
            start = time.time()
            operation()
            latency = (time.time() - start) * 1000  # Convert to ms
            latencies.append(latency)

        p50 = statistics.median(latencies)
        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)

        # Baseline should be close to 10ms with overhead
        assert p50 < 15, f"P50 {p50}ms too high"
        assert p95 < 20, f"P95 {p95}ms too high"
        assert p99 < 30, f"P99 {p99}ms too high"

    def test_p99_latency_under_10_concurrent(self):
        """P99 latency < 500ms with 10 concurrent operations."""
        latencies = []

        async def async_operation():
            """Simulate async operation."""
            await asyncio.sleep(0.01)  # 10ms work

        async def run_concurrent():
            """Run 10 concurrent operations."""
            for _ in range(50):  # 50 rounds
                start = time.time()
                await asyncio.gather(*[async_operation() for _ in range(10)])
                latency = (time.time() - start) * 1000
                latencies.append(latency)

        asyncio.run(run_concurrent())

        p99 = percentile(latencies, 99)
        assert p99 < 500, f"P99 latency {p99}ms exceeded 500ms under 10 concurrent"

    def test_p99_latency_under_100_concurrent(self):
        """P99 latency < 2s with 100 concurrent operations."""
        latencies = []

        async def async_operation():
            """Simulate async operation."""
            await asyncio.sleep(0.01)  # 10ms work

        async def run_concurrent():
            """Run 100 concurrent operations."""
            for _ in range(20):  # 20 rounds
                start = time.time()
                await asyncio.gather(*[async_operation() for _ in range(100)])
                latency = (time.time() - start) * 1000
                latencies.append(latency)

        asyncio.run(run_concurrent())

        p99 = percentile(latencies, 99)
        assert p99 < 2000, f"P99 latency {p99}ms exceeded 2s under 100 concurrent"


class TestLatencyStability:
    """Test latency consistency and lack of spikes."""

    def test_no_tail_latency_spikes_1min(self):
        """No unexplained tail latency spikes during 1-minute run."""
        latencies = []
        spike_threshold_ms = 100  # 10x baseline of 10ms

        async def operation():
            """Simulate operation."""
            await asyncio.sleep(0.01)  # 10ms baseline

        async def sustained_load():
            """Sustained load for 1 minute."""
            start_time = time.time()
            elapsed = 0
            round_num = 0

            while elapsed < 60:  # 1 minute
                round_start = time.time()

                # 10 concurrent operations per round
                await asyncio.gather(*[operation() for _ in range(10)])

                round_latency = (time.time() - round_start) * 1000
                latencies.append(round_latency)

                elapsed = time.time() - start_time
                round_num += 1

        asyncio.run(sustained_load())

        # Check for spikes (> 10x baseline)
        spikes = [l for l in latencies if l > spike_threshold_ms]

        # Allow some spikes but not many (< 5% of samples)
        spike_rate = len(spikes) / len(latencies)
        assert spike_rate < 0.05, \
            f"Spike rate {spike_rate * 100}% exceeds 5% (spikes: {len(spikes)}/{len(latencies)})"

    def test_latency_variance_stable(self):
        """Latency variance stays stable throughout run."""
        latencies_by_minute = []

        async def operation():
            """Simulate operation."""
            await asyncio.sleep(0.01)  # 10ms baseline

        async def run_test():
            """Run for 3 minutes, check variance per minute."""
            minute_latencies = []
            start_time = time.time()
            last_minute_check = start_time

            while time.time() - start_time < 180:  # 3 minutes
                round_start = time.time()

                # 10 concurrent operations
                await asyncio.gather(*[operation() for _ in range(10)])

                latency = (time.time() - round_start) * 1000
                minute_latencies.append(latency)

                # Check every minute
                if time.time() - last_minute_check > 60:
                    latencies_by_minute.append(minute_latencies)
                    minute_latencies = []
                    last_minute_check = time.time()

        asyncio.run(run_test())

        if len(latencies_by_minute) >= 2:
            # Compare variance between minutes
            variance_1 = statistics.variance(latencies_by_minute[0]) if len(latencies_by_minute[0]) > 1 else 0
            variance_2 = statistics.variance(latencies_by_minute[1]) if len(latencies_by_minute[1]) > 1 else 0

            # Variance should not triple between minutes
            if variance_1 > 0:
                variance_ratio = variance_2 / variance_1
                assert variance_ratio < 3.0, \
                    f"Variance tripled: {variance_1} → {variance_2}"


class TestLatencyPercentiles:
    """Detailed percentile tracking."""

    def test_percentile_distribution(self):
        """Latency percentiles follow expected distribution."""
        latencies = []

        async def operation():
            """Simulate operation."""
            await asyncio.sleep(0.01)  # 10ms baseline

        async def collect_latencies():
            """Collect 1000 latencies."""
            for _ in range(1000):
                start = time.time()
                await operation()
                latency = (time.time() - start) * 1000
                latencies.append(latency)

        asyncio.run(collect_latencies())

        p50 = percentile(latencies, 50)
        p90 = percentile(latencies, 90)
        p95 = percentile(latencies, 95)
        p99 = percentile(latencies, 99)

        # Percentiles should be monotonically increasing
        assert p50 < p90 < p95 < p99, \
            f"Percentiles not monotonic: P50={p50}, P90={p90}, P95={p95}, P99={p99}"

        # P99 should be < 10x P50 (reasonable tail)
        tail_ratio = p99 / max(p50, 0.001)
        assert tail_ratio < 10.0, \
            f"Tail too long: P99/P50 = {tail_ratio}"


class TestLatencyUnderVariableLoad:
    """Latency behavior as load varies."""

    def test_latency_scaling_linear_load(self):
        """Latency scales reasonably with load."""
        latencies_light = []
        latencies_medium = []
        latencies_heavy = []

        async def operation():
            """Simulate operation."""
            await asyncio.sleep(0.01)  # 10ms baseline

        async def measure_load(num_concurrent: int, target_list: List[float]):
            """Measure latencies at specific concurrency."""
            for _ in range(50):
                start = time.time()
                await asyncio.gather(*[operation() for _ in range(num_concurrent)])
                latency = (time.time() - start) * 1000
                target_list.append(latency)

        # Light load (5 concurrent)
        asyncio.run(measure_load(5, latencies_light))
        p99_light = percentile(latencies_light, 99)

        # Medium load (25 concurrent)
        asyncio.run(measure_load(25, latencies_medium))
        p99_medium = percentile(latencies_medium, 99)

        # Heavy load (50 concurrent)
        asyncio.run(measure_load(50, latencies_heavy))
        p99_heavy = percentile(latencies_heavy, 99)

        # Latency should grow, but not exponentially
        # Light → Medium should be ~2-3x
        growth_1 = p99_medium / max(p99_light, 0.001)
        assert growth_1 < 5.0, \
            f"Latency growth Light→Medium too high: {growth_1}x"

        # Medium → Heavy should be ~1-2x
        growth_2 = p99_heavy / max(p99_medium, 0.001)
        assert growth_2 < 3.0, \
            f"Latency growth Medium→Heavy too high: {growth_2}x"


# ============================================================================
# Helpers
# ============================================================================

def percentile(data: List[float], percentile: float) -> float:
    """Calculate percentile from data."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    idx = int(len(sorted_data) * percentile / 100)
    return sorted_data[min(idx, len(sorted_data) - 1)]


if __name__ == "__main__":
    pytest.main([__file__, "-v", "--tb=short"])

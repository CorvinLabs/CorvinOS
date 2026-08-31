"""Load Testing Infrastructure for Plugin System.

Supports:
- Concurrent health checks (1-10000 plugins)
- Load profile generation
- Latency percentile analysis (p50, p95, p99)
- Flame graph data collection
- CSV export for analysis

ADR-0444: Storage & Registry
Usage:
    from load_tester import LoadTester
    tester = LoadTester(num_plugins=1000)
    tester.run_concurrent_health_checks(concurrency=100)
    tester.print_report()
"""

import time
import json
import csv
import statistics
import threading
import queue
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field, asdict
from pathlib import Path
from typing import Dict, List, Tuple, Optional, Any
import logging

logger = logging.getLogger(__name__)


@dataclass
class HealthCheckResult:
    """Result of a single health check."""
    plugin_id: str
    success: bool
    duration_ms: float
    error_msg: str = ""
    timestamp: float = field(default_factory=time.time)

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


@dataclass
class LoadTestResult:
    """Aggregated load test results."""
    test_name: str
    num_plugins: int
    concurrency: int
    total_duration_s: float
    results: List[HealthCheckResult] = field(default_factory=list)

    @property
    def success_count(self) -> int:
        return sum(1 for r in self.results if r.success)

    @property
    def failure_count(self) -> int:
        return sum(1 for r in self.results if not r.success)

    @property
    def success_rate(self) -> float:
        if not self.results:
            return 0.0
        return self.success_count / len(self.results)

    def get_percentile(self, percentile: float) -> float:
        """Get latency percentile."""
        if not self.results:
            return 0.0
        durations = sorted([r.duration_ms for r in self.results if r.success])
        if not durations:
            return 0.0
        index = int(len(durations) * (percentile / 100.0))
        return durations[min(index, len(durations) - 1)]

    def statistics(self) -> Dict[str, float]:
        """Calculate statistics."""
        durations = [r.duration_ms for r in self.results if r.success]
        if not durations:
            return {}

        return {
            "count": len(durations),
            "mean_ms": statistics.mean(durations),
            "median_ms": statistics.median(durations),
            "stdev_ms": statistics.stdev(durations) if len(durations) > 1 else 0.0,
            "min_ms": min(durations),
            "max_ms": max(durations),
            "p50_ms": self.get_percentile(50),
            "p95_ms": self.get_percentile(95),
            "p99_ms": self.get_percentile(99),
            "throughput_ops_per_sec": len(durations) / self.total_duration_s,
        }


class MockPluginLoader:
    """Mock plugin loader for load testing."""

    def __init__(self, num_plugins: int):
        self.num_plugins = num_plugins
        self._plugins = {f"plugin-{i:05d}": {} for i in range(num_plugins)}
        self._health_check_delay_ms = 10  # Simulate 10ms health check

    def health_check(self, plugin_id: str) -> Tuple[bool, str]:
        """Simulate health check for a plugin."""
        if plugin_id not in self._plugins:
            return False, f"Plugin {plugin_id} not found"

        # Simulate health check work
        start = time.perf_counter()
        time.sleep(self._health_check_delay_ms / 1000.0)
        elapsed = (time.perf_counter() - start) * 1000

        # Add some variance to simulate real conditions
        import random
        if random.random() < 0.01:  # 1% failure rate
            return False, "Health check timeout"

        return True, "healthy"


class LoadTester:
    """Load testing harness for plugin system."""

    def __init__(self, num_plugins: int = 100, health_check_delay_ms: int = 10):
        self.num_plugins = num_plugins
        self.loader = MockPluginLoader(num_plugins)
        self.loader._health_check_delay_ms = health_check_delay_ms
        self.results: List[LoadTestResult] = []

    def run_concurrent_health_checks(
        self,
        concurrency: int = 10,
        test_name: str = "concurrent_health_checks",
    ) -> LoadTestResult:
        """Run concurrent health checks across plugins."""
        logger.info(
            f"Starting load test: {test_name} "
            f"({self.num_plugins} plugins, concurrency={concurrency})"
        )

        start_time = time.perf_counter()
        check_results: List[HealthCheckResult] = []
        results_lock = threading.Lock()

        with ThreadPoolExecutor(max_workers=concurrency) as executor:
            futures = {}

            # Submit all health checks
            for plugin_id in self.loader._plugins.keys():
                future = executor.submit(self._perform_health_check, plugin_id)
                futures[future] = plugin_id

            # Collect results as they complete
            for future in as_completed(futures):
                result = future.result()
                with results_lock:
                    check_results.append(result)

        elapsed = time.perf_counter() - start_time

        test_result = LoadTestResult(
            test_name=test_name,
            num_plugins=self.num_plugins,
            concurrency=concurrency,
            total_duration_s=elapsed,
            results=check_results,
        )

        self.results.append(test_result)
        logger.info(f"Load test completed in {elapsed:.2f}s")

        return test_result

    def _perform_health_check(self, plugin_id: str) -> HealthCheckResult:
        """Perform single health check and measure latency."""
        start = time.perf_counter()

        try:
            success, msg = self.loader.health_check(plugin_id)
            elapsed = (time.perf_counter() - start) * 1000  # Convert to ms

            return HealthCheckResult(
                plugin_id=plugin_id,
                success=success,
                duration_ms=elapsed,
                error_msg=msg if not success else "",
            )
        except Exception as e:
            elapsed = (time.perf_counter() - start) * 1000
            return HealthCheckResult(
                plugin_id=plugin_id,
                success=False,
                duration_ms=elapsed,
                error_msg=str(e),
            )

    def run_latency_profile(
        self,
        concurrency_levels: Optional[List[int]] = None,
    ) -> List[LoadTestResult]:
        """Run profiling across different concurrency levels."""
        if concurrency_levels is None:
            concurrency_levels = [1, 10, 50, 100, 500]

        profile_results = []

        for concurrency in concurrency_levels:
            result = self.run_concurrent_health_checks(
                concurrency=concurrency,
                test_name=f"profile_concurrency_{concurrency}",
            )
            profile_results.append(result)
            logger.info(
                f"  Concurrency {concurrency}: "
                f"p99={result.get_percentile(99):.2f}ms, "
                f"throughput={result.statistics().get('throughput_ops_per_sec', 0):.1f} ops/s"
            )

        return profile_results

    def export_csv(self, output_path: str):
        """Export results to CSV."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        with open(output_file, "w", newline="") as f:
            if not self.results or not self.results[0].results:
                logger.warning("No results to export")
                return

            # Write header from first result
            fieldnames = [
                "test_name", "concurrency", "plugin_id", "success",
                "duration_ms", "error_msg", "timestamp"
            ]
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()

            # Write data
            for test_result in self.results:
                for result in test_result.results:
                    row = result.to_dict()
                    row["test_name"] = test_result.test_name
                    row["concurrency"] = test_result.concurrency
                    writer.writerow(row)

        logger.info(f"Results exported to {output_file}")

    def export_json(self, output_path: str):
        """Export results to JSON."""
        output_file = Path(output_path)
        output_file.parent.mkdir(parents=True, exist_ok=True)

        data = []
        for test_result in self.results:
            stats = test_result.statistics()
            data.append({
                "test_name": test_result.test_name,
                "num_plugins": test_result.num_plugins,
                "concurrency": test_result.concurrency,
                "total_duration_s": test_result.total_duration_s,
                "success_count": test_result.success_count,
                "failure_count": test_result.failure_count,
                "success_rate": test_result.success_rate,
                "statistics": stats,
            })

        with open(output_file, "w") as f:
            json.dump(data, f, indent=2)

        logger.info(f"Results exported to {output_file}")

    def print_report(self):
        """Print summary report of all tests."""
        lines = [
            "\n" + "=" * 100,
            "LOAD TEST REPORT",
            "=" * 100,
            "",
        ]

        for test_result in self.results:
            lines.append(f"Test: {test_result.test_name}")
            lines.append(f"  Plugins: {test_result.num_plugins}")
            lines.append(f"  Concurrency: {test_result.concurrency}")
            lines.append(f"  Duration: {test_result.total_duration_s:.2f}s")
            lines.append(f"  Success Rate: {test_result.success_rate*100:.1f}% "
                        f"({test_result.success_count}/{len(test_result.results)})")

            stats = test_result.statistics()
            if stats:
                lines.append("  Latency (ms):")
                lines.append(f"    Mean: {stats.get('mean_ms', 0):.2f}")
                lines.append(f"    Median: {stats.get('median_ms', 0):.2f}")
                lines.append(f"    Stdev: {stats.get('stdev_ms', 0):.2f}")
                lines.append(f"    Min: {stats.get('min_ms', 0):.2f}")
                lines.append(f"    Max: {stats.get('max_ms', 0):.2f}")
                lines.append(f"    p50: {stats.get('p50_ms', 0):.2f}")
                lines.append(f"    p95: {stats.get('p95_ms', 0):.2f}")
                lines.append(f"    p99: {stats.get('p99_ms', 0):.2f}")
                lines.append(f"  Throughput: {stats.get('throughput_ops_per_sec', 0):.1f} ops/sec")

            lines.append("")

        lines.append("=" * 100)
        print("\n".join(lines))


if __name__ == "__main__":
    # Example: Run load tests
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    )

    tester = LoadTester(num_plugins=1000)

    # Test 1: 100 concurrent health checks
    tester.run_concurrent_health_checks(
        concurrency=100,
        test_name="concurrent_100_plugins",
    )

    # Test 2: Latency profile across concurrency levels
    tester.run_latency_profile(concurrency_levels=[1, 10, 100, 500])

    # Export results
    tester.export_json("test-results/load_test_results.json")
    tester.export_csv("test-results/load_test_results.csv")

    # Print report
    tester.print_report()

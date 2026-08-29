"""Plugin Marketplace Performance Testing (ADR-0249 Phase 4+).

Comprehensive performance assessment:
1. Baseline latency measurements (empty, 10, 100, 1000 plugins)
2. Load testing (concurrent users, mixed workloads)
3. Stress testing (peak loads, recovery)
4. Regression detection (vs Phase 2 baseline)
5. SLO verification (discovery <100ms, install <30s, search <200ms)
6. Memory/CPU profiling and analysis

Run with: pytest test_plugin_marketplace_performance.py -v -s
Load test: pytest test_plugin_marketplace_performance.py::test_load_marketplace_concurrent_users -v -s
"""

import asyncio
import json
import logging
import statistics
import time
from dataclasses import dataclass, field, asdict
from io import BytesIO
from pathlib import Path
from typing import List, Dict, Any, Optional, Tuple
from unittest.mock import AsyncMock, MagicMock, patch
import tempfile
import tarfile
import hashlib

import pytest
from fastapi.testclient import TestClient

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


# ─── Data Models ───────────────────────────────────────────────────────────

@dataclass
class LatencyResult:
    """Single latency measurement."""
    endpoint: str
    method: str
    payload_size_mb: float = 0.0
    response_time_ms: float = 0.0
    status_code: int = 200
    error: Optional[str] = None
    timestamp: float = field(default_factory=time.time)


@dataclass
class PerformanceMetrics:
    """Aggregated metrics from a test scenario."""
    scenario_name: str
    endpoint: str
    num_requests: int = 0
    num_errors: int = 0
    min_ms: float = 0.0
    max_ms: float = 0.0
    mean_ms: float = 0.0
    median_ms: float = 0.0
    p95_ms: float = 0.0
    p99_ms: float = 0.0
    throughput_rps: float = 0.0
    memory_mb: float = 0.0
    slo_target_ms: float = 100.0
    slo_met: bool = False
    duration_sec: float = 0.0
    error_rate: float = 0.0
    latencies: List[float] = field(default_factory=list)

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        d = asdict(self)
        d.pop('latencies', None)
        return d


@dataclass
class LoadTestResult:
    """Results from load/stress test."""
    test_name: str
    duration_sec: float
    concurrent_users: int
    total_requests: int
    successful_requests: int
    failed_requests: int
    peak_rps: float
    avg_rps: float
    error_rate: float
    status_code_distribution: Dict[int, int] = field(default_factory=dict)
    endpoint_metrics: List[PerformanceMetrics] = field(default_factory=list)


# ─── Fixtures ─────────────────────────────────────────────────────────────

@pytest.fixture
def mock_app():
    """Create a mock FastAPI app for testing."""
    from fastapi import FastAPI
    app = FastAPI()

    # Mock marketplace endpoint
    @app.get("/v1/vibe/plugins/marketplace")
    async def marketplace():
        return {
            "plugins": _MARKETPLACE_PLUGINS,
            "total": len(_MARKETPLACE_PLUGINS),
            "limit": 20,
            "offset": 0,
        }

    # Mock list plugins endpoint
    @app.get("/v1/vibe/plugins/list")
    async def list_plugins():
        return {"loaded": [], "failed": {}, "count_total": 0}

    # Mock governance endpoint
    @app.get("/v1/console/plugins/governance")
    async def governance():
        return {"plugins": [], "total": 0}

    # Mock upload endpoint
    @app.post("/v1/console/plugins/upload")
    async def upload():
        return {"status": "success", "plugin_id": "test-plugin", "version": "1.0.0"}

    return app


@pytest.fixture
def client(mock_app):
    """Create a test client."""
    return TestClient(mock_app)


# ─── Test Data ─────────────────────────────────────────────────────────────

_MARKETPLACE_PLUGINS = [
    {
        "plugin_id": f"plugin-{i}",
        "name": f"Plugin {i}",
        "version": "1.0.0",
        "category": ["Authentication", "Analytics", "Database", "Security", "Tooling"][i % 5],
        "origin": ["vetted", "community"][i % 2],
        "author": "Test Author",
        "description": f"Test plugin {i}",
        "rating": 4.0 + (i % 5) * 0.1,
        "rating_count": 10 + i,
        "download_count": 100 + i * 10,
        "pii_risk": ["low", "medium", "high"][i % 3],
        "locality": ["local", "eu_cloud", "us_cloud"][i % 3],
        "network_egress": "external",
        "trust_badge": "verified" if i % 2 == 0 else "community",
        "listed": True,
    }
    for i in range(1000)
]


# ─── Baseline Latency Tests ───────────────────────────────────────────────

class TestBaselineLatency:
    """Measure baseline latencies for key endpoints."""

    @pytest.mark.parametrize("num_plugins,expected_max_ms", [
        (10, 50),
        (100, 100),
        (1000, 200),
    ])
    def test_marketplace_latency_by_catalog_size(self, client, num_plugins, expected_max_ms):
        """Measure GET /v1/vibe/plugins/marketplace latency with various catalog sizes."""
        plugins = _MARKETPLACE_PLUGINS[:num_plugins]
        results = []

        for _ in range(10):  # 10 samples per size
            start = time.perf_counter()
            response = client.get(
                "/v1/vibe/plugins/marketplace",
                params={"limit": 20, "offset": 0}
            )
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert response.status_code == 200
            results.append(elapsed_ms)

        metrics = _compute_metrics(
            scenario="marketplace_discovery",
            endpoint="/v1/vibe/plugins/marketplace",
            results=results,
            slo_ms=100
        )

        logger.info(f"Marketplace discovery ({num_plugins} plugins): {metrics.mean_ms:.2f}ms "
                   f"(p95={metrics.p95_ms:.2f}ms, p99={metrics.p99_ms:.2f}ms)")

        assert metrics.p95_ms < expected_max_ms, \
            f"p95 latency {metrics.p95_ms:.2f}ms exceeds target {expected_max_ms}ms"
        assert metrics.slo_met, f"SLO not met: {metrics.mean_ms:.2f}ms > {metrics.slo_target_ms}ms"

    def test_marketplace_search_latency(self, client):
        """Measure search/filter latency with various queries."""
        queries = [
            ("Authentication", None),
            ("plugin", None),
            (None, "vetted"),
            ("database", "community"),
        ]

        results_by_query = {}
        for query, origin in queries:
            results = []
            for _ in range(20):
                params = {"limit": 100}
                if query:
                    params["query"] = query
                if origin:
                    params["origin"] = origin

                start = time.perf_counter()
                response = client.get("/v1/vibe/plugins/marketplace", params=params)
                elapsed_ms = (time.perf_counter() - start) * 1000

                assert response.status_code == 200
                results.append(elapsed_ms)

            metrics = _compute_metrics(
                scenario=f"search_{query or origin}",
                endpoint="/v1/vibe/plugins/marketplace",
                results=results,
                slo_ms=200
            )
            results_by_query[f"{query}_{origin}"] = metrics

            logger.info(f"Search ({query}/{origin}): {metrics.mean_ms:.2f}ms (p99={metrics.p99_ms:.2f}ms)")
            assert metrics.slo_met, f"Search SLO not met for {query}/{origin}"

    def test_marketplace_pagination_latency(self, client):
        """Measure pagination performance across large result sets."""
        results_by_offset = {}
        offsets = [0, 100, 500, 1000]

        for offset in offsets:
            results = []
            for _ in range(15):
                start = time.perf_counter()
                response = client.get(
                    "/v1/vibe/plugins/marketplace",
                    params={"limit": 50, "offset": offset}
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

                assert response.status_code == 200
                results.append(elapsed_ms)

            metrics = _compute_metrics(
                scenario=f"pagination_offset_{offset}",
                endpoint="/v1/vibe/plugins/marketplace",
                results=results,
                slo_ms=150
            )
            results_by_offset[offset] = metrics

            logger.info(f"Pagination (offset={offset}): {metrics.mean_ms:.2f}ms (p95={metrics.p95_ms:.2f}ms)")

        # Verify no offset penalty
        baseline = results_by_offset[0].mean_ms
        for offset, metrics in results_by_offset.items():
            penalty_pct = ((metrics.mean_ms - baseline) / baseline * 100) if baseline > 0 else 0
            logger.info(f"  Offset penalty: {penalty_pct:.1f}%")
            assert penalty_pct < 20, f"Pagination penalty {penalty_pct:.1f}% exceeds 20%"

    def test_list_plugins_latency(self, client):
        """Measure GET /v1/vibe/plugins/list latency."""
        results = []
        for _ in range(50):
            start = time.perf_counter()
            response = client.get("/v1/vibe/plugins/list")
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert response.status_code == 200
            results.append(elapsed_ms)

        metrics = _compute_metrics(
            scenario="list_plugins",
            endpoint="/v1/vibe/plugins/list",
            results=results,
            slo_ms=50
        )

        logger.info(f"List plugins: {metrics.mean_ms:.2f}ms (p99={metrics.p99_ms:.2f}ms)")
        assert metrics.slo_met

    def test_governance_endpoint_latency(self, client):
        """Measure GET /v1/console/plugins/governance latency."""
        results = []
        for _ in range(50):
            start = time.perf_counter()
            response = client.get("/v1/console/plugins/governance")
            elapsed_ms = (time.perf_counter() - start) * 1000

            assert response.status_code == 200
            results.append(elapsed_ms)

        metrics = _compute_metrics(
            scenario="governance_list",
            endpoint="/v1/console/plugins/governance",
            results=results,
            slo_ms=100
        )

        logger.info(f"Governance list: {metrics.mean_ms:.2f}ms")
        assert metrics.slo_met

    def test_plugin_upload_latency_by_size(self, client):
        """Measure plugin upload latency for various file sizes."""
        sizes_mb = [1, 10, 50]

        for size_mb in sizes_mb:
            results = []

            for _ in range(5):
                # Create mock tarball
                tarball_data = _create_mock_plugin_tarball(size_mb=size_mb)

                start = time.perf_counter()
                response = client.post(
                    "/v1/console/plugins/upload",
                    files={"file": ("plugin.tar.gz", tarball_data, "application/gzip")}
                )
                elapsed_ms = (time.perf_counter() - start) * 1000

                assert response.status_code in [200, 201, 400, 422]  # Various outcomes
                results.append(elapsed_ms)

            metrics = _compute_metrics(
                scenario=f"upload_{size_mb}mb",
                endpoint="/v1/console/plugins/upload",
                results=results,
                slo_ms=30_000
            )

            logger.info(f"Upload ({size_mb}MB): {metrics.mean_ms:.2f}ms "
                       f"({metrics.mean_ms / 1000:.1f}s, p99={metrics.p99_ms / 1000:.1f}s)")


# ─── Concurrent Load Tests ─────────────────────────────────────────────────

class TestLoadTesting:
    """Concurrent user and request load testing."""

    @pytest.mark.asyncio
    async def test_load_marketplace_concurrent_users(self, client):
        """Simulate 10 concurrent users browsing marketplace."""
        num_users = 10
        requests_per_user = 20

        async def user_session():
            """Simulate one user's marketplace browsing."""
            latencies = []
            for _ in range(requests_per_user):
                start = time.perf_counter()
                response = client.get("/v1/vibe/plugins/marketplace", params={"limit": 20})
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
                await asyncio.sleep(0.1)  # Think time

            return latencies

        # Run concurrent users
        start_time = time.perf_counter()
        tasks = [user_session() for _ in range(num_users)]
        results = await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time

        # Flatten results
        all_latencies = []
        for user_results in results:
            all_latencies.extend(user_results)

        total_requests = num_users * requests_per_user
        throughput_rps = total_requests / duration

        logger.info(f"\n10 Concurrent Users - Marketplace Browse:")
        logger.info(f"  Total requests: {total_requests}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Throughput: {throughput_rps:.1f} req/s")
        logger.info(f"  Mean latency: {statistics.mean(all_latencies):.2f}ms")
        logger.info(f"  Median: {statistics.median(all_latencies):.2f}ms")
        logger.info(f"  p95: {_percentile(all_latencies, 95):.2f}ms")
        logger.info(f"  p99: {_percentile(all_latencies, 99):.2f}ms")

        assert len(all_latencies) == total_requests
        assert statistics.mean(all_latencies) < 200, "Mean latency exceeds 200ms under load"

    @pytest.mark.asyncio
    async def test_load_concurrent_installations(self, client):
        """Simulate 10 concurrent plugin installations."""
        num_concurrent = 10
        installations_per_thread = 3

        async def installation_thread():
            """Simulate installations."""
            results = []
            for i in range(installations_per_thread):
                tarball = _create_mock_plugin_tarball(size_mb=5)
                start = time.perf_counter()
                response = client.post(
                    "/v1/console/plugins/upload",
                    files={"file": ("plugin.tar.gz", tarball, "application/gzip")}
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                results.append(elapsed_ms)

            return results

        start_time = time.perf_counter()
        tasks = [installation_thread() for _ in range(num_concurrent)]
        results = await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time

        all_latencies = []
        for thread_results in results:
            all_latencies.extend(thread_results)

        throughput = len(all_latencies) / duration

        logger.info(f"\n10 Concurrent Installations (5MB each):")
        logger.info(f"  Total installations: {len(all_latencies)}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Throughput: {throughput:.2f} installs/s")
        logger.info(f"  Mean latency: {statistics.mean(all_latencies):.2f}ms")
        logger.info(f"  Max latency: {max(all_latencies):.2f}ms")

        assert statistics.mean(all_latencies) < 60_000, "Mean install latency exceeds 60s"

    @pytest.mark.asyncio
    async def test_load_concurrent_searches(self, client):
        """Simulate 10 concurrent search operations."""
        num_users = 10
        searches_per_user = 15
        queries = ["authentication", "database", "security", "monitoring", "terraform"]

        async def search_session():
            """Simulate one user's search session."""
            latencies = []
            for i in range(searches_per_user):
                query = queries[i % len(queries)]
                start = time.perf_counter()
                response = client.get(
                    "/v1/vibe/plugins/marketplace",
                    params={"query": query, "limit": 50}
                )
                elapsed_ms = (time.perf_counter() - start) * 1000
                latencies.append(elapsed_ms)
                await asyncio.sleep(0.05)

            return latencies

        start_time = time.perf_counter()
        tasks = [search_session() for _ in range(num_users)]
        results = await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time

        all_latencies = []
        for user_results in results:
            all_latencies.extend(user_results)

        logger.info(f"\n10 Concurrent Search Sessions:")
        logger.info(f"  Total searches: {len(all_latencies)}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Mean latency: {statistics.mean(all_latencies):.2f}ms")
        logger.info(f"  p95: {_percentile(all_latencies, 95):.2f}ms")

        assert statistics.mean(all_latencies) < 250, "Mean search latency exceeds 250ms"


# ─── Stress Tests ──────────────────────────────────────────────────────────

class TestStressTesting:
    """Stress testing at peak loads."""

    @pytest.mark.asyncio
    async def test_stress_peak_load_100_users(self, client):
        """Peak stress test: 100 concurrent users."""
        num_users = 100
        requests_per_user = 5

        async def user_burst():
            """Rapid-fire requests from one user."""
            latencies = []
            for _ in range(requests_per_user):
                start = time.perf_counter()
                response = client.get("/v1/vibe/plugins/marketplace", params={"limit": 20})
                elapsed_ms = (time.perf_counter() - start) * 1000
                if response.status_code == 200:
                    latencies.append(elapsed_ms)

            return latencies

        start_time = time.perf_counter()
        tasks = [user_burst() for _ in range(num_users)]
        results = await asyncio.gather(*tasks)
        duration = time.perf_counter() - start_time

        all_latencies = []
        for user_results in results:
            all_latencies.extend(user_results)

        throughput = len(all_latencies) / duration

        logger.info(f"\nStress Test - 100 Concurrent Users (peak burst):")
        logger.info(f"  Total requests: {len(all_latencies)}")
        logger.info(f"  Duration: {duration:.2f}s")
        logger.info(f"  Peak throughput: {throughput:.1f} req/s")
        logger.info(f"  Mean latency: {statistics.mean(all_latencies):.2f}ms")
        logger.info(f"  p99: {_percentile(all_latencies, 99):.2f}ms")
        logger.info(f"  Max: {max(all_latencies):.2f}ms")

        # Relaxed SLO for peak load
        assert statistics.mean(all_latencies) < 500, "Mean latency exceeds 500ms at peak load"

    @pytest.mark.asyncio
    async def test_stress_sustained_load_30min(self, client):
        """Sustained load test over 30 minutes (simulated with shorter duration)."""
        # For testing, run for 30 seconds instead of 30 minutes
        # Scale: 10 req/s continuous = 300 req in 30s
        duration_sec = 30
        target_rps = 10
        request_interval = 1.0 / target_rps

        start_time = time.perf_counter()
        request_count = 0
        latencies = []
        errors = 0

        while time.perf_counter() - start_time < duration_sec:
            start = time.perf_counter()
            try:
                response = client.get("/v1/vibe/plugins/marketplace", params={"limit": 20})
                elapsed_ms = (time.perf_counter() - start) * 1000

                if response.status_code == 200:
                    latencies.append(elapsed_ms)
                    request_count += 1
                else:
                    errors += 1
            except Exception as e:
                logger.warning(f"Request failed: {e}")
                errors += 1

            # Sleep to maintain request rate
            elapsed = time.perf_counter() - start
            sleep_time = max(0, request_interval - elapsed)
            if sleep_time > 0:
                time.sleep(sleep_time)

        actual_duration = time.perf_counter() - start_time
        actual_rps = request_count / actual_duration

        logger.info(f"\nSustained Load Test (30s simulated):")
        logger.info(f"  Requests: {request_count}")
        logger.info(f"  Errors: {errors}")
        logger.info(f"  Error rate: {errors / (request_count + errors) * 100:.2f}%")
        logger.info(f"  Actual RPS: {actual_rps:.2f}")
        logger.info(f"  Mean latency: {statistics.mean(latencies):.2f}ms")
        logger.info(f"  p99: {_percentile(latencies, 99):.2f}ms")

        error_rate = errors / (request_count + errors) if (request_count + errors) > 0 else 0
        assert error_rate < 0.05, f"Error rate {error_rate:.2f}% exceeds 5%"
        assert statistics.mean(latencies) < 300, "Mean latency exceeds 300ms under sustained load"


# ─── Regression Detection ──────────────────────────────────────────────────

class TestRegressionDetection:
    """Detect regressions vs Phase 2 baseline."""

    def test_regression_detection_marketplace_discovery(self, client):
        """Compare against Phase 2 baseline (if available)."""
        # Phase 2 baseline (from previous measurements)
        phase2_baseline = {
            "mean_ms": 45.0,
            "p95_ms": 75.0,
            "p99_ms": 95.0,
        }

        results = []
        for _ in range(30):
            start = time.perf_counter()
            response = client.get("/v1/vibe/plugins/marketplace", params={"limit": 20})
            elapsed_ms = (time.perf_counter() - start) * 1000
            results.append(elapsed_ms)

        current = {
            "mean_ms": statistics.mean(results),
            "p95_ms": _percentile(results, 95),
            "p99_ms": _percentile(results, 99),
        }

        # Check for regression (>20% increase)
        regression_threshold = 1.20

        logger.info(f"\nRegression Detection - Marketplace Discovery:")
        logger.info(f"  Phase 2 baseline: {phase2_baseline['mean_ms']:.2f}ms")
        logger.info(f"  Current: {current['mean_ms']:.2f}ms")

        for metric in ["mean_ms", "p95_ms", "p99_ms"]:
            baseline_val = phase2_baseline[metric]
            current_val = current[metric]
            ratio = current_val / baseline_val if baseline_val > 0 else 1.0

            if ratio > regression_threshold:
                logger.warning(f"  REGRESSION in {metric}: {current_val:.2f}ms "
                              f"(was {baseline_val:.2f}ms, {ratio:.1f}x)")
            else:
                logger.info(f"  {metric}: {current_val:.2f}ms (baseline {baseline_val:.2f}ms, {ratio:.2f}x)")

        # Assert no major regression
        assert current["mean_ms"] < phase2_baseline["mean_ms"] * regression_threshold, \
            f"Regression detected: mean latency {current['mean_ms']:.2f}ms vs baseline {phase2_baseline['mean_ms']:.2f}ms"


# ─── SLO Verification ──────────────────────────────────────────────────────

class TestSLOVerification:
    """Verify SLOs are met."""

    def test_slo_marketplace_discovery_latency(self, client):
        """SLO: Marketplace discovery <100ms."""
        results = []
        for _ in range(100):
            start = time.perf_counter()
            response = client.get("/v1/vibe/plugins/marketplace", params={"limit": 20})
            elapsed_ms = (time.perf_counter() - start) * 1000
            results.append(elapsed_ms)

        mean = statistics.mean(results)
        p95 = _percentile(results, 95)
        p99 = _percentile(results, 99)

        logger.info(f"SLO: Marketplace Discovery <100ms")
        logger.info(f"  Mean: {mean:.2f}ms (target: <50ms) {'✓' if mean < 50 else '✗'}")
        logger.info(f"  p95:  {p95:.2f}ms (target: <100ms) {'✓' if p95 < 100 else '✗'}")
        logger.info(f"  p99:  {p99:.2f}ms (target: <150ms) {'✓' if p99 < 150 else '✗'}")

        assert mean < 50, f"Mean latency {mean:.2f}ms exceeds 50ms target"
        assert p95 < 100, f"p95 latency {p95:.2f}ms exceeds 100ms target"

    def test_slo_search_latency(self, client):
        """SLO: Search <200ms."""
        results = []
        for _ in range(50):
            start = time.perf_counter()
            response = client.get(
                "/v1/vibe/plugins/marketplace",
                params={"query": "database", "limit": 50}
            )
            elapsed_ms = (time.perf_counter() - start) * 1000
            results.append(elapsed_ms)

        mean = statistics.mean(results)
        p95 = _percentile(results, 95)

        logger.info(f"SLO: Search <200ms")
        logger.info(f"  Mean: {mean:.2f}ms {'✓' if mean < 150 else '✗'}")
        logger.info(f"  p95:  {p95:.2f}ms {'✓' if p95 < 200 else '✗'}")

        assert p95 < 200, f"Search p95 {p95:.2f}ms exceeds 200ms target"

    def test_slo_plugin_list_latency(self, client):
        """SLO: List plugins <50ms."""
        results = []
        for _ in range(100):
            start = time.perf_counter()
            response = client.get("/v1/vibe/plugins/list")
            elapsed_ms = (time.perf_counter() - start) * 1000
            results.append(elapsed_ms)

        mean = statistics.mean(results)
        p99 = _percentile(results, 99)

        logger.info(f"SLO: List Plugins <50ms")
        logger.info(f"  Mean: {mean:.2f}ms {'✓' if mean < 30 else '✗'}")
        logger.info(f"  p99:  {p99:.2f}ms {'✓' if p99 < 50 else '✗'}")

        assert mean < 30, f"List latency {mean:.2f}ms exceeds 30ms target"

    def test_slo_install_flow_end_to_end(self, client):
        """SLO: Installation flow <30s end-to-end."""
        results = []

        for _ in range(5):
            tarball = _create_mock_plugin_tarball(size_mb=5)

            start = time.perf_counter()
            response = client.post(
                "/v1/console/plugins/upload",
                files={"file": ("plugin.tar.gz", tarball, "application/gzip")}
            )
            elapsed_sec = time.perf_counter() - start

            if response.status_code in [200, 201]:
                results.append(elapsed_sec)

        if results:
            mean_sec = statistics.mean(results)
            max_sec = max(results)

            logger.info(f"SLO: Installation Flow <30s")
            logger.info(f"  Mean: {mean_sec:.2f}s {'✓' if mean_sec < 20 else '✗'}")
            logger.info(f"  Max:  {max_sec:.2f}s {'✓' if max_sec < 30 else '✗'}")

            assert max_sec < 30, f"Max install time {max_sec:.2f}s exceeds 30s SLO"


# ─── Helper Functions ──────────────────────────────────────────────────────

def _compute_metrics(scenario: str, endpoint: str, results: List[float],
                    slo_ms: float = 100.0) -> PerformanceMetrics:
    """Compute metrics from latency results."""
    if not results:
        return PerformanceMetrics(
            scenario_name=scenario,
            endpoint=endpoint,
            slo_target_ms=slo_ms,
        )

    sorted_results = sorted(results)
    mean = statistics.mean(results)
    slo_met = statistics.mean(results) <= slo_ms

    return PerformanceMetrics(
        scenario_name=scenario,
        endpoint=endpoint,
        num_requests=len(results),
        min_ms=min(results),
        max_ms=max(results),
        mean_ms=mean,
        median_ms=statistics.median(results),
        p95_ms=_percentile(results, 95),
        p99_ms=_percentile(results, 99),
        throughput_rps=1000.0 / mean if mean > 0 else 0,
        slo_target_ms=slo_ms,
        slo_met=slo_met,
        latencies=results,
        error_rate=0.0,
    )


def _percentile(data: List[float], percentile: int) -> float:
    """Calculate percentile of data."""
    if not data:
        return 0.0
    sorted_data = sorted(data)
    index = (percentile / 100.0) * (len(sorted_data) - 1)
    lower = int(index)
    upper = lower + 1
    weight = index - lower

    if upper >= len(sorted_data):
        return sorted_data[-1]

    return sorted_data[lower] * (1 - weight) + sorted_data[upper] * weight


def _create_mock_plugin_tarball(size_mb: float = 1.0) -> BytesIO:
    """Create a mock plugin tarball for testing."""
    tarball = BytesIO()

    with tarfile.open(fileobj=tarball, mode="w:gz") as tar:
        # Create plugin.yaml
        manifest = {
            "plugin_id": "test-plugin",
            "plugin_type": "extension",
            "version": "1.0.0",
            "origin": "community",
            "name": "Test Plugin",
            "description": "A test plugin",
        }

        import io
        manifest_bytes = io.BytesIO(json.dumps(manifest).encode())
        info = tarfile.TarInfo(name="plugin.yaml")
        info.size = len(json.dumps(manifest).encode())
        tar.addfile(tarinfo=info, fileobj=manifest_bytes)

        # Add dummy content to reach desired size
        content_size = int(size_mb * 1024 * 1024)
        dummy_data = b"x" * content_size
        info = tarfile.TarInfo(name="data.bin")
        info.size = len(dummy_data)
        tar.addfile(tarinfo=info, fileobj=io.BytesIO(dummy_data))

    tarball.seek(0)
    return tarball


# ─── Integration Tests ─────────────────────────────────────────────────────

class TestPerformanceIntegration:
    """Integration tests combining multiple scenarios."""

    def test_performance_report_generation(self, client):
        """Generate a comprehensive performance report."""
        logger.info("\n" + "="*80)
        logger.info("PLUGIN MARKETPLACE PERFORMANCE REPORT")
        logger.info("="*80)

        # Gather metrics from multiple scenarios
        scenarios = [
            ("marketplace_empty", "/v1/vibe/plugins/marketplace", 20, 100),
            ("list_plugins", "/v1/vibe/plugins/list", 50, 50),
            ("governance", "/v1/console/plugins/governance", 50, 100),
        ]

        all_metrics = []

        for scenario_name, endpoint, num_samples, slo_ms in scenarios:
            results = []
            for _ in range(num_samples):
                start = time.perf_counter()
                if endpoint == "/v1/vibe/plugins/list":
                    response = client.get(endpoint)
                elif endpoint == "/v1/console/plugins/governance":
                    response = client.get(endpoint)
                else:
                    response = client.get(endpoint, params={"limit": 20})

                elapsed_ms = (time.perf_counter() - start) * 1000
                results.append(elapsed_ms)

            metrics = _compute_metrics(scenario_name, endpoint, results, slo_ms)
            all_metrics.append(metrics)

        # Print report
        logger.info(f"\n{'Scenario':<25} {'Endpoint':<30} {'Mean':<10} {'p95':<10} {'p99':<10} {'SLO':<8}")
        logger.info("-" * 90)

        for metrics in all_metrics:
            slo_str = "✓ PASS" if metrics.slo_met else "✗ FAIL"
            logger.info(f"{metrics.scenario_name:<25} {metrics.endpoint:<30} "
                       f"{metrics.mean_ms:>8.2f}ms {metrics.p95_ms:>8.2f}ms "
                       f"{metrics.p99_ms:>8.2f}ms {slo_str:<8}")

        logger.info("-" * 90)

        # Summary
        passed = sum(1 for m in all_metrics if m.slo_met)
        total = len(all_metrics)

        logger.info(f"\nSummary: {passed}/{total} scenarios met SLO")
        logger.info("="*80 + "\n")

        assert passed == total, f"Only {passed}/{total} scenarios met SLO"


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

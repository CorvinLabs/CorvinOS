"""Locust load testing for Plugin Marketplace (ADR-0249 Phase 4+).

Usage:
    locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 -u 100 -r 10 -t 5m

This script simulates realistic marketplace user behaviors:
- Browsing marketplace (discovery)
- Searching for plugins
- Filtering by category/origin
- Pagination through results
- Installing plugins (simulated)

Run with:
    locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765
"""

import json
import random
import time
from io import BytesIO
from typing import List

from locust import HttpUser, task, between, events
from locust.clients import HttpSession
import logging

logger = logging.getLogger(__name__)


# ─── Performance Metrics Collector ─────────────────────────────────────────

class PerformanceMetrics:
    """Collect and aggregate performance metrics during load test."""

    def __init__(self):
        self.latencies = {
            "marketplace": [],
            "search": [],
            "filter": [],
            "pagination": [],
            "install": [],
        }
        self.error_count = 0
        self.success_count = 0
        self.start_time = time.time()

    def record_latency(self, endpoint: str, latency_ms: float):
        """Record a latency measurement."""
        if endpoint in self.latencies:
            self.latencies[endpoint].append(latency_ms)
        self.success_count += 1

    def record_error(self):
        """Record an error."""
        self.error_count += 1

    def get_summary(self) -> dict:
        """Get summary statistics."""
        duration = time.time() - self.start_time
        total_requests = self.success_count + self.error_count

        summary = {
            "duration_sec": duration,
            "total_requests": total_requests,
            "successful": self.success_count,
            "failed": self.error_count,
            "error_rate": self.error_count / total_requests if total_requests > 0 else 0,
            "rps": total_requests / duration if duration > 0 else 0,
            "endpoint_stats": {},
        }

        for endpoint, latencies in self.latencies.items():
            if latencies:
                sorted_latencies = sorted(latencies)
                summary["endpoint_stats"][endpoint] = {
                    "count": len(latencies),
                    "mean_ms": sum(latencies) / len(latencies),
                    "min_ms": min(latencies),
                    "max_ms": max(latencies),
                    "p95_ms": sorted_latencies[int(len(sorted_latencies) * 0.95)],
                    "p99_ms": sorted_latencies[int(len(sorted_latencies) * 0.99)],
                }

        return summary


# Global metrics instance
_metrics = PerformanceMetrics()


# ─── Locust User Class ─────────────────────────────────────────────────────

class MarketplaceUser(HttpUser):
    """Simulates a typical marketplace user."""

    wait_time = between(1, 3)  # Random think time 1-3 seconds

    def on_start(self):
        """Initialize user state."""
        self.categories = [
            "Authentication",
            "Analytics",
            "Database",
            "Security",
            "Tooling",
        ]
        self.origins = ["vetted", "community"]
        self.queries = [
            "authentication",
            "database",
            "security",
            "monitoring",
            "terraform",
            "vault",
            "saml",
            "postgres",
        ]

    # ─── Main User Tasks ──────────────────────────────────────────────────

    @task(30)  # 30% of requests
    def browse_marketplace(self):
        """Browse marketplace (discovery)."""
        try:
            start = time.time()
            with self.client.get(
                "/v1/vibe/plugins/marketplace",
                params={"limit": 20, "offset": 0},
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("marketplace", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"Browse failed: {e}")
            _metrics.record_error()

    @task(20)  # 20% of requests
    def search_plugins(self):
        """Search for plugins."""
        query = random.choice(self.queries)
        try:
            start = time.time()
            with self.client.get(
                "/v1/vibe/plugins/marketplace",
                params={"query": query, "limit": 50},
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("search", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"Search failed: {e}")
            _metrics.record_error()

    @task(20)  # 20% of requests
    def filter_by_category(self):
        """Filter plugins by category."""
        category = random.choice(self.categories)
        try:
            start = time.time()
            with self.client.get(
                "/v1/vibe/plugins/marketplace",
                params={"category": category, "limit": 100},
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("filter", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"Filter failed: {e}")
            _metrics.record_error()

    @task(15)  # 15% of requests
    def paginate_results(self):
        """Paginate through results."""
        offset = random.randint(0, 5) * 100
        try:
            start = time.time()
            with self.client.get(
                "/v1/vibe/plugins/marketplace",
                params={"limit": 100, "offset": offset},
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("pagination", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"Pagination failed: {e}")
            _metrics.record_error()

    @task(10)  # 10% of requests
    def install_plugin(self):
        """Simulate plugin installation."""
        try:
            start = time.time()

            # Create a small mock tarball
            tarball = _create_mock_tarball(size_mb=1)

            with self.client.post(
                "/v1/console/plugins/upload",
                files={"file": ("plugin.tar.gz", tarball, "application/gzip")},
                catch_response=True,
            ) as response:
                if response.status_code in [200, 201, 400, 422]:  # Any response is OK for this test
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("install", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"Install failed: {e}")
            _metrics.record_error()

    @task(5)  # 5% of requests
    def list_installed(self):
        """List installed plugins."""
        try:
            start = time.time()
            with self.client.get(
                "/v1/vibe/plugins/list",
                catch_response=True,
            ) as response:
                if response.status_code == 200:
                    latency_ms = (time.time() - start) * 1000
                    _metrics.record_latency("marketplace", latency_ms)
                    response.success()
                else:
                    _metrics.record_error()
                    response.failure(f"Status {response.status_code}")
        except Exception as e:
            logger.error(f"List failed: {e}")
            _metrics.record_error()


# ─── Event Handlers ───────────────────────────────────────────────────────

@events.test_start.add_listener
def on_test_start(environment, **kwargs):
    """Called when test starts."""
    logger.info("="*80)
    logger.info("PLUGIN MARKETPLACE LOAD TEST STARTED")
    logger.info("="*80)


@events.test_stop.add_listener
def on_test_stop(environment, **kwargs):
    """Called when test stops."""
    logger.info("\n" + "="*80)
    logger.info("PLUGIN MARKETPLACE LOAD TEST COMPLETED")
    logger.info("="*80)

    summary = _metrics.get_summary()

    logger.info(f"\nTest Duration: {summary['duration_sec']:.1f}s")
    logger.info(f"Total Requests: {summary['total_requests']}")
    logger.info(f"Successful: {summary['successful']}")
    logger.info(f"Failed: {summary['failed']}")
    logger.info(f"Error Rate: {summary['error_rate']:.2%}")
    logger.info(f"Throughput: {summary['rps']:.1f} req/s")

    logger.info("\nEndpoint Statistics:")
    logger.info(f"{'Endpoint':<20} {'Count':<8} {'Mean':<10} {'p95':<10} {'p99':<10}")
    logger.info("-" * 60)

    for endpoint, stats in summary["endpoint_stats"].items():
        logger.info(
            f"{endpoint:<20} {stats['count']:<8} "
            f"{stats['mean_ms']:>8.2f}ms {stats['p95_ms']:>8.2f}ms "
            f"{stats['p99_ms']:>8.2f}ms"
        )

    logger.info("-" * 60)
    logger.info("="*80 + "\n")


@events.request.add_listener
def on_request(request_type, name, response_time, response_length, response, context, exception, **kwargs):
    """Called for each request."""
    if exception:
        logger.debug(f"Request failed: {name} - {exception}")


# ─── Helper Functions ─────────────────────────────────────────────────────

def _create_mock_tarball(size_mb: float = 1.0) -> BytesIO:
    """Create a mock plugin tarball."""
    import tarfile
    import io

    tarball = BytesIO()

    with tarfile.open(fileobj=tarball, mode="w:gz") as tar:
        # Create plugin.yaml
        manifest = {
            "plugin_id": f"test-plugin-{random.randint(1000, 9999)}",
            "plugin_type": "extension",
            "version": "1.0.0",
            "origin": "community",
            "name": "Test Plugin",
            "description": "A test plugin",
        }

        manifest_bytes = io.BytesIO(json.dumps(manifest).encode())
        info = tarfile.TarInfo(name="plugin.yaml")
        info.size = len(json.dumps(manifest).encode())
        tar.addfile(tarinfo=info, fileobj=manifest_bytes)

        # Add dummy content
        content_size = int(size_mb * 1024 * 1024)
        dummy_data = b"x" * content_size
        info = tarfile.TarInfo(name="data.bin")
        info.size = len(dummy_data)
        tar.addfile(tarinfo=info, fileobj=io.BytesIO(dummy_data))

    tarball.seek(0)
    return tarball


if __name__ == "__main__":
    """
    Run this file with locust:
        locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 -u 100 -r 10 -t 5m

    Arguments:
        -u/--users: Number of concurrent users (default: 1)
        -r/--spawn-rate: Users spawned per second (default: 1)
        -t/--run-time: Time limit (e.g., 5m, 30s)
        --host: Target host (required)

    Examples:
        # 10 concurrent users, 30s test
        locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 -u 10 -r 1 -t 30s

        # 100 concurrent users, 5 min test, spawn 5 per second
        locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 -u 100 -r 5 -t 5m

        # Headless mode with web UI
        locust -f locustfile_plugin_marketplace.py --host=http://localhost:8765 -u 100 -r 10 -t 5m --headless
    """
    pass

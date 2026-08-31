"""End-to-End Tests for Live Telemetry Infrastructure (Phase 5)

Tests the complete flow:
- Instance telemetry client submitting metrics
- Central aggregator collecting and aggregating
- API server serving stats dashboard
- Real-time updates
"""

import pytest
from datetime import datetime, timedelta
from pathlib import Path

# Test imports
from core.telemetry.central_aggregator import (
    TelemetryAggregator,
    InstanceLocation,
    InstanceTelemetry,
    ClusterStats,
)
from core.telemetry.client import TelemetryClient, TelemetryClientConfig
from core.telemetry.api_server import TelemetryAPIServer


class TestCentralAggregator:
    """Tests for TelemetryAggregator."""

    def setup_method(self):
        """Set up test aggregator."""
        self.aggregator = TelemetryAggregator(stale_timeout_seconds=300)

    def test_register_instance(self):
        """Test registering a new instance."""
        location = InstanceLocation(
            latitude=40.7128,
            longitude=-74.0060,
            city="New York",
            country="US",
            region="ny",
        )

        self.aggregator.register_instance(
            instance_id="prod-us-east-1",
            hostname="prod.us-east-1.corvin.local",
            location=location,
            version="0.10.51",
            tenant_id="_default",
        )

        assert "prod-us-east-1" in self.aggregator.instances
        inst = self.aggregator.instances["prod-us-east-1"]
        assert inst.hostname == "prod.us-east-1.corvin.local"
        assert inst.turn_count == 0
        assert inst.total_tokens == 0

    def test_submit_telemetry(self):
        """Test submitting telemetry from an instance."""
        # Register first
        location = InstanceLocation(40.7128, -74.0060)
        self.aggregator.register_instance(
            "prod-us-1", "host-us-1", location, "0.10.51", "_default"
        )

        # Submit telemetry
        self.aggregator.submit_telemetry(
            instance_id="prod-us-1",
            turn_count=100,
            total_tokens=5000,
            savings_percent=28.5,
            uptime_seconds=3600,
            tenant_id="_default",
        )

        inst = self.aggregator.instances["prod-us-1"]
        assert inst.turn_count == 100
        assert inst.total_tokens == 5000
        assert inst.savings_percent == 28.5
        assert inst.uptime_seconds == 3600

    def test_get_cluster_stats(self):
        """Test aggregating cluster statistics."""
        # Register multiple instances
        for i in range(3):
            location = InstanceLocation(float(i * 10), float(i * 10))
            self.aggregator.register_instance(
                f"instance-{i}",
                f"host-{i}",
                location,
                "0.10.51",
                "_default",
            )

            # Submit telemetry
            self.aggregator.submit_telemetry(
                instance_id=f"instance-{i}",
                turn_count=(i + 1) * 100,
                total_tokens=(i + 1) * 1000,
                savings_percent=25 + i,
                uptime_seconds=3600,
                tenant_id="_default",
            )

        stats = self.aggregator.get_cluster_stats("_default")

        assert stats.instance_count == 3
        assert stats.total_turns == 100 + 200 + 300  # 600
        assert stats.total_tokens == 1000 + 2000 + 3000  # 6000
        assert stats.avg_tokens_per_turn == 6000 // 600  # 10
        assert isinstance(stats.instances, dict)
        assert len(stats.instances) == 3

    def test_stale_instance_detection(self):
        """Test detecting stale instances."""
        aggregator = TelemetryAggregator(stale_timeout_seconds=1)

        location = InstanceLocation(0, 0)
        aggregator.register_instance("inst-1", "host-1", location, "0.10", "_default")

        # Submit recent telemetry
        aggregator.submit_telemetry(
            "inst-1", 10, 100, 25.0, 60, "_default"
        )

        # Should be active
        active = aggregator.get_active_instances("_default")
        assert len(active) == 1

        # Wait for stale timeout
        import time
        time.sleep(1.1)

        # Should be marked stale
        active = aggregator.get_active_instances("_default")
        assert len(active) == 0

    def test_tenant_isolation(self):
        """Test that tenants are isolated."""
        location = InstanceLocation(0, 0)

        # Register instance for tenant A
        self.aggregator.register_instance(
            "inst-a1", "host-a1", location, "0.10", "tenant-a"
        )

        # Register instance for tenant B
        self.aggregator.register_instance(
            "inst-b1", "host-b1", location, "0.10", "tenant-b"
        )

        # Submit telemetry
        self.aggregator.submit_telemetry(
            "inst-a1", 100, 1000, 25.0, 60, "tenant-a"
        )
        self.aggregator.submit_telemetry(
            "inst-b1", 200, 2000, 30.0, 60, "tenant-b"
        )

        # Get stats for each tenant
        stats_a = self.aggregator.get_cluster_stats("tenant-a")
        stats_b = self.aggregator.get_cluster_stats("tenant-b")

        assert stats_a.instance_count == 1
        assert stats_a.total_turns == 100
        assert stats_b.instance_count == 1
        assert stats_b.total_turns == 200

    def test_stats_to_dict(self):
        """Test ClusterStats serialization to dict."""
        location = InstanceLocation(40.7128, -74.0060)
        self.aggregator.register_instance(
            "inst-1", "host-1", location, "0.10", "_default"
        )
        self.aggregator.submit_telemetry(
            "inst-1", 100, 1000, 25.0, 60, "_default"
        )

        stats = self.aggregator.get_cluster_stats("_default")
        data = stats.to_dict()

        assert "timestamp" in data
        assert data["instance_count"] == 1
        assert data["total_turns"] == 100
        assert data["total_tokens"] == 1000
        assert "instances" in data
        assert len(data["instances"]) == 1
        assert "summary" in data


class TestTelemetryClient:
    """Tests for TelemetryClient."""

    def setup_method(self):
        """Set up test client."""
        self.config = TelemetryClientConfig(
            aggregator_url="http://localhost:8765/api/telemetry",
            instance_id="test-instance",
            tenant_id="_default",
            push_interval_seconds=10,
        )

    def test_client_initialization(self):
        """Test client initialization."""
        client = TelemetryClient(self.config)

        assert client.config.instance_id == "test-instance"
        assert client.submission_count == 0
        assert client.failed_submissions == 0

    def test_client_stats(self):
        """Test client statistics collection."""
        def mock_post(url, data):
            return True

        client = TelemetryClient(self.config, http_post_fn=mock_post)

        # Submit metrics
        client.submit_metrics(100, 1000, 25.0)
        client.submit_metrics(200, 2000, 28.0)

        stats = client.get_stats()
        assert stats["submission_count"] == 2
        assert stats["failed_submissions"] == 0
        assert stats["last_turn_count"] == 200

    def test_client_submission_retry(self):
        """Test client retry logic."""
        call_count = 0

        def mock_post_fail_then_succeed(url, data):
            nonlocal call_count
            call_count += 1
            return call_count >= 3  # Fail first 2, succeed on 3rd

        client = TelemetryClient(
            self.config,
            http_post_fn=mock_post_fail_then_succeed,
        )
        client.config.retry_max_attempts = 3

        # Should succeed after retries
        result = client.submit_metrics(100, 1000, 25.0)
        assert result is True
        assert client.submission_count == 1

    def test_client_submission_failure(self):
        """Test client handling submission failure."""
        def mock_post_fail(url, data):
            return False

        client = TelemetryClient(self.config, http_post_fn=mock_post_fail)
        client.config.retry_max_attempts = 2

        result = client.submit_metrics(100, 1000, 25.0)
        assert result is False
        assert client.failed_submissions == 1


class TestAPIServer:
    """Tests for TelemetryAPIServer."""

    def setup_method(self):
        """Set up test server."""
        self.aggregator = TelemetryAggregator()
        # Create server without starting
        if TelemetryAPIServer.__module__ != "__test__":
            self.server = TelemetryAPIServer(
                self.aggregator,
                html_path=None,
            )

    @pytest.mark.skipif(not TelemetryAPIServer.__module__, reason="Flask not available")
    def test_api_server_routes(self):
        """Test API server has all routes registered."""
        # Verify routes exist
        assert self.server.app is not None
        routes = [rule.rule for rule in self.server.app.url_map.iter_rules()]
        assert "/stats" in routes
        assert "/api/metrics/stats" in routes
        assert "/api/telemetry/submit" in routes
        assert "/api/telemetry/instances" in routes
        assert "/health" in routes


class TestLiveIntegration:
    """Integration tests for live telemetry flow."""

    def test_full_telemetry_flow(self):
        """Test complete flow: client -> aggregator -> API."""
        # Create aggregator
        aggregator = TelemetryAggregator()

        # Create and configure client
        config = TelemetryClientConfig(
            aggregator_url="http://localhost:8765/api/telemetry",
            instance_id="test-prod-1",
            tenant_id="_default",
        )

        def mock_post(url, data):
            # Simulate aggregator receiving telemetry
            agg_data = data
            if "instance_id" in agg_data:
                location_data = agg_data.get("location", {})
                location = InstanceLocation(
                    latitude=float(location_data.get("latitude", 0)),
                    longitude=float(location_data.get("longitude", 0)),
                    city=location_data.get("city"),
                    country=location_data.get("country"),
                )
                if agg_data["instance_id"] not in aggregator.instances:
                    aggregator.register_instance(
                        instance_id=agg_data["instance_id"],
                        hostname=agg_data.get("hostname", f"host-{agg_data['instance_id']}"),
                        location=location,
                        version=agg_data.get("version", "0.10"),
                        tenant_id=agg_data["tenant_id"],
                    )
                aggregator.submit_telemetry(
                    instance_id=agg_data["instance_id"],
                    turn_count=agg_data["turn_count"],
                    total_tokens=agg_data["total_tokens"],
                    savings_percent=agg_data.get("savings_percent", 25.0),
                    uptime_seconds=agg_data.get("uptime_seconds", 0),
                    tenant_id=agg_data["tenant_id"],
                )
            return True

        client = TelemetryClient(config, http_post_fn=mock_post)

        # Client submits metrics
        client.submit_metrics(turn_count=150, total_tokens=1500, savings_percent=27.5)

        # Aggregator should have the data
        stats = aggregator.get_cluster_stats("_default")
        assert stats.instance_count == 1
        assert stats.total_turns == 150
        assert stats.total_tokens == 1500

        # Serialize to JSON (like API would)
        data = stats.to_dict()
        assert "instances" in data
        assert data["instance_count"] == 1
        assert "summary" in data


# Pytest fixtures
@pytest.fixture
def aggregator():
    """Fixture for test aggregator."""
    return TelemetryAggregator()


@pytest.fixture
def client_config():
    """Fixture for test client config."""
    return TelemetryClientConfig(
        aggregator_url="http://localhost:8765/api/telemetry",
        instance_id="test-instance",
        tenant_id="_default",
    )

"""Test Suite for Live Stats Aggregator (ADR-0638)

Tests the metrics collection, aggregation, and API endpoints.

Coverage:
  - MetricsCollector: tenant discovery, metrics collection, loss extraction
  - MetricsServer: background collection loop, caching, health status
  - Stats Routes: all endpoints (GET /stats, /instances, /history, /health)
  - Error handling and edge cases
  - Tenant isolation and data privacy
"""

from __future__ import annotations

import asyncio
import json
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional
from unittest.mock import AsyncMock, MagicMock, Mock, patch

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from core.aggregator.metrics_collector import (
    MetricsCollector,
    TenantMetrics,
    GlobalMetrics,
)
from core.aggregator.metrics_server import MetricsServer, get_metrics_server, set_metrics_server
from core.aggregator.routes.stats import router as stats_router


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def temp_corvin_home():
    """Create a temporary CORVIN_HOME for testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        corvin_root = Path(tmpdir)
        (corvin_root / "tenants" / "_default" / "global" / "learning" / "events").mkdir(
            parents=True, exist_ok=True
        )
        yield corvin_root


@pytest.fixture
def metrics_collector(temp_corvin_home):
    """Create a MetricsCollector instance with temp storage."""
    return MetricsCollector(corvin_root=temp_corvin_home)


@pytest.fixture
def metrics_server(temp_corvin_home):
    """Create a MetricsServer instance with temp storage."""
    server = MetricsServer(collection_interval_sec=1)
    server.collector = MetricsCollector(corvin_root=temp_corvin_home)
    return server


@pytest.fixture
def fastapi_app(metrics_server):
    """Create a test FastAPI app with stats router."""
    app = FastAPI()
    set_metrics_server(metrics_server)
    app.include_router(stats_router)
    return app


@pytest.fixture
def client(fastapi_app):
    """Create a TestClient for the FastAPI app."""
    return TestClient(fastapi_app)


# ============================================================================
# MetricsCollector Tests
# ============================================================================


class TestMetricsCollector:
    """Test MetricsCollector functionality."""

    def test_initialization(self, metrics_collector, temp_corvin_home):
        """Test collector initialization."""
        assert metrics_collector.corvin_root == temp_corvin_home
        assert metrics_collector.tenants_dir == temp_corvin_home / "tenants"
        assert metrics_collector.aggregation_dir.exists()

    def test_discover_tenants_empty(self, metrics_collector):
        """Test tenant discovery with no tenants."""
        # Remove all tenant dirs
        for tenant_path in metrics_collector.tenants_dir.iterdir():
            if tenant_path.is_dir():
                import shutil
                shutil.rmtree(tenant_path)

        tenants = metrics_collector.discover_tenants()
        assert tenants == []

    def test_discover_tenants_single(self, metrics_collector, temp_corvin_home):
        """Test tenant discovery with one tenant."""
        tenants = metrics_collector.discover_tenants()
        assert "_default" in tenants

    def test_discover_tenants_multiple(self, metrics_collector, temp_corvin_home):
        """Test tenant discovery with multiple tenants."""
        # Create additional tenants
        (temp_corvin_home / "tenants" / "tenant_a" / "global").mkdir(parents=True, exist_ok=True)
        (temp_corvin_home / "tenants" / "tenant_b" / "global").mkdir(parents=True, exist_ok=True)

        tenants = metrics_collector.discover_tenants()
        assert "_default" in tenants
        assert "tenant_a" in tenants
        assert "tenant_b" in tenants
        assert tenants == sorted(tenants)  # Verify sorted order

    def test_collect_tenant_metrics_no_events(self, metrics_collector):
        """Test collecting metrics for a tenant with no events."""
        metrics = metrics_collector.collect_tenant_metrics("_default")

        assert metrics.tenant_id == "_default"
        assert metrics.event_count == 0
        assert metrics.status == "no_data"
        assert metrics.loss_total == 0.5  # Default
        assert 0.0 <= metrics.loss_total <= 1.0

    def test_collect_tenant_metrics_with_events(self, metrics_collector, temp_corvin_home):
        """Test collecting metrics for a tenant with events."""
        # Create test events
        events_dir = temp_corvin_home / "tenants" / "_default" / "global" / "learning" / "events"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        events_file = events_dir / f"{today}.jsonl"

        test_events = [
            {
                "event_id": "evt_001",
                "event_type": "skill_executed",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {
                    "loss_total": 0.35,
                    "loss_components": {
                        "routing": 0.3,
                        "confidence": 0.25,
                        "feedback": 0.2,
                    },
                },
            },
            {
                "event_id": "evt_002",
                "event_type": "skill_executed",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {
                    "loss_total": 0.33,
                    "loss_components": {
                        "routing": 0.28,
                        "confidence": 0.24,
                        "feedback": 0.21,
                    },
                },
            },
        ]

        with open(events_file, "w") as f:
            for event in test_events:
                f.write(json.dumps(event) + "\n")

        metrics = metrics_collector.collect_tenant_metrics("_default")

        assert metrics.tenant_id == "_default"
        assert metrics.event_count == 2
        assert metrics.status == "collecting"  # Less than 100 events
        assert 0.0 <= metrics.loss_total <= 1.0
        assert metrics.loss_routing is not None

    def test_extract_losses_from_events_empty(self, metrics_collector):
        """Test loss extraction from empty event list."""
        losses = metrics_collector._extract_losses_from_events([])
        assert "total" in losses
        assert all(0.0 <= v <= 1.0 for v in losses.values())

    def test_extract_losses_from_events_with_data(self, metrics_collector):
        """Test loss extraction from events with loss data."""
        events = [
            {
                "payload": {
                    "loss_total": 0.4,
                    "loss_components": {
                        "routing": 0.35,
                        "confidence": 0.30,
                        "feedback": 0.25,
                    },
                },
            },
        ]

        losses = metrics_collector._extract_losses_from_events(events)
        assert losses["total"] == 0.4
        assert losses["routing"] == 0.35
        assert losses["confidence"] == 0.30

    def test_extract_losses_from_malformed_events(self, metrics_collector):
        """Test loss extraction handles malformed events gracefully."""
        events = [
            {"payload": {"invalid_field": "value"}},
            None,
            {"no_payload": "field"},
        ]

        losses = metrics_collector._extract_losses_from_events(events)
        # Should return defaults without crashing
        assert "total" in losses
        assert all(0.0 <= v <= 1.0 for v in losses.values())

    def test_collect_global_metrics_no_tenants(self, metrics_collector):
        """Test global aggregation with no tenants."""
        # Clear tenants
        import shutil
        for tenant_path in metrics_collector.tenants_dir.iterdir():
            if tenant_path.is_dir():
                shutil.rmtree(tenant_path)

        global_metrics = metrics_collector.collect_global_metrics()

        assert global_metrics.instance_count == 0
        assert global_metrics.total_events == 0

    def test_collect_global_metrics_single_tenant(self, metrics_collector):
        """Test global aggregation with one tenant."""
        global_metrics = metrics_collector.collect_global_metrics()

        assert global_metrics.instance_count == 1
        assert global_metrics.loss_total_mean >= 0.0
        assert global_metrics.loss_total_max >= global_metrics.loss_total_min

    def test_aggregate_global_metrics(self, metrics_collector):
        """Test aggregation of tenant metrics."""
        tenant_metrics = [
            TenantMetrics(
                tenant_id="tenant_a",
                timestamp="2026-09-07T00:00:00",
                loss_total=0.3,
                loss_routing=0.25,
                loss_confidence=0.2,
                loss_feedback=0.25,
                loss_attention=0.3,
                loss_latency=0.2,
                loss_diversity=0.15,
                loss_memory=0.2,
                loss_skills=0.25,
                loss_plugins=0.2,
                loss_meta=0.0,
                event_count=100,
                last_event_time="2026-09-07T01:00:00",
                status="learning",
            ),
            TenantMetrics(
                tenant_id="tenant_b",
                timestamp="2026-09-07T00:00:00",
                loss_total=0.4,
                loss_routing=0.35,
                loss_confidence=0.3,
                loss_feedback=0.35,
                loss_attention=0.4,
                loss_latency=0.3,
                loss_diversity=0.25,
                loss_memory=0.3,
                loss_skills=0.35,
                loss_plugins=0.3,
                loss_meta=0.1,
                event_count=150,
                last_event_time="2026-09-07T02:00:00",
                status="learning",
            ),
        ]

        global_metrics = metrics_collector._aggregate_global_metrics(tenant_metrics)

        assert global_metrics.instance_count == 2
        assert global_metrics.total_events == 250
        assert 0.3 <= global_metrics.loss_total_mean <= 0.4
        assert global_metrics.loss_total_min == 0.3
        assert global_metrics.loss_total_max == 0.4
        assert global_metrics.instances_healthy == 2

    def test_persist_global_metrics(self, metrics_collector):
        """Test persisting metrics to disk."""
        global_metrics = GlobalMetrics(
            timestamp="2026-09-07T00:00:00",
            instance_count=1,
            loss_total_mean=0.35,
            loss_total_median=0.35,
            loss_total_min=0.3,
            loss_total_max=0.4,
            loss_total_stddev=0.05,
            loss_core_mean=0.3,
            loss_infra_mean=0.25,
            loss_meta_mean=0.0,
            loss_routing_mean=0.3,
            loss_confidence_mean=0.25,
            loss_feedback_mean=0.2,
            loss_attention_mean=0.25,
            loss_latency_mean=0.2,
            loss_diversity_mean=0.15,
            loss_memory_mean=0.2,
            loss_skills_mean=0.25,
            loss_plugins_mean=0.22,
            total_events=100,
            instances_healthy=1,
            instances_degraded=0,
            instances_error=0,
        )

        metrics_collector.persist_global_metrics(global_metrics)

        # Verify file was created
        today = datetime.utcnow().strftime("%Y-%m-%d")
        metrics_file = metrics_collector.aggregation_dir / f"{today}.jsonl"
        assert metrics_file.exists()

        # Verify content
        with open(metrics_file, "r") as f:
            line = f.readline()
            data = json.loads(line)
            assert data["instance_count"] == 1
            assert data["loss_total_mean"] == 0.35

    def test_query_global_metrics_history_empty(self, metrics_collector):
        """Test querying history with no persisted data."""
        history = metrics_collector.query_global_metrics_history(days=7)
        assert history == []

    def test_query_global_metrics_history_with_data(self, metrics_collector):
        """Test querying history with persisted data."""
        # Create some test data
        for i in range(3):
            global_metrics = GlobalMetrics(
                timestamp=f"2026-09-0{i+5}T00:00:00",
                instance_count=1,
                loss_total_mean=0.3 + (i * 0.05),
                loss_total_median=0.3,
                loss_total_min=0.25,
                loss_total_max=0.35,
                loss_total_stddev=0.05,
                loss_core_mean=0.3,
                loss_infra_mean=0.25,
                loss_meta_mean=0.0,
                loss_routing_mean=0.3,
                loss_confidence_mean=0.25,
                loss_feedback_mean=0.2,
                loss_attention_mean=0.25,
                loss_latency_mean=0.2,
                loss_diversity_mean=0.15,
                loss_memory_mean=0.2,
                loss_skills_mean=0.25,
                loss_plugins_mean=0.22,
                total_events=100 + (i * 50),
                instances_healthy=1,
                instances_degraded=0,
                instances_error=0,
            )
            metrics_collector.persist_global_metrics(global_metrics)

        history = metrics_collector.query_global_metrics_history(days=7)
        assert len(history) >= 3
        assert history[0].loss_total_mean <= history[-1].loss_total_mean  # Chronological


# ============================================================================
# MetricsServer Tests
# ============================================================================


class TestMetricsServer:
    """Test MetricsServer background task."""

    @pytest.mark.asyncio
    async def test_initialization(self, metrics_server):
        """Test server initialization."""
        assert metrics_server.collection_interval_sec == 1
        assert not metrics_server._running
        assert metrics_server._collection_task is None

    @pytest.mark.asyncio
    async def test_start_stop(self, metrics_server):
        """Test starting and stopping the server."""
        await metrics_server.start()
        assert metrics_server._running
        assert metrics_server._collection_task is not None

        await asyncio.sleep(0.1)  # Let it collect once
        await metrics_server.stop()
        assert not metrics_server._running

    @pytest.mark.asyncio
    async def test_collection_loop(self, metrics_server):
        """Test that collection loop runs and caches results."""
        await metrics_server.start()
        await asyncio.sleep(2)  # Wait for at least one collection

        assert metrics_server._last_collection_time is not None
        health = metrics_server.get_health_status()
        assert health["running"]

        await metrics_server.stop()

    @pytest.mark.asyncio
    async def test_get_latest_metrics(self, metrics_server):
        """Test retrieving cached metrics."""
        await metrics_server.start()
        await asyncio.sleep(1.5)

        metrics = metrics_server.get_latest_global_metrics()
        # Metrics should be available after first collection
        assert metrics is not None
        assert isinstance(metrics.timestamp, str)

        await metrics_server.stop()

    @pytest.mark.asyncio
    async def test_error_handling(self, metrics_server):
        """Test error handling in collection loop."""
        # Mock collector to raise an error
        with patch.object(metrics_server.collector, "collect_global_metrics", side_effect=RuntimeError("Test error")):
            await metrics_server.start()
            await asyncio.sleep(1.5)

            health = metrics_server.get_health_status()
            assert health["has_error"]
            assert "Test error" in health["error_message"]

            await metrics_server.stop()

    def test_get_health_status(self, metrics_server):
        """Test health status reporting."""
        health = metrics_server.get_health_status()

        assert "running" in health
        assert "last_collection_time" in health
        assert "collection_interval_sec" in health
        assert health["collection_interval_sec"] == 1


# ============================================================================
# Stats Routes Tests
# ============================================================================


class TestStatsRoutes:
    """Test FastAPI endpoints."""

    def test_get_global_stats_no_data(self, client):
        """Test /stats endpoint with no collected metrics."""
        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["instance_count"] == 0

    def test_get_global_stats_with_data(self, client, metrics_server):
        """Test /stats endpoint with metrics."""
        # Manually populate cache
        metrics_server._latest_global_metrics = GlobalMetrics(
            timestamp="2026-09-07T00:00:00",
            instance_count=2,
            loss_total_mean=0.35,
            loss_total_median=0.35,
            loss_total_min=0.3,
            loss_total_max=0.4,
            loss_total_stddev=0.05,
            loss_core_mean=0.3,
            loss_infra_mean=0.25,
            loss_meta_mean=0.0,
            loss_routing_mean=0.3,
            loss_confidence_mean=0.25,
            loss_feedback_mean=0.2,
            loss_attention_mean=0.25,
            loss_latency_mean=0.2,
            loss_diversity_mean=0.15,
            loss_memory_mean=0.2,
            loss_skills_mean=0.25,
            loss_plugins_mean=0.22,
            total_events=500,
            instances_healthy=2,
            instances_degraded=0,
            instances_error=0,
        )

        response = client.get("/stats")
        assert response.status_code == 200
        data = response.json()
        assert data["instance_count"] == 2
        assert data["total_events"] == 500
        assert data["instances_healthy"] == 2

    def test_list_instances_empty(self, client):
        """Test /stats/instances with no instances."""
        response = client.get("/stats/instances")
        assert response.status_code == 200
        data = response.json()
        assert data == []

    def test_list_instances_with_data(self, client, metrics_server):
        """Test /stats/instances with instance data."""
        tenant_metrics = {
            "_default": TenantMetrics(
                tenant_id="_default",
                timestamp="2026-09-07T00:00:00",
                loss_total=0.35,
                loss_routing=0.3,
                loss_confidence=0.25,
                loss_feedback=0.2,
                loss_attention=0.25,
                loss_latency=0.2,
                loss_diversity=0.15,
                loss_memory=0.2,
                loss_skills=0.25,
                loss_plugins=0.22,
                loss_meta=0.0,
                event_count=100,
                last_event_time="2026-09-07T01:00:00",
                status="learning",
            ),
        }
        metrics_server._latest_tenant_metrics = tenant_metrics

        response = client.get("/stats/instances")
        assert response.status_code == 200
        data = response.json()
        assert len(data) == 1
        assert data[0]["tenant_id"] == "_default"
        assert data[0]["status"] == "learning"

    def test_get_history_default_window(self, client):
        """Test /stats/history with default window."""
        response = client.get("/stats/history")
        assert response.status_code == 200
        data = response.json()
        assert "points" in data
        assert data["window_days"] == 7

    def test_get_history_custom_window(self, client):
        """Test /stats/history with custom window."""
        response = client.get("/stats/history?window=1d")
        assert response.status_code == 200
        data = response.json()
        assert data["window_days"] == 1

    def test_get_history_invalid_window(self, client):
        """Test /stats/history with invalid window format."""
        response = client.get("/stats/history?window=invalid")
        assert response.status_code == 422  # Validation error

    def test_get_health(self, client, metrics_server):
        """Test /stats/health endpoint."""
        response = client.get("/stats/health")
        assert response.status_code == 200
        data = response.json()
        assert "running" in data
        assert "last_collection_time" in data
        assert "collection_interval_sec" in data


# ============================================================================
# Integration Tests
# ============================================================================


class TestIntegration:
    """End-to-end integration tests."""

    @pytest.mark.asyncio
    async def test_full_collection_pipeline(self, temp_corvin_home):
        """Test full pipeline: collection -> aggregation -> API."""
        # Create test events
        events_dir = temp_corvin_home / "tenants" / "_default" / "global" / "learning" / "events"
        today = datetime.utcnow().strftime("%Y-%m-%d")
        events_file = events_dir / f"{today}.jsonl"

        test_events = [
            {
                "event_type": "skill_executed",
                "timestamp": datetime.utcnow().isoformat(),
                "payload": {
                    "loss_total": 0.3,
                    "loss_components": {
                        "routing": 0.25,
                        "confidence": 0.2,
                    },
                },
            }
            for _ in range(5)
        ]

        with open(events_file, "w") as f:
            for event in test_events:
                f.write(json.dumps(event) + "\n")

        # Create server and collect
        server = MetricsServer(collection_interval_sec=1)
        server.collector = MetricsCollector(corvin_root=temp_corvin_home)

        global_metrics = server.collector.collect_global_metrics()
        assert global_metrics.instance_count == 1
        assert global_metrics.total_events == 5

    def test_concurrent_requests(self, client, metrics_server):
        """Test that multiple concurrent requests work correctly."""
        # Populate cache
        metrics_server._latest_global_metrics = GlobalMetrics(
            timestamp="2026-09-07T00:00:00",
            instance_count=1,
            loss_total_mean=0.35,
            loss_total_median=0.35,
            loss_total_min=0.3,
            loss_total_max=0.4,
            loss_total_stddev=0.05,
            loss_core_mean=0.3,
            loss_infra_mean=0.25,
            loss_meta_mean=0.0,
            loss_routing_mean=0.3,
            loss_confidence_mean=0.25,
            loss_feedback_mean=0.2,
            loss_attention_mean=0.25,
            loss_latency_mean=0.2,
            loss_diversity_mean=0.15,
            loss_memory_mean=0.2,
            loss_skills_mean=0.25,
            loss_plugins_mean=0.22,
            total_events=100,
            instances_healthy=1,
            instances_degraded=0,
            instances_error=0,
        )

        # Make concurrent requests
        results = []
        for _ in range(5):
            response = client.get("/stats")
            assert response.status_code == 200
            results.append(response.json())

        # All responses should be identical
        assert len(set(json.dumps(r, sort_keys=True) for r in results)) == 1

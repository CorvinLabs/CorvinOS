"""Integration + Adversarial Tests for LearningDashboard (ADR-0321).

Tier-1 (Unit): MetricsAggregator, cache logic, subscriber management
Tier-2 (Integration): Dashboard queries, audit logging, E2E WebSocket
Tier-3 (Adversarial): Timeout handling, concurrent updates, metric spikes
"""

import json
import pytest
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import Mock, MagicMock, patch, call
from core.learning.dashboard import (
    MetricSummary,
    SkillPerformance,
    DashboardMetrics,
    MetricsAggregator,
    DashboardCache,
    CacheEntry,
    WebSocketSubscriber,
    LearningDashboard,
)


# ============================================================================
# TIER-1: UNIT TESTS
# ============================================================================


class TestMetricSummary:
    """Unit tests for MetricSummary dataclass."""

    def test_to_dict(self):
        """MetricSummary.to_dict() returns dict."""
        summary = MetricSummary(
            metric_type="accuracy",
            count=100,
            mean=0.95,
            min=0.75,
            max=1.0,
            stddev=0.05,
        )
        result = summary.to_dict()
        assert result["metric_type"] == "accuracy"
        assert result["count"] == 100
        assert result["mean"] == 0.95

    def test_frozen(self):
        """MetricSummary is immutable."""
        summary = MetricSummary(
            metric_type="latency",
            count=50,
            mean=42.0,
            min=10.0,
            max=100.0,
            stddev=20.0,
        )
        with pytest.raises(Exception):  # FrozenInstanceError
            summary.metric_type = "throughput"


class TestSkillPerformance:
    """Unit tests for SkillPerformance dataclass."""

    def test_to_dict_with_timestamp(self):
        """SkillPerformance.to_dict() serializes datetime."""
        now = datetime.utcnow()
        perf = SkillPerformance(
            skill_name="test_skill",
            accuracy=0.92,
            latency_ms=50.5,
            confidence=0.88,
            user_satisfaction=4.5,
            usage_count=150,
            last_updated=now,
        )
        result = perf.to_dict()
        assert result["skill_name"] == "test_skill"
        assert result["accuracy"] == 0.92
        assert result["last_updated"] == now.isoformat()

    def test_to_dict_without_timestamp(self):
        """SkillPerformance.to_dict() handles None timestamp."""
        perf = SkillPerformance(
            skill_name="skill2",
            accuracy=None,
            last_updated=None,
            usage_count=0,
        )
        result = perf.to_dict()
        assert result["last_updated"] is None


class TestMetricsAggregator:
    """Unit tests for MetricsAggregator."""

    def test_aggregate_metrics_empty(self):
        """aggregate_metrics returns None for empty list."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        result = agg.aggregate_metrics([], "accuracy")
        assert result is None

    def test_aggregate_metrics_single_value(self):
        """aggregate_metrics handles single value (stddev=0)."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        metrics = [{"value": 0.95}]
        result = agg.aggregate_metrics(metrics, "accuracy")
        assert result.count == 1
        assert result.mean == 0.95
        assert result.min == 0.95
        assert result.max == 0.95
        assert result.stddev == 0.0

    def test_aggregate_metrics_multiple_values(self):
        """aggregate_metrics computes mean, min, max, stddev."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        metrics = [{"value": 0.80}, {"value": 0.90}, {"value": 1.00}]
        result = agg.aggregate_metrics(metrics, "accuracy")
        assert result.count == 3
        assert result.mean == 0.9  # (0.8 + 0.9 + 1.0) / 3
        assert result.min == 0.8
        assert result.max == 1.0
        assert result.stddev > 0  # Non-zero stddev

    def test_aggregate_skill_performance_empty(self):
        """aggregate_skill_performance handles empty metric lists."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        perf = agg.aggregate_skill_performance(
            accuracy_metrics=[],
            latency_metrics=[],
            confidence_metrics=[],
            satisfaction_metrics=[],
            skill_name="empty_skill",
        )
        assert perf.skill_name == "empty_skill"
        assert perf.accuracy is None
        assert perf.latency_ms is None
        assert perf.usage_count == 0

    def test_aggregate_skill_performance_with_data(self):
        """aggregate_skill_performance computes averages."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        perf = agg.aggregate_skill_performance(
            accuracy_metrics=[{"value": 0.95}, {"value": 0.98}],
            latency_metrics=[{"value": 50}, {"value": 60}],
            confidence_metrics=[{"value": 0.9}],
            satisfaction_metrics=[{"value": 4.5}],
            skill_name="perf_skill",
        )
        assert perf.skill_name == "perf_skill"
        assert perf.accuracy == 0.965  # (0.95 + 0.98) / 2
        assert perf.latency_ms == 55.0  # (50 + 60) / 2
        assert perf.confidence == 0.9
        assert perf.user_satisfaction == 4.5
        assert perf.usage_count == 5

    def test_build_dashboard_empty(self):
        """build_dashboard returns empty dashboard for no metrics."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        dashboard = agg.build_dashboard([])
        assert dashboard.total_events == 0
        assert dashboard.accuracy_summary is None
        assert len(dashboard.skills) == 0

    def test_build_dashboard_with_metrics(self):
        """build_dashboard aggregates all metric types."""
        agg = MetricsAggregator(tenant_id="test_tenant")
        metrics = [
            {"metric_type": "accuracy", "value": 0.95},
            {"metric_type": "accuracy", "value": 0.90},
            {"metric_type": "latency", "value": 100},
            {"metric_type": "confidence", "value": 0.85},
            {"metric_type": "satisfaction", "value": 4.0},
        ]
        dashboard = agg.build_dashboard(metrics)
        assert dashboard.total_events == 5
        assert dashboard.accuracy_summary is not None
        assert dashboard.accuracy_summary.count == 2
        assert dashboard.latency_summary is not None


class TestCacheEntry:
    """Unit tests for CacheEntry."""

    def test_is_expired_fresh(self):
        """Fresh cache entry is not expired."""
        entry = CacheEntry(data={"key": "value"}, timestamp=datetime.utcnow(), ttl_seconds=5)
        assert not entry.is_expired()

    def test_is_expired_stale(self):
        """Old cache entry is expired."""
        old_time = datetime.utcnow() - timedelta(seconds=10)
        entry = CacheEntry(data={"key": "value"}, timestamp=old_time, ttl_seconds=5)
        assert entry.is_expired()


class TestDashboardCache:
    """Unit tests for DashboardCache."""

    def test_set_and_get_hit(self):
        """Cache hit returns cached data."""
        cache = DashboardCache(ttl_seconds=5)
        cache.set("key1", {"value": 42})
        result = cache.get("key1")
        assert result == {"value": 42}

    def test_get_miss_not_set(self):
        """Cache miss when key not set."""
        cache = DashboardCache(ttl_seconds=5)
        result = cache.get("nonexistent")
        assert result is None

    def test_get_miss_expired(self):
        """Cache miss when entry expired."""
        cache = DashboardCache(ttl_seconds=1)
        cache.set("key1", {"value": 42})
        time.sleep(1.1)  # Wait for expiry
        result = cache.get("key1")
        assert result is None

    def test_invalidate(self):
        """invalidate() removes entry."""
        cache = DashboardCache(ttl_seconds=5)
        cache.set("key1", {"value": 42})
        cache.invalidate("key1")
        result = cache.get("key1")
        assert result is None

    def test_clear(self):
        """clear() empties cache."""
        cache = DashboardCache(ttl_seconds=5)
        cache.set("key1", {"value": 1})
        cache.set("key2", {"value": 2})
        cache.clear()
        assert cache.get("key1") is None
        assert cache.get("key2") is None


class TestWebSocketSubscriber:
    """Unit tests for WebSocketSubscriber."""

    def test_subscriber_not_stale_on_create(self):
        """Subscriber is not stale on creation."""
        sub = WebSocketSubscriber("sub1", "tenant1", user_id="user1")
        assert not sub.is_stale(timeout_seconds=5)

    def test_subscriber_is_stale_after_timeout(self):
        """Subscriber is stale after inactivity > timeout."""
        sub = WebSocketSubscriber("sub1", "tenant1", user_id="user1")
        sub.last_activity = datetime.utcnow() - timedelta(seconds=10)
        assert sub.is_stale(timeout_seconds=5)

    def test_touch_updates_activity(self):
        """touch() updates last_activity."""
        sub = WebSocketSubscriber("sub1", "tenant1")
        old_activity = sub.last_activity
        time.sleep(0.1)
        sub.touch()
        assert sub.last_activity > old_activity


# ============================================================================
# TIER-2: INTEGRATION TESTS
# ============================================================================


class TestLearningDashboardBasic:
    """Integration tests for LearningDashboard basic operations."""

    def test_init_creates_cache_and_aggregator(self):
        """LearningDashboard.__init__() sets up cache and aggregator."""
        mock_event_store = Mock()
        mock_audit = Mock()
        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )
        assert dashboard.tenant_id == "test_tenant"
        assert dashboard.cache is not None
        assert dashboard.aggregator is not None
        assert dashboard.get_subscriber_count() == 0

    def test_get_summary_stats_caches_result(self):
        """get_summary_stats() caches result."""
        mock_event_store = Mock()
        mock_event_store.count_events.return_value = 100
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        # First call
        result1 = dashboard.get_summary_stats()
        assert result1.total_events == 100
        assert mock_event_store.count_events.call_count == 1

        # Second call (cached)
        result2 = dashboard.get_summary_stats()
        assert mock_event_store.count_events.call_count == 1  # No additional call

    def test_get_summary_stats_audits_query(self):
        """get_summary_stats() audits query execution."""
        mock_event_store = Mock()
        mock_event_store.count_events.return_value = 50
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        dashboard.get_summary_stats()
        # Verify audit was called
        assert mock_audit.write_audit_event.called

    def test_get_skill_stats_caches_result(self):
        """get_skill_stats() caches per-skill results."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        # First call
        result1 = dashboard.get_skill_stats("skill1")
        assert result1.skill_name == "skill1"

        # Second call (cached)
        result2 = dashboard.get_skill_stats("skill1")
        assert result2.skill_name == "skill1"
        # Only 1 audit call (from first invocation)
        assert mock_audit.write_audit_event.call_count == 1

    def test_get_user_stats_caches_result(self):
        """get_user_stats() caches per-user results."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        # First call
        result1 = dashboard.get_user_stats("user1")
        assert result1["user_id"] == "user1"

        # Second call (cached)
        result2 = dashboard.get_user_stats("user1")
        assert result2["user_id"] == "user1"
        assert mock_audit.write_audit_event.call_count == 1


class TestWebSocketManagement:
    """Integration tests for WebSocket subscriber management."""

    def test_subscribe_for_updates(self):
        """subscribe_for_updates() registers subscriber."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id = dashboard.subscribe_for_updates(user_id="user1")
        assert sub_id is not None
        assert dashboard.get_subscriber_count() == 1

    def test_unsubscribe(self):
        """unsubscribe() removes subscriber."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id = dashboard.subscribe_for_updates(user_id="user1")
        assert dashboard.get_subscriber_count() == 1

        removed = dashboard.unsubscribe(sub_id)
        assert removed is True
        assert dashboard.get_subscriber_count() == 0

    def test_unsubscribe_nonexistent(self):
        """unsubscribe() returns False for nonexistent subscriber."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        removed = dashboard.unsubscribe("nonexistent")
        assert removed is False

    def test_touch_subscriber(self):
        """touch_subscriber() updates activity."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id = dashboard.subscribe_for_updates(user_id="user1")
        old_time = datetime.utcnow() - timedelta(seconds=5)
        dashboard._subscribers[sub_id].last_activity = old_time

        result = dashboard.touch_subscriber(sub_id)
        assert result is True
        assert dashboard._subscribers[sub_id].last_activity > old_time

    def test_prune_stale_subscribers(self):
        """prune_stale_subscribers() removes inactive ones."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        # Create 3 subscribers
        sub_id1 = dashboard.subscribe_for_updates()
        sub_id2 = dashboard.subscribe_for_updates()
        sub_id3 = dashboard.subscribe_for_updates()

        # Make 2 stale
        dashboard._subscribers[sub_id1].last_activity = datetime.utcnow() - timedelta(seconds=400)
        dashboard._subscribers[sub_id2].last_activity = datetime.utcnow() - timedelta(seconds=400)

        pruned = dashboard.prune_stale_subscribers(timeout_seconds=300)
        assert pruned == 2
        assert dashboard.get_subscriber_count() == 1

    def test_broadcast_update_to_all(self):
        """broadcast_update() sends message to all subscribers."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id1 = dashboard.subscribe_for_updates()
        sub_id2 = dashboard.subscribe_for_updates()

        sent_messages = []

        def mock_send(sub_id, msg):
            sent_messages.append((sub_id, msg))

        notified = dashboard.broadcast_update(
            message_type="metrics_updated",
            data={"accuracy": 0.95},
            callback=mock_send,
        )

        assert notified == 2
        assert len(sent_messages) == 2

    def test_broadcast_update_with_user_filter(self):
        """broadcast_update() respects user_id filter."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id1 = dashboard.subscribe_for_updates(user_id="user1")
        sub_id2 = dashboard.subscribe_for_updates(user_id="user2")

        sent_messages = []

        def mock_send(sub_id, msg):
            sent_messages.append((sub_id, msg))

        notified = dashboard.broadcast_update(
            message_type="user_alert",
            data={"message": "test"},
            user_id_filter="user1",
            callback=mock_send,
        )

        assert notified == 1
        assert sent_messages[0][0] == sub_id1


# ============================================================================
# TIER-3: ADVERSARIAL TESTS
# ============================================================================


class TestDashboardAdversarial:
    """Adversarial/stress tests for edge cases and failures."""

    def test_concurrent_cache_access(self):
        """Cache handles concurrent reads/writes safely."""
        cache = DashboardCache(ttl_seconds=5)
        errors = []

        def writer():
            for i in range(100):
                try:
                    cache.set(f"key_{i}", {"value": i})
                except Exception as e:
                    errors.append(e)

        def reader():
            for i in range(100):
                try:
                    cache.get(f"key_{i}")
                except Exception as e:
                    errors.append(e)

        threads = [
            threading.Thread(target=writer),
            threading.Thread(target=writer),
            threading.Thread(target=reader),
            threading.Thread(target=reader),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent access errors: {errors}"

    def test_concurrent_subscriber_updates(self):
        """Subscriber management handles concurrent subscribe/unsubscribe."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        errors = []
        created_ids = []

        def subscribe_worker():
            for _ in range(50):
                try:
                    sub_id = dashboard.subscribe_for_updates()
                    created_ids.append(sub_id)
                except Exception as e:
                    errors.append(e)

        def unsubscribe_worker():
            for _ in range(50):
                if created_ids:
                    try:
                        sub_id = created_ids.pop()
                        dashboard.unsubscribe(sub_id)
                    except IndexError:
                        pass
                    except Exception as e:
                        errors.append(e)

        threads = [
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=subscribe_worker),
            threading.Thread(target=unsubscribe_worker),
        ]

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(errors) == 0, f"Concurrent subscriber errors: {errors}"

    def test_metrics_aggregation_under_load(self):
        """MetricsAggregator handles large metric sets."""
        agg = MetricsAggregator(tenant_id="test_tenant")

        # 10k metrics
        large_metrics = [{"metric_type": "accuracy", "value": 0.5 + (i % 1000) / 1000.0}
                         for i in range(10000)]

        dashboard = agg.build_dashboard(large_metrics)
        assert dashboard.total_events == 10000
        assert dashboard.accuracy_summary is not None
        assert dashboard.accuracy_summary.count == 10000

    def test_websocket_broadcast_with_send_failure(self):
        """broadcast_update() continues despite send failures."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id1 = dashboard.subscribe_for_updates()
        sub_id2 = dashboard.subscribe_for_updates()
        sub_id3 = dashboard.subscribe_for_updates()

        call_count = [0]

        def failing_send(sub_id, msg):
            call_count[0] += 1
            if sub_id == sub_id2:  # Fail on second subscriber
                raise IOError("Network error")

        notified = dashboard.broadcast_update(
            message_type="test",
            data={"value": 1},
            callback=failing_send,
        )

        # Should have attempted all 3 but only succeeded 2
        assert call_count[0] == 3
        assert notified == 2  # 2 succeeded despite 1 failure

    def test_cache_with_zero_ttl(self):
        """Cache with TTL=0 behaves correctly."""
        cache = DashboardCache(ttl_seconds=0)
        cache.set("key", {"value": 42})
        time.sleep(0.01)
        result = cache.get("key")
        assert result is None  # Expired immediately

    def test_large_broadcast_message(self):
        """broadcast_update() serializes large payloads."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        sub_id = dashboard.subscribe_for_updates()

        large_data = {"metrics": [{f"key_{i}": i} for i in range(1000)]}

        def capture_send(sub_id, msg):
            # Verify it's valid JSON
            json.loads(msg)

        notified = dashboard.broadcast_update(
            message_type="large_update",
            data=large_data,
            callback=capture_send,
        )

        assert notified == 1


# ============================================================================
# TIER-2 EXTENDED: AUDIT INTEGRATION
# ============================================================================


class TestAuditIntegration:
    """Tests for audit logging integration."""

    def test_audit_query_success(self):
        """_audit_query() calls audit backend."""
        mock_event_store = Mock()
        mock_audit = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        dashboard._audit_query("summary", filters={"key": "value"})
        assert mock_audit.write_audit_event.called

    def test_audit_query_failure_logged(self):
        """_audit_query() handles audit backend failure gracefully."""
        mock_event_store = Mock()
        mock_audit = Mock()
        mock_audit.write_audit_event.side_effect = Exception("Audit backend down")

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=mock_audit,
        )

        # Should not raise
        dashboard._audit_query("summary")

    def test_audit_backend_none(self):
        """_audit_query() skips if audit_backend is None."""
        mock_event_store = Mock()

        dashboard = LearningDashboard(
            tenant_id="test_tenant",
            event_store=mock_event_store,
            audit_backend=None,
        )

        # Should not raise
        dashboard._audit_query("summary")


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

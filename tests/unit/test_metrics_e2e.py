"""E2E tests for Metrics Collection (ADR-0320)."""

import pytest
from pathlib import Path
from tempfile import TemporaryDirectory

from core.learning.metrics import MetricsCollector, MetricType
from core.learning.event_emitter import EventEmitter


@pytest.fixture
def temp_tenant_home():
    """Create a temporary tenant home directory."""
    with TemporaryDirectory() as tmpdir:
        tenant_home = Path(tmpdir) / "tenants" / "_default"
        tenant_home.mkdir(parents=True, exist_ok=True)
        yield tenant_home


class TestMetricsE2E:
    """End-to-end tests for metrics."""

    @pytest.mark.asyncio
    async def test_record_and_emit_metric(self, temp_tenant_home):
        """Record a metric and emit as event."""
        collector = MetricsCollector("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record metric
        metric = collector.record_accuracy(
            session_id="session-123",
            value=0.92,
            skill_name="ranking",
        )

        # Emit as event
        await emitter.emit_metric(
            metric_id=metric.metric_id,
            metric_type=metric.metric_type.value,
            value=metric.value,
            session_id=metric.session_id,
            skill_name=metric.skill_name,
            tags=metric.tags,
        )

        await emitter.flush()
        await emitter.stop()

        # Read back
        metrics = await emitter.store.read_metrics("_default", session_id="session-123")
        assert len(metrics) == 1
        assert metrics[0]["metric_type"] == "accuracy"
        assert metrics[0]["value"] == 0.92

    @pytest.mark.asyncio
    async def test_multiple_metric_types(self, temp_tenant_home):
        """Emit different metric types."""
        collector = MetricsCollector("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record multiple metrics
        accuracy = collector.record_accuracy("s1", 0.85, skill_name="summarizer")
        latency = collector.record_latency("s1", 245.5, skill_name="summarizer")
        confidence = collector.record_confidence("s1", 0.72, skill_name="summarizer")

        # Emit all
        for metric in [accuracy, latency, confidence]:
            await emitter.emit_metric(
                metric_id=metric.metric_id,
                metric_type=metric.metric_type.value,
                value=metric.value,
                session_id=metric.session_id,
                skill_name=metric.skill_name,
            )

        await emitter.flush()
        await emitter.stop()

        # Read all metrics
        all_metrics = await emitter.store.read_metrics("_default")
        assert len(all_metrics) == 3

        # Filter by type
        accuracy_metrics = await emitter.store.read_metrics(
            "_default", metric_type="accuracy"
        )
        assert len(accuracy_metrics) == 1
        assert accuracy_metrics[0]["value"] == 0.85

    @pytest.mark.asyncio
    async def test_filter_by_skill_and_session(self, temp_tenant_home):
        """Filter metrics by skill and session."""
        collector = MetricsCollector("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Emit metrics for different skills/sessions
        m1 = collector.record_latency("s1", 100.0, skill_name="skill-a")
        m2 = collector.record_latency("s2", 150.0, skill_name="skill-a")
        m3 = collector.record_latency("s1", 200.0, skill_name="skill-b")

        for m in [m1, m2, m3]:
            await emitter.emit_metric(
                metric_id=m.metric_id,
                metric_type=m.metric_type.value,
                value=m.value,
                session_id=m.session_id,
                skill_name=m.skill_name,
            )

        await emitter.flush()
        await emitter.stop()

        # Filter by skill-a
        skill_a = await emitter.store.read_metrics(
            "_default", skill_name="skill-a"
        )
        assert len(skill_a) == 2

        # Filter by session s1
        session_s1 = await emitter.store.read_metrics(
            "_default", session_id="s1"
        )
        assert len(session_s1) == 2

        # Filter by skill-a AND session s1
        specific = await emitter.store.read_metrics(
            "_default", skill_name="skill-a", session_id="s1"
        )
        assert len(specific) == 1
        assert specific[0]["value"] == 100.0

    @pytest.mark.asyncio
    async def test_metrics_with_tags(self, temp_tenant_home):
        """Metrics can include metadata tags."""
        collector = MetricsCollector("_default")
        emitter = EventEmitter(temp_tenant_home, "_default")

        await emitter.start()

        # Record metric with tags
        metric = collector.record_accuracy(
            session_id="session-123",
            value=0.88,
            skill_name="code_review",
            tags={"model": "opus", "engine": "claude"},
        )

        await emitter.emit_metric(
            metric_id=metric.metric_id,
            metric_type=metric.metric_type.value,
            value=metric.value,
            session_id=metric.session_id,
            skill_name=metric.skill_name,
            tags=metric.tags,
        )

        await emitter.flush()
        await emitter.stop()

        # Read back with tags
        metrics = await emitter.store.read_metrics("_default")
        assert len(metrics) == 1
        assert metrics[0]["tags"]["model"] == "opus"

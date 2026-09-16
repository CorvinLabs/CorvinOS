"""Test suite for ConfidenceMetricsCalculator (25 tests, k=4 Track A)."""

import tempfile
import json
import time
from pathlib import Path
from datetime import datetime, timedelta
import pytest

from core.skills.confidence_metrics_calculator import ConfidenceMetricsCalculator
from core.compliance.audit_chain_writer import AuditChainWriter
from core.skills.models.learning_event import (
    LearningEventStore, SkillExecutedEvent, ConfidenceScoreEvent, LearningEventType
)


@pytest.fixture
def temp_audit_path():
    with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
        path = f.name
    yield path
    Path(path).unlink(missing_ok=True)


@pytest.fixture
def audit_chain(temp_audit_path):
    return AuditChainWriter(temp_audit_path)


@pytest.fixture
def event_store():
    return LearningEventStore("_default")


@pytest.fixture
def calculator(audit_chain, event_store):
    return ConfidenceMetricsCalculator("_default", audit_chain, event_store)


class TestConfidenceHistory:
    """Test confidence history retrieval and caching."""

    def test_get_confidence_history_empty(self, calculator):
        result = calculator.get_confidence_history("test.skill")
        assert result["skill_id"] == "test.skill"
        assert result["data_points"] == []
        assert result["converged"] is False

    def test_get_confidence_history_with_events(self, calculator):
        # Add some events to store
        for i in range(5):
            event = ConfidenceScoreEvent(
                skill_id="test.skill",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
                input={},
                output={"confidence": 0.5 + i * 0.1},
                confidence=0.5 + i * 0.1,
                basis="test",
            )
            calculator.event_store.append_event(event)

        result = calculator.get_confidence_history("test.skill")
        assert len(result["data_points"]) == 5
        assert result["current_confidence"] == 0.9

    def test_confidence_history_hours_filter(self, calculator):
        # Recent event
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 0.8},
            confidence=0.8,
            basis="test",
        )
        calculator.event_store.append_event(event)

        # Request 1 hour history
        result = calculator.get_confidence_history("test.skill", hours=1)
        assert len(result["data_points"]) == 1

    def test_cache_ttl(self, calculator):
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 0.8},
            confidence=0.8,
            basis="test",
        )
        calculator.event_store.append_event(event)

        # First call
        result1 = calculator.get_confidence_history("test.skill")

        # Second call within cache TTL should return cached data
        result2 = calculator.get_confidence_history("test.skill")
        assert result1 == result2

    def test_cache_clear(self, calculator):
        calculator._cache["test"] = (time.time(), {"test": "data"})
        calculator.clear_cache()
        assert len(calculator._cache) == 0

    def test_convergence_band_calculated(self, calculator):
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 0.75},
            confidence=0.75,
            basis="test",
        )
        calculator.event_store.append_event(event)

        result = calculator.get_confidence_history("test.skill")
        dp = result["data_points"][0]
        assert dp["convergence_band_upper"] == 0.80
        assert dp["convergence_band_lower"] == 0.70


class TestPerModelConfidence:
    """Test per-model trust score aggregation."""

    def test_per_model_confidence_empty(self, calculator):
        result = calculator.get_per_model_confidence()
        assert result == {}

    def test_per_model_confidence_single_model(self, calculator):
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 0.8, "model_id": "claude-opus-4"},
            confidence=0.8,
            basis="test",
        )
        calculator.event_store.append_event(event)

        result = calculator.get_per_model_confidence()
        assert "claude-opus-4" in result
        assert result["claude-opus-4"] == 0.8

    def test_per_model_confidence_averaging(self, calculator):
        for conf in [0.6, 0.7, 0.8]:
            event = ConfidenceScoreEvent(
                skill_id="test.skill",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
                input={},
                output={"confidence": conf, "model_id": "claude-opus-4"},
                confidence=conf,
                basis="test",
            )
            calculator.event_store.append_event(event)

        result = calculator.get_per_model_confidence()
        assert abs(result["claude-opus-4"] - 0.7) < 0.01  # Average


class TestConvergenceRate:
    """Test convergence rate estimation."""

    def test_convergence_rate_insufficient_data(self, calculator):
        result = calculator.get_convergence_rate("test.skill")
        assert result["estimated_weeks"] == 0.0

    def test_convergence_rate_with_data(self, calculator):
        for i in range(10):
            event = ConfidenceScoreEvent(
                skill_id="test.skill",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
                input={},
                output={"confidence": 0.7 + i * 0.01},
                confidence=0.7 + i * 0.01,
                basis="test",
            )
            calculator.event_store.append_event(event)

        result = calculator.get_convergence_rate("test.skill")
        assert result["skill_id"] == "test.skill"
        assert "estimated_weeks" in result
        assert result["convergence_threshold"] == 0.05


class TestAuditIntegration:
    """Test audit trail integration."""

    def test_dashboard_view_audit_written(self, calculator, temp_audit_path):
        calculator.get_confidence_history("test.skill")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        assert len(events) > 0
        view_events = [e for e in events if e["event_type"] == "dashboard_view"]
        assert len(view_events) > 0

    def test_metric_accessed_audit_written(self, calculator, temp_audit_path):
        calculator.get_per_model_confidence()

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        accessed_events = [e for e in events if e["event_type"] == "metric_accessed"]
        assert len(accessed_events) > 0

    def test_audit_fail_closed(self, calculator):
        calculator.audit_chain.log_path = Path("/nonexistent/audit.jsonl")

        with pytest.raises(RuntimeError, match="Audit chain write failed"):
            calculator.get_confidence_history("test.skill")

    def test_audit_chain_integrity(self, calculator, temp_audit_path):
        calculator.get_confidence_history("test.skill")
        calculator.get_per_model_confidence()

        with open(temp_audit_path, 'r') as f:
            lines = f.readlines()

        if len(lines) > 1:
            prev = json.loads(lines[0])
            curr = json.loads(lines[1])
            assert curr["prev_hash"] == prev["hash"]

    def test_tenant_isolation(self, temp_audit_path):
        audit_chain = AuditChainWriter(temp_audit_path)

        calc1 = ConfidenceMetricsCalculator("tenant_1", audit_chain, LearningEventStore("tenant_1"))
        calc2 = ConfidenceMetricsCalculator("tenant_2", audit_chain, LearningEventStore("tenant_2"))

        calc1.get_confidence_history("skill1")
        calc2.get_confidence_history("skill2")

        with open(temp_audit_path, 'r') as f:
            events = [json.loads(line) for line in f.readlines()]

        tenants = {e["tenant_id"] for e in events}
        assert "tenant_1" in tenants and "tenant_2" in tenants


class TestPerformanceSLA:
    """Test performance SLAs (<500ms load, <100ms metric updates)."""

    def test_get_history_under_500ms(self, calculator):
        for i in range(10):
            event = ConfidenceScoreEvent(
                skill_id="test.skill",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
                input={},
                output={"confidence": 0.5 + i * 0.01},
                confidence=0.5 + i * 0.01,
                basis="test",
            )
            calculator.event_store.append_event(event)

        start = time.time()
        calculator.get_confidence_history("test.skill")
        elapsed = (time.time() - start) * 1000

        assert elapsed < 500, f"Dashboard load took {elapsed:.1f}ms (SLA: <500ms)"

    def test_metric_update_under_100ms(self, calculator):
        for i in range(20):
            event = ConfidenceScoreEvent(
                skill_id="test.skill",
                tenant_id="_default",
                timestamp=datetime.utcnow(),
                input={},
                output={"confidence": 0.5 + i * 0.01, "model_id": "claude-opus-4"},
                confidence=0.5 + i * 0.01,
                basis="test",
            )
            calculator.event_store.append_event(event)

        start = time.time()
        calculator.get_per_model_confidence()
        elapsed = (time.time() - start) * 1000

        assert elapsed < 100, f"Metric update took {elapsed:.1f}ms (SLA: <100ms)"


class TestEdgeCases:
    """Test edge cases."""

    def test_skill_not_found(self, calculator):
        result = calculator.get_confidence_history("nonexistent")
        assert result["data_points"] == []

    def test_zero_confidence(self, calculator):
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 0.0},
            confidence=0.0,
            basis="test",
        )
        calculator.event_store.append_event(event)

        result = calculator.get_confidence_history("test.skill")
        assert result["current_confidence"] == 0.0

    def test_high_confidence(self, calculator):
        event = ConfidenceScoreEvent(
            skill_id="test.skill",
            tenant_id="_default",
            timestamp=datetime.utcnow(),
            input={},
            output={"confidence": 1.0},
            confidence=1.0,
            basis="test",
        )
        calculator.event_store.append_event(event)

        result = calculator.get_confidence_history("test.skill")
        assert result["current_confidence"] == 1.0

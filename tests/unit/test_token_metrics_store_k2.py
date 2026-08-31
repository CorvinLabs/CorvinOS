"""Unit tests for Phase 1.K2: TokenMetricsStore (EventStore persistence).

Tests:
- Write token metrics to store
- Query by turn, session, timespan
- Aggregate by task type and subsystem
- Summary calculation
"""

from datetime import datetime, timedelta

import pytest

from core.learning.token_instrumentation import TokenCounter, TokenInstrumentationHooks
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.event_emitter import EventEmitter
from core.learning.event_schema import LearningEventType


class MockEventEmitter:
    """Mock EventEmitter for testing (doesn't write to audit trail)."""

    def __init__(self):
        self.events = []

    def emit(self, event):
        """Record event in memory."""
        self.events.append(event)


class TestTokenMetricsStore:
    """Test TokenMetricsStore persistence."""

    @pytest.fixture
    def store(self):
        """Create test store with mock emitter."""
        emitter = MockEventEmitter()
        return TokenMetricsStore(emitter)

    def test_write_token_metrics(self, store):
        """Test writing token metrics to store."""
        counter = TokenCounter(turn_id="turn_001", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1200, output_tokens=800)
        counter.record_subsystem_usage("confidence", 200)
        counter.baseline_tokens = 2800
        counter.task_type = "code"
        counter.finalize()

        event_id = store.write_token_metrics(
            counter,
            tenant_id="test-tenant",
            instance_id="inst-001",
            session_id="sess-001",
            user_id="user-001",
        )

        assert event_id is not None
        assert len(store._cache) == 1

    def test_query_by_turn(self, store):
        """Test retrieving metrics for a specific turn."""
        counter = TokenCounter(turn_id="turn_query", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1200, output_tokens=800)
        counter.baseline_tokens = 2800
        counter.finalize()

        store.write_token_metrics(
            counter,
            tenant_id="test-tenant",
            instance_id="inst-001",
            session_id="sess-001",
        )

        event = store.query_by_turn("turn_query")
        assert event is not None
        assert event.event_type == LearningEventType.TOKEN_METRICS

    def test_query_by_session(self, store):
        """Test retrieving all metrics for a session."""
        for i in range(3):
            counter = TokenCounter(turn_id=f"turn_{i:03d}", engine="claude-opus-5")
            counter.record_llm_call(input_tokens=1000 + i, output_tokens=800)
            counter.baseline_tokens = 2800
            counter.finalize()

            store.write_token_metrics(
                counter,
                tenant_id="test-tenant",
                instance_id="inst-001",
                session_id="sess-001",
            )

        events = store.query_by_session("sess-001")
        assert len(events) == 3

    def test_aggregate_by_task_type(self, store):
        """Test aggregating metrics by task type."""
        task_types = ["code", "research", "code"]
        tokens_list = [(1200, 800), (1500, 900), (1300, 750)]

        for task_type, (input_tok, output_tok) in zip(task_types, tokens_list):
            counter = TokenCounter(turn_id=f"turn_{task_type}", engine="claude-opus-5")
            counter.record_llm_call(input_tokens=input_tok, output_tokens=output_tok)
            counter.baseline_tokens = 3000
            counter.task_type = task_type
            counter.finalize()

            store.write_token_metrics(
                counter,
                tenant_id="test-tenant",
                instance_id="inst-001",
                session_id="sess-001",
            )

        aggregates = store.aggregate_by_task_type("sess-001")

        assert "code" in aggregates
        assert "research" in aggregates
        assert aggregates["code"]["turns"] == 2
        assert aggregates["research"]["turns"] == 1

    def test_aggregate_by_subsystem(self, store):
        """Test aggregating metrics by subsystem."""
        subsystems = [
            {"confidence": 200, "cache": 150},
            {"confidence": 180, "cache": 160},
        ]

        for i, subsystem_dict in enumerate(subsystems):
            counter = TokenCounter(turn_id=f"turn_{i:03d}", engine="claude-opus-5")
            counter.record_llm_call(input_tokens=1200, output_tokens=800)
            for subsystem, tokens in subsystem_dict.items():
                counter.record_subsystem_usage(subsystem, tokens)
            counter.baseline_tokens = 2800
            counter.finalize()

            store.write_token_metrics(
                counter,
                tenant_id="test-tenant",
                instance_id="inst-001",
                session_id="sess-001",
            )

        aggregates = store.aggregate_by_subsystem("sess-001")

        assert "confidence" in aggregates
        assert "cache" in aggregates
        assert aggregates["confidence"]["count"] == 2
        assert aggregates["cache"]["count"] == 2

    def test_summary_stats(self, store):
        """Test summary statistics calculation."""
        for i in range(5):
            counter = TokenCounter(turn_id=f"turn_{i:03d}", engine="claude-opus-5")
            counter.record_llm_call(
                input_tokens=1000 + i * 100,
                output_tokens=800 + i * 50,
            )
            counter.record_subsystem_usage("confidence", 200)
            counter.baseline_tokens = 3000
            counter.task_type = "code" if i % 2 == 0 else "research"
            counter.finalize()

            store.write_token_metrics(
                counter,
                tenant_id="test-tenant",
                instance_id="inst-001",
                session_id="sess-001",
            )

        summary = store.summary("sess-001")

        assert summary["turn_count"] == 5
        assert summary["total_tokens"] > 0
        assert summary["baseline_tokens"] > 0
        assert summary["savings_percent"] > 0
        assert summary["avg_tokens_per_turn"] > 0
        assert "code" in summary["by_task_type"]
        assert "research" in summary["by_task_type"]
        assert "confidence" in summary["subsystems"]

    def test_query_by_timespan(self, store):
        """Test retrieving metrics within a time range."""
        now = datetime.utcnow()

        for i in range(3):
            counter = TokenCounter(turn_id=f"turn_{i:03d}", engine="claude-opus-5")
            counter.record_llm_call(input_tokens=1200, output_tokens=800)
            counter.baseline_tokens = 2800
            counter.start_time = now - timedelta(hours=1-i)  # Spread over 3 hours
            counter.finalize()

            store.write_token_metrics(
                counter,
                tenant_id="test-tenant",
                instance_id="inst-001",
                session_id="sess-001",
            )

        # Query for events in the last 2 hours
        start = now - timedelta(hours=2)
        end = now
        events = store.query_by_timespan("test-tenant", start, end)

        assert len(events) >= 1

    def test_savings_percent_calculation(self, store):
        """Test that savings percentage is calculated correctly."""
        counter = TokenCounter(turn_id="turn_savings", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1000, output_tokens=500)  # 1500 total
        counter.baseline_tokens = 2000  # Native cost
        counter.finalize()

        store.write_token_metrics(
            counter,
            tenant_id="test-tenant",
            instance_id="inst-001",
            session_id="sess-001",
        )

        summary = store.summary("sess-001")

        # (2000 - 1500) / 2000 = 0.25 = 25%
        assert pytest.approx(summary["savings_percent"], abs=0.1) == 25.0

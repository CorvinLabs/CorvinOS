"""Phase 2 Integration Tests — TokenMetricsDB, API, Dashboard (K=2-K=4).

Tests database backend, REST endpoints, and React component integration.
"""

import pytest
import sqlite3
import json
from datetime import datetime, timedelta

from core.learning.token_instrumentation import TokenCounter
from core.learning.token_metrics_db import TokenMetricsDB
from core.learning.token_metrics_store import TokenMetricsStore
from core.learning.token_metrics_aggregator import TokenMetricsAggregator
from core.learning.token_baseline import ComparisonEngine
from core.learning.event_schema import LearningEventType
from tests.unit.test_token_metrics_store_k2 import MockEventEmitter


class TestTokenMetricsDB:
    """Test database backend (Phase 2.K=2)."""

    @pytest.fixture
    def db(self, tmp_path):
        """Create temporary database."""
        return TokenMetricsDB(tmp_path / "test_metrics.db")

    def test_database_initialization(self, db):
        """Test schema creation."""
        with sqlite3.connect(db.db_path) as conn:
            cursor = conn.execute("""
                SELECT name FROM sqlite_master
                WHERE type='table' AND name='token_metrics'
            """)
            assert cursor.fetchone() is not None

    def test_insert_token_metrics(self, db):
        """Test inserting token metrics into database."""
        import asyncio
        from core.learning.event_schema import LearningEvent

        counter = TokenCounter(turn_id="t1", engine="claude")
        counter.record_llm_call(1000, 500)
        counter.baseline_tokens = 2000
        counter.task_type = "code"
        counter.finalize()

        event = counter.to_event(
            tenant_id="test",
            instance_id="inst1",
            session_id="sess1"
        )

        # Insert (async operation)
        asyncio.run(db.insert_token_metrics(event))

        # Verify in database
        row = db.query_by_turn("t1")
        assert row is not None
        assert row["total_tokens"] == 1500
        assert row["task_type"] == "code"

    def test_query_by_session(self, db):
        """Test querying all metrics for a session."""
        import asyncio

        for i in range(3):
            counter = TokenCounter(turn_id=f"t{i}", engine="claude")
            counter.record_llm_call(1000 + i * 100, 500)
            counter.baseline_tokens = 2000
            counter.finalize()

            event = counter.to_event(
                tenant_id="test",
                instance_id="inst1",
                session_id="sess1"
            )
            asyncio.run(db.insert_token_metrics(event))

        rows = db.query_by_session("sess1")
        assert len(rows) == 3

    def test_aggregate_by_task_type(self, db):
        """Test task type aggregation."""
        import asyncio

        for i, task_type in enumerate(["code", "code", "research"]):
            counter = TokenCounter(turn_id=f"t{i}", engine="claude")
            counter.record_llm_call(1000, 500)
            counter.baseline_tokens = 2000
            counter.task_type = task_type
            counter.finalize()

            event = counter.to_event(
                tenant_id="test",
                instance_id="inst1",
                session_id="sess1"
            )
            asyncio.run(db.insert_token_metrics(event))

        agg = db.aggregate_by_task_type("sess1")

        assert "code" in agg
        assert agg["code"]["turns"] == 2
        assert "research" in agg
        assert agg["research"]["turns"] == 1

    def test_summary_calculation(self, db):
        """Test complete session summary."""
        import asyncio

        counter = TokenCounter(turn_id="t1", engine="claude")
        counter.record_llm_call(1200, 800)
        counter.baseline_tokens = 2800
        counter.finalize()

        event = counter.to_event(
            tenant_id="test",
            instance_id="inst1",
            session_id="sess1"
        )
        asyncio.run(db.insert_token_metrics(event))

        summary = db.summary("sess1")

        assert summary["turn_count"] == 1
        assert summary["total_tokens"] == 2000
        assert summary["baseline_tokens"] == 2800
        assert summary["savings_tokens"] == 800
        assert round(summary["savings_percent"], 1) == 28.6


class TestTokenMetricsStore_WithDB:
    """Test TokenMetricsStore with database backend."""

    @pytest.fixture
    def store_with_db(self, tmp_path):
        """Create store with DB backend."""
        from core.learning.token_metrics_db import TokenMetricsDB
        emitter = MockEventEmitter()
        db = TokenMetricsDB(tmp_path / "test.db")
        return TokenMetricsStore(emitter, db=db), db

    def test_store_with_db_backend(self, store_with_db):
        """Test store writes to both EventStore and DB."""
        store, db = store_with_db

        counter = TokenCounter(turn_id="t1", engine="claude")
        counter.record_llm_call(1000, 500)
        counter.baseline_tokens = 2000
        counter.finalize()

        # Write to store
        event_id = store.write_token_metrics(
            counter,
            tenant_id="test",
            instance_id="inst1",
            session_id="sess1"
        )

        assert event_id is not None

        # Verify in cache (Phase 1)
        cached = store.get_event(event_id)
        assert cached is not None

        # Verify in DB (Phase 2)
        db_row = db.query_by_turn("t1")
        assert db_row is not None


class TestTokenMetricsAggregator_Complete:
    """Test aggregation pipeline (Phase 2.K=4)."""

    @pytest.fixture
    def aggregator(self, tmp_path):
        """Create aggregator with DB backend."""
        from core.learning.token_metrics_db import TokenMetricsDB
        emitter = MockEventEmitter()
        db = TokenMetricsDB(tmp_path / "test.db")
        store = TokenMetricsStore(emitter, db=db)
        comparison_engine = ComparisonEngine()
        return TokenMetricsAggregator(store, comparison_engine)

    def test_dashboard_data_with_db(self, aggregator):
        """Test complete dashboard data generation with DB backend."""
        # Write 5 turns
        for i in range(5):
            counter = TokenCounter(turn_id=f"t{i}", engine="claude")
            counter.record_llm_call(1000 + i * 100, 500 + i * 50)
            counter.baseline_tokens = 2000
            counter.task_type = "code" if i < 3 else "analysis"
            counter.outcome_quality = "good"
            counter.finalize()

            aggregator.store.write_token_metrics(
                counter,
                tenant_id="test",
                instance_id="inst1",
                session_id="sess1"
            )

        # Get dashboard data
        dashboard = aggregator.get_session_dashboard_data("sess1")

        assert dashboard["summary"]["turn_count"] == 5
        assert dashboard["summary"]["total_tokens"] > 0
        assert dashboard["summary"]["savings_tokens"] > 0
        assert dashboard["is_significant"]
        assert "code" in dashboard["by_task_type"]
        assert "analysis" in dashboard["by_task_type"]

    def test_session_metrics_list(self, aggregator):
        """Test detailed metrics per-turn list."""
        for i in range(3):
            counter = TokenCounter(turn_id=f"t{i}", engine="claude")
            counter.record_llm_call(1000, 500)
            counter.baseline_tokens = 2000
            counter.task_type = "code"
            counter.outcome_quality = "excellent"
            counter.finalize()

            aggregator.store.write_token_metrics(
                counter,
                tenant_id="test",
                instance_id="inst1",
                session_id="sess1"
            )

        metrics = aggregator.get_session_metrics("sess1")

        assert len(metrics) == 3
        assert all(m["task_type"] == "code" for m in metrics)
        assert all(m["outcome_quality"] == "excellent" for m in metrics)


class TestPhase2Complete:
    """End-to-end Phase 2 flow: Instrumentation → DB → API → Dashboard."""

    def test_full_phase2_pipeline(self, tmp_path):
        """Test complete Phase 2 pipeline."""
        from core.learning.token_metrics_db import TokenMetricsDB

        # K=1: Instrument
        counter = TokenCounter(turn_id="turn_phase2", engine="claude-opus-5")
        counter.record_llm_call(input_tokens=1400, output_tokens=900)
        counter.record_subsystem_usage("confidence", 200)
        counter.record_subsystem_usage("cache", 150)
        counter.baseline_tokens = 3000
        counter.task_type = "code"
        counter.task_domain = "backend"
        counter.outcome_quality = "excellent"
        counter.finalize()

        # K=2: DB backend
        emitter = MockEventEmitter()
        db = TokenMetricsDB(tmp_path / "test.db")
        store = TokenMetricsStore(emitter, db=db)

        event_id = store.write_token_metrics(
            counter,
            tenant_id="prod",
            instance_id="inst001",
            session_id="sess_abc123",
            user_id="user123"
        )

        assert event_id is not None

        # K=3/K=4: Aggregation for API/Dashboard
        comparison_engine = ComparisonEngine()
        aggregator = TokenMetricsAggregator(store, comparison_engine)

        dashboard = aggregator.get_session_dashboard_data("sess_abc123")

        # Verify complete data pipeline
        assert dashboard["session_id"] == "sess_abc123"
        assert dashboard["summary"]["turn_count"] == 1
        assert dashboard["summary"]["total_tokens"] == 2300
        assert dashboard["summary"]["baseline_tokens"] == 3000
        assert dashboard["summary"]["savings_tokens"] == 700
        assert round(dashboard["summary"]["savings_percent"], 1) == 23.3
        assert dashboard["is_significant"]
        assert "confidence" in dashboard["subsystems"]
        assert "cache" in dashboard["subsystems"]

        # Verify metrics list (what API returns)
        metrics = aggregator.get_session_metrics("sess_abc123")
        assert len(metrics) == 1
        assert metrics[0]["turn_id"] == "turn_phase2"
        assert metrics[0]["task_type"] == "code"
        assert metrics[0]["outcome_quality"] == "excellent"

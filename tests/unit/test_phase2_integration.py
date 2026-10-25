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
# token_metrics_aggregator was retired by ADR-2282 (it fabricated a timestamp and a
# confidence); its aggregation tests left with it.
from core.learning.token_baseline import ComparisonEngine
from core.learning.event_schema import LearningEventType


# Inlined from the retired test_token_metrics_store_k2.py (it described an
# async SqliteMetricsDB that was never written; see token_metrics_db.py).
class MockEventEmitter:
    """Mock EventEmitter for testing (doesn't write to audit trail)."""

    def __init__(self):
        self.events = []

    def emit(self, event):
        """Record event in memory."""
        self.events.append(event)



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
        row = db.query_by_turn("t1", "test")
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

        rows = db.query_by_session("sess1", "test")
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

        agg = db.aggregate_by_task_type("sess1", "test")

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

        summary = db.summary("sess1", "test")

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
        db_row = db.query_by_turn("t1", "test")
        assert db_row is not None


class TestPhase2Complete:
    """Phase 2 flow: Instrumentation → DB-backed store."""

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

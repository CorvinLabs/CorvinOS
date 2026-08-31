"""Test TokenMetricsDB SQLite Backend (Phase 2.K=2).

Tests database schema, CRUD operations, and aggregation queries.
"""

import json
import pytest
from datetime import datetime, timedelta
from pathlib import Path

from core.learning.token_metrics_db import SqliteMetricsDB, TokenMetricsDB
from core.learning.token_metrics_db_factory import create_metrics_db
from core.learning.event_schema import LearningEvent, LearningEventType
from core.learning.token_instrumentation import TokenCounter


@pytest.fixture
def db():
    """In-memory SQLite database for tests."""
    db = SqliteMetricsDB("sqlite:///:memory:")
    yield db
    # Connection closes automatically


@pytest.fixture
def sample_event() -> LearningEvent:
    """Create a sample token metrics event."""
    return LearningEvent(
        event_type=LearningEventType.TOKEN_METRICS,
        tenant_id="default",
        instance_id="inst1",
        skill_name="test_skill",
        session_id="session1",
        timestamp_utc=datetime.utcnow(),
        event_id="evt-001",
        user_id="user1",
        payload={
            "token_metrics": {
                "turn_id": "t1",
                "input_tokens": 1000,
                "output_tokens": 500,
                "total_tokens": 1500,
                "engine": "claude",
                "engine_tier": "cloud",
                "baseline_tokens": 2000,
                "savings_tokens": 500,
                "savings_percent": 25.0,
                "task_type": "code",
                "task_domain": "backend",
                "task_complexity": "moderate",
                "outcome_quality": "good",
                "subsystem_tokens": {
                    "confidence": 200,
                    "cache": 150,
                    "formatting": 50,
                },
                "iterations_count": 1,
                "latency_ms": 1200,
                "error_rate": 0.0,
                "required_followup": False,
                "user_satisfaction": 5,
            }
        },
    )


class TestSqliteMetricsDB:
    """SQLite backend tests."""

    @pytest.mark.asyncio
    async def test_init_creates_schema(self):
        """Test that initialization creates tables and indexes."""
        db = SqliteMetricsDB("sqlite:///:memory:")

        # Verify tables exist
        tables = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = [t[0] for t in tables]

        assert "token_metrics" in table_names

        # Verify indexes exist
        indexes = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index'"
        ).fetchall()
        index_names = [i[0] for i in indexes]

        assert "idx_session_timestamp" in index_names
        assert "idx_tenant_timestamp" in index_names
        assert "idx_turn_id" in index_names

        await db.close()

    @pytest.mark.asyncio
    async def test_insert_and_query_by_turn(self, db, sample_event):
        """Test inserting and retrieving a single event by turn ID."""
        # Insert
        event_id = await db.insert_token_metrics(sample_event)
        assert event_id == "evt-001"

        # Query
        row = await db.query_by_turn("t1", "default")
        assert row is not None
        assert row["turn_id"] == "t1"
        assert row["total_tokens"] == 1500
        assert row["input_tokens"] == 1000
        assert row["output_tokens"] == 500
        assert row["savings_percent"] == 25.0

    @pytest.mark.asyncio
    async def test_insert_multiple_and_query_by_session(self, db):
        """Test inserting multiple events and querying by session."""
        # Create 3 events
        for i in range(1, 4):
            event = LearningEvent(
                event_type=LearningEventType.TOKEN_METRICS,
                tenant_id="default",
                instance_id=f"inst{i}",
                skill_name=None,
                session_id="session1",
                timestamp_utc=datetime.utcnow() + timedelta(seconds=i),
                event_id=f"evt-{i:03d}",
                payload={
                    "token_metrics": {
                        "turn_id": f"t{i}",
                        "input_tokens": 1000 * i,
                        "output_tokens": 500 * i,
                        "total_tokens": 1500 * i,
                        "engine": "claude",
                        "baseline_tokens": 2000 * i,
                    }
                },
            )
            await db.insert_token_metrics(event)

        # Query by session
        rows = await db.query_by_session("session1", "default", limit=100)

        assert len(rows) == 3
        # Should be sorted DESC by timestamp
        assert rows[0]["turn_id"] == "t3"
        assert rows[1]["turn_id"] == "t2"
        assert rows[2]["turn_id"] == "t1"

    @pytest.mark.asyncio
    async def test_query_by_timespan(self, db):
        """Test querying events within a time range."""
        base_time = datetime.utcnow()

        # Create 3 events at different times
        for i in range(1, 4):
            event = LearningEvent(
                event_type=LearningEventType.TOKEN_METRICS,
                tenant_id="default",
                instance_id=f"inst{i}",
                skill_name=None,
                session_id="session1",
                timestamp_utc=base_time + timedelta(minutes=i * 10),
                event_id=f"evt-{i:03d}",
                payload={
                    "token_metrics": {
                        "turn_id": f"t{i}",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "total_tokens": 1500,
                        "engine": "claude",
                    }
                },
            )
            await db.insert_token_metrics(event)

        # Query narrow range (should get 1 event)
        start = base_time + timedelta(minutes=8)
        end = base_time + timedelta(minutes=12)
        rows = await db.query_by_timespan("default", start, end)

        assert len(rows) == 1
        assert rows[0]["turn_id"] == "t1"

        # Query wide range (should get all 3)
        start = base_time
        end = base_time + timedelta(minutes=40)
        rows = await db.query_by_timespan("default", start, end)

        assert len(rows) == 3

    @pytest.mark.asyncio
    async def test_tenant_isolation(self, db):
        """Test that queries are tenant-isolated."""
        # Insert event for tenant A
        event_a = LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id="tenant_a",
            instance_id="inst1",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-001",
            payload={
                "token_metrics": {
                    "turn_id": "t1",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "engine": "claude",
                }
            },
        )
        await db.insert_token_metrics(event_a)

        # Insert event for tenant B with same session ID
        event_b = LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id="tenant_b",
            instance_id="inst2",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-002",
            payload={
                "token_metrics": {
                    "turn_id": "t2",
                    "input_tokens": 2000,
                    "output_tokens": 1000,
                    "total_tokens": 3000,
                    "engine": "claude",
                }
            },
        )
        await db.insert_token_metrics(event_b)

        # Query tenant A should only return 1 event
        rows_a = await db.query_by_session("session1", "tenant_a")
        assert len(rows_a) == 1
        assert rows_a[0]["event_id"] == "evt-001"

        # Query tenant B should only return 1 event
        rows_b = await db.query_by_session("session1", "tenant_b")
        assert len(rows_b) == 1
        assert rows_b[0]["event_id"] == "evt-002"

    @pytest.mark.asyncio
    async def test_aggregate_by_task_type(self, db):
        """Test task type aggregation."""
        base_time = datetime.utcnow()

        # Create events with different task types
        task_types = [
            ("code", 1000, 500, 2000),
            ("code", 1200, 600, 2400),
            ("research", 800, 400, 1600),
        ]

        for i, (task_type, input_tok, output_tok, baseline) in enumerate(task_types):
            event = LearningEvent(
                event_type=LearningEventType.TOKEN_METRICS,
                tenant_id="default",
                instance_id=f"inst{i}",
                skill_name=None,
                session_id="session1",
                timestamp_utc=base_time + timedelta(seconds=i),
                event_id=f"evt-{i:03d}",
                payload={
                    "token_metrics": {
                        "turn_id": f"t{i}",
                        "input_tokens": input_tok,
                        "output_tokens": output_tok,
                        "total_tokens": input_tok + output_tok,
                        "engine": "claude",
                        "task_type": task_type,
                        "baseline_tokens": baseline,
                        "savings_tokens": baseline - (input_tok + output_tok),
                    }
                },
            )
            await db.insert_token_metrics(event)

        # Aggregate
        agg = await db.aggregate_by_task_type("session1", "default")

        # Verify code aggregation
        assert "code" in agg
        assert agg["code"]["turns"] == 2
        assert agg["code"]["total_tokens"] == 1500 + 1800  # 3300
        assert agg["code"]["baseline_tokens"] == 2000 + 2400  # 4400

        # Verify research aggregation
        assert "research" in agg
        assert agg["research"]["turns"] == 1
        assert agg["research"]["total_tokens"] == 1200

        # Verify savings percent calculation
        code_savings = agg["code"]["savings_tokens"]
        code_baseline = agg["code"]["baseline_tokens"]
        expected_pct = (code_savings / code_baseline) * 100
        assert abs(agg["code"]["savings_percent"] - expected_pct) < 0.01

    @pytest.mark.asyncio
    async def test_aggregate_by_subsystem(self, db):
        """Test subsystem token aggregation."""
        # Create events with different subsystem breakdowns
        subsystems_list = [
            {"confidence": 200, "cache": 100},
            {"confidence": 250, "cache": 120, "formatting": 50},
            {"confidence": 180, "cache": 90},
        ]

        for i, subsystem_tokens in enumerate(subsystems_list):
            event = LearningEvent(
                event_type=LearningEventType.TOKEN_METRICS,
                tenant_id="default",
                instance_id=f"inst{i}",
                skill_name=None,
                session_id="session1",
                timestamp_utc=datetime.utcnow() + timedelta(seconds=i),
                event_id=f"evt-{i:03d}",
                payload={
                    "token_metrics": {
                        "turn_id": f"t{i}",
                        "input_tokens": 1000,
                        "output_tokens": 500,
                        "total_tokens": 1500,
                        "engine": "claude",
                        "subsystem_tokens": subsystem_tokens,
                    }
                },
            )
            await db.insert_token_metrics(event)

        # Aggregate
        agg = await db.aggregate_by_subsystem("session1", "default")

        # Verify confidence aggregation
        assert "confidence" in agg
        assert agg["confidence"]["count"] == 3
        assert agg["confidence"]["total_tokens"] == 200 + 250 + 180
        assert abs(agg["confidence"]["avg_tokens"] - ((200 + 250 + 180) / 3)) < 0.01

        # Verify cache aggregation
        assert "cache" in agg
        assert agg["cache"]["count"] == 3
        assert agg["cache"]["total_tokens"] == 100 + 120 + 90

        # Verify formatting only in one event
        assert "formatting" in agg
        assert agg["formatting"]["count"] == 1
        assert agg["formatting"]["total_tokens"] == 50

    @pytest.mark.asyncio
    async def test_summary(self, db):
        """Test comprehensive session summary."""
        base_time = datetime.utcnow()

        # Create diverse events
        events_data = [
            {
                "turn_id": "t1",
                "input": 1000,
                "output": 500,
                "task_type": "code",
                "baseline": 2000,
                "subsystems": {"confidence": 200},
            },
            {
                "turn_id": "t2",
                "input": 800,
                "output": 400,
                "task_type": "research",
                "baseline": 1600,
                "subsystems": {"cache": 150},
            },
            {
                "turn_id": "t3",
                "input": 1200,
                "output": 600,
                "task_type": "code",
                "baseline": 2400,
                "subsystems": {"formatting": 100},
            },
        ]

        for i, data in enumerate(events_data):
            event = LearningEvent(
                event_type=LearningEventType.TOKEN_METRICS,
                tenant_id="default",
                instance_id=f"inst{i}",
                skill_name=None,
                session_id="session1",
                timestamp_utc=base_time + timedelta(seconds=i),
                event_id=f"evt-{i:03d}",
                payload={
                    "token_metrics": {
                        "turn_id": data["turn_id"],
                        "input_tokens": data["input"],
                        "output_tokens": data["output"],
                        "total_tokens": data["input"] + data["output"],
                        "engine": "claude",
                        "task_type": data["task_type"],
                        "baseline_tokens": data["baseline"],
                        "savings_tokens": data["baseline"] - (data["input"] + data["output"]),
                        "subsystem_tokens": data["subsystems"],
                    }
                },
            )
            await db.insert_token_metrics(event)

        # Get summary
        summary = await db.summary("session1", "default")

        # Verify counts
        assert summary["turn_count"] == 3
        assert summary["total_tokens"] == 1500 + 1200 + 1800
        assert summary["baseline_tokens"] == 2000 + 1600 + 2400

        # Verify averages
        expected_avg = (1500 + 1200 + 1800) / 3
        assert abs(summary["avg_tokens_per_turn"] - expected_avg) < 0.01

        # Verify subsystem breakdown
        assert "subsystems" in summary
        assert "confidence" in summary["subsystems"]
        assert "cache" in summary["subsystems"]
        assert "formatting" in summary["subsystems"]

        # Verify task type breakdown
        assert "by_task_type" in summary
        assert "code" in summary["by_task_type"]
        assert "research" in summary["by_task_type"]
        assert summary["by_task_type"]["code"]["turns"] == 2
        assert summary["by_task_type"]["research"]["turns"] == 1

    @pytest.mark.asyncio
    async def test_unique_turn_id_constraint(self, db, sample_event):
        """Test that turn_id is unique (UNIQUE constraint)."""
        # Insert first event
        await db.insert_token_metrics(sample_event)

        # Attempt to insert duplicate turn_id
        duplicate_event = LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id="default",
            instance_id="inst2",
            skill_name=None,
            session_id="session2",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-002",
            payload={
                "token_metrics": {
                    "turn_id": "t1",  # Same turn_id
                    "input_tokens": 2000,
                    "output_tokens": 1000,
                    "total_tokens": 3000,
                    "engine": "claude",
                }
            },
        )

        with pytest.raises(Exception):  # Should raise sqlite3.IntegrityError
            await db.insert_token_metrics(duplicate_event)

    @pytest.mark.asyncio
    async def test_subsystem_tokens_json_serialization(self, db):
        """Test that subsystem_tokens dict is properly serialized/deserialized."""
        complex_subsystems = {
            "confidence": 200,
            "cache": 150,
            "formatting": 75,
            "validation": 50,
            "orchestration": 100,
        }

        event = LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id="default",
            instance_id="inst1",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-001",
            payload={
                "token_metrics": {
                    "turn_id": "t1",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "engine": "claude",
                    "subsystem_tokens": complex_subsystems,
                }
            },
        )

        # Insert
        await db.insert_token_metrics(event)

        # Retrieve
        row = await db.query_by_turn("t1", "default")

        # Verify subsystems deserialized correctly
        assert row["subsystem_tokens"] == complex_subsystems
        assert row["subsystem_tokens"]["confidence"] == 200
        assert row["subsystem_tokens"]["orchestration"] == 100

    @pytest.mark.asyncio
    async def test_invalid_event_type_raises_error(self, db):
        """Test that inserting non-TOKEN_METRICS event raises error."""
        invalid_event = LearningEvent(
            event_type=LearningEventType.CONFIDENCE_SCORE,  # Wrong type
            tenant_id="default",
            instance_id="inst1",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-001",
            payload={"score": 0.95},
        )

        with pytest.raises(ValueError):
            await db.insert_token_metrics(invalid_event)


class TestDbFactory:
    """Test database factory."""

    def test_factory_creates_sqlite_by_default(self, tmp_path):
        """Test that factory creates SQLite by default."""
        import os

        # Clear env var
        os.environ.pop("CORVIN_METRICS_DB_URI", None)

        db = create_metrics_db(tenant_id="test_tenant")

        assert isinstance(db, SqliteMetricsDB)
        # Don't need to close since it's the real filesystem
        if hasattr(db, 'conn'):
            db.conn.close()

    def test_factory_respects_env_var(self):
        """Test that factory respects CORVIN_METRICS_DB_URI env var."""
        import os

        os.environ["CORVIN_METRICS_DB_URI"] = "sqlite:///:memory:"
        try:
            db = create_metrics_db()
            assert isinstance(db, SqliteMetricsDB)
            db.conn.close()
        finally:
            os.environ.pop("CORVIN_METRICS_DB_URI", None)

    def test_factory_respects_config_dict(self):
        """Test that factory respects config dict."""
        config = {"metrics_db_uri": "sqlite:///:memory:"}
        db = create_metrics_db(config=config)

        assert isinstance(db, SqliteMetricsDB)
        db.conn.close()

    def test_factory_rejects_invalid_uri(self):
        """Test that factory rejects invalid URIs."""
        config = {"metrics_db_uri": "invalid://uri"}

        with pytest.raises(ValueError):
            create_metrics_db(config=config)

    def test_factory_rejects_postgres_uri(self):
        """Test that factory rejects PostgreSQL URIs (not yet supported)."""
        config = {"metrics_db_uri": "postgresql://localhost/metrics"}

        with pytest.raises(ValueError):
            create_metrics_db(config=config)


class TestSqlitePersistence:
    """Test that data persists across connections."""

    def test_data_survives_close_and_reopen(self, tmp_path):
        """Test persistence to disk."""
        db_path = tmp_path / "test_metrics.db"
        db_uri = f"sqlite:///{db_path}"

        # Write data
        db1 = SqliteMetricsDB(db_uri)
        event = LearningEvent(
            event_type=LearningEventType.TOKEN_METRICS,
            tenant_id="default",
            instance_id="inst1",
            skill_name=None,
            session_id="session1",
            timestamp_utc=datetime.utcnow(),
            event_id="evt-001",
            payload={
                "token_metrics": {
                    "turn_id": "t1",
                    "input_tokens": 1000,
                    "output_tokens": 500,
                    "total_tokens": 1500,
                    "engine": "claude",
                }
            },
        )

        # Synchronous insert for test
        db1.conn.execute(
            """
            INSERT INTO token_metrics (
                event_id, tenant_id, session_id, turn_id,
                input_tokens, output_tokens, total_tokens,
                engine, engine_tier, timestamp_utc
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (
                event.event_id,
                event.tenant_id,
                event.session_id,
                "t1",
                1000,
                500,
                1500,
                "claude",
                "cloud",
                event.timestamp_utc,
            ),
        )
        db1.conn.commit()
        db1.conn.close()

        # Read data from new connection
        db2 = SqliteMetricsDB(db_uri)
        row = db2.conn.execute(
            "SELECT * FROM token_metrics WHERE turn_id = ?", ("t1",)
        ).fetchone()

        assert row is not None
        assert dict(row)["total_tokens"] == 1500

        db2.conn.close()

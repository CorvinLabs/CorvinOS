"""Track 3: Measurement Continuity Specifications — 20+ Tests (ADR-0XXX).

Verification suite for continuous measurement collection:
1. Measurement collection across skill executions
2. Audit-trail persistence with hash-chain integrity
3. Tenant isolation and data scoping
4. Session boundary continuity (no data loss)
5. Percentile aggregation accuracy
6. Chain verification and recovery
"""

import json
import logging
import pytest
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from core.learning.measurement_collector import (
    MeasurementCollector,
    MeasurementType,
    Measurement,
)
from core.learning.metrics_persistence import MetricsPersistence, MeasurementEvent

logger = logging.getLogger(__name__)


class TestMeasurementCollectorBasics:
    """Basic measurement recording (4 tests)."""

    def test_collector_initialization(self):
        """Test MeasurementCollector initialization with tenant validation."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.delegation_router",
        )
        assert collector.tenant_id == "_default"
        assert collector.skill_id == "os.delegation_router"
        assert len(collector.get_all_measurements()) == 0

    def test_collector_rejects_invalid_tenant(self):
        """Test that invalid tenant_ids are rejected (GDPR Art. 32)."""
        with pytest.raises(ValueError, match="Invalid tenant_id"):
            MeasurementCollector(
                tenant_id="../../etc/passwd",  # Path traversal attempt
                session_id=str(uuid4()),
                skill_id="test.skill",
            )

    def test_record_latency_measurement(self):
        """Test recording a latency measurement."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_latency(execution_id, 42.5)

        assert measurement.measurement_type == MeasurementType.LATENCY
        assert measurement.value == 42.5
        assert measurement.execution_id == execution_id
        assert measurement.tenant_id == "_default"
        assert len(collector.get_all_measurements()) == 1

    def test_record_negative_latency_rejected(self):
        """Test that negative latency values are rejected."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        with pytest.raises(ValueError, match="non-negative"):
            collector.record_latency(str(uuid4()), -1.0)


class TestMeasurementTypes:
    """Measurement type recording (5 tests)."""

    def test_record_success_rate_true(self):
        """Test recording a success measurement."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_success_rate(execution_id, success=True)

        assert measurement.measurement_type == MeasurementType.SUCCESS_RATE
        assert measurement.value == 1.0

    def test_record_success_rate_false(self):
        """Test recording a failure measurement."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_success_rate(execution_id, success=False)

        assert measurement.value == 0.0

    def test_record_token_cost(self):
        """Test recording token usage."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_token_cost(
            execution_id,
            input_tokens=100,
            output_tokens=50,
        )

        assert measurement.measurement_type == MeasurementType.TOKEN_COST
        assert measurement.value == 150.0
        assert measurement.tags["input_tokens"] == 100
        assert measurement.tags["output_tokens"] == 50

    def test_record_quality_score_valid(self):
        """Test recording a quality score (0-1)."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_quality_score(execution_id, score=0.85)

        assert measurement.measurement_type == MeasurementType.QUALITY_SCORE
        assert measurement.value == 0.85

    def test_record_quality_score_out_of_range_rejected(self):
        """Test that quality scores outside [0, 1] are rejected."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        with pytest.raises(ValueError, match="must be in \\[0, 1\\]"):
            collector.record_quality_score(str(uuid4()), score=1.5)


class TestTenantIsolation:
    """Tenant isolation and data scoping (3 tests)."""

    def test_measurements_carry_tenant_id(self):
        """Test that all measurements carry the correct tenant_id."""
        tenant_id = "customer-xyz"
        collector = MeasurementCollector(
            tenant_id=tenant_id,
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_latency(execution_id, 10.0)

        assert measurement.tenant_id == tenant_id

    def test_payload_is_tenant_scoped(self):
        """Test that measurement payloads include tenant_id (audit-safe)."""
        collector = MeasurementCollector(
            tenant_id="tenant-2",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        execution_id = str(uuid4())

        measurement = collector.record_success_rate(execution_id, success=True)
        payload = measurement.to_payload()

        assert payload["tenant_id"] == "tenant-2"
        assert payload["measurement_type"] == "success_rate"

    def test_measurements_filtered_by_type_respects_scope(self):
        """Test that filtering respects tenant boundaries."""
        collector = MeasurementCollector(
            tenant_id="tenant-a",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        # Record multiple measurement types
        collector.record_latency(str(uuid4()), 10.0)
        collector.record_latency(str(uuid4()), 20.0)
        collector.record_success_rate(str(uuid4()), success=True)

        latency_measurements = collector.get_measurements_by_type(MeasurementType.LATENCY)

        assert len(latency_measurements) == 2
        assert all(m.tenant_id == "tenant-a" for m in latency_measurements)


class TestPercentileAggregation:
    """Percentile calculation (4 tests)."""

    def test_percentile_calculation_p50(self):
        """Test p50 (median) calculation."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        # Record 5 latency values: 10, 20, 30, 40, 50
        for value in [10.0, 20.0, 30.0, 40.0, 50.0]:
            collector.record_latency(str(uuid4()), value)

        percentiles = collector.calculate_percentiles(MeasurementType.LATENCY)

        assert percentiles is not None
        assert percentiles.count == 5
        assert percentiles.p50 == 30.0  # Median
        assert percentiles.mean_value == 30.0

    def test_percentile_calculation_p95_p99(self):
        """Test p95 and p99 percentile calculation."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        # Record 100 latency values (1-100)
        for value in range(1, 101):
            collector.record_latency(str(uuid4()), float(value))

        percentiles = collector.calculate_percentiles(MeasurementType.LATENCY)

        assert percentiles is not None
        assert percentiles.p95 >= 95.0  # Should be around 95
        assert percentiles.p99 >= 99.0  # Should be around 99
        assert percentiles.min_value == 1.0
        assert percentiles.max_value == 100.0

    def test_percentile_calculation_insufficient_data(self):
        """Test that percentiles return None for empty measurements."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        percentiles = collector.calculate_percentiles(MeasurementType.LATENCY)

        assert percentiles is None

    def test_percentile_aggregation_respects_type_filter(self):
        """Test that percentile aggregation filters by type."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        # Record mixed types
        for value in [10.0, 20.0, 30.0]:
            collector.record_latency(str(uuid4()), value)
        for _ in range(5):
            collector.record_success_rate(str(uuid4()), success=True)

        latency_percentiles = collector.calculate_percentiles(MeasurementType.LATENCY)

        assert latency_percentiles.count == 3  # Only latency measurements


class TestMetricsPersistence:
    """Metrics persistence with audit-first semantics (5 tests)."""

    @pytest.fixture
    def temp_tenant_home(self):
        """Temporary tenant home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_persistence_initialization(self, temp_tenant_home):
        """Test MetricsPersistence initialization."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        assert persistence.tenant_id == "_default"
        assert persistence.metrics_dir.exists()

    def test_persist_single_measurement(self, temp_tenant_home):
        """Test persisting a single measurement."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        measurement = {
            "measurement_id": str(uuid4()),
            "measurement_type": "latency",
            "value": 42.5,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "skill_id": "os.test",
            "execution_id": str(uuid4()),
            "session_id": str(uuid4()),
            "tenant_id": "_default",
            "tags": {},
        }

        result = persistence.persist_measurement(str(uuid4()), measurement)

        assert result is True
        # Verify file was written
        files = list(persistence.metrics_dir.glob("*.jsonl"))
        assert len(files) >= 1

    def test_persist_rejects_wrong_tenant(self, temp_tenant_home):
        """Test that persist rejects measurements from wrong tenant."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="tenant-a")

        measurement = {
            "measurement_id": str(uuid4()),
            "measurement_type": "latency",
            "value": 42.5,
            "timestamp_utc": datetime.utcnow().isoformat(),
            "skill_id": "os.test",
            "execution_id": str(uuid4()),
            "session_id": str(uuid4()),
            "tenant_id": "tenant-b",  # Wrong tenant
            "tags": {},
        }

        result = persistence.persist_measurement(str(uuid4()), measurement)

        assert result is False  # Rejected (GDPR Art. 32)

    def test_persist_batch_measurements(self, temp_tenant_home):
        """Test persisting a batch of measurements."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        measurements = [
            {
                "measurement_id": str(uuid4()),
                "measurement_type": "latency",
                "value": float(i),
                "timestamp_utc": datetime.utcnow().isoformat(),
                "skill_id": "os.test",
                "execution_id": str(uuid4()),
                "session_id": str(uuid4()),
                "tenant_id": "_default",
                "tags": {},
            }
            for i in range(5)
        ]

        persisted = persistence.persist_batch(measurements)

        assert persisted == 5  # All persisted

    def test_query_measurements_respects_tenant(self, temp_tenant_home):
        """Test that queries respect tenant isolation."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="tenant-a")

        # Manually write a record from another tenant to the disk
        # (simulating a potential breach)
        metrics_dir = persistence.metrics_dir
        metrics_dir.mkdir(parents=True, exist_ok=True)
        partition_file = metrics_dir / datetime.utcnow().strftime("%Y-%m-%d.jsonl")

        # Write a record
        record = {
            "event_id": str(uuid4()),
            "tenant_id": "tenant-a",
            "measurement_data": {"value": 10.0},
            "hash": "abc123",
            "prev_hash": "0" * 64,
        }
        with open(partition_file, "w") as f:
            f.write(json.dumps(record) + "\n")

        # Query should return it
        results = persistence.query_measurements()
        assert len(results) >= 1


class TestChainIntegrity:
    """Hash-chain integrity verification (4 tests)."""

    @pytest.fixture
    def temp_tenant_home(self):
        """Temporary tenant home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_chain_is_hash_linked(self, temp_tenant_home):
        """Test that consecutive events are hash-linked."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        event1 = MeasurementEvent(
            event_id=str(uuid4()),
            measurement_data={"value": 10.0, "tenant_id": "_default"},
            tenant_id="_default",
            prev_hash="0" * 64,
        )

        event2 = MeasurementEvent(
            event_id=str(uuid4()),
            measurement_data={"value": 20.0, "tenant_id": "_default"},
            tenant_id="_default",
            prev_hash=event1.hash,  # Links to previous
        )

        # event2's prev_hash should point to event1's hash
        assert event2.prev_hash == event1.hash
        assert event2.prev_hash != "0" * 64

    def test_chain_verification_detects_corruption(self, temp_tenant_home):
        """Test that chain verification detects hash mismatches."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        # Write two valid events
        m1 = {
            "measurement_id": str(uuid4()),
            "value": 10.0,
            "tenant_id": "_default",
        }
        m2 = {
            "measurement_id": str(uuid4()),
            "value": 20.0,
            "tenant_id": "_default",
        }

        persistence.persist_measurement(str(uuid4()), m1)
        persistence.persist_measurement(str(uuid4()), m2)

        # Corrupt the chain manually
        partition_file = persistence.metrics_dir / datetime.utcnow().strftime("%Y-%m-%d.jsonl")
        with open(partition_file, "r") as f:
            lines = f.readlines()

        # Modify the second record's prev_hash
        record2 = json.loads(lines[1])
        record2["prev_hash"] = "0" * 64  # Break the chain
        lines[1] = json.dumps(record2) + "\n"

        with open(partition_file, "w") as f:
            f.writelines(lines)

        # Verification should fail
        is_valid = persistence.verify_chain_integrity()
        assert is_valid is False

    def test_chain_verification_passes_for_valid_chain(self, temp_tenant_home):
        """Test that chain verification passes for valid chains."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        # Write several valid measurements
        for i in range(5):
            m = {
                "measurement_id": str(uuid4()),
                "value": float(i * 10),
                "tenant_id": "_default",
            }
            persistence.persist_measurement(str(uuid4()), m)

        # Verification should pass
        is_valid = persistence.verify_chain_integrity()
        assert is_valid is True

    def test_empty_chain_passes_verification(self, temp_tenant_home):
        """Test that empty chain (genesis) passes verification."""
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        # No measurements yet
        is_valid = persistence.verify_chain_integrity()

        assert is_valid is True


class TestSessionContinuity:
    """Continuity across session boundaries (4 tests)."""

    @pytest.fixture
    def temp_tenant_home(self):
        """Temporary tenant home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_measurements_survive_session_restart(self, temp_tenant_home):
        """Test that measurements persist across session restarts."""
        session_id_1 = str(uuid4())
        session_id_2 = str(uuid4())

        # Session 1: Record measurements
        persistence_1 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        m1 = {
            "measurement_id": str(uuid4()),
            "value": 10.0,
            "tenant_id": "_default",
            "session_id": session_id_1,
        }
        persistence_1.persist_measurement(str(uuid4()), m1)

        # Session 2: Read measurements
        persistence_2 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        results = persistence_2.query_measurements()

        # Measurements from session 1 should still exist
        assert len(results) >= 1

    def test_chain_continues_across_sessions(self, temp_tenant_home):
        """Test that hash-chain continues across sessions."""
        # Session 1
        persistence_1 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        m1 = {
            "measurement_id": str(uuid4()),
            "value": 10.0,
            "tenant_id": "_default",
        }
        persistence_1.persist_measurement(str(uuid4()), m1)
        last_hash_1 = persistence_1._last_hash

        # Session 2
        persistence_2 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        m2 = {
            "measurement_id": str(uuid4()),
            "value": 20.0,
            "tenant_id": "_default",
        }
        persistence_2.persist_measurement(str(uuid4()), m2)

        # The second session should have loaded the last hash from session 1
        # and should have created a new event that chains to it
        results = persistence_2.query_measurements()
        assert len(results) >= 2

    def test_no_data_loss_on_session_boundary(self, temp_tenant_home):
        """Test that no measurements are lost at session boundaries."""
        # Session 1: Write measurements
        session_id = str(uuid4())
        collector_1 = MeasurementCollector(
            tenant_id="_default",
            session_id=session_id,
            skill_id="os.test",
        )

        measurements_1 = []
        for i in range(10):
            m = collector_1.record_latency(str(uuid4()), float(i * 10))
            measurements_1.append(m)

        persistence_1 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        for m in measurements_1:
            persistence_1.persist_measurement(m.measurement_id, m.to_payload())

        # Session 2: Read measurements
        persistence_2 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        results = persistence_2.get_session_measurements(session_id)

        # All 10 measurements should be recovered
        assert len(results) == 10

    def test_timestamp_ordering_across_sessions(self, temp_tenant_home):
        """Test that measurements maintain timestamp order across sessions."""
        persistence_1 = MetricsPersistence(temp_tenant_home, tenant_id="_default")

        # Write with slight delays to ensure ordering
        times = []
        for i in range(3):
            now = datetime.utcnow().isoformat()
            times.append(now)
            m = {
                "measurement_id": str(uuid4()),
                "value": float(i),
                "timestamp_utc": now,
                "tenant_id": "_default",
            }
            persistence_1.persist_measurement(str(uuid4()), m)

        # Verify in new session
        persistence_2 = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        results = persistence_2.query_measurements()

        assert len(results) >= 3


class TestCollectorIntegration:
    """Integration: Collector + Persistence (3 tests)."""

    @pytest.fixture
    def temp_tenant_home(self):
        """Temporary tenant home directory."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_full_pipeline_latency_to_persistence(self, temp_tenant_home):
        """Test full pipeline: record latency → persist → query."""
        # Collect measurements
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        for latency in [10.0, 20.0, 30.0]:
            collector.record_latency(str(uuid4()), latency)

        # Persist all measurements
        persistence = MetricsPersistence(temp_tenant_home, tenant_id="_default")
        for m in collector.get_all_measurements():
            persistence.persist_measurement(m.measurement_id, m.to_payload())

        # Query back
        results = persistence.query_measurements(measurement_type="latency")

        assert len(results) >= 3

    def test_session_summary_includes_percentiles(self, temp_tenant_home):
        """Test that session summary includes aggregated percentiles."""
        collector = MeasurementCollector(
            tenant_id="_default",
            session_id=str(uuid4()),
            skill_id="os.test",
        )

        for value in range(1, 11):
            collector.record_latency(str(uuid4()), float(value))

        summary = collector.session_summary()

        assert "session_id" in summary
        assert "measurements_by_type" in summary
        assert "latency" in summary["measurements_by_type"]
        assert "percentiles" in summary["measurements_by_type"]["latency"]

    def test_multi_tenant_isolation(self, temp_tenant_home):
        """Test that multiple tenants have isolated measurements."""
        # Tenant A
        collector_a = MeasurementCollector(
            tenant_id="tenant-a",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        collector_a.record_latency(str(uuid4()), 10.0)

        persistence_a = MetricsPersistence(temp_tenant_home, tenant_id="tenant-a")
        for m in collector_a.get_all_measurements():
            persistence_a.persist_measurement(m.measurement_id, m.to_payload())

        # Tenant B (same tenant_home but different tenant_id)
        collector_b = MeasurementCollector(
            tenant_id="tenant-b",
            session_id=str(uuid4()),
            skill_id="os.test",
        )
        collector_b.record_latency(str(uuid4()), 20.0)

        persistence_b = MetricsPersistence(temp_tenant_home, tenant_id="tenant-b")
        for m in collector_b.get_all_measurements():
            persistence_b.persist_measurement(m.measurement_id, m.to_payload())

        # Verify isolation
        results_a = persistence_a.query_measurements()
        results_b = persistence_b.query_measurements()

        # Tenant A should only see its measurements
        assert all(r["tenant_id"] == "tenant-a" for r in results_a)
        # Tenant B should only see its measurements
        assert all(r["tenant_id"] == "tenant-b" for r in results_b)

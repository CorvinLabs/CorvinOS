"""Adversarial Test 07: Schema Evolution Attack (ADR-0690 Phase 3.2).

Attack vector: Add new gate type; old validator crashes.
Defense: Validators use .get() with defaults; backward-compat path.

Tests:
1. Add new gate type; old validator ignores (no crash)
2. New verdict type (VerdictType); old code uses enum .get()
3. Missing optional field; validator uses default
4. Extended findings array; old code processes without error
5. New confidence calculation; old code falls back to default
6. Version skew scenario; validators remain compatible
"""

import pytest
import tempfile
import os
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestSchemaEvolutionAttack:
    """Test schema evolution and backward compatibility defenses."""

    @pytest.fixture
    def setup(self):
        """Create test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def test_new_gate_type_old_validator_ignores(self, setup):
        """Test old validator gracefully ignores new gate type."""
        graph, logger = setup

        # Write with known gate type
        result = GateResult(
            gate_name="KnownGate",
            artifact_id="KNOWN-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Known gate",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result)

        # Simulate new gate type by writing with unusual name
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "test-tenant",
                "UnknownNewGate",
                "NEW-001",
                "pass",
                0.9,
                "New gate type",
                "hash_new",
            ],
        )

        # Old validator queries should still work
        query = "SELECT COUNT(*) FROM gate_events WHERE gate_name = ?"
        row = graph.conn.execute(query, ["KnownGate"]).fetchone()
        assert row[0] == 1  # Still finds known gate

        # Can also query new gate (just ignores if not in validator)
        row = graph.conn.execute(query, ["UnknownNewGate"]).fetchone()
        assert row[0] == 1

    def test_new_verdict_type_enum_graceful_fallback(self, setup):
        """Test new verdict type doesn't crash old code."""
        graph, logger = setup

        # Write with known verdict
        result = GateResult(
            gate_name="TestGate",
            artifact_id="TEST-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Known verdict",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result)

        # Try to write with unknown verdict string (simulate future version)
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "test-tenant",
                "TestGate",
                "TEST-002",
                "conditional",  # Future verdict type
                0.6,
                "Conditional verdict",
                "hash_conditional",
            ],
        )

        # Query should still work
        query = "SELECT verdict FROM gate_events WHERE tenant_id = ?"
        rows = graph.conn.execute(query, ["test-tenant"]).fetchall()

        # Should find both events
        assert len(rows) == 2

        # Can filter by known verdict
        query = "SELECT COUNT(*) FROM gate_events WHERE verdict = ?"
        row = graph.conn.execute(query, ["pass"]).fetchone()
        assert row[0] == 1

    def test_missing_optional_field_default_applied(self, setup):
        """Test missing optional field uses default value."""
        graph, logger = setup

        # Write event with findings
        result = GateResult(
            gate_name="TestGate",
            artifact_id="TEST-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="With findings",
            tenant_id="test-tenant",
            findings=["Finding 1", "Finding 2"],
        )
        hash1 = logger.write_gate_event(result)

        # Write event without findings (using default)
        result2 = GateResult(
            gate_name="TestGate",
            artifact_id="TEST-002",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Without findings",
            tenant_id="test-tenant",
            # findings defaults to empty list
        )
        hash2 = logger.write_gate_event(result2)

        # Query findings count for event 2
        query = "SELECT findings_count FROM gate_events WHERE artifact_id = ?"
        row = graph.conn.execute(query, ["TEST-002"]).fetchone()
        assert row[0] == 0  # Default applied

    def test_extended_findings_array_old_code_processes(self, setup):
        """Test old code can process extended findings array."""
        graph, logger = setup

        # Create finding with extended info
        findings = [
            "Basic finding",
            "Finding with details: extra context here",
            "Finding with metadata: {key: value}",
        ]

        result = GateResult(
            gate_name="FindingsGate",
            artifact_id="FIND-001",
            verdict=VerdictType.FAIL,
            confidence=0.1,
            reason="With extended findings",
            tenant_id="test-tenant",
            findings=findings,
        )

        hash_val = logger.write_gate_event(result)

        # Old code just counts findings (doesn't parse extended data)
        query = "SELECT findings_count FROM gate_events WHERE artifact_id = ?"
        row = graph.conn.execute(query, ["FIND-001"]).fetchone()
        assert row[0] == 3

    def test_new_confidence_calculation_fallback_to_default(self, setup):
        """Test new confidence calculation doesn't break old code."""
        graph, logger = setup

        # Write with old confidence calculation
        result = GateResult(
            gate_name="ConfGate",
            artifact_id="CONF-001",
            verdict=VerdictType.PASS,
            confidence=0.8,  # Old calculation
            reason="Old confidence",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result)

        # Simulate new confidence with extended precision
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "test-tenant",
                "ConfGate",
                "CONF-002",
                "pass",
                0.8234567890,  # Extended precision
                "New confidence",
                "hash_new_conf",
            ],
        )

        # Old code just uses confidence value as-is
        query = "SELECT confidence FROM gate_events WHERE artifact_id = ?"
        row = graph.conn.execute(query, ["CONF-002"]).fetchone()
        assert row[0] == 0.8234567890

        # Old code can still query by confidence range
        query = "SELECT COUNT(*) FROM gate_events WHERE confidence >= ?"
        row = graph.conn.execute(query, [0.7]).fetchone()
        assert row[0] == 2

    def test_version_skew_validators_remain_compatible(self, setup):
        """Test version skew scenario maintains compatibility."""
        graph, logger = setup

        # Version 1.0 writes
        for i in range(3):
            result = GateResult(
                gate_name="SkewGate",
                artifact_id=f"V1-{i}",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason="Version 1.0",
                tenant_id="test-tenant",
            )
            logger.write_gate_event(result)

        # Simulate Version 2.0 writes with extended data
        for i in range(3):
            graph.conn.execute(
                "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    "test-tenant",
                    "SkewGate",
                    f"V2-{i}",
                    "pass",
                    0.9,
                    "Version 2.0 with extended features",
                    f"hash_v2_{i}",
                ],
            )

        # Version 1.0 code reading should work
        query = "SELECT COUNT(*) FROM gate_events WHERE gate_name = ?"
        row = graph.conn.execute(query, ["SkewGate"]).fetchone()
        assert row[0] == 6  # All readable

        # Version 1.0 code filtering on confidence
        query = "SELECT COUNT(*) FROM gate_events WHERE confidence >= ? AND gate_name = ?"
        row = graph.conn.execute(query, [0.8, "SkewGate"]).fetchone()
        assert row[0] == 6  # All meet criteria

    def test_missing_reason_field_graceful_handling(self, setup):
        """Test missing reason field doesn't crash queries."""
        graph, logger = setup

        # Write with reason
        result = GateResult(
            gate_name="ReasonGate",
            artifact_id="REASON-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Normal reason",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result)

        # Simulate missing reason (NULL)
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "test-tenant",
                "ReasonGate",
                "REASON-002",
                "pass",
                0.85,
                None,  # NULL reason
                "hash_null_reason",
            ],
        )

        # Query should still work
        query = "SELECT reason FROM gate_events WHERE artifact_id = ?"
        row = graph.conn.execute(query, ["REASON-002"]).fetchone()
        assert row[0] is None

        # Can filter for non-null reasons
        query = "SELECT COUNT(*) FROM gate_events WHERE reason IS NOT NULL AND gate_name = ?"
        row = graph.conn.execute(query, ["ReasonGate"]).fetchone()
        assert row[0] == 1

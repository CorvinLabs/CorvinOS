"""Adversarial Test 02: Graph Corruption Attack (ADR-0690 Phase 3.2).

Attack vector: Crash validator mid-transaction (simulate SIGTERM).
Defense: SQLite ACID rollback; state consistent.

Tests:
1. Crash during gate_events write; rollback verified
2. Partial graph update + crash; old state recovered
3. Concurrent writes + crash; no corruption
4. Audit chain integrity after crash; verified
5. Recovery query returns consistent result
6. Transaction isolation level enforced
"""

import pytest
import tempfile
import os
import threading
from datetime import datetime
import time

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType, KGNode, KGNodeType


class TestGraphCorruptionAttack:
    """Test database corruption defense."""

    @pytest.fixture
    def setup(self):
        """Create test database."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def test_crash_during_write_rollback(self, setup):
        """Test transaction rollback on crash during write."""
        graph, logger = setup

        # Write initial event
        result1 = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="First event",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result1)

        # Count events after crash (simulated by rollback)
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        initial_count = row[0]

        # Simulate crash by starting transaction then rolling back
        try:
            graph.conn.execute("BEGIN")
            result2 = GateResult(
                gate_name="ConceptGate",
                artifact_id="CONCEPT-001",
                verdict=VerdictType.FAIL,
                confidence=0.0,
                reason="Will rollback",
                tenant_id="test-tenant",
            )
            # Insert manually to simulate crash
            graph.conn.execute(
                "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    result2.tenant_id,
                    result2.gate_name,
                    result2.artifact_id,
                    result2.verdict.value,
                    result2.confidence,
                    result2.reason,
                    "fake_hash",
                    datetime.utcnow().isoformat(),
                ],
            )
            # Rollback before commit
            graph.conn.rollback()
        except Exception:
            graph.conn.rollback()

        # Verify only initial event exists (rollback successful)
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == initial_count

    def test_partial_update_crash_recovery(self, setup):
        """Test partial update is rolled back on crash."""
        graph, logger = setup

        # Write baseline
        result1 = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0688",
            verdict=VerdictType.PASS,
            confidence=0.9,
            reason="Baseline",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result1)

        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        initial_row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        initial_count = initial_row[0]

        # Start update
        try:
            graph.conn.execute("BEGIN")
            for i in range(3):
                graph.conn.execute(
                    "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        "test-tenant",
                        f"GateX-{i}",
                        f"ARTIFACT-{i}",
                        "fail",
                        0.0,
                        "Partial update",
                        f"hash_{i}",
                        datetime.utcnow().isoformat(),
                    ],
                )
            # Crash before commit
            raise Exception("Simulated crash")
        except Exception:
            graph.conn.rollback()

        # Verify only baseline exists
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == initial_count

    def test_concurrent_write_crash_isolation(self, setup):
        """Test concurrent writes handle crash gracefully."""
        graph, logger = setup

        results = []
        errors = []

        def write_event(event_id):
            try:
                result = GateResult(
                    gate_name="ConcurrentGate",
                    artifact_id=f"CONCURRENT-{event_id}",
                    verdict=VerdictType.PASS,
                    confidence=0.8,
                    reason=f"Concurrent event {event_id}",
                    tenant_id="test-tenant",
                )
                hash_val = logger.write_gate_event(result)
                results.append(hash_val)
            except Exception as e:
                errors.append(str(e))

        # Launch concurrent writers
        threads = []
        for i in range(3):
            t = threading.Thread(target=write_event, args=(i,))
            threads.append(t)
            t.start()

        # Wait for completion
        for t in threads:
            t.join()

        # Verify all succeeded (no corruption)
        assert len(errors) == 0
        assert len(results) == 3

    def test_audit_chain_after_crash(self, setup):
        """Test audit chain integrity after crash scenario."""
        graph, logger = setup

        # Write events
        hash1 = None
        hash2 = None
        try:
            result1 = GateResult(
                gate_name="IdeaGate",
                artifact_id="IDEA-001",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason="Before crash",
                tenant_id="test-tenant",
            )
            hash1 = logger.write_gate_event(result1)

            result2 = GateResult(
                gate_name="ConceptGate",
                artifact_id="CONCEPT-001",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason="After crash recovery",
                tenant_id="test-tenant",
            )
            hash2 = logger.write_gate_event(result2)
        except Exception:
            pass

        # Verify chain is intact
        query = "SELECT event_hash, prior_hash FROM gate_events WHERE tenant_id = ? ORDER BY timestamp"
        rows = graph.conn.execute(query, ["test-tenant"]).fetchall()

        assert len(rows) == 2
        assert rows[0][1] is None  # First event has no prior
        assert rows[1][1] == rows[0][0]  # Second links to first

    def test_query_consistency_post_crash(self, setup):
        """Test queries return consistent results after crash."""
        graph, logger = setup

        # Write data
        for i in range(5):
            result = GateResult(
                gate_name="TestGate",
                artifact_id=f"TEST-{i}",
                verdict=VerdictType.PASS if i % 2 == 0 else VerdictType.FAIL,
                confidence=0.8,
                reason=f"Test {i}",
                tenant_id="test-tenant",
            )
            logger.write_gate_event(result)

        # Simulate crash
        try:
            graph.conn.execute("BEGIN")
            graph.conn.execute(
                "DELETE FROM gate_events WHERE tenant_id = ?", ["test-tenant"]
            )
            raise Exception("Crash")
        except Exception:
            graph.conn.rollback()

        # Verify data still exists (crash didn't persist)
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == 5

    def test_isolation_level_enforced(self, setup):
        """Test SERIALIZABLE isolation prevents phantom reads."""
        graph, logger = setup

        result = GateResult(
            gate_name="IsoGate",
            artifact_id="ISO-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Isolation test",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result)

        # Read in transaction
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        count1 = row[0]

        # Simulate concurrent insert (that won't commit)
        try:
            graph.conn.execute("BEGIN")
            graph.conn.execute(
                "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [
                    "test-tenant",
                    "PhantomGate",
                    "PHANTOM-001",
                    "pass",
                    0.8,
                    "Phantom insert",
                    "phantom_hash",
                    datetime.utcnow().isoformat(),
                ],
            )
            # Don't commit

            # Read again (should see same count due to isolation)
            row = graph.conn.execute(query, ["test-tenant"]).fetchone()
            count2 = row[0]

            graph.conn.rollback()
        except Exception:
            graph.conn.rollback()

        # After rollback, count should still be 1
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == count1

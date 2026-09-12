"""Tests for audit chain integration (ADR-0688)."""

import pytest
import tempfile
import os
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestAuditChain:
    """Test audit trail integration."""

    @pytest.fixture
    def setup(self):
        """Create test database and audit logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def test_write_gate_event(self, setup):
        """Test writing a gate event to audit chain."""
        graph, logger = setup

        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Sufficient evidence",
            tenant_id="test-tenant",
        )

        event_hash = logger.write_gate_event(result)
        assert event_hash is not None
        assert len(event_hash) == 64  # SHA256 hex digest

    def test_hash_chain_linking(self, setup):
        """Test that hashes are properly chained."""
        graph, logger = setup

        # Write first event
        result1 = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="First event",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result1)

        # Write second event
        result2 = GateResult(
            gate_name="ConceptGate",
            artifact_id="CONCEPT-001",
            verdict=VerdictType.FAIL,
            confidence=0.2,
            reason="Second event",
            tenant_id="test-tenant",
        )
        hash2 = logger.write_gate_event(result2)

        # Verify chain linking in database
        query = "SELECT event_hash, prior_hash FROM gate_events WHERE tenant_id = ? ORDER BY timestamp ASC"
        result = graph.conn.execute(query, ["test-tenant"]).fetchall()

        assert len(result) == 2
        assert result[0][0] == hash1
        assert result[0][1] is None  # First event has no prior
        assert result[1][0] == hash2
        assert result[1][1] == hash1  # Second event links to first

    def test_compute_event_hash(self, setup):
        """Test event hash computation."""
        graph, logger = setup

        event_data = {
            "event_type": "quality_gate_decided",
            "gate_name": "IdeaGate",
            "verdict": "pass",
            "confidence": 0.85,
        }

        hash1 = logger.compute_event_hash(event_data, prior_hash=None)
        hash2 = logger.compute_event_hash(event_data, prior_hash="abc123")

        # Different prior_hash should produce different hash
        assert hash1 != hash2
        assert len(hash1) == 64
        assert len(hash2) == 64

    def test_get_last_event_hash(self, setup):
        """Test retrieving last event hash."""
        graph, logger = setup

        # No events initially
        last_hash = logger.get_last_event_hash("test-tenant")
        assert last_hash is None

        # Write event
        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Test",
            tenant_id="test-tenant",
        )
        event_hash = logger.write_gate_event(result)

        # Now should retrieve it
        last_hash = logger.get_last_event_hash("test-tenant")
        assert last_hash == event_hash

    def test_verify_chain_empty(self, setup):
        """Test chain verification on empty graph."""
        graph, logger = setup

        verified = logger.verify_chain("test-tenant")
        assert verified is True

    def test_verify_chain_valid(self, setup):
        """Test chain verification with valid events."""
        graph, logger = setup

        # Write multiple events
        for i in range(3):
            result = GateResult(
                gate_name="IdeaGate",
                artifact_id=f"IDEA-{i:03d}",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason=f"Event {i}",
                tenant_id="test-tenant",
            )
            logger.write_gate_event(result)

        verified = logger.verify_chain("test-tenant")
        assert verified is True

    def test_create_audit_event(self, setup):
        """Test creating an AuditEvent from GateResult."""
        graph, logger = setup

        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Test reason",
            tenant_id="test-tenant",
            findings=["finding1", "finding2"],
        )

        audit_event = logger.create_audit_event(result, event_hash="abc123", prior_hash="def456")

        assert audit_event.gate_name == "IdeaGate"
        assert audit_event.artifact_id == "IDEA-001"
        assert audit_event.verdict == VerdictType.PASS
        assert audit_event.event_hash == "abc123"
        assert audit_event.prior_hash == "def456"
        assert audit_event.findings_count == 2

    def test_write_gate_event_requires_tenant_id(self, setup):
        """Test that writing gate event requires tenant_id."""
        graph, logger = setup

        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Test",
            tenant_id="",
        )

        with pytest.raises(ValueError, match="tenant_id"):
            logger.write_gate_event(result)

    def test_audit_event_with_findings(self, setup):
        """Test audit event stores findings count."""
        graph, logger = setup

        result = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0688",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason="Missing fields",
            tenant_id="test-tenant",
            findings=[
                "Missing field: id",
                "Missing field: status",
            ],
        )

        event_hash = logger.write_gate_event(result)

        # Verify findings count is stored
        query = "SELECT findings_count FROM gate_events WHERE event_hash = ?"
        result = graph.conn.execute(query, [event_hash]).fetchall()

        assert len(result) == 1
        assert result[0][0] == 2

    def test_tenant_isolation_in_audit(self, setup):
        """Test that audit events are tenant-isolated."""
        graph, logger = setup

        # Write event for test-tenant
        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Test",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result)

        # Query as different tenant should return nothing
        query = "SELECT * FROM gate_events WHERE tenant_id = ?"
        result = graph.conn.execute(query, ["other-tenant"]).fetchall()

        assert len(result) == 0

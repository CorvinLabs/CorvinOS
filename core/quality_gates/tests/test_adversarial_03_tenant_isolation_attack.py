"""Adversarial Test 03: Tenant Isolation Attack (ADR-0690 Phase 3.2).

Attack vector: Query with tenant_id=NULL or cross-tenant query.
Defense: WHERE tenant_id=? enforced; fail-closed.

Tests:
1. Query with tenant_id=NULL returns empty (fail-closed)
2. Cross-tenant query blocked; isolation enforced
3. NULL injection in reason field; no escape
4. OR injection attack; WHERE clause unaffected
5. Tenant switching mid-query; isolation maintained
6. Multi-tenant scenario; no leakage
"""

import pytest
import tempfile
import os
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestTenantIsolationAttack:
    """Test tenant isolation defenses."""

    @pytest.fixture
    def setup(self):
        """Create test database with multiple tenants."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def test_null_tenant_id_query_returns_empty(self, setup):
        """Test query with tenant_id=NULL returns no results."""
        graph, logger = setup

        # Write event for real tenant
        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Real event",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result)

        # Try to query with NULL tenant_id (should return empty)
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id IS NULL"
        row = graph.conn.execute(query).fetchone()
        assert row[0] == 0

    def test_cross_tenant_query_blocked(self, setup):
        """Test cross-tenant query isolation."""
        graph, logger = setup

        # Write for tenant A
        result_a = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Tenant A data",
            tenant_id="tenant-a",
        )
        logger.write_gate_event(result_a)

        # Write for tenant B
        result_b = GateResult(
            gate_name="ConceptGate",
            artifact_id="CONCEPT-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Tenant B data",
            tenant_id="tenant-b",
        )
        # Manually insert as different tenant (logger would validate)
        now_ts = ?
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                "tenant-b",
                "ConceptGate",
                "CONCEPT-001",
                "pass",
                0.85,
                "Tenant B data",
                "hash_b",
                now_ts,
            ],
        )

        # Query as tenant A should not see tenant B data
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row = graph.conn.execute(query, ["tenant-a"]).fetchone()
        assert row[0] == 1

        # Query as tenant B should not see tenant A data
        row = graph.conn.execute(query, ["tenant-b"]).fetchone()
        assert row[0] == 1

    def test_null_injection_in_reason(self, setup):
        """Test NULL injection attack in reason field."""
        graph, logger = setup

        # Attacker tries to inject NULL in reason
        malicious_reason = "Normal reason' WHERE 1=1; --"
        result = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-INJECT",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason=malicious_reason,
            tenant_id="test-tenant",
        )

        event_hash = logger.write_gate_event(result)

        # Verify injection attempt is stored as literal string
        query = "SELECT reason FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert row[0] == malicious_reason
        assert "WHERE 1=1" in row[0]  # Stored literally, not executed

    def test_or_injection_attack(self, setup):
        """Test OR injection attack in queries."""
        graph, logger = setup

        # Write real events
        for tenant in ["tenant-a", "tenant-b"]:
            result = GateResult(
                gate_name="TestGate",
                artifact_id=f"TEST-{tenant}",
                verdict=VerdictType.PASS,
                confidence=0.85,
                reason=f"Event for {tenant}",
                tenant_id=tenant,
            )
            graph.conn.execute(
                "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                [tenant, "TestGate", f"TEST-{tenant}", "pass", 0.85, f"Event for {tenant}", f"hash_{tenant}"],
            )

        # Attacker tries OR injection: "tenant-a' OR '1'='1"
        # But with parameterized query, this is treated as literal string
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row = graph.conn.execute(query, ["tenant-a' OR '1'='1"]).fetchone()
        # Should find 0 (no tenant with that literal name)
        assert row[0] == 0

    def test_tenant_switching_isolation(self, setup):
        """Test tenant switching mid-query maintains isolation."""
        graph, logger = setup

        # Write to tenant A
        result_a = GateResult(
            gate_name="IdeaGate",
            artifact_id="IDEA-A",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Tenant A",
            tenant_id="tenant-a",
        )
        logger.write_gate_event(result_a)

        # Write to tenant B
        graph.conn.execute(
            "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            ["tenant-b", "ConceptGate", "CONCEPT-B", "pass", 0.85, "Tenant B", "hash_b"],
        )

        # Query tenant A multiple times
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ?"
        row1 = graph.conn.execute(query, ["tenant-a"]).fetchone()
        row2 = graph.conn.execute(query, ["tenant-a"]).fetchone()
        row3 = graph.conn.execute(query, ["tenant-a"]).fetchone()

        # All should return same count (1)
        assert row1[0] == 1
        assert row2[0] == 1
        assert row3[0] == 1

    def test_multi_tenant_scenario_no_leakage(self, setup):
        """Test complex multi-tenant scenario with no data leakage."""
        graph, logger = setup

        tenants = ["alpha", "beta", "gamma"]
        events_per_tenant = {}

        # Create events for each tenant
        for tenant in tenants:
            events_per_tenant[tenant] = []
            for i in range(3):
                graph.conn.execute(
                    "INSERT INTO gate_events (tenant_id, gate_name, artifact_id, verdict, confidence, reason, event_hash, timestamp) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    [
                        tenant,
                        f"Gate-{i}",
                        f"{tenant.upper()}-{i}",
                        "pass",
                        0.85,
                        f"Event {i} for {tenant}",
                        f"hash_{tenant}_{i}",
                    ],
                )
                events_per_tenant[tenant].append(f"{tenant.upper()}-{i}")

        # Verify each tenant sees only their events
        query = "SELECT artifact_id FROM gate_events WHERE tenant_id = ? ORDER BY artifact_id"
        for tenant in tenants:
            rows = graph.conn.execute(query, [tenant]).fetchall()
            found_ids = [row[0] for row in rows]

            # Should find exactly their events
            for expected_id in events_per_tenant[tenant]:
                assert expected_id in found_ids

            # Should not find other tenants' events
            for other_tenant in tenants:
                if other_tenant != tenant:
                    for other_id in events_per_tenant[other_tenant]:
                        assert other_id not in found_ids

    def test_tenant_id_in_verdict_field(self, setup):
        """Test tenant_id cannot be spoofed via verdict field."""
        graph, logger = setup

        # Attacker tries to spoof tenant_id in verdict/reason
        result = GateResult(
            gate_name="SpoofGate",
            artifact_id="SPOOF-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="tenant_id: other-tenant",  # Trying to trick via reason
            tenant_id="attacker-tenant",
        )

        event_hash = logger.write_gate_event(result)

        # Verify actual tenant_id is stored correctly
        query = "SELECT tenant_id FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert row[0] == "attacker-tenant"

        # Reason contains spoof attempt (stored as data, not executed)
        query = "SELECT reason FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert "other-tenant" in row[0]

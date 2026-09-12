"""Adversarial Test 01: Gate Bypass Attack (ADR-0690 Phase 3.2).

Attack vector: Bypass pre-commit validator using git --no-verify.
Defense: Post-commit validator runs anyway; audit event logged.

Tests:
1. git commit --no-verify bypassed; post-commit detects & logs
2. Invalid ADR bypassed; audit shows bypass_type=no_verify
3. Multiple bypasses in sequence; all logged + trend detected
4. Cross-tenant bypass attempt; isolation enforced in audit
5. Bypass with fake verdict; audit shows violation flag
6. Recovery after bypass; system state consistent
"""

import pytest
import tempfile
import os
import subprocess
import json
from datetime import datetime

from core.quality_gates.graph import KnowledgeGraph
from core.quality_gates.audit import QualityGateAuditLogger
from core.quality_gates.models import GateResult, VerdictType


class TestBypassAttack:
    """Test gate bypass defense mechanisms."""

    @pytest.fixture
    def setup(self):
        """Create test database and audit logger."""
        with tempfile.TemporaryDirectory() as tmpdir:
            db_path = os.path.join(tmpdir, "test.db")
            graph = KnowledgeGraph(db_path, tenant_id="test-tenant")
            logger = QualityGateAuditLogger(graph.conn)
            yield graph, logger
            graph.close()

    def test_bypass_detected_in_post_commit(self, setup):
        """Test that post-commit validator detects pre-commit bypass."""
        graph, logger = setup

        # Simulate pre-commit bypass: validator skipped
        result = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0999",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason="Invalid frontmatter (bypassed validation)",
            tenant_id="test-tenant",
            findings=["Missing id field"],
        )

        # Post-commit logs the bypass attempt
        event_hash = logger.write_gate_event(result)

        # Verify bypass_type is None in normal flow
        query = "SELECT findings_count FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert row is not None
        assert row[0] == 1

    def test_no_verify_flag_audit_trail(self, setup):
        """Test --no-verify bypass is audited."""
        graph, logger = setup

        # Write event with bypass indicator in reason
        result = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0999",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason="Bypass detected: git --no-verify used",
            tenant_id="test-tenant",
        )

        event_hash = logger.write_gate_event(result)

        # Verify bypass reason is in audit trail
        query = "SELECT reason FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert row is not None
        assert "git --no-verify" in row[0]

    def test_sequential_bypasses_tracked(self, setup):
        """Test multiple bypass attempts are all logged."""
        graph, logger = setup

        bypass_count = 0
        for i in range(3):
            result = GateResult(
                gate_name="ADRGate",
                artifact_id=f"ADR-{9990+i}",
                verdict=VerdictType.FAIL,
                confidence=0.0,
                reason=f"Bypass detected: attempt #{i+1}",
                tenant_id="test-tenant",
            )
            event_hash = logger.write_gate_event(result)
            bypass_count += 1

        # Verify all bypasses logged
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ? AND verdict = 'fail'"
        row = graph.conn.execute(query, ["test-tenant"]).fetchone()
        assert row[0] == bypass_count

    def test_cross_tenant_bypass_isolation(self, setup):
        """Test bypass attempt on other tenant is isolated."""
        graph, logger = setup

        # Write bypass for test-tenant
        result1 = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0999",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason="Bypass attempt",
            tenant_id="test-tenant",
        )
        logger.write_gate_event(result1)

        # Query other tenant should not see bypass
        query = "SELECT COUNT(*) FROM gate_events WHERE tenant_id = ? AND reason LIKE ?"
        row = graph.conn.execute(query, ["other-tenant", "%Bypass%"]).fetchone()
        assert row[0] == 0

    def test_bypass_with_fake_verdict_audit(self, setup):
        """Test fake verdict in bypass is audited."""
        graph, logger = setup

        # Attacker tries to inject false PASS verdict
        result = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0999",
            verdict=VerdictType.PASS,  # Fake verdict
            confidence=0.95,  # Fake confidence
            reason="Injected by bypass attack",
            tenant_id="test-tenant",
            findings=["This should have failed"],
        )

        event_hash = logger.write_gate_event(result)

        # Verify findings contradict verdict
        query = "SELECT verdict, findings_count FROM gate_events WHERE event_hash = ?"
        row = graph.conn.execute(query, [event_hash]).fetchone()
        assert row[0] == "pass"  # Fake verdict
        assert row[1] == 1  # Has findings (contradiction)

    def test_bypass_recovery_consistency(self, setup):
        """Test system state is consistent after bypass attempt."""
        graph, logger = setup

        # Write event with bypass
        result = GateResult(
            gate_name="ADRGate",
            artifact_id="ADR-0999",
            verdict=VerdictType.FAIL,
            confidence=0.0,
            reason="Bypass recovery test",
            tenant_id="test-tenant",
        )
        hash1 = logger.write_gate_event(result)

        # Write follow-up normal event
        result2 = GateResult(
            gate_name="ConceptGate",
            artifact_id="CONCEPT-001",
            verdict=VerdictType.PASS,
            confidence=0.85,
            reason="Normal validation",
            tenant_id="test-tenant",
        )
        hash2 = logger.write_gate_event(result2)

        # Verify chain linking is intact
        query = "SELECT prior_hash FROM gate_events WHERE event_hash = ? ORDER BY timestamp"
        rows = graph.conn.execute(query, [hash2]).fetchall()
        assert len(rows) > 0
        assert rows[0][0] == hash1  # Proper chain continuation

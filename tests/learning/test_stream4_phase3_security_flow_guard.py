"""Stream 4 Phase 3: Security Orchestrator + Flow Guard Integration Tests (ADR-2050).

24 E2E tests covering:
  - Stream 2 (Security Orchestrator): 12 tests
  - Stream 3 (Flow Guard): retired with the flow-guard learning modules (ADR-2282)

Tests verify:
  - Audit trail integrity (chain binding)
  - Tenant isolation (GDPR Art. 32)
  - Cross-stream coordination (simultaneous feedback)
  - Fail-closed behavior
"""

import tempfile
import pytest
import unittest
from pathlib import Path
from unittest.mock import patch

from core.learning.event_store import EventStore
from core.learning.learning_events import EventType
from core.skills.os_skills.security_orchestrator.feedback_handler import (
    IncidentFeedbackHandler,
    IncidentFeedback,
    IncidentFeedbackType,
)
# flow_guard/feedback_handler.py (and the rest of the flow-guard learning modules)
# was retired by ADR-2282; its Stream-3 tests left with it. L34 itself is
# unchanged (tests/skills/test_flow_guard_skill_shadow_e2e.py).



@pytest.fixture(autouse=True)
def _downstream_of_the_feedback_gate(monkeypatch):
    """This suite exercises code downstream of the ADR-0534 feedback gate (writers,
    aggregations, optimizers), with bulk feedback on skills its sandbox never ran.
    The gate itself is proven in tests/learning/test_feedback_trust_gate_e2e.py."""
    from tests.learning.feedback_gate_helpers import open_gate
    open_gate(monkeypatch)

class TestPhase3Stream2Security(unittest.TestCase):
    """Stream 2 (Security Orchestrator) integration tests (12 tests)."""

    def setUp(self):
        """Create EventStore + IncidentFeedbackHandler."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home, tenant_id="_default")
        self.incident_handler = IncidentFeedbackHandler(
            event_store=self.event_store,
            tenant_id="_default",
            skill_id="os.security_orchestrator",
            skill_version="1.0.0",
        )

    def tearDown(self):
        """Cleanup."""
        self.temp_dir.cleanup()

    def test_s2_write_real_threat_feedback(self):
        """Test 1: Write 'real_threat' feedback (threat detection correct)."""
        feedback = IncidentFeedback(
            incident_id="incident_001",
            threat_type="brute_force",
            severity_level="high",
            feedback_type=IncidentFeedbackType.REAL_THREAT,
            confidence_score=0.95,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_001"
            event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal.get("feedback_type"), "real_threat")

    def test_s2_write_false_positive_feedback(self):
        """Test 2: Write 'false_positive' feedback (threat was not real)."""
        feedback = IncidentFeedback(
            incident_id="incident_002",
            threat_type="injection",
            severity_level="critical",
            feedback_type=IncidentFeedbackType.FALSE_POSITIVE,
            confidence_score=0.05,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_002"
            event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal.get("feedback_type"), "false_positive")

    def test_s2_write_missed_threat_feedback(self):
        """Test 3: Write 'missed_threat' feedback (we failed to detect)."""
        feedback = IncidentFeedback(
            incident_id="incident_003",
            threat_type="exfil",
            severity_level="critical",
            feedback_type=IncidentFeedbackType.MISSED_THREAT,
            confidence_score=0.1,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_003"
            event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal.get("feedback_type"), "missed_threat")

    def test_s2_write_severity_overestimated_feedback(self):
        """Test 4: Write 'severity_overestimated' feedback."""
        feedback = IncidentFeedback(
            incident_id="incident_004",
            threat_type="port_scan",
            severity_level="high",
            feedback_type=IncidentFeedbackType.SEVERITY_OVERESTIMATED,
            confidence_score=0.8,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_004"
            event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "severity_overestimated")

    def test_s2_write_severity_underestimated_feedback(self):
        """Test 5: Write 'severity_underestimated' feedback."""
        feedback = IncidentFeedback(
            incident_id="incident_005",
            threat_type="injection",
            severity_level="medium",
            feedback_type=IncidentFeedbackType.SEVERITY_UNDERESTIMATED,
            confidence_score=0.75,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_005"
            event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "severity_underestimated")

    def test_s2_skip_feedback_no_event(self):
        """Test 6: SKIP feedback produces no event."""
        feedback = IncidentFeedback(
            incident_id="incident_006",
            threat_type="brute_force",
            severity_level="low",
            feedback_type=IncidentFeedbackType.SKIP,
            confidence_score=0.5,
            tenant_id="_default",
        )

        event_id = self.incident_handler.process_feedback(feedback)

        self.assertIsNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 0)

    def test_s2_tenant_isolation_writer_rejects_foreign(self):
        """Test 7: Handler bound to _default rejects acme_corp feedback."""
        feedback = IncidentFeedback(
            incident_id="incident_007",
            threat_type="brute_force",
            severity_level="high",
            feedback_type=IncidentFeedbackType.REAL_THREAT,
            confidence_score=0.9,
            tenant_id="acme_corp",  # Foreign tenant
        )

        with self.assertRaises(ValueError) as ctx:
            self.incident_handler.process_feedback(feedback)

        self.assertIn("Tenant mismatch", str(ctx.exception))

    def test_s2_audit_chain_binding_verified(self):
        """Test 8: Audit chain binding present in stored event."""
        feedback = IncidentFeedback(
            incident_id="incident_008",
            threat_type="injection",
            severity_level="critical",
            feedback_type=IncidentFeedbackType.REAL_THREAT,
            confidence_score=0.98,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s2_008"
            self.incident_handler.process_feedback(feedback)

        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(events[0].audit_ref, "chain_s2_008")

    def test_s2_multiple_incidents_no_crosstalk(self):
        """Test 9: Multiple incident feedback events don't interfere."""
        for i in range(3):
            feedback = IncidentFeedback(
                incident_id=f"incident_00{i}",
                threat_type="brute_force" if i % 2 == 0 else "injection",
                severity_level="high" if i % 2 == 0 else "medium",
                feedback_type=IncidentFeedbackType.REAL_THREAT if i % 2 == 0 else IncidentFeedbackType.FALSE_POSITIVE,
                confidence_score=0.9 - (0.1 * i),
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s2_00{i}"
                self.incident_handler.process_feedback(feedback)

        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 3)

    def test_s2_get_recent_feedback_chronological_order(self):
        """Test 10: get_recent_feedback returns events in order."""
        for i in range(5):
            feedback = IncidentFeedback(
                incident_id=f"incident_{i:03d}",
                threat_type="brute_force",
                severity_level="high",
                feedback_type=IncidentFeedbackType.REAL_THREAT,
                confidence_score=0.95,
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s2_{i:03d}"
                self.incident_handler.process_feedback(feedback)

        recent = self.incident_handler.get_recent_feedback(limit=10)
        self.assertEqual(len(recent), 5)
        # Verify chronological order (oldest first)
        for i in range(len(recent) - 1):
            self.assertLessEqual(recent[i].timestamp, recent[i + 1].timestamp)

    def test_s2_count_feedback_accurate(self):
        """Test 11: count_feedback returns correct total."""
        for i in range(7):
            feedback = IncidentFeedback(
                incident_id=f"incident_{i:03d}",
                threat_type="brute_force",
                severity_level="high",
                feedback_type=IncidentFeedbackType.REAL_THREAT,
                confidence_score=0.95,
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s2_{i:03d}"
                self.incident_handler.process_feedback(feedback)

        count = self.incident_handler.count_feedback()
        self.assertEqual(count, 7)

    def test_s2_eventstore_unavailable_raises_error(self):
        """Test 12: EventStore unavailable → RuntimeError (fail-closed)."""
        feedback = IncidentFeedback(
            incident_id="incident_err",
            threat_type="brute_force",
            severity_level="high",
            feedback_type=IncidentFeedbackType.REAL_THREAT,
            confidence_score=0.95,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "write_event", side_effect=RuntimeError("EventStore down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.incident_handler.process_feedback(feedback)

            self.assertIn("audit chain", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

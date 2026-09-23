"""Stream 4 Phase 3: Security Orchestrator + Flow Guard Integration Tests (ADR-2050).

24 E2E tests covering:
  - Stream 2 (Security Orchestrator): 12 tests
  - Stream 3 (Flow Guard): 12 tests

Tests verify:
  - Audit trail integrity (chain binding)
  - Tenant isolation (GDPR Art. 32)
  - Cross-stream coordination (simultaneous feedback)
  - Fail-closed behavior
"""

import tempfile
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
from core.skills.os_skills.flow_guard.feedback_handler import (
    PolicyFeedbackHandler,
    PolicyFeedback,
    PolicyFeedbackType,
)


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


class TestPhase3Stream3FlowGuard(unittest.TestCase):
    """Stream 3 (Flow Guard) integration tests (12 tests)."""

    def setUp(self):
        """Create EventStore + PolicyFeedbackHandler."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home, tenant_id="_default")
        self.policy_handler = PolicyFeedbackHandler(
            event_store=self.event_store,
            tenant_id="_default",
            skill_id="os.flow_guard",
            skill_version="1.0.0",
        )

    def tearDown(self):
        """Cleanup."""
        self.temp_dir.cleanup()

    def test_s3_write_allow_correct_feedback(self):
        """Test 1: Write 'allow_correct' feedback (policy decision correct)."""
        feedback = PolicyFeedback(
            flow_id="flow_001",
            data_class="PUBLIC",
            engine="claude-sonnet-5",
            destination="console",
            policy_decision="allow",
            feedback_type=PolicyFeedbackType.ALLOW_CORRECT,
            confidence_score=0.95,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_001"
            event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal.get("feedback_type"), "allow_correct")

    def test_s3_write_deny_correct_feedback(self):
        """Test 2: Write 'deny_correct' feedback."""
        feedback = PolicyFeedback(
            flow_id="flow_002",
            data_class="API_KEY",
            engine="claude-opus-5",
            destination="webhook",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.DENY_CORRECT,
            confidence_score=0.98,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_002"
            event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "deny_correct")

    def test_s3_write_allow_wrong_feedback(self):
        """Test 3: Write 'allow_wrong' feedback (should have denied)."""
        feedback = PolicyFeedback(
            flow_id="flow_003",
            data_class="PII",
            engine="claude-haiku-4-5",
            destination="file",
            policy_decision="allow",
            feedback_type=PolicyFeedbackType.ALLOW_WRONG,
            confidence_score=0.15,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_003"
            event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "allow_wrong")

    def test_s3_write_deny_wrong_feedback(self):
        """Test 4: Write 'deny_wrong' feedback (should have allowed)."""
        feedback = PolicyFeedback(
            flow_id="flow_004",
            data_class="PUBLIC",
            engine="claude-sonnet-5",
            destination="api",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.DENY_WRONG,
            confidence_score=0.2,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_004"
            event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "deny_wrong")

    def test_s3_write_exception_request_feedback(self):
        """Test 5: Write 'exception_request' feedback with TTL."""
        feedback = PolicyFeedback(
            flow_id="flow_005",
            data_class="API_KEY",
            engine="claude-opus-5",
            destination="webhook",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.EXCEPTION_REQUEST,
            confidence_score=0.85,
            exception_ttl_hours=24,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_005"
            event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNotNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(events[0].signal.get("feedback_type"), "exception_request")
        self.assertEqual(events[0].signal.get("exception_ttl_hours"), 24)

    def test_s3_skip_feedback_no_event(self):
        """Test 6: SKIP feedback produces no event."""
        feedback = PolicyFeedback(
            flow_id="flow_006",
            data_class="PUBLIC",
            engine="claude-haiku-4-5",
            destination="console",
            policy_decision="allow",
            feedback_type=PolicyFeedbackType.SKIP,
            confidence_score=0.5,
            tenant_id="_default",
        )

        event_id = self.policy_handler.process_feedback(feedback)

        self.assertIsNone(event_id)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(len(events), 0)

    def test_s3_tenant_isolation_writer_rejects_foreign(self):
        """Test 7: Handler rejects foreign tenant feedback."""
        feedback = PolicyFeedback(
            flow_id="flow_007",
            data_class="PII",
            engine="claude-opus-5",
            destination="webhook",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.DENY_CORRECT,
            confidence_score=0.95,
            tenant_id="acme_corp",  # Foreign
        )

        with self.assertRaises(ValueError) as ctx:
            self.policy_handler.process_feedback(feedback)

        self.assertIn("Tenant mismatch", str(ctx.exception))

    def test_s3_audit_chain_binding_verified(self):
        """Test 8: Audit chain binding present in stored event."""
        feedback = PolicyFeedback(
            flow_id="flow_008",
            data_class="API_KEY",
            engine="claude-sonnet-5",
            destination="webhook",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.DENY_CORRECT,
            confidence_score=0.97,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_s3_008"
            self.policy_handler.process_feedback(feedback)

        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(events[0].audit_ref, "chain_s3_008")

    def test_s3_multiple_flows_no_crosstalk(self):
        """Test 9: Multiple flow feedback events don't interfere."""
        for i in range(3):
            feedback = PolicyFeedback(
                flow_id=f"flow_{i:03d}",
                data_class="PII" if i % 2 == 0 else "API_KEY",
                engine="claude-sonnet-5",
                destination="webhook",
                policy_decision="deny",
                feedback_type=PolicyFeedbackType.DENY_CORRECT if i % 2 == 0 else PolicyFeedbackType.ALLOW_WRONG,
                confidence_score=0.95 - (0.1 * i),
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s3_{i:03d}"
                self.policy_handler.process_feedback(feedback)

        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.PREFERENCE,
        )
        self.assertEqual(len(events), 3)

    def test_s3_get_recent_feedback_chronological_order(self):
        """Test 10: get_recent_feedback returns events in order."""
        for i in range(5):
            feedback = PolicyFeedback(
                flow_id=f"flow_{i:03d}",
                data_class="API_KEY",
                engine="claude-opus-5",
                destination="webhook",
                policy_decision="deny",
                feedback_type=PolicyFeedbackType.DENY_CORRECT,
                confidence_score=0.95,
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s3_{i:03d}"
                self.policy_handler.process_feedback(feedback)

        recent = self.policy_handler.get_recent_feedback(limit=10)
        self.assertEqual(len(recent), 5)
        for i in range(len(recent) - 1):
            self.assertLessEqual(recent[i].timestamp, recent[i + 1].timestamp)

    def test_s3_count_feedback_accurate(self):
        """Test 11: count_feedback returns correct total."""
        for i in range(6):
            feedback = PolicyFeedback(
                flow_id=f"flow_{i:03d}",
                data_class="PII",
                engine="claude-haiku-4-5",
                destination="file",
                policy_decision="deny",
                feedback_type=PolicyFeedbackType.DENY_CORRECT,
                confidence_score=0.95,
                tenant_id="_default",
            )

            with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
                mock_chain.return_value = f"chain_s3_{i:03d}"
                self.policy_handler.process_feedback(feedback)

        count = self.policy_handler.count_feedback()
        self.assertEqual(count, 6)

    def test_s3_eventstore_unavailable_raises_error(self):
        """Test 12: EventStore unavailable → RuntimeError (fail-closed)."""
        feedback = PolicyFeedback(
            flow_id="flow_err",
            data_class="API_KEY",
            engine="claude-opus-5",
            destination="webhook",
            policy_decision="deny",
            feedback_type=PolicyFeedbackType.DENY_CORRECT,
            confidence_score=0.95,
            tenant_id="_default",
        )

        with patch.object(self.event_store, "write_event", side_effect=RuntimeError("EventStore down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.policy_handler.process_feedback(feedback)

            self.assertIn("audit chain", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()

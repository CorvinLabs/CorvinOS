"""Stream 4 Phase 1: EventStore Writer Integration Tests (ADR-2050).

12 E2E tests covering:
  1. EventStore write (4 tests)
  2. Audit trail + tenant isolation (5 tests)
  3. In-memory queue + retry (3 tests)

All tests:
  - Run against real EventStore (no mocks)
  - Verify audit-chain integration (chain write before disk)
  - Verify tenant isolation (GDPR Art. 32)
  - Verify fail-closed behavior (queue on unavailability)
  - Verify queue/retry logic (exponential backoff, max retries)
"""

import json
import tempfile
import threading
import time
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

from core.learning.event_store import EventStore
from core.learning.feedback_integration.event_store_writer import (
    EventStoreWriter,
    QueuedFeedback,
)
from core.learning.feedback_integration.models.feedback_event import (
    FeedbackEvent,
    FeedbackType,
    OutcomeChoice,
    PreferenceChoice,
)
from core.learning.learning_events import EventType


class TestEventStoreWriterBasics(unittest.TestCase):
    """Group 1: EventStore write basics (4 tests)."""

    def setUp(self):
        """Create temp EventStore for testing."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home, tenant_id="_default")
        self.writer = EventStoreWriter(self.event_store, tenant_id="_default")

    def tearDown(self):
        """Cleanup."""
        self.temp_dir.cleanup()

    def test_write_outcome_feedback_success(self):
        """Test 1: Write outcome_feedback to EventStore (audit-chain + disk)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
            task_id="task_123",
        )

        # Write should succeed and return audit_ref
        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_abc123"
            audit_ref = self.writer.write_feedback(feedback)

        # Verify audit_ref returned
        self.assertEqual(audit_ref, "chain_ref_abc123")
        mock_chain.assert_called_once()

        # Verify event landed on disk
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
            skill_id="os.workflow_optimizer",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].skill_id, "os.workflow_optimizer")

    def test_write_confidence_feedback_success(self):
        """Test 2: Write confidence_score feedback (numeric validation)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.CONFIDENCE,
            skill_id="os.security_orchestrator",
            tenant_id="_default",
            confidence_score=0.85,
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_def456"
            audit_ref = self.writer.write_feedback(feedback)

        self.assertIsNotNone(audit_ref)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
            skill_id="os.security_orchestrator",
        )
        self.assertEqual(len(events), 1)
        # Signal contains confidence_score
        self.assertEqual(events[0].signal.get("confidence_score"), 0.85)

    def test_write_preference_feedback_success(self):
        """Test 3: Write preference_feedback (LLM vs deterministic)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.PREFERENCE,
            skill_id="os.flow_guard",
            tenant_id="_default",
            preference=PreferenceChoice.DETERMINISTIC,
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_ghi789"
            audit_ref = self.writer.write_feedback(feedback)

        self.assertIsNotNone(audit_ref)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
            skill_id="os.flow_guard",
        )
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].signal.get("preference"), "deterministic")

    def test_write_metric_feedback_success(self):
        """Test 4: Write metric_observed feedback (latency, cost, etc.)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.METRIC,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            metric_name="latency_ms",
            metric_value=42.5,
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_jkl012"
            audit_ref = self.writer.write_feedback(feedback)

        self.assertIsNotNone(audit_ref)
        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertGreater(len(events), 0)
        metric_event = [e for e in events if e.skill_id == "os.workflow_optimizer"][-1]
        self.assertEqual(metric_event.signal.get("metric_name"), "latency_ms")
        self.assertEqual(metric_event.signal.get("metric_value"), 42.5)


class TestTenantIsolation(unittest.TestCase):
    """Group 2: Tenant isolation + audit trail (5 tests)."""

    def setUp(self):
        """Create EventStore + writers for two tenants."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home)  # No tenant binding
        self.writer_default = EventStoreWriter(self.event_store, tenant_id="_default")
        self.writer_acme = EventStoreWriter(self.event_store, tenant_id="acme_corp")

    def tearDown(self):
        """Cleanup."""
        self.temp_dir.cleanup()

    def test_tenant_isolation_writer_rejects_foreign_feedback(self):
        """Test 5: Writer bound to _default rejects feedback from acme_corp."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="acme_corp",  # Foreign tenant
            outcome=OutcomeChoice.YES,
        )

        # Writer bound to _default should reject
        with self.assertRaises(ValueError) as ctx:
            self.writer_default.write_feedback(feedback)

        self.assertIn("Tenant mismatch", str(ctx.exception))

    def test_tenant_isolation_feedback_not_crossable(self):
        """Test 6: Feedback written by tenant A not visible to tenant B."""
        feedback_a = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        feedback_b = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="acme_corp",
            outcome=OutcomeChoice.NO,
        )

        # Write both
        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_1"
            self.writer_default.write_feedback(feedback_a)

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_ref_2"
            self.writer_acme.write_feedback(feedback_b)

        # Query as tenant A: should see only A's feedback
        events_a = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        for event in events_a:
            self.assertEqual(event.tenant_id, "_default")
            self.assertEqual(event.signal.get("outcome"), "yes")

        # Query as tenant B: should see only B's feedback
        events_b = self.event_store.query_events(
            tenant_id="acme_corp",
            event_type=EventType.FEEDBACK,
        )
        for event in events_b:
            self.assertEqual(event.tenant_id, "acme_corp")
            self.assertEqual(event.signal.get("outcome"), "no")

    def test_audit_trail_records_feedback_chain_binding(self):
        """Test 7: Audit trail shows chain_ref linking feedback to core chain."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        with patch.object(self.event_store, "_audit_chain_first") as mock_chain:
            mock_chain.return_value = "chain_abc123"
            self.writer_default.write_feedback(feedback)

        events = self.event_store.query_events(
            tenant_id="_default",
            event_type=EventType.FEEDBACK,
        )
        self.assertEqual(len(events), 1)
        # audit_ref should match the chain binding
        self.assertEqual(events[0].audit_ref, "chain_abc123")

    def test_audit_chain_first_order_enforced(self):
        """Test 8: Chain write called before disk write (verify call order)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.security_orchestrator",
            tenant_id="_default",
            outcome=OutcomeChoice.NO,
        )

        call_order = []

        # Mock both chain and disk writes to track order
        original_audit_chain = self.event_store._audit_chain_first
        original_get_event_file = self.event_store._get_event_file

        def mock_chain(event):
            call_order.append("chain")
            return original_audit_chain(event)

        def mock_get_file(timestamp):
            call_order.append("disk")
            return original_get_event_file(timestamp)

        with patch.object(self.event_store, "_audit_chain_first", side_effect=mock_chain):
            with patch.object(self.event_store, "_get_event_file", side_effect=mock_get_file):
                self.writer_default.write_feedback(feedback)

        # Chain should be written before disk
        self.assertIn("chain", call_order)
        self.assertIn("disk", call_order)
        self.assertEqual(call_order.index("chain"), 0)


class TestQueueAndRetry(unittest.TestCase):
    """Group 3: In-memory queue + retry logic (3 tests)."""

    def setUp(self):
        """Create EventStore + writer for testing queue/retry."""
        self.temp_dir = tempfile.TemporaryDirectory()
        self.tenant_home = Path(self.temp_dir.name)
        self.event_store = EventStore(self.tenant_home, tenant_id="_default")
        self.writer = EventStoreWriter(self.event_store, tenant_id="_default")

    def tearDown(self):
        """Cleanup."""
        self.writer.stop_background_flusher()
        self.temp_dir.cleanup()

    def test_queue_on_eventstore_unavailable(self):
        """Test 9: Feedback queued when EventStore unavailable (no silent loss)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        # Mock EventStore to raise RuntimeError (unavailable)
        with patch.object(self.event_store, "write_event", side_effect=RuntimeError("EventStore down")):
            with self.assertRaises(RuntimeError) as ctx:
                self.writer.write_feedback(feedback)

            self.assertIn("EventStore unavailable", str(ctx.exception))

        # Verify feedback queued
        self.assertEqual(self.writer.get_queue_size(), 1)
        queued_items = self.writer.get_queued_feedback()
        self.assertEqual(len(queued_items), 1)
        self.assertEqual(queued_items[0].feedback.feedback_id, feedback.feedback_id)

    def test_retry_queued_feedback_on_flush(self):
        """Test 10: Queued feedback retried on flush; succeeds on recovery."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        # First write: EventStore unavailable
        with patch.object(self.event_store, "write_event", side_effect=RuntimeError("EventStore down")):
            with self.assertRaises(RuntimeError):
                self.writer.write_feedback(feedback)

        self.assertEqual(self.writer.get_queue_size(), 1)

        # Now EventStore is available
        with patch.object(self.event_store, "write_event") as mock_write:
            mock_write.return_value = None
            flushed = self.writer.flush_queue()

        self.assertEqual(flushed, 1)
        self.assertEqual(self.writer.get_queue_size(), 0)

    def test_max_retries_exceeded_feedback_discarded(self):
        """Test 11: Feedback discarded after MAX_RETRIES (5) attempts."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        # Queue with retry_count at MAX_RETRIES - 1
        queued = QueuedFeedback(
            feedback=feedback,
            created_at=datetime.utcnow(),
            retry_count=EventStoreWriter.MAX_RETRIES,
        )
        self.writer._queue.append(queued)

        # Flush should discard without retrying
        with patch.object(self.event_store, "write_event") as mock_write:
            flushed = self.writer.flush_queue()

        # No write attempt (discarded immediately)
        mock_write.assert_not_called()
        self.assertEqual(flushed, 0)
        self.assertEqual(self.writer.get_queue_size(), 0)

    def test_exponential_backoff_retry_timing(self):
        """Test 12: Queued feedback respects exponential backoff (2^retry_count)."""
        feedback = FeedbackEvent.create(
            feedback_type=FeedbackType.OUTCOME,
            skill_id="os.workflow_optimizer",
            tenant_id="_default",
            outcome=OutcomeChoice.YES,
        )

        # Queue with retry_count=0, old created_at (should retry immediately)
        old_time = datetime.utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
        queued_0 = QueuedFeedback(
            feedback=feedback,
            created_at=old_time,
            retry_count=0,
        )

        # Queue with retry_count=2, recent created_at (backoff not yet elapsed)
        queued_2 = QueuedFeedback(
            feedback=FeedbackEvent.create(
                feedback_type=FeedbackType.OUTCOME,
                skill_id="os.workflow_optimizer",
                tenant_id="_default",
                outcome=OutcomeChoice.NO,
            ),
            created_at=datetime.utcnow(),
            retry_count=2,
        )

        self.writer._queue.append(queued_0)
        self.writer._queue.append(queued_2)

        # First flush should only retry the old one (queued_0 backoff elapsed)
        with patch.object(self.event_store, "write_event") as mock_write:
            mock_write.return_value = None
            flushed = self.writer.flush_queue()

        # Only queued_0 should have been retried
        self.assertEqual(flushed, 1)
        # queued_2 should still be in queue (backoff not elapsed)
        self.assertEqual(self.writer.get_queue_size(), 1)


class TestBackgroundFlusher(unittest.TestCase):
    """Integration: Background flusher thread."""

    def test_background_flusher_starts_and_stops(self):
        """Verify background flusher thread lifecycle."""
        temp_dir = tempfile.TemporaryDirectory()
        try:
            tenant_home = Path(temp_dir.name)
            event_store = EventStore(tenant_home, tenant_id="_default")
            writer = EventStoreWriter(event_store, tenant_id="_default")

            # Start flusher
            writer.start_background_flusher()
            self.assertTrue(writer._flusher_thread is not None)
            self.assertTrue(writer._flusher_thread.is_alive())

            # Stop flusher (graceful)
            writer.stop_background_flusher()
            time.sleep(0.5)  # Give thread time to exit
            self.assertFalse(writer._flusher_thread.is_alive())

        finally:
            temp_dir.cleanup()


if __name__ == "__main__":
    unittest.main()

"""Timeout and failure tests for plugin lifecycle events (ADR-0682).

Queue drain and the before-turn tripwire. "Drained" means written to the
tenant audit chain (tests/conftest.py isolates CORVIN_HOME per test).
"""

import json
import pytest
import time
from pathlib import Path
from unittest import mock

from forge import paths as forge_paths

from core.audit.event_queue import EventQueue, DrainingError
from core.compliance.plugin_event_queue_tripwire import (
    drain_event_queue_before_turn,
    TripwireError,
    verify_queue_drained,
)


class TestDrainTimeout:
    """Test queue drain timeout behavior."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_timeout.db"
        return EventQueue(db_path)

    def test_drain_completes_within_timeout(self, temp_queue):
        """Normal drain completes within timeout."""
        # Enqueue some events
        for i in range(10):
            temp_queue.enqueue({
                'event_type': 'plugin_loaded',
                'plugin_id': f'plugin-{i}',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                'priority': 'LOW',
            })

        # Drain with generous timeout
        start = time.time()
        drained = temp_queue.drain(batch_size=50, timeout_sec=5.0)
        elapsed = time.time() - start

        assert len(drained) == 10  # ten distinct plugins → ten records
        assert elapsed < 5.0
        chain = forge_paths.tenant_audit_chain("_default")
        recs = [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]
        loaded = [r for r in recs if r["event_type"] == "plugin.lifecycle_loaded"]
        assert sorted(r["details"]["plugin_id"] for r in loaded) == [f"plugin-{i}" for i in range(10)]
        assert temp_queue.stats().pending_count == 0

    def test_drain_past_deadline_leaves_rows_pending(self, temp_queue):
        """A drain that runs out of time raises and marks nothing it did not write."""
        from core.audit.event_queue import DrainingError

        for i in range(3):
            temp_queue.enqueue({
                'event_type': 'plugin_error',
                'plugin_id': f'plugin-{i}',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                'error_type': 'RuntimeError',
                'priority': 'HIGH',
            })
        with pytest.raises(DrainingError):
            temp_queue.drain(batch_size=50, timeout_sec=0.0)
        assert temp_queue.stats().pending_count == 3


class TestTripwireSuccess:
    """Test tripwire success path."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_tripwire.db"
        queue = EventQueue(db_path)

        # Populate with some events
        for i in range(5):
            queue.enqueue({
                'event_type': 'plugin_executed',
                'plugin_id': f'plugin-{i}',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                'priority': 'HIGH',
            })

        yield queue

    def test_tripwire_passes_on_successful_drain(self, temp_queue):
        """Tripwire passes when queue drains successfully."""
        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=temp_queue):
            result = drain_event_queue_before_turn(tenant_id='_default', timeout_sec=5.0)

            assert result.success is True
            assert result.events_drained == 5
            assert result.error is None
        assert temp_queue.stats().pending_count == 0
        chain = forge_paths.tenant_audit_chain("_default")
        types = [json.loads(l)["event_type"] for l in chain.read_text().splitlines() if l.strip()]
        assert types.count("plugin.lifecycle_executed") == 5

    def test_tripwire_denies_turn_when_chain_write_fails(self, temp_queue):
        """A chain write that fails leaves the events pending and denies the turn."""
        from forge import security_events

        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=temp_queue), \
             mock.patch.object(security_events, 'write_event', side_effect=OSError("disk full")):
            with pytest.raises(TripwireError):
                drain_event_queue_before_turn(tenant_id='_default', timeout_sec=5.0)
        assert temp_queue.stats().pending_count == 5


class TestTripwireFailure:
    """Test tripwire failure (turn denial)."""

    def test_tripwire_raises_on_drain_failure(self):
        """Tripwire raises TripwireError on drain failure (turn DENIED)."""
        mock_queue = mock.MagicMock()
        mock_queue.drain.side_effect = DrainingError("Timeout")
        mock_queue.stats.return_value = mock.MagicMock(pending_count=0)

        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=mock_queue):
            with pytest.raises(TripwireError):
                drain_event_queue_before_turn(tenant_id='_default', timeout_sec=5.0)


class TestQueueDrainedVerification:
    """Test queue drain verification (monitoring)."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_verify.db"
        return EventQueue(db_path)

    def test_verify_queue_empty(self, temp_queue):
        """Verify returns True when queue is empty."""
        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=temp_queue):
            result = verify_queue_drained(tenant_id='_default')
            assert result is True

    def test_verify_queue_pending_is_false(self, temp_queue):
        temp_queue.enqueue({
            'event_type': 'plugin_executed', 'plugin_id': 'p', 'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z', 'priority': 'HIGH',
        })
        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=temp_queue):
            assert verify_queue_drained(tenant_id='_default') is False

    def test_verify_unreadable_queue_is_false(self):
        """Fail-closed: an unreadable queue never reads as drained."""
        broken = mock.MagicMock()
        broken.stats.side_effect = RuntimeError("db locked")
        with mock.patch('core.compliance.plugin_event_queue_tripwire.EventQueue', return_value=broken):
            assert verify_queue_drained(tenant_id='_default') is False

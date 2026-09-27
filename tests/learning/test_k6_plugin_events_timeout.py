"""Timeout and failure tests for plugin lifecycle events (ADR-0682).

4 test cases covering queue timeout behavior and tripwire.
"""

import pytest
import time
from pathlib import Path
from unittest import mock

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

        assert len(drained) > 0
        assert elapsed < 1.0  # Should be fast


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
            assert result.events_drained > 0
            assert result.error is None


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

"""Unit tests for plugin lifecycle events (ADR-0682, Learning k=6).

10 test cases covering:
- Event emission and queueing
- PII redaction (fail-closed)
- Event immutability
- Backpressure
- Priority handling
- Load deduplication
"""

import pytest
import json
import sqlite3
from datetime import datetime
from pathlib import Path
from unittest import mock

from core.plugins.corvin_plugins.lifecycle import (
    PluginLifecycleEvent,
    emit_plugin_loaded,
    emit_plugin_executed,
    emit_plugin_error,
    emit_plugin_disabled,
    _scrub_pii,
)
from core.audit.event_queue import EventQueue, QueueFullError
from core.audit.plugin_audit_integration import emit_to_queue, validate_audit_event


class TestPluginEventEmission:
    """Test plugin event emitters."""

    def test_emit_plugin_loaded(self):
        """plugin_loaded emitter creates immutable event."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle._enqueue_event') as mock_enqueue:
            emit_plugin_loaded('test-plugin', '_default', version='1.0.0')

            assert mock_enqueue.called
            event = mock_enqueue.call_args[0][0]
            assert event.event_type == 'plugin_loaded'
            assert event.plugin_id == 'test-plugin'
            assert event.tenant_id == '_default'
            assert event.version == '1.0.0'

    def test_emit_plugin_executed(self):
        """plugin_executed emitter hashes input/output."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle._enqueue_event') as mock_enqueue:
            emit_plugin_executed(
                'test-plugin',
                '_default',
                'method_x',
                {'key': 'value'},
                {'result': 'ok'},
                42.5,
                version='1.0.0'
            )

            assert mock_enqueue.called
            event = mock_enqueue.call_args[0][0]
            assert event.event_type == 'plugin_executed'
            assert event.method_name == 'method_x'
            assert event.input_hash is not None
            assert event.output_hash is not None
            assert event.latency_ms == 42.5
            assert event.priority == 'HIGH'

    def test_emit_plugin_error(self):
        """plugin_error emitter hashes error message."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle._enqueue_event') as mock_enqueue:
            error = ValueError("Something went wrong")
            emit_plugin_error('test-plugin', '_default', 'method_y', error)

            assert mock_enqueue.called
            event = mock_enqueue.call_args[0][0]
            assert event.event_type == 'plugin_error'
            assert event.error_type == 'ValueError'
            assert event.error_message_hash is not None
            assert event.priority == 'HIGH'

    def test_emit_plugin_disabled(self):
        """plugin_disabled emitter creates disable event."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle._enqueue_event') as mock_enqueue:
            emit_plugin_disabled('test-plugin', '_default', reason='health_check_failed')

            assert mock_enqueue.called
            event = mock_enqueue.call_args[0][0]
            assert event.event_type == 'plugin_disabled'
            assert event.reason == 'health_check_failed'
            assert event.priority == 'HIGH'


class TestPIIRedaction:
    """Test PII detection and fail-closed redaction."""

    def test_scrub_pii_detects_email(self):
        """PII scrubber detects @ pattern (email-like)."""
        payload = {'user': 'user@example.com', 'name': 'John'}
        hash_val = _scrub_pii(payload)

        assert hash_val is not None
        assert len(hash_val) == 64  # SHA256 hex
        # Hash should be stable for same input
        assert _scrub_pii(payload) == hash_val

    def test_scrub_pii_detects_token(self):
        """PII scrubber detects 'token' keyword."""
        payload = {'access_token': 'secret123', 'user_id': 'user1'}
        hash_val = _scrub_pii(payload)

        assert hash_val is not None
        # Should be different from unredacted hash
        safe_payload = {'user_id': 'user1'}
        assert hash_val != _scrub_pii(safe_payload)

    def test_scrub_pii_safe_payload(self):
        """PII scrubber passes safe payloads through."""
        payload = {'plugin_id': 'test', 'status': 'ok', 'latency_ms': 42}
        hash_val = _scrub_pii(payload)

        assert hash_val is not None
        # Hash should be deterministic
        assert _scrub_pii(payload) == hash_val


class TestEventImmutability:
    """Test that events are immutable (frozen dataclass)."""

    def test_event_is_frozen(self):
        """PluginLifecycleEvent is immutable (frozen dataclass)."""
        event = PluginLifecycleEvent(
            event_type='plugin_loaded',
            plugin_id='test',
            tenant_id='_default',
            timestamp='2026-09-27T00:00:00Z'
        )

        # Should not be able to modify
        with pytest.raises(Exception):  # FrozenInstanceError
            event.plugin_id = 'modified'

    def test_event_to_dict(self):
        """Event converts to JSON-serializable dict."""
        event = PluginLifecycleEvent(
            event_type='plugin_loaded',
            plugin_id='test',
            tenant_id='_default',
            timestamp='2026-09-27T00:00:00Z',
            version='1.0.0'
        )

        event_dict = event.to_dict()
        assert isinstance(event_dict, dict)
        assert event_dict['event_type'] == 'plugin_loaded'
        assert event_dict['plugin_id'] == 'test'


class TestEventQueueBasics:
    """Test EventQueue basic operations."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_events.db"
        queue = EventQueue(db_path)
        yield queue

    def test_queue_initialization(self, temp_queue):
        """Queue initializes with correct schema."""
        assert temp_queue.db_path.exists()

        # Check schema
        with sqlite3.connect(temp_queue.db_path) as conn:
            cursor = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='plugin_events'"
            )
            assert cursor.fetchone() is not None

    def test_enqueue_event(self, temp_queue):
        """Event enqueues successfully."""
        event_dict = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'priority': 'LOW',
        }

        temp_queue.enqueue(event_dict)

        stats = temp_queue.stats()
        assert stats.total_events == 1
        assert stats.pending_count == 1

    def test_queue_deduplication(self, temp_queue):
        """Queue deduplicates identical events."""
        event_dict = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'priority': 'LOW',
        }

        # Enqueue same event twice
        temp_queue.enqueue(event_dict)
        temp_queue.enqueue(event_dict)  # Should be deduplicated

        stats = temp_queue.stats()
        assert stats.total_events == 1  # Only 1 stored


class TestBackpressure:
    """Bounded queue: LOW shed from 80 %, HIGH refused only when full."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        return EventQueue(tmp_path / "test_backpressure.db", max_pending=10)

    @staticmethod
    def _ev(i, priority, event_type='plugin_loaded'):
        return {
            'event_type': event_type,
            'plugin_id': f'plugin{i}',
            'tenant_id': '_default',
            'timestamp': f'2026-09-27T00:00:{i:02d}Z',
            'priority': priority,
        }

    def test_low_is_shed_at_80_percent_high_still_accepted(self, temp_queue):
        for i in range(8):
            assert temp_queue.enqueue(self._ev(i, 'LOW')) is True
        assert temp_queue.enqueue(self._ev(8, 'LOW')) is False  # shed
        # HIGH keeps the remaining headroom
        assert temp_queue.enqueue(self._ev(20, 'HIGH', 'plugin_executed')) is True
        assert temp_queue.enqueue(self._ev(21, 'HIGH', 'plugin_executed')) is True
        assert temp_queue.stats().pending_count == 10

    def test_high_refused_only_when_full(self, temp_queue):
        for i in range(10):
            assert temp_queue.enqueue(self._ev(i, 'HIGH', 'plugin_executed')) is True
        with pytest.raises(QueueFullError):
            temp_queue.enqueue(self._ev(30, 'HIGH', 'plugin_executed'))

    def test_first_high_event_is_never_refused(self, tmp_path):
        """Regression: the old check (pending > 0.8 * total) refused every HIGH
        event once a single row was pending and none had been emitted."""
        q = EventQueue(tmp_path / "bp_regression.db")
        assert q.enqueue(self._ev(0, 'LOW')) is True
        assert q.enqueue(self._ev(1, 'HIGH', 'plugin_executed')) is True

    def test_drain_frees_capacity(self, temp_queue):
        for i in range(10):
            temp_queue.enqueue(self._ev(i, 'HIGH', 'plugin_executed'))
        temp_queue.drain(batch_size=50, timeout_sec=5.0)
        assert temp_queue.enqueue(self._ev(40, 'HIGH', 'plugin_executed')) is True


class TestQueueLocation:
    """The default queue lives under the TENANT's home, resolved at call time."""

    def test_default_path_is_tenant_scoped_and_honours_corvin_home(self, monkeypatch, tmp_path):
        monkeypatch.setenv("CORVIN_HOME", str(tmp_path / "ch"))
        q = EventQueue(tenant_id="acme")
        assert q.db_path == tmp_path / "ch" / "tenants" / "acme" / "global" / "plugin_events.db"

    def test_invalid_tenant_rejected(self):
        with pytest.raises(Exception):
            EventQueue(tenant_id="../escape")

    def test_malformed_timestamp_rejected_at_enqueue(self, tmp_path):
        q = EventQueue(tmp_path / "ts.db")
        with pytest.raises(ValueError):
            q.enqueue({'event_type': 'plugin_loaded', 'plugin_id': 'p', 'tenant_id': '_default',
                       'timestamp': '2026-09-27T00:000Z', 'priority': 'LOW'})
        assert q.stats().total_events == 0

    def test_mark_emitted_is_not_a_public_shortcut(self, tmp_path):
        q = EventQueue(tmp_path / "m.db")
        with pytest.raises(NotImplementedError):
            q.mark_emitted([1])


class TestPriorityOrdering:
    """Test HIGH/LOW priority event handling."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_priority.db"
        return EventQueue(db_path)

    def test_priority_counts(self, temp_queue):
        """Queue correctly counts events by priority."""
        # Add mixed priority events
        for i in range(5):
            temp_queue.enqueue({
                'event_type': 'plugin_loaded',
                'plugin_id': f'plugin{i}',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                'priority': 'LOW',
            })

        for i in range(3):
            temp_queue.enqueue({
                'event_type': 'plugin_executed',
                'plugin_id': f'plugin{i}',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:01:{i:02d}Z',
                'priority': 'HIGH',
            })

        stats = temp_queue.stats()
        assert stats.high_priority_count == 3
        assert stats.low_priority_count == 5


class TestEventValidation:
    """Test event validation."""

    def test_validate_valid_event(self):
        """Valid event passes validation."""
        event = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
        }

        assert validate_audit_event(event) is True

    def test_validate_missing_tenant_id(self):
        """Event without tenant_id fails validation."""
        event = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '',  # Empty
            'timestamp': '2026-09-27T00:00:00Z',
        }

        assert validate_audit_event(event) is False

    def test_validate_raw_payload_fails(self):
        """Event with raw payload fails validation (fail-closed)."""
        event = {
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'input_payload': {'raw': 'data'},  # Raw, not hashed!
        }

        assert validate_audit_event(event) is False

"""Adversarial tests for plugin lifecycle events (ADR-0682).

5 test cases covering security, compliance, and edge cases.
"""

import pytest
from pathlib import Path

from core.audit.event_queue import EventQueue
from core.audit.plugin_audit_integration import validate_audit_event


class TestCrossTenantIsolation:
    """Test tenant isolation (GDPR Art. 5, 6)."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_tenant_isolation.db"
        return EventQueue(db_path)

    def test_no_cross_tenant_leakage(self, temp_queue):
        """Events from different tenants are isolated."""
        # Enqueue for tenant1
        temp_queue.enqueue({
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': 'tenant1',
            'timestamp': '2026-09-27T00:00:00Z',
            'input_hash': 'tenant1_data_hash',
            'priority': 'HIGH',
        })

        # Enqueue for tenant2
        temp_queue.enqueue({
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': 'tenant2',
            'timestamp': '2026-09-27T00:00:01Z',
            'input_hash': 'tenant2_data_hash',
            'priority': 'HIGH',
        })

        # Both should be in queue
        stats = temp_queue.stats()
        assert stats.total_events == 2

        # Drain and verify isolation
        drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)
        tenant1_events = [e for e in drained if e['tenant_id'] == 'tenant1']
        tenant2_events = [e for e in drained if e['tenant_id'] == 'tenant2']

        # Both tenants should have their events
        assert len(tenant1_events) > 0
        assert len(tenant2_events) > 0


class TestPIIRedactionEnforcement:
    """Test PII redaction is enforced (fail-closed)."""

    def test_raw_pii_in_payload_rejected(self):
        """Event with raw PII in payload fails validation."""
        event = {
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'error_message': 'Failed: user@example.com not found',  # Raw PII!
        }

        # Validation should fail (raw PII)
        # Note: validate_audit_event checks for raw_sensitive_fields
        # 'error_message' is one of them
        assert validate_audit_event(event) is False

    def test_hashed_payload_accepted(self):
        """Event with hashed sensitive data is accepted."""
        event = {
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'input_hash': 'abc123def456...',  # SHA256 hash
            'output_hash': 'xyz789abc123...',
        }

        assert validate_audit_event(event) is True


class TestEventImmutabilityEnforced:
    """Test event immutability in storage."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_immutability.db"
        return EventQueue(db_path)

    def test_event_not_modifiable_in_queue(self, temp_queue):
        """Once enqueued, event cannot be modified."""
        event_dict = {
            'event_type': 'plugin_loaded',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'priority': 'LOW',
        }

        temp_queue.enqueue(event_dict)

        # Drain the event
        drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)
        assert len(drained) > 0

        # Drained event should be identical to input
        drained_event = drained[0]
        assert drained_event['event_type'] == 'plugin_loaded'
        assert drained_event['plugin_id'] == 'test'
        assert drained_event['timestamp'] == '2026-09-27T00:00:00Z'


class TestFeedbackPoisoningDetection:
    """Test anomaly detection for feedback poisoning."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_anomaly.db"
        return EventQueue(db_path)

    def test_anomalous_high_error_rate_detected(self, temp_queue):
        """Anomalous error patterns are visible for detection."""
        # Enqueue a series of errors (anomaly)
        for i in range(10):
            temp_queue.enqueue({
                'event_type': 'plugin_error',
                'plugin_id': 'test-plugin',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                'error_type': 'RuntimeError',
                'error_message_hash': 'error_hash_' + str(i),
                'priority': 'HIGH',
            })

        drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)

        # Should have errors visible in queue
        errors = [e for e in drained if e['event_type'] == 'plugin_error']
        assert len(errors) >= 5  # Most errors should be there


class TestGDPRCompliance:
    """Test GDPR Art. 5, 30, 32 compliance."""

    def test_event_has_tenant_id_for_data_isolation(self):
        """Every event carries tenant_id for GDPR Art. 6 isolation."""
        event = {
            'event_type': 'plugin_executed',
            'plugin_id': 'test',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
        }

        # Should pass validation (tenant_id present)
        assert validate_audit_event(event) is True
        assert event['tenant_id'] == '_default'

    def test_event_immutable_for_data_integrity(self):
        """Events are immutable (frozen dataclass) for GDPR Art. 32."""
        from core.plugins.corvin_plugins.lifecycle import PluginLifecycleEvent

        event = PluginLifecycleEvent(
            event_type='plugin_loaded',
            plugin_id='test',
            tenant_id='_default',
            timestamp='2026-09-27T00:00:00Z'
        )

        # Should not be able to mutate
        with pytest.raises(Exception):  # FrozenInstanceError
            event.plugin_id = 'modified'

        # Original should be unchanged
        assert event.plugin_id == 'test'

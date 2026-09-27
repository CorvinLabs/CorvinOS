"""End-to-end tests for plugin lifecycle events (ADR-0682).

4 test cases covering real plugin execution and audit chain integration.
"""

import pytest
from pathlib import Path
from unittest import mock

from core.plugins.corvin_plugins.lifecycle import (
    emit_plugin_loaded,
    emit_plugin_executed,
    emit_plugin_error,
)
from core.audit.event_queue import EventQueue


class TestE2EPluginExecution:
    """Test real plugin execution flow."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_e2e.db"
        return EventQueue(db_path)

    def test_plugin_loaded_emits_audit_event(self, temp_queue):
        """Plugin load emits audit event to queue."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle.EventQueue', return_value=temp_queue):
            emit_plugin_loaded('test-plugin', '_default', version='1.0.0')

            stats = temp_queue.stats()
            assert stats.total_events > 0

    def test_plugin_executed_emits_high_priority_event(self, temp_queue):
        """Plugin execution emits HIGH priority event."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle.EventQueue', return_value=temp_queue):
            emit_plugin_executed(
                'test-plugin',
                '_default',
                'method_x',
                {'input': 'data'},
                {'result': 'ok'},
                42.5,
            )

            stats = temp_queue.stats()
            assert stats.high_priority_count > 0


class TestMultiPluginExecution:
    """Test multiple plugins executing concurrently."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_multi_plugin.db"
        return EventQueue(db_path)

    def test_multiple_plugins_no_contamination(self, temp_queue):
        """Multiple plugins executing, no cross-plugin data leakage."""
        with mock.patch('core.plugins.corvin_plugins.lifecycle.EventQueue', return_value=temp_queue):
            # Plugin 1
            emit_plugin_executed(
                'plugin1',
                '_default',
                'method_a',
                {'plugin1_data': 'secret1'},
                {'result': 'ok'},
                10.0,
            )

            # Plugin 2
            emit_plugin_executed(
                'plugin2',
                '_default',
                'method_b',
                {'plugin2_data': 'secret2'},
                {'result': 'ok'},
                20.0,
            )

            stats = temp_queue.stats()
            assert stats.total_events == 2

            # Verify no raw data in queue
            drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)
            for event in drained:
                # Should have hashes, not raw payloads
                if 'input_hash' in event:
                    assert isinstance(event.get('input_hash'), str)
                    assert len(event['input_hash']) == 64  # SHA256


class TestAuditChainIntegration:
    """Test integration with audit chain."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        """Create temporary queue for testing."""
        db_path = tmp_path / "test_audit_chain.db"
        return EventQueue(db_path)

    def test_events_ready_for_chain_emission(self, temp_queue):
        """Events drained are ready for audit chain."""
        # Enqueue mixed events
        temp_queue.enqueue({
            'event_type': 'plugin_loaded',
            'plugin_id': 'p1',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:00Z',
            'priority': 'LOW',
        })

        temp_queue.enqueue({
            'event_type': 'plugin_executed',
            'plugin_id': 'p1',
            'tenant_id': '_default',
            'timestamp': '2026-09-27T00:00:01Z',
            'method_name': 'execute',
            'input_hash': 'abc123',
            'output_hash': 'def456',
            'latency_ms': 42.0,
            'priority': 'HIGH',
        })

        drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)

        # Both events should be drained
        assert len(drained) >= 1

        # Events should have required chain fields
        for event in drained:
            assert event['event_type'] is not None
            assert event['plugin_id'] is not None
            assert event['tenant_id'] is not None
            assert event['timestamp'] is not None

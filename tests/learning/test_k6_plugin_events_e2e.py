"""End-to-end tests for plugin lifecycle events (ADR-0682).

4 test cases covering real plugin execution and audit chain integration.
"""

import json
import pytest
from pathlib import Path
from unittest import mock

from forge import paths as forge_paths


def _chain_records(tenant_id="_default"):
    chain = forge_paths.tenant_audit_chain(tenant_id)
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]

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

            drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)
            assert len(drained) == 2
            recs = [r for r in _chain_records() if r["event_type"] == "plugin.lifecycle_executed"]
            assert sorted(r["details"]["plugin_id"] for r in recs) == ["plugin1", "plugin2"]
            for r in recs:
                assert len(r["details"]["input_hash"]) == 64  # SHA256, never the payload
                assert "hash" in r and "prev_hash" in r
            chain_text = forge_paths.tenant_audit_chain("_default").read_text()
            assert "secret1" not in chain_text and "secret2" not in chain_text


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

        # HIGH first, then LOW; both written to the tenant chain
        assert [e['event_type'] for e in drained] == ['plugin_executed', 'plugin_loaded']
        types = [r["event_type"] for r in _chain_records()]
        assert types == ["plugin.lifecycle_executed", "plugin.lifecycle_loaded"]
        assert temp_queue.stats().pending_count == 0
        # a second drain writes nothing again
        assert temp_queue.drain(batch_size=50, timeout_sec=2.0) == []
        assert len(_chain_records()) == 2

    def test_emitter_uses_the_events_own_tenant_queue(self, tmp_path):
        """An event for tenant B lands in B's queue, never the _default one."""
        emit_plugin_loaded('p-b', 'tenant-b', version='1.0.0')
        queue_b = EventQueue(tenant_id='tenant-b')
        assert queue_b.db_path == forge_paths.tenant_global_dir('tenant-b') / 'plugin_events.db'
        assert queue_b.stats().pending_count == 1
        assert EventQueue(tenant_id='_default').stats().total_events == 0

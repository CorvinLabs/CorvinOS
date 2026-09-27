"""Concurrency tests for plugin lifecycle events (ADR-0682).

Multi-threaded enqueue/drain against the durable queue; a drained event is one
written to the tenant audit chain (tests/conftest.py isolates CORVIN_HOME).
"""

import json
from concurrent.futures import ThreadPoolExecutor

import pytest

from core.audit.event_queue import EventQueue, EventQueueTenantMismatch
from forge import paths as forge_paths
from forge import security_events


def _chain_records(tenant_id="_default"):
    chain = forge_paths.tenant_audit_chain(tenant_id)
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text().splitlines() if l.strip()]


class TestConcurrentEmission:
    """Test concurrent plugin event emission."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        return EventQueue(tmp_path / "test_concurrent.db")

    def test_concurrent_enqueue_5_threads(self, temp_queue):
        """5 threads × 20 distinct HIGH events = 100 stored, none lost."""
        def enqueue_batch(thread_id):
            for i in range(20):
                assert temp_queue.enqueue({
                    'event_type': 'plugin_executed',
                    'plugin_id': f'plugin-t{thread_id}',
                    'tenant_id': '_default',
                    'timestamp': f'2026-09-27T00:{thread_id:02d}:{i:02d}Z',
                    'priority': 'HIGH',
                }) is True

        with ThreadPoolExecutor(max_workers=5) as executor:
            for future in [executor.submit(enqueue_batch, i) for i in range(5)]:
                future.result()

        stats = temp_queue.stats()
        assert stats.total_events == 100
        assert stats.pending_count == 100

    def test_concurrent_drain_and_enqueue(self, temp_queue):
        """Concurrent enqueue + drain: every event reaches the chain exactly once."""
        def enqueue_worker():
            for i in range(10):
                temp_queue.enqueue({
                    'event_type': 'plugin_error',
                    'plugin_id': f'plugin-{i}',
                    'tenant_id': '_default',
                    'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                    'error_type': 'RuntimeError',
                    'priority': 'HIGH',
                })

        def drain_worker():
            for _ in range(3):
                temp_queue.drain(batch_size=10, timeout_sec=2.0)

        with ThreadPoolExecutor(max_workers=2) as executor:
            e1 = executor.submit(enqueue_worker)
            d1 = executor.submit(drain_worker)
            e1.result()
            d1.result()
        temp_queue.drain(batch_size=50, timeout_sec=2.0)

        assert temp_queue.stats().pending_count == 0
        recs = [r for r in _chain_records() if r["event_type"] == "plugin.lifecycle_error"]
        assert sorted(r["details"]["plugin_id"] for r in recs) == sorted(f"plugin-{i}" for i in range(10))
        assert security_events.verify_chain(forge_paths.tenant_audit_chain("_default"))[0]


class TestLoadDeduplication:
    """plugin_loaded rows within the 60 s window become one summary record."""

    @pytest.fixture
    def temp_queue(self, tmp_path):
        return EventQueue(tmp_path / "test_dedup.db")

    def test_load_deduplication_within_window(self, temp_queue):
        for i in range(5):
            temp_queue.enqueue({
                'event_type': 'plugin_loaded',
                'plugin_id': 'test-plugin',
                'tenant_id': '_default',
                'timestamp': f'2026-09-27T00:00:0{i}Z',
                'priority': 'LOW',
                'version': f'1.{i}',
            })
        # one more outside the window → a second record
        temp_queue.enqueue({
            'event_type': 'plugin_loaded', 'plugin_id': 'test-plugin', 'tenant_id': '_default',
            'timestamp': '2026-09-27T00:05:00Z', 'priority': 'LOW',
        })

        drained = temp_queue.drain(batch_size=50, timeout_sec=2.0)

        assert [e.get('load_count', 1) for e in drained] == [5, 1]
        assert drained[0]['event_type'] == 'plugin_loaded_summary'
        recs = [r for r in _chain_records() if r["event_type"] == "plugin.lifecycle_loaded"]
        assert [r["details"]["load_count"] for r in recs] == [5, 1]
        assert temp_queue.stats().pending_count == 0  # all six rows marked


class TestConcurrentTenantIsolation:
    """One queue per tenant; a queue refuses another tenant's event."""

    def test_multi_tenant_concurrent_enqueue(self):
        tenants = ['tenant1', 'tenant2', 'tenant3']

        def enqueue_for_tenant(tenant_id):
            queue = EventQueue(tenant_id=tenant_id)
            for i in range(10):
                queue.enqueue({
                    'event_type': 'plugin_executed',
                    'plugin_id': f'plugin-{i}',
                    'tenant_id': tenant_id,
                    'timestamp': f'2026-09-27T00:00:{i:02d}Z',
                    'priority': 'HIGH',
                })

        with ThreadPoolExecutor(max_workers=3) as executor:
            for future in [executor.submit(enqueue_for_tenant, t) for t in tenants]:
                future.result()

        for t in tenants:
            q = EventQueue(tenant_id=t)
            assert q.db_path == forge_paths.tenant_global_dir(t) / "plugin_events.db"
            rows = q.batch_read(batch_size=100)
            assert len(rows) == 10
            assert {r['tenant_id'] for r in rows} == {t}

    def test_queue_refuses_foreign_tenant_event(self):
        queue = EventQueue(tenant_id='tenant1')
        with pytest.raises(EventQueueTenantMismatch):
            queue.enqueue({
                'event_type': 'plugin_executed', 'plugin_id': 'p', 'tenant_id': 'tenant2',
                'timestamp': '2026-09-27T00:00:00Z', 'priority': 'HIGH',
            })
        assert queue.stats().total_events == 0

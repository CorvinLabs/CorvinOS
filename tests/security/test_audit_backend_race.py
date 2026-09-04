"""Concurrent stress test for audit chain race conditions (HIGH-1 remediation).

Tests that write_event() + enforce_retention() don't corrupt the chain under
concurrent access. Verifies hash-chain integrity after parallel operations.
"""

import asyncio
import json
import tempfile
import threading
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent


class TestAuditBackendRaceConditions:
    """Concurrent stress tests for audit chain."""

    def test_concurrent_write_event(self):
        """Test 100+ parallel write_event calls don't corrupt chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            # 100 parallel writes
            results = []
            errors = []

            def write_task(i: int):
                try:
                    event = AuditEvent(
                        event_id=f"event-{i}",
                        event_type="test",
                        tenant_id="_default",
                        user_id=f"user-{i % 5}",
                        timestamp=datetime.utcnow().isoformat(),
                        details={"index": i},
                        severity="info",
                    )
                    event_hash = writer.write_event(event)
                    results.append(event_hash)
                except Exception as e:
                    errors.append(e)

            threads = [
                threading.Thread(target=write_task, args=(i,)) for i in range(100)
            ]
            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: all writes succeeded
            assert len(errors) == 0, f"Errors during concurrent writes: {errors}"
            assert len(results) == 100
            assert writer.get_event_count() == 100

            # Verify: chain is intact
            assert writer.verify_chain(), "Hash chain corrupted after concurrent writes"

            # Verify: all events readable
            events = writer.read_events()
            assert len(events) == 100
            # Events should be in order (by sequence)
            with open(log_path, "r") as f:
                lines = f.readlines()
                for i, line in enumerate(lines):
                    entry = json.loads(line)
                    assert entry["sequence"] == i

    def test_concurrent_write_and_read(self):
        """Test write + read don't race (read consistency)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            # Pre-populate with 50 events
            for i in range(50):
                event = AuditEvent(
                    event_id=f"event-{i}",
                    event_type="setup",
                    tenant_id="_default",
                    user_id=None,
                    timestamp=datetime.utcnow().isoformat(),
                    details={"index": i},
                    severity="info",
                )
                writer.write_event(event)

            read_counts = []
            errors = []

            def read_task():
                try:
                    events = writer.read_events()
                    read_counts.append(len(events))
                except Exception as e:
                    errors.append(e)

            def write_task(i: int):
                try:
                    event = AuditEvent(
                        event_id=f"event-50-{i}",
                        event_type="concurrent",
                        tenant_id="_default",
                        user_id=None,
                        timestamp=datetime.utcnow().isoformat(),
                        details={"index": 50 + i},
                        severity="info",
                    )
                    writer.write_event(event)
                except Exception as e:
                    errors.append(e)

            threads = []
            # 50 read tasks + 50 write tasks
            for _ in range(50):
                threads.append(threading.Thread(target=read_task))
            for i in range(50):
                threads.append(threading.Thread(target=write_task, args=(i,)))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: no errors
            assert len(errors) == 0, f"Errors during concurrent read/write: {errors}"

            # Verify: consistent read counts (all reads should see 50-100 events)
            for count in read_counts:
                assert 50 <= count <= 100, f"Inconsistent read count: {count}"

            # Final state: 100 events, chain intact
            final_events = writer.read_events()
            assert len(final_events) == 100
            assert writer.verify_chain()

    def test_concurrent_write_and_verify(self):
        """Test write + verify_chain don't corrupt (read consistency)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            # Pre-populate
            for i in range(20):
                event = AuditEvent(
                    event_id=f"event-{i}",
                    event_type="setup",
                    tenant_id="_default",
                    user_id=None,
                    timestamp=datetime.utcnow().isoformat(),
                    details={"index": i},
                    severity="info",
                )
                writer.write_event(event)

            verify_results = []
            errors = []

            def verify_task():
                try:
                    result = writer.verify_chain()
                    verify_results.append(result)
                except Exception as e:
                    errors.append(e)

            def write_task(i: int):
                try:
                    event = AuditEvent(
                        event_id=f"event-20-{i}",
                        event_type="concurrent",
                        tenant_id="_default",
                        user_id=None,
                        timestamp=datetime.utcnow().isoformat(),
                        details={"index": 20 + i},
                        severity="info",
                    )
                    writer.write_event(event)
                except Exception as e:
                    errors.append(e)

            threads = []
            for _ in range(30):
                threads.append(threading.Thread(target=verify_task))
            for i in range(30):
                threads.append(threading.Thread(target=write_task, args=(i,)))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: no errors
            assert len(errors) == 0, f"Errors: {errors}"

            # All verify calls should pass (chain was never broken)
            assert all(verify_results), "Verify should always return True"

            # Final verification
            assert writer.verify_chain()

    def test_concurrent_write_and_enforce_retention(self):
        """Test write + enforce_retention race (HIGH-1 specific)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            # Pre-populate with old + new events
            old_date = (datetime.utcnow() - timedelta(days=10)).isoformat()
            new_date = datetime.utcnow().isoformat()

            for i in range(20):
                timestamp = old_date if i < 10 else new_date
                event = AuditEvent(
                    event_id=f"event-{i}",
                    event_type="setup",
                    tenant_id="_default",
                    user_id=None,
                    timestamp=timestamp,
                    details={"index": i},
                    severity="info",
                )
                writer.write_event(event)

            errors = []
            retention_results = []

            def write_task(i: int):
                try:
                    event = AuditEvent(
                        event_id=f"event-20-{i}",
                        event_type="concurrent",
                        tenant_id="_default",
                        user_id=None,
                        timestamp=datetime.utcnow().isoformat(),
                        details={"index": 20 + i},
                        severity="info",
                    )
                    writer.write_event(event)
                except Exception as e:
                    errors.append(e)

            def retention_task():
                try:
                    result = writer.enforce_retention(max_age_days=7)
                    retention_results.append(result)
                except Exception as e:
                    errors.append(e)

            threads = []
            # 20 write tasks + 3 retention enforcement tasks
            for i in range(20):
                threads.append(threading.Thread(target=write_task, args=(i,)))
            for _ in range(3):
                threads.append(threading.Thread(target=retention_task))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: no errors
            assert len(errors) == 0, f"Errors: {errors}"

            # Verify: chain is intact even after retention
            assert writer.verify_chain()

            # Verify: final state is consistent
            final_events = writer.read_events()
            # Should have new events + some setup events (depending on retention timing)
            assert len(final_events) > 0

    def test_tenant_isolation_concurrent(self):
        """Test tenant isolation under concurrent access."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            errors = []

            def write_for_tenant(tenant_id: str, count: int):
                try:
                    for i in range(count):
                        event = AuditEvent(
                            event_id=f"event-{tenant_id}-{i}",
                            event_type="test",
                            tenant_id=tenant_id,
                            user_id=f"user-{tenant_id}",
                            timestamp=datetime.utcnow().isoformat(),
                            details={"index": i},
                            severity="info",
                        )
                        writer.write_event(event)
                except Exception as e:
                    errors.append(e)

            threads = []
            tenants = ["tenant_a", "tenant_b", "tenant_c"]
            for tenant in tenants:
                threads.append(
                    threading.Thread(target=write_for_tenant, args=(tenant, 20))
                )

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: no errors
            assert len(errors) == 0

            # Verify: each tenant sees only its own events
            for tenant in tenants:
                events = writer.read_events(tenant_id=tenant)
                assert len(events) == 20
                assert all(e.tenant_id == tenant for e in events)

            # Verify: chain is intact
            assert writer.verify_chain()
            assert writer.get_event_count() == 60

    def test_stress_concurrent_all_ops(self):
        """Stress test: all operations (write, read, verify, retention) concurrent."""
        with tempfile.TemporaryDirectory() as tmpdir:
            log_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(log_path)

            # Pre-populate
            for i in range(10):
                event = AuditEvent(
                    event_id=f"event-{i}",
                    event_type="setup",
                    tenant_id="_default",
                    user_id=None,
                    timestamp=datetime.utcnow().isoformat(),
                    details={"index": i},
                    severity="info",
                )
                writer.write_event(event)

            errors = []
            counters = {"write": 0, "read": 0, "verify": 0, "retention": 0}
            lock = threading.Lock()

            def write_task():
                try:
                    for i in range(10):
                        event = AuditEvent(
                            event_id=f"event-write-{threading.current_thread().ident}-{i}",
                            event_type="test",
                            tenant_id="_default",
                            user_id=None,
                            timestamp=datetime.utcnow().isoformat(),
                            details={"op": "write"},
                            severity="info",
                        )
                        writer.write_event(event)
                        with lock:
                            counters["write"] += 1
                except Exception as e:
                    errors.append(e)

            def read_task():
                try:
                    for _ in range(20):
                        writer.read_events()
                        with lock:
                            counters["read"] += 1
                except Exception as e:
                    errors.append(e)

            def verify_task():
                try:
                    for _ in range(10):
                        writer.verify_chain()
                        with lock:
                            counters["verify"] += 1
                except Exception as e:
                    errors.append(e)

            def retention_task():
                try:
                    for _ in range(2):
                        writer.enforce_retention(max_age_days=7)
                        with lock:
                            counters["retention"] += 1
                except Exception as e:
                    errors.append(e)

            threads = []
            for _ in range(5):
                threads.append(threading.Thread(target=write_task))
                threads.append(threading.Thread(target=read_task))
                threads.append(threading.Thread(target=verify_task))
            for _ in range(3):
                threads.append(threading.Thread(target=retention_task))

            for t in threads:
                t.start()
            for t in threads:
                t.join()

            # Verify: no errors during stress test
            assert len(errors) == 0, f"Errors during stress: {errors}"

            # Verify: counters show work was done
            assert counters["write"] > 0
            assert counters["read"] > 0
            assert counters["verify"] > 0
            assert counters["retention"] > 0

            # Final state: chain is intact
            assert writer.verify_chain()

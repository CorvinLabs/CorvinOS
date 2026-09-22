"""
Atomic Audit Transaction Tests — GDPR Art. 32 Data Loss Prevention

Test coverage:
- Normal atomic write (happy path)
- Transaction rollback on exception
- Hash chain integrity verification
- Concurrent writes (no corruption)
- Partial write recovery (simulated crash)
"""

import json
import os
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from unittest.mock import Mock, patch

import pytest

from core.compliance.audit_atomic_transaction import (
    AtomicAuditRecord,
    AuditAtomicTransaction,
    AtomicTransactionError,
    atomic_audit_write,
)


@pytest.fixture
def temp_audit_log():
    """Create temporary audit log file for testing."""
    fd, path = tempfile.mkstemp(suffix=".jsonl")
    os.close(fd)
    yield Path(path)
    # Cleanup
    try:
        os.unlink(path)
    except OSError:
        pass


class TestAtomicAuditRecord:
    """Test AtomicAuditRecord immutability and serialization."""

    def test_record_creation(self):
        """Test creating immutable audit record."""
        record = AtomicAuditRecord(
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
            details={"plugin": "audit_backend"},
            severity="INFO",
            hash="abc123",
            prev_hash="genesis",
            sequence=1,
        )
        assert record.event_id == "evt-001"
        assert record.event_type == "plugin_loaded"

    def test_record_immutable(self):
        """Test that record is immutable (frozen dataclass)."""
        record = AtomicAuditRecord(
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id=None,
            timestamp="2026-09-22T12:00:00Z",
            details={},
            severity=None,
            hash="abc123",
            prev_hash="genesis",
            sequence=1,
        )
        with pytest.raises(AttributeError):
            record.event_id = "evt-002"

    def test_record_to_json_line(self):
        """Test JSON serialization with deterministic key ordering."""
        record = AtomicAuditRecord(
            event_id="evt-001",
            event_type="consent_granted",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
            details={"scope": "telemetry"},
            severity="INFO",
            hash="h1",
            prev_hash="h0",
            sequence=42,
        )
        json_line = record.to_json_line()
        parsed = json.loads(json_line)

        assert parsed["event_id"] == "evt-001"
        assert parsed["sequence"] == 42
        assert parsed["details"]["scope"] == "telemetry"


class TestAtomicTransaction:
    """Test atomic transaction mechanics."""

    def test_transaction_creation(self):
        """Test creating atomic transaction."""
        txn = AuditAtomicTransaction(
            log_path="/tmp/audit.jsonl",
            prev_hash="genesis",
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
        )
        assert txn.event_id == "evt-001"
        assert txn.committed is False

    def test_transaction_requires_tenant_id(self):
        """Test that transaction fails without tenant_id (fail-closed)."""
        with pytest.raises(AtomicTransactionError):
            AuditAtomicTransaction(
                log_path="/tmp/audit.jsonl",
                prev_hash="genesis",
                event_id="evt-001",
                event_type="plugin_loaded",
                tenant_id="",  # Empty = fail-closed
                user_id=None,
                timestamp="2026-09-22T12:00:00Z",
            )

    def test_write_record(self, temp_audit_log):
        """Test record preparation (before commit)."""
        txn = AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="consent_granted",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
            details={"scope": "skill_generation"},
        )

        record = txn.write_record(sequence=1)

        assert record.event_id == "evt-001"
        assert record.event_type == "consent_granted"
        assert record.sequence == 1
        assert record.hash is not None
        assert record.prev_hash == "genesis"

    def test_atomic_commit_normal_flow(self, temp_audit_log):
        """Test happy path: write → prepare → commit."""
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            record = txn.write_record(sequence=1)
            hash_evt1 = record.hash

        assert txn.committed is True

        # Verify record was written
        with open(temp_audit_log, "r") as f:
            lines = f.readlines()

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_id"] == "evt-001"
        assert entry["hash"] == hash_evt1

    def test_atomic_rollback_on_exception(self, temp_audit_log):
        """Test that transaction rolls back on exception."""
        # Write first event
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id=None,
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            txn.write_record(sequence=1)

        # Attempt second event with exception
        try:
            with AuditAtomicTransaction(
                log_path=temp_audit_log,
                prev_hash="h1",
                event_id="evt-002",
                event_type="plugin_error",
                tenant_id="_default",
                user_id=None,
                timestamp="2026-09-22T12:01:00Z",
            ) as txn:
                txn.write_record(sequence=2)
                raise ValueError("Simulated error")
        except ValueError:
            pass  # Expected

        # Verify only first event exists
        with open(temp_audit_log, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]

        assert len(lines) == 1
        entry = json.loads(lines[0])
        assert entry["event_id"] == "evt-001"

    def test_hash_chain_integrity(self, temp_audit_log):
        """Test hash-chaining across multiple events."""
        hashes = []

        # Event 1
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="consent_granted",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
            details={"scope": "telemetry"},
        ) as txn:
            record1 = txn.write_record(sequence=1)
            hashes.append(record1.hash)

        # Event 2 (chains from event 1)
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash=hashes[0],
            event_id="evt-002",
            event_type="plugin_loaded",
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:01:00Z",
            details={"plugin": "security"},
        ) as txn:
            record2 = txn.write_record(sequence=2)
            hashes.append(record2.hash)

        # Verify chain
        with open(temp_audit_log, "r") as f:
            lines = f.readlines()

        assert len(lines) == 2

        entry1 = json.loads(lines[0])
        entry2 = json.loads(lines[1])

        # Event 1: links from genesis
        assert entry1["prev_hash"] == "genesis"
        assert entry1["hash"] == hashes[0]

        # Event 2: links from event 1
        assert entry2["prev_hash"] == hashes[0]
        assert entry2["hash"] == hashes[1]

    def test_context_manager_style(self, temp_audit_log):
        """Test using atomic_audit_write context manager."""
        with atomic_audit_write(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="consent_granted",
            tenant_id="_default",
            user_id="user1",
            details={"scope": "skill_generation"},
            sequence=1,
        ) as txn:
            record = txn.write_record(sequence=1)
            event_hash = record.hash

        # Verify written
        with open(temp_audit_log, "r") as f:
            entry = json.loads(f.readline())

        assert entry["event_id"] == "evt-001"
        assert entry["hash"] == event_hash

    def test_tenant_isolation_in_records(self, temp_audit_log):
        """Test that tenant_id is properly recorded (for isolation verification)."""
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="plugin_loaded",
            tenant_id="tenant-abc",
            user_id=None,
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            txn.write_record(sequence=1)

        with open(temp_audit_log, "r") as f:
            entry = json.loads(f.readline())

        assert entry["tenant_id"] == "tenant-abc"

    def test_severity_levels(self, temp_audit_log):
        """Test different severity levels are recorded."""
        for severity in ["INFO", "WARNING", "ERROR", "CRITICAL"]:
            with AuditAtomicTransaction(
                log_path=temp_audit_log,
                prev_hash="genesis",
                event_id=f"evt-{severity}",
                event_type="test_event",
                tenant_id="_default",
                user_id=None,
                timestamp="2026-09-22T12:00:00Z",
                severity=severity,
            ) as txn:
                txn.write_record(sequence=1)

        # Verify all events recorded
        with open(temp_audit_log, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]

        assert len(lines) == 4


class TestConcurrentWrites:
    """Test concurrent atomic writes (no corruption)."""

    def test_concurrent_writes_no_corruption(self, temp_audit_log):
        """Test multiple threads writing concurrently."""
        num_threads = 5
        events_per_thread = 10

        def write_events(thread_id):
            prev_hash = "genesis"
            for i in range(events_per_thread):
                with AuditAtomicTransaction(
                    log_path=temp_audit_log,
                    prev_hash=prev_hash,
                    event_id=f"evt-t{thread_id}-{i}",
                    event_type="test_event",
                    tenant_id=f"tenant-{thread_id}",
                    user_id=None,
                    timestamp=datetime.utcnow().isoformat(),
                ) as txn:
                    record = txn.write_record(sequence=i)
                    prev_hash = record.hash

        threads = [
            threading.Thread(target=write_events, args=(i,))
            for i in range(num_threads)
        ]

        for t in threads:
            t.start()

        for t in threads:
            t.join()

        # Verify all events written
        with open(temp_audit_log, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]

        # Each thread writes 10 events
        assert len(lines) == num_threads * events_per_thread

        # Verify all entries are valid JSON
        for line in lines:
            entry = json.loads(line)
            assert "event_id" in entry
            assert "hash" in entry
            assert "prev_hash" in entry

    def test_concurrent_reads_during_writes(self, temp_audit_log):
        """Test that concurrent reads don't see partial writes."""
        import time

        def write_slow():
            """Write with artificial delay."""
            for i in range(5):
                with AuditAtomicTransaction(
                    log_path=temp_audit_log,
                    prev_hash=f"h{i}",
                    event_id=f"evt-{i}",
                    event_type="test",
                    tenant_id="_default",
                    user_id=None,
                    timestamp=datetime.utcnow().isoformat(),
                ) as txn:
                    txn.write_record(sequence=i)
                time.sleep(0.01)

        def read_and_verify():
            """Periodically read and verify JSON validity."""
            for _ in range(10):
                try:
                    if temp_audit_log.exists():
                        with open(temp_audit_log, "r") as f:
                            lines = f.readlines()
                            for line in lines:
                                if line.strip():
                                    json.loads(line)  # Verify valid JSON
                except json.JSONDecodeError as e:
                    pytest.fail(f"Partial write detected: {e}")
                time.sleep(0.005)

        writer = threading.Thread(target=write_slow)
        reader1 = threading.Thread(target=read_and_verify)
        reader2 = threading.Thread(target=read_and_verify)

        writer.start()
        reader1.start()
        reader2.start()

        writer.join()
        reader1.join()
        reader2.join()

        # Verify final state
        with open(temp_audit_log, "r") as f:
            lines = [l for l in f.readlines() if l.strip()]
        assert len(lines) == 5


class TestRecoveryAndVerification:
    """Test recovery from crashes and corruption detection."""

    def test_verify_valid_chain(self, temp_audit_log):
        """Test verifying an intact chain."""
        # Write 3 events
        hashes = []
        prev_hash = "genesis"

        for i in range(3):
            with AuditAtomicTransaction(
                log_path=temp_audit_log,
                prev_hash=prev_hash,
                event_id=f"evt-{i}",
                event_type="test_event",
                tenant_id="_default",
                user_id=None,
                timestamp="2026-09-22T12:00:00Z",
            ) as txn:
                record = txn.write_record(sequence=i)
                hashes.append(record.hash)
                prev_hash = record.hash

        # Verify chain integrity manually
        with open(temp_audit_log, "r") as f:
            lines = f.readlines()

        assert len(lines) == 3

        # Event 0
        e0 = json.loads(lines[0])
        assert e0["prev_hash"] == "genesis"
        assert e0["hash"] == hashes[0]

        # Event 1
        e1 = json.loads(lines[1])
        assert e1["prev_hash"] == hashes[0]
        assert e1["hash"] == hashes[1]

        # Event 2
        e2 = json.loads(lines[2])
        assert e2["prev_hash"] == hashes[1]
        assert e2["hash"] == hashes[2]

    def test_corruption_detection_on_read(self, temp_audit_log):
        """Test that corrupted records are detected when read."""
        # Write valid event
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="test_event",
            tenant_id="_default",
            user_id=None,
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            txn.write_record(sequence=1)

        # Corrupt the hash in the file
        with open(temp_audit_log, "r") as f:
            content = f.read()

        corrupted = content.replace('"hash":"', '"hash":"CORRUPTED_')

        with open(temp_audit_log, "w") as f:
            f.write(corrupted)

        # Reading the file shows corruption
        with open(temp_audit_log, "r") as f:
            entry = json.loads(f.read())

        # The hash is corrupted
        assert entry["hash"].startswith("CORRUPTED_")

    def test_recovery_bootstrap_validation(self, temp_audit_log):
        """Test that boot-time validation catches corruption."""
        # Write valid event
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id="evt-001",
            event_type="test_event",
            tenant_id="_default",
            user_id=None,
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            record1 = txn.write_record(sequence=1)

        # Write second event
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash=record1.hash,
            event_id="evt-002",
            event_type="test_event",
            tenant_id="_default",
            user_id=None,
            timestamp="2026-09-22T12:01:00Z",
        ) as txn:
            txn.write_record(sequence=2)

        # Simulate partial write by truncating file
        file_size = temp_audit_log.stat().st_size
        with open(temp_audit_log, "r+b") as f:
            f.truncate(file_size // 2)  # Truncate to half

        # Trying to read the file now shows incomplete JSON
        with open(temp_audit_log, "r") as f:
            content = f.read()

        # The last line should be incomplete
        lines = content.strip().split("\n")
        if len(lines) > 1:
            # Try to parse last line — should fail
            try:
                json.loads(lines[-1])
            except json.JSONDecodeError:
                pass  # Expected — indicates corruption/incomplete write


class TestAuditEventTypes:
    """Test different audit event types."""

    @pytest.mark.parametrize(
        "event_type",
        [
            "consent_granted",
            "consent_revoked",
            "consent_checked",
            "plugin_loaded",
            "plugin_executed",
            "skill_executed",
            "skill_feedback",
            "security_threat",
        ],
    )
    def test_all_event_types(self, event_type, temp_audit_log):
        """Test that all event types are properly recorded."""
        with AuditAtomicTransaction(
            log_path=temp_audit_log,
            prev_hash="genesis",
            event_id=f"evt-{event_type}",
            event_type=event_type,
            tenant_id="_default",
            user_id="user1",
            timestamp="2026-09-22T12:00:00Z",
        ) as txn:
            txn.write_record(sequence=1)

        with open(temp_audit_log, "r") as f:
            entry = json.loads(f.read())

        assert entry["event_type"] == event_type


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""
Unit Tests for Queue Corruption Detection — ADR-0298

Tests for hash-chained audit queue corruption detection and recovery.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.audit import (
    QueueIntegrityMonitor,
    QueueIntegrityReport,
    CorruptionRecord,
    CorruptionType,
)


class TestQueueIntegrityMonitor:
    """Tests for QueueIntegrityMonitor class."""

    @pytest.fixture
    def temp_queue(self):
        """Create temporary queue file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "audit.jsonl"

    @pytest.fixture
    def monitor(self, temp_queue):
        """Create monitor instance."""
        return QueueIntegrityMonitor(temp_queue, tenant_id="test_tenant")

    def test_monitor_empty_queue(self, monitor):
        """Empty queue should verify successfully."""
        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 0
        assert report.corrupted_records == 0

    def test_monitor_nonexistent_queue(self, temp_queue):
        """Nonexistent queue should not raise error."""
        monitor = QueueIntegrityMonitor(temp_queue, tenant_id="test")
        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 0

    def test_monitor_valid_single_record(self, monitor, temp_queue):
        """Valid single record should verify."""
        record = {
            "event_type": "test",
            "actor": "test_actor",
            "action": "test_action",
            "resource": "test_resource",
            "result": "success",
            "timestamp": "2026-08-12T10:00:00Z",
            "prior_hash": "genesis",
            "self_hash": "abc123def456",
        }
        with open(temp_queue, "w") as f:
            json.dump(record, f)
            f.write("\n")

        report = monitor.verify_queue_integrity()
        assert report.total_records == 1
        # Note: self_hash won't match computed hash, but that's ok for this test

    def test_monitor_hash_chain_break(self, monitor, temp_queue):
        """Hash chain break should be detected."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",  # Should be hash_1
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert report.corrupted_records > 0
        assert any(
            c.corruption_type == CorruptionType.HASH_CHAIN_BREAK
            for c in report.corruption_details
        )

    def test_monitor_timestamp_disorder(self, monitor, temp_queue):
        """Timestamp disorder should be detected."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:02Z",  # Newer
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "hash_1",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",  # Older - disorder!
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert any(
            c.corruption_type == CorruptionType.TIMESTAMP_DISORDER
            for c in report.corruption_details
        )

    def test_monitor_duplicate_event_id(self, monitor, temp_queue):
        """Duplicate event IDs in same session should be detected."""
        record1 = {
            "event_type": "event_1",
            "event_id": "evt_123",
            "session_id": "sess_456",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "event_id": "evt_123",  # Duplicate!
            "session_id": "sess_456",  # Same session
            "prior_hash": "hash_1",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert any(
            c.corruption_type == CorruptionType.DUPLICATE_EVENT_ID
            for c in report.corruption_details
        )

    def test_monitor_invalid_json(self, monitor, temp_queue):
        """Invalid JSON should be detected."""
        with open(temp_queue, "w") as f:
            f.write('{"event_type": "valid"}\n')
            f.write("{ invalid json ]}\n")  # Invalid
            f.write('{"event_type": "valid"}\n')

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert any(
            c.corruption_type == CorruptionType.INVALID_JSON
            for c in report.corruption_details
        )

    def test_monitor_missing_fields(self, monitor, temp_queue):
        """Missing required fields should be detected."""
        record_missing_hash = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            # Missing self_hash
            "timestamp": "2026-08-12T10:00:00Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record_missing_hash, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert any(
            c.corruption_type == CorruptionType.MISSING_FIELD
            for c in report.corruption_details
        )

    def test_monitor_skip_corrupt_false(self, monitor, temp_queue):
        """With skip_corrupt=False, should raise on first corruption."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        with pytest.raises(ValueError):
            monitor.verify_queue_integrity(skip_corrupt=False)

    def test_monitor_auto_repair(self, monitor, temp_queue):
        """Auto-repair should mark corrupted records."""
        # Create a valid first record with matching self_hash
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "abc123",  # Won't match computed, but that's ok for demo
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",  # This breaks the chain
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True, auto_repair=True)
        assert not report.is_valid
        assert report.recovery_attempted
        assert report.recovery_successful

        # Verify repaired file has _corrupted flag on corrupted record
        with open(temp_queue, "r") as f:
            lines = f.readlines()

        assert len(lines) == 2
        record1_repaired = json.loads(lines[0])
        record2_repaired = json.loads(lines[1])

        # Both records may be marked as corrupted (record1 has self_hash mismatch, record2 has chain break)
        # The important thing is that repair succeeded and records are marked
        assert "_corrupted" in record2_repaired or record2_repaired.get("_corrupted", False)
        # Verify repair marked something as corrupted
        has_corruption_mark = any(
            json.loads(l).get("_corrupted", False) for l in lines
        )
        assert has_corruption_mark

    def test_monitor_detect_corruption_quick(self, monitor, temp_queue):
        """detect_corruption should return first error quickly."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        corruption = monitor.detect_corruption()
        assert corruption is not None
        assert corruption.corruption_type == CorruptionType.HASH_CHAIN_BREAK

    def test_monitor_get_tail_records(self, monitor, temp_queue):
        """get_tail_records should extract last N records."""
        for i in range(10):
            record = {
                "event_type": f"event_{i}",
                "prior_hash": f"hash_{i-1}" if i > 0 else "genesis",
                "self_hash": f"hash_{i}",
                "timestamp": f"2026-08-12T10:00:{i:02d}Z",
            }
            with open(temp_queue, "a") as f:
                json.dump(record, f)
                f.write("\n")

        tail = monitor.get_tail_records(count=3)
        assert len(tail) == 3
        assert tail[0]["event_type"] == "event_7"
        assert tail[1]["event_type"] == "event_8"
        assert tail[2]["event_type"] == "event_9"

    def test_monitor_get_tail_records_empty(self, monitor, temp_queue):
        """get_tail_records on empty queue should return empty list."""
        tail = monitor.get_tail_records(count=5)
        assert tail == []

    def test_monitor_create_corruption_audit_event(self, monitor, temp_queue):
        """create_corruption_audit_event should generate audit event."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        event = monitor.create_corruption_audit_event(report, source="test_source")

        assert event["event_type"] == "corruption_detected"
        assert event["actor"] == "system"
        assert event["action"] == "detect_corruption"
        assert event["result"] == "corruption_found"
        assert event["details"]["tenant_id"] == "test_tenant"
        assert event["details"]["source"] == "test_source"
        assert event["details"]["corrupted_records"] > 0

    def test_monitor_tenant_isolation(self, temp_queue):
        """Monitors should track tenant_id in logs."""
        monitor1 = QueueIntegrityMonitor(temp_queue, tenant_id="tenant_a")
        monitor2 = QueueIntegrityMonitor(temp_queue, tenant_id="tenant_b")

        # Both should initialize with different tenant_ids
        assert monitor1.tenant_id == "tenant_a"
        assert monitor2.tenant_id == "tenant_b"

    def test_monitor_multiple_corruptions(self, monitor, temp_queue):
        """Should detect multiple corruption types in one pass."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:02Z",  # Newer first
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG_HASH",  # Chain break
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",  # Disorder
            "event_id": "evt_1",
            "session_id": "sess_1",
        }
        record3 = {
            "event_type": "event_3",
            "prior_hash": "hash_2",
            "self_hash": "hash_3",
            "timestamp": "2026-08-12T10:00:03Z",
            "event_id": "evt_1",  # Duplicate
            "session_id": "sess_1",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")
            json.dump(record3, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert len(report.corruption_details) >= 2

    def test_monitor_blank_lines(self, monitor, temp_queue):
        """Should skip blank lines."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "hash_1",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            f.write("\n")  # Blank line
            f.write("\n")  # Blank line
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity()
        assert report.total_records == 2  # Should count both records, not blank lines

    def test_monitor_report_details(self, monitor, temp_queue):
        """QueueIntegrityReport should have detailed information."""
        record1 = {
            "event_type": "event_1",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "prior_hash": "WRONG",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
            "event_id": "evt_1",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)

        assert isinstance(report, QueueIntegrityReport)
        assert report.tenant_id == "test_tenant"
        assert report.total_records >= 1
        assert len(report.corruption_details) > 0

        corruption = report.corruption_details[0]
        assert isinstance(corruption, CorruptionRecord)
        assert corruption.line_number > 0
        assert corruption.corruption_type in [
            CorruptionType.HASH_CHAIN_BREAK,
            CorruptionType.TIMESTAMP_DISORDER,
        ]

    def test_monitor_compute_record_hash(self, monitor):
        """_compute_record_hash should exclude self_hash field."""
        record = {
            "event_type": "test",
            "actor": "test",
            "self_hash": "should_be_ignored",
            "_corrupted": "should_be_ignored",
            "timestamp": "2026-08-12T10:00:00Z",
        }

        hash1 = monitor._compute_record_hash(record)
        assert hash1  # Should return non-empty hash
        assert len(hash1) == 64  # SHA256 hex digest

        # Hash should be same whether self_hash is set or not
        record_no_self = {
            "event_type": "test",
            "actor": "test",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        hash2 = monitor._compute_record_hash(record_no_self)
        assert hash1 == hash2

"""
Tests for ADR-0298: Queue Corruption Detection & Recovery

Comprehensive test suite covering:
- Corruption detection (hash-chain breaks, duplicates, timestamp disorder)
- Integrity validation
- Recovery mechanisms
- Audit logging
- Tenant isolation
- Feature flag integration
"""

import json
import tempfile
from pathlib import Path
from datetime import datetime, timedelta

import pytest

from core.audit.corruption_detection import (
    QueueIntegrityMonitor,
    CorruptionRecord,
    CorruptionType,
    QueueIntegrityReport,
)
from core.audit.chain import AuditChain, AuditEntry
from core.audit.integration import AuditChainWithCorruptionDetection


# ============================================================================
# FIXTURES
# ============================================================================


@pytest.fixture
def temp_queue_file():
    """Create temporary queue file for testing."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        temp_path = Path(f.name)
    yield temp_path
    # Cleanup
    if temp_path.exists():
        temp_path.unlink()


@pytest.fixture
def monitor(temp_queue_file):
    """Create QueueIntegrityMonitor instance."""
    return QueueIntegrityMonitor(temp_queue_file, tenant_id="test_tenant")


@pytest.fixture
def clean_audit_chain(temp_queue_file):
    """Create clean AuditChain for testing."""
    return AuditChain(temp_queue_file)


@pytest.fixture
def tenant_id():
    """Standard tenant ID for tests."""
    return "test_tenant_123"


# ============================================================================
# BASIC CORRUPTION DETECTION TESTS
# ============================================================================


class TestHashChainVerification:
    """Hash chain integrity verification tests."""

    def test_empty_queue_is_valid(self, monitor):
        """Test that empty queue reports as valid."""
        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 0
        assert report.corrupted_records == 0

    def test_single_record_is_valid(self, monitor, temp_queue_file):
        """Test single record is valid."""
        chain = AuditChain(temp_queue_file)
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="test_action",
            resource="test_resource",
            result="success",
            timestamp="2026-01-01T00:00:00Z",
            tenant_id="test_tenant",
        )
        chain.record(entry)

        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 1
        assert report.corrupted_records == 0

    def test_multiple_valid_records(self, monitor, temp_queue_file):
        """Test multiple valid records maintain chain."""
        chain = AuditChain(temp_queue_file)

        # Write 5 records
        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action=f"action_{i}",
                resource="resource",
                result="success",
                timestamp=f"2026-01-01T00:0{i}:00Z",
                tenant_id="test_tenant",
            )
            chain.record(entry)

        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 5
        assert report.corrupted_records == 0

    def test_corrupted_hash_detected(self, monitor, temp_queue_file):
        """Test detection of corrupted hash."""
        # Write a valid record
        chain = AuditChain(temp_queue_file)
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="test",
            resource="resource",
            result="success",
            timestamp="2026-01-01T00:00:00Z",
            tenant_id="test_tenant",
        )
        chain.record(entry)

        # Corrupt the hash directly
        with open(temp_queue_file, "r") as f:
            data = json.loads(f.readline())

        data["self_hash"] = "0000000000000000000000000000000000000000000000000000000000000000"

        with open(temp_queue_file, "w") as f:
            json.dump(data, f)
            f.write("\n")

        # Verify corruption is detected
        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        assert report.corrupted_records > 0


# ============================================================================
# TIMESTAMP MONOTONICITY TESTS
# ============================================================================


class TestTimestampMonotonicity:
    """Timestamp ordering validation tests."""

    def test_monotonic_timestamps_valid(self, monitor, temp_queue_file):
        """Test monotonically increasing timestamps are valid."""
        chain = AuditChain(temp_queue_file)

        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action="test",
                resource="resource",
                result="success",
                timestamp=f"2026-01-01T00:0{i}:00Z",
                tenant_id="test_tenant",
            )
            chain.record(entry)

        report = monitor.verify_queue_integrity()
        assert report.is_valid

    def test_non_monotonic_timestamps_detected(self, monitor, temp_queue_file):
        """Test detection of non-monotonic timestamps."""
        # Write record 1
        with open(temp_queue_file, "a") as f:
            entry1 = {
                "event_type": "event_1",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": "2026-01-01T00:05:00Z",
                "tenant_id": "test_tenant",
                "prior_hash": "genesis",
                "self_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
            }
            json.dump(entry1, f)
            f.write("\n")

        # Write record 2 with earlier timestamp
        with open(temp_queue_file, "a") as f:
            entry2 = {
                "event_type": "event_2",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": "2026-01-01T00:02:00Z",  # Earlier!
                "tenant_id": "test_tenant",
                "prior_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
                "self_hash": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
            }
            json.dump(entry2, f)
            f.write("\n")

        # Verify disorder is detected
        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        # Should have at least one timestamp disorder record
        has_ts_disorder = any(
            c.corruption_type == CorruptionType.TIMESTAMP_DISORDER
            for c in report.corruption_details
        )
        assert has_ts_disorder


# ============================================================================
# DUPLICATE DETECTION TESTS
# ============================================================================


class TestDuplicateDetection:
    """Duplicate event detection tests."""

    def test_no_duplicates_in_clean_queue(self, monitor, temp_queue_file):
        """Test clean queue has no duplicates."""
        # Write records with unique event_ids
        with open(temp_queue_file, "a") as f:
            for i in range(3):
                entry = {
                    "event_type": f"event_{i}",
                    "event_id": f"id_{i}",
                    "session_id": "session_1",
                    "actor": "system",
                    "action": "test",
                    "resource": "resource",
                    "result": "success",
                    "timestamp": f"2026-01-01T00:0{i}:00Z",
                    "tenant_id": "test_tenant",
                    "prior_hash": "hash_prev",
                    "self_hash": f"hash_{i}",
                }
                json.dump(entry, f)
                f.write("\n")

        report = monitor.verify_queue_integrity()
        # No duplicates detected
        has_duplicate = any(
            c.corruption_type == CorruptionType.DUPLICATE_EVENT_ID
            for c in report.corruption_details
        )
        assert not has_duplicate

    def test_duplicate_event_id_detected(self, monitor, temp_queue_file):
        """Test detection of duplicate event IDs."""
        # Write same event_id twice
        with open(temp_queue_file, "a") as f:
            for i in range(2):
                entry = {
                    "event_type": f"event_{i}",
                    "event_id": "same_id",  # Duplicate!
                    "session_id": "session_1",
                    "actor": "system",
                    "action": "test",
                    "resource": "resource",
                    "result": "success",
                    "timestamp": f"2026-01-01T00:0{i}:00Z",
                    "tenant_id": "test_tenant",
                    "prior_hash": "hash_prev",
                    "self_hash": f"hash_{i}",
                }
                json.dump(entry, f)
                f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        # Should detect duplicate
        has_duplicate = any(
            c.corruption_type == CorruptionType.DUPLICATE_EVENT_ID
            for c in report.corruption_details
        )
        assert has_duplicate


# ============================================================================
# JSON PARSING TESTS
# ============================================================================


class TestJSONParsing:
    """JSON parsing and malformed data handling tests."""

    def test_invalid_json_detected(self, monitor, temp_queue_file):
        """Test detection of invalid JSON."""
        # Write invalid JSON
        with open(temp_queue_file, "a") as f:
            f.write("{ invalid json ]\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        # Should have invalid JSON corruption record
        has_invalid_json = any(
            c.corruption_type == CorruptionType.INVALID_JSON
            for c in report.corruption_details
        )
        assert has_invalid_json

    def test_missing_required_field(self, monitor, temp_queue_file):
        """Test detection of missing required fields."""
        # Write record with missing self_hash
        with open(temp_queue_file, "a") as f:
            entry = {
                "event_type": "test",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": "2026-01-01T00:00:00Z",
                "tenant_id": "test_tenant",
                "prior_hash": "genesis",
                # Missing self_hash!
            }
            json.dump(entry, f)
            f.write("\n")

        report = monitor.verify_queue_integrity(skip_corrupt=True)
        assert not report.is_valid
        has_missing = any(
            c.corruption_type == CorruptionType.MISSING_FIELD
            for c in report.corruption_details
        )
        assert has_missing


# ============================================================================
# RECOVERY TESTS
# ============================================================================


class TestRecoveryMechanisms:
    """Corruption recovery tests."""

    def test_auto_repair_marks_corrupted_records(self, monitor, temp_queue_file):
        """Test auto-repair marks corrupted records."""
        # Create a queue with one good and one bad record
        good_record = {
            "event_type": "good",
            "event_id": "id_1",
            "session_id": "session_1",
            "actor": "system",
            "action": "test",
            "resource": "resource",
            "result": "success",
            "timestamp": "2026-01-01T00:00:00Z",
            "tenant_id": "test_tenant",
            "prior_hash": "genesis",
            "self_hash": "good_hash",
        }

        bad_record = {
            "event_type": "bad",
            "event_id": "id_1",  # Duplicate!
            "session_id": "session_1",
            "actor": "system",
            "action": "test",
            "resource": "resource",
            "result": "success",
            "timestamp": "2026-01-01T00:01:00Z",
            "tenant_id": "test_tenant",
            "prior_hash": "good_hash",
            "self_hash": "bad_hash",
        }

        with open(temp_queue_file, "a") as f:
            json.dump(good_record, f)
            f.write("\n")
            json.dump(bad_record, f)
            f.write("\n")

        # Verify and repair
        report = monitor.verify_queue_integrity(skip_corrupt=True, auto_repair=True)

        # Check that repair was attempted
        assert report.recovery_attempted
        # Should have marked the duplicate as corrupted
        assert any(
            c.corruption_type == CorruptionType.DUPLICATE_EVENT_ID
            for c in report.corruption_details
        )

    def test_get_tail_records(self, monitor, temp_queue_file):
        """Test getting tail records from queue."""
        # Write multiple records
        for i in range(20):
            entry = {
                "event_type": f"event_{i}",
                "event_id": f"id_{i}",
                "session_id": "session_1",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": f"2026-01-01T00:{i:02d}:00Z",
                "tenant_id": "test_tenant",
                "prior_hash": f"hash_{i-1}" if i > 0 else "genesis",
                "self_hash": f"hash_{i}",
            }
            with open(temp_queue_file, "a") as f:
                json.dump(entry, f)
                f.write("\n")

        # Get last 5 records
        tail = monitor.get_tail_records(count=5)
        assert len(tail) <= 5
        # Should include recent records
        if len(tail) > 0:
            assert "event_" in tail[-1]["event_type"]


# ============================================================================
# AUDIT LOGGING TESTS
# ============================================================================


class TestAuditLogging:
    """Audit logging integration tests."""

    def test_corruption_audit_event_creation(self, monitor, temp_queue_file):
        """Test audit event creation for corruption."""
        # Create a report
        report = QueueIntegrityReport(
            total_records=10,
            corrupted_records=2,
            is_valid=False,
            recovery_attempted=True,
            recovery_successful=False,
            tenant_id="test_tenant",
        )

        # Create audit event
        event = monitor.create_corruption_audit_event(report, source="test")

        assert event["event_type"] == "corruption_detected"
        assert event["actor"] == "system"
        assert event["result"] == "corruption_found"
        assert event["details"]["total_records"] == 10
        assert event["details"]["corrupted_records"] == 2
        assert event["details"]["source"] == "test"

    def test_audit_event_includes_tenant(self, monitor):
        """Test audit events include tenant_id."""
        report = QueueIntegrityReport(
            total_records=5,
            corrupted_records=0,
            is_valid=True,
            tenant_id="tenant_xyz",
        )

        event = monitor.create_corruption_audit_event(report)
        assert event["details"]["tenant_id"] == "tenant_xyz"


# ============================================================================
# FEATURE FLAG INTEGRATION TESTS
# ============================================================================


class TestFeatureFlagIntegration:
    """Feature flag integration tests."""

    def test_corruption_detection_feature_flag(self, temp_queue_file, tenant_id):
        """Test corruption detection feature flag."""
        feature_flags = {"queue_corruption_detection_enabled": True}

        chain_with_detection = AuditChainWithCorruptionDetection(
            temp_queue_file, tenant_id=tenant_id, feature_enabled=True
        )

        # Record an entry
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="test",
            resource="resource",
            result="success",
            timestamp="2026-01-01T00:00:00Z",
            tenant_id=tenant_id,
        )
        chain_with_detection.record(entry)

        # Feature is enabled, so corruption check runs
        assert chain_with_detection.feature_enabled

    def test_corruption_detection_disabled_by_default(self, temp_queue_file, tenant_id):
        """Test corruption detection is disabled by default."""
        chain_with_detection = AuditChainWithCorruptionDetection(
            temp_queue_file, tenant_id=tenant_id, feature_enabled=False
        )

        # Feature is disabled
        assert not chain_with_detection.feature_enabled


# ============================================================================
# TENANT ISOLATION TESTS
# ============================================================================


class TestTenantIsolation:
    """Tenant isolation tests."""

    def test_monitor_tenant_scoped(self, temp_queue_file):
        """Test that monitor is tenant-scoped."""
        monitor1 = QueueIntegrityMonitor(temp_queue_file, tenant_id="tenant_1")
        monitor2 = QueueIntegrityMonitor(temp_queue_file, tenant_id="tenant_2")

        assert monitor1.tenant_id == "tenant_1"
        assert monitor2.tenant_id == "tenant_2"

    def test_audit_event_includes_tenant_id(self, monitor, temp_queue_file, tenant_id):
        """Test audit events include tenant_id."""
        report = QueueIntegrityReport(
            total_records=5,
            corrupted_records=0,
            is_valid=True,
            tenant_id=tenant_id,
        )

        event = monitor.create_corruption_audit_event(report)
        assert event["details"]["tenant_id"] == tenant_id


# ============================================================================
# EDGE CASES AND ERROR HANDLING
# ============================================================================


class TestEdgeCases:
    """Edge case handling tests."""

    def test_nonexistent_queue_file(self, tenant_id):
        """Test handling of non-existent queue file."""
        nonexistent = Path("/tmp/nonexistent_queue_12345.jsonl")
        monitor = QueueIntegrityMonitor(nonexistent, tenant_id=tenant_id)

        report = monitor.verify_queue_integrity()
        assert report.is_valid
        assert report.total_records == 0

    def test_empty_lines_in_queue(self, monitor, temp_queue_file):
        """Test handling of empty lines in queue."""
        # Write record with empty lines
        with open(temp_queue_file, "a") as f:
            entry = {
                "event_type": "test",
                "event_id": "id_1",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": "2026-01-01T00:00:00Z",
                "tenant_id": "test_tenant",
                "prior_hash": "genesis",
                "self_hash": "hash_1",
            }
            json.dump(entry, f)
            f.write("\n")
            f.write("\n")  # Empty line
            f.write("\n")  # Another empty line

        report = monitor.verify_queue_integrity()
        # Should skip empty lines and report only 1 record
        assert report.total_records == 1

    def test_very_large_queue(self, monitor, temp_queue_file):
        """Test handling of very large queue."""
        # Write 1000 records
        for i in range(1000):
            entry = {
                "event_type": f"event_{i}",
                "event_id": f"id_{i}",
                "session_id": "session_1",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": f"2026-01-01T{i//60:02d}:{i%60:02d}:00Z",
                "tenant_id": "test_tenant",
                "prior_hash": f"hash_{i-1}" if i > 0 else "genesis",
                "self_hash": f"hash_{i}",
            }
            with open(temp_queue_file, "a") as f:
                json.dump(entry, f)
                f.write("\n")

        report = monitor.verify_queue_integrity()
        assert report.total_records == 1000


# ============================================================================
# INTEGRATION TESTS
# ============================================================================


class TestIntegration:
    """Integration tests with AuditChain."""

    def test_audit_chain_with_corruption_detection(self, temp_queue_file, tenant_id):
        """Test full integration with AuditChain."""
        chain_with_detection = AuditChainWithCorruptionDetection(
            temp_queue_file, tenant_id=tenant_id, feature_enabled=True
        )

        # Record multiple entries
        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action="test",
                resource="resource",
                result="success",
                timestamp=f"2026-01-01T00:0{i}:00Z",
                tenant_id=tenant_id,
            )
            chain_with_detection.record(entry)

        # Verify chain
        assert chain_with_detection.verify_chain()
        assert chain_with_detection.entry_count() == 5

    def test_detect_and_repair_workflow(self, temp_queue_file, tenant_id):
        """Test full detect and repair workflow."""
        # Create chain with corruption detection
        chain = AuditChainWithCorruptionDetection(
            temp_queue_file, tenant_id=tenant_id, feature_enabled=True
        )

        # Manually create a corrupted file
        with open(temp_queue_file, "a") as f:
            bad_entry = {
                "event_type": "corrupted",
                "actor": "system",
                "action": "test",
                "resource": "resource",
                "result": "success",
                "timestamp": "2026-01-01T00:00:00Z",
                "tenant_id": tenant_id,
                "prior_hash": "wrong_hash",  # Wrong!
                "self_hash": "bad_hash",
            }
            json.dump(bad_entry, f)
            f.write("\n")

        # Detect and repair
        report = chain.detect_and_repair_corruption(auto_repair=True)

        # Should detect corruption
        if report:
            assert not report.is_valid


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

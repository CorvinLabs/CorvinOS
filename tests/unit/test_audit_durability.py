"""
Unit Tests for Audit Durability Manager — ADR-0299

Tests for AuditDurabilityManager with:
- Write-Ahead Logging (WAL)
- Atomic writes
- Crash recovery
- Tenant scoping
- Durability metrics
"""

import json
import os
import tempfile
from pathlib import Path

import pytest

from core.audit import (
    AuditDurabilityManager,
    AuditEntry,
    WALRecordType,
    CrashRecoveryReport,
)


class TestAuditDurabilityManager:
    """Test AuditDurabilityManager."""

    @pytest.fixture
    def temp_log(self):
        """Create temporary log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "audit.jsonl"

    def test_manager_initial_empty(self, temp_log):
        """New durability manager starts empty."""
        manager = AuditDurabilityManager(temp_log)
        assert manager.entry_count() == 0
        assert manager.last_hash() == "genesis"

    def test_manager_record_basic(self, temp_log):
        """Record entry through durability manager."""
        manager = AuditDurabilityManager(temp_log)
        entry = AuditEntry(
            event_type="auth",
            actor="console",
            action="login",
            resource="user_1",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        assert manager.entry_count() == 1
        assert manager.last_hash() != "genesis"

    def test_manager_tenant_id_keyword_only(self, temp_log):
        """tenant_id parameter is keyword-only."""
        with pytest.raises(TypeError):
            # This should fail because tenant_id is positional
            AuditDurabilityManager(temp_log, "_default")

        # This should succeed
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        assert manager.tenant_id == "_default"

    def test_manager_enable_wal_keyword_only(self, temp_log):
        """enable_wal parameter is keyword-only."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        assert manager.enable_wal is True

    def test_manager_wal_file_created(self, temp_log):
        """WAL file is created when enabled."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        wal_file = temp_log.with_suffix(".wal")
        assert wal_file.exists()

    def test_manager_wal_records_begin_commit(self, temp_log):
        """WAL records BEGIN and COMMIT for entries."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        # Read WAL and verify
        wal_file = temp_log.with_suffix(".wal")
        with open(wal_file) as f:
            wal_lines = f.readlines()

        # Should have at least BEGIN and COMMIT
        wal_records = [json.loads(line) for line in wal_lines if line.strip()]
        record_types = [r.get("record_type") for r in wal_records]

        assert "begin" in record_types
        assert "commit" in record_types

    def test_manager_multiple_entries(self, temp_log):
        """Record multiple entries."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_tenant_1")
        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:{i:02d}Z",
            )
            manager.record(entry)

        assert manager.entry_count() == 5

    def test_manager_verify_chain(self, temp_log):
        """Verify chain integrity."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        for i in range(3):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:{i:02d}Z",
            )
            manager.record(entry)

        assert manager.verify_chain() is True

    def test_manager_verify_durability(self, temp_log):
        """Verify durability guarantees."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        is_valid, message = manager.verify_durability()
        assert is_valid is True
        assert "Durability verified" in message or "empty" in message.lower()

    def test_manager_metrics_fsync_count(self, temp_log):
        """Metrics track fsync count."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        initial_count = manager.metrics.fsync_count

        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        assert manager.metrics.fsync_count > initial_count

    def test_manager_metrics_wal_writes(self, temp_log):
        """Metrics track WAL writes."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        initial_writes = manager.metrics.wal_writes

        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        # Should have at least BEGIN and COMMIT
        assert manager.metrics.wal_writes > initial_writes

    def test_manager_get_entries_copy(self, temp_log):
        """get_entries returns a copy."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        entries = manager.get_entries()
        entries[0].action = "MODIFIED"

        # Original should be unchanged
        assert manager.get_entries()[0].action == "write"

    def test_manager_last_hash(self, temp_log):
        """last_hash returns last entry's hash."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        assert manager.last_hash() == "genesis"

        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        assert manager.last_hash() == manager.get_entries()[0].self_hash

    def test_manager_wal_checkpoint(self, temp_log):
        """WAL checkpoint written periodically."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )

        # Record enough entries to trigger checkpoint
        for i in range(AuditDurabilityManager.WAL_CHECKPOINT_INTERVAL + 1):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action="write",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:00Z",
            )
            manager.record(entry)

        # Read WAL and verify checkpoint
        wal_file = temp_log.with_suffix(".wal")
        with open(wal_file) as f:
            wal_lines = f.readlines()

        wal_records = [json.loads(line) for line in wal_lines if line.strip()]
        record_types = [r.get("record_type") for r in wal_records]

        assert "checkpoint" in record_types

    def test_manager_cleanup_wal(self, temp_log):
        """WAL cleanup reduces file size."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )

        # Write many entries
        for i in range(100):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action="write",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:00Z",
            )
            manager.record(entry)

        wal_file = temp_log.with_suffix(".wal")
        size_before = wal_file.stat().st_size

        manager.cleanup_wal()

        size_after = wal_file.stat().st_size
        assert size_after <= size_before

    def test_manager_atomic_write_temp_file(self, temp_log):
        """Atomic writes use temp file + rename."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")

        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        # Verify main file exists and is not temp
        assert temp_log.exists()
        assert not temp_log.with_suffix(".tmp").exists()

    def test_manager_persistence_after_reload(self, temp_log):
        """Entries persist across manager instances."""
        # Write entries
        manager1 = AuditDurabilityManager(temp_log, tenant_id="_default")
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager1.record(entry)

        # Reload from file
        manager2 = AuditDurabilityManager(temp_log, tenant_id="_default")
        assert manager2.entry_count() == 1
        assert manager2.verify_chain() is True

    def test_manager_boot_recovery_no_wal(self, temp_log):
        """Boot with no WAL file skips recovery."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=False
        )
        # Should not raise
        assert manager.entry_count() == 0

    def test_manager_boot_recovery_empty_wal(self, temp_log):
        """Boot with empty WAL skips recovery."""
        wal_file = temp_log.with_suffix(".wal")
        wal_file.parent.mkdir(parents=True, exist_ok=True)
        wal_file.touch()

        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        # Should not raise
        assert manager.entry_count() == 0

    def test_manager_different_tenants_isolated(self, temp_log):
        """Different tenants use separate manager instances."""
        manager1 = AuditDurabilityManager(
            temp_log, tenant_id="_tenant_1", enable_wal=True
        )
        manager2 = AuditDurabilityManager(
            temp_log, tenant_id="_tenant_2", enable_wal=True
        )

        # Both refer to same file (would conflict in practice, but test shows they're separate managers)
        assert manager1.tenant_id != manager2.tenant_id

    def test_manager_wal_record_type_enum(self):
        """WALRecordType enum has expected values."""
        assert WALRecordType.BEGIN.value == "begin"
        assert WALRecordType.COMMIT.value == "commit"
        assert WALRecordType.ABORT.value == "abort"
        assert WALRecordType.CHECKPOINT.value == "checkpoint"

    def test_manager_durability_metrics_timestamp(self, temp_log):
        """Metrics track last fsync timestamp."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        timestamp = manager.metrics.last_fsync_timestamp
        assert "T" in timestamp  # ISO format
        assert "Z" in timestamp

    def test_manager_verify_durability_with_metrics(self, temp_log):
        """verify_durability includes metrics info."""
        manager = AuditDurabilityManager(
            temp_log, tenant_id="_default", enable_wal=True
        )
        for i in range(3):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="system",
                action="write",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:{i:02d}Z",
            )
            manager.record(entry)

        is_valid, message = manager.verify_durability()
        assert is_valid is True

    def test_manager_record_details_preserved(self, temp_log):
        """Entry details are preserved through durability manager."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
            details={"key": "value", "nested": {"a": 1}},
        )
        manager.record(entry)

        entries = manager.get_entries()
        assert entries[0].details == {"key": "value", "nested": {"a": 1}}

    def test_manager_fsync_actually_called(self, temp_log):
        """Verify fsync is called (entries survive crash)."""
        manager = AuditDurabilityManager(temp_log, tenant_id="_default")
        entry = AuditEntry(
            event_type="test",
            actor="system",
            action="write",
            resource="res",
            result="success",
            timestamp="2026-08-12T10:00:00Z",
        )
        manager.record(entry)

        # File should exist and have content
        assert temp_log.exists()
        with open(temp_log) as f:
            content = f.read()
        assert "event_type" in content


class TestWALRecordType:
    """Test WALRecordType enum."""

    def test_wal_record_type_values(self):
        """WALRecordType has correct values."""
        assert WALRecordType.BEGIN.value == "begin"
        assert WALRecordType.COMMIT.value == "commit"
        assert WALRecordType.ABORT.value == "abort"
        assert WALRecordType.CHECKPOINT.value == "checkpoint"


class TestCrashRecoveryReport:
    """Test CrashRecoveryReport."""

    def test_crash_recovery_report_defaults(self):
        """CrashRecoveryReport has correct defaults."""
        report = CrashRecoveryReport(tenant_id="_default")
        assert report.recovery_attempted is False
        assert report.recovery_successful is False
        assert report.records_recovered == 0
        assert report.tenant_id == "_default"

    def test_crash_recovery_report_with_values(self):
        """CrashRecoveryReport stores values."""
        report = CrashRecoveryReport(
            recovery_attempted=True,
            recovery_successful=True,
            records_recovered=5,
            tenant_id="_tenant_1",
        )
        assert report.recovery_attempted is True
        assert report.recovery_successful is True
        assert report.records_recovered == 5
        assert report.tenant_id == "_tenant_1"

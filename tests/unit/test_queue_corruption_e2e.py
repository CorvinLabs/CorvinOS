"""
E2E Tests for Queue Corruption Detection — ADR-0298

End-to-end tests verifying integration with AuditChain and feature flags.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.audit import AuditChain, AuditEntry
from core.audit.integration import AuditChainWithCorruptionDetection, create_audit_chain_with_flag


class TestQueueCorruptionE2E:
    """E2E tests for corruption detection integration."""

    @pytest.fixture
    def temp_queue(self):
        """Create temporary queue file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "audit.jsonl"

    def test_e2e_normal_operation_no_corruption(self, temp_queue):
        """Normal operation should work without corruption detection."""
        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=False
        )

        # Record entries
        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="test_actor",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:{i:02d}Z", tenant_id="_default",
            )
            chain.record(entry)

        # Verify
        assert chain.entry_count() == 5
        assert chain.verify_chain()

    def test_e2e_corruption_detection_enabled(self, temp_queue):
        """With feature enabled, corruption should be detected."""
        # Write corrupted queue
        record1 = {
            "event_type": "event_1",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        # Create chain with corruption detection enabled
        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )

        # Detect corruption
        report = chain.detect_and_repair_corruption(auto_repair=True)

        assert report is not None
        assert not report.is_valid
        assert report.corrupted_records > 0
        assert report.recovery_attempted

    def test_e2e_corruption_event_audit(self, temp_queue):
        """Corruption detection should create audit events."""
        # Write corrupted queue
        record1 = {
            "event_type": "event_1",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        # Create chain with corruption detection enabled
        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )

        # Detect and repair corruption
        chain.detect_and_repair_corruption(auto_repair=True)

        # Check that corruption event was logged
        entries = chain.get_entries()
        corruption_events = [e for e in entries if e.event_type == "corruption_detected"]

        assert len(corruption_events) > 0
        corruption_event = corruption_events[0]
        assert corruption_event.actor == "system"
        assert corruption_event.action == "detect_corruption"
        assert "corrupted_records" in corruption_event.details

    def test_e2e_feature_flag_integration(self, temp_queue):
        """Feature flag should control corruption detection."""
        # Write a valid queue
        entry = AuditEntry(
            event_type="test",
            actor="test_actor",
            action="test",
            resource="test_res",
            result="success",
            timestamp="2026-08-12T10:00:00Z", tenant_id="_default",
        )

        chain_uncorrupted = AuditChain(temp_queue)
        chain_uncorrupted.record(entry)

        # Create wrapped chain with flag OFF
        chain_no_flag = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=False
        )
        assert chain_no_flag.feature_enabled is False

        # Create wrapped chain with flag ON
        chain_with_flag = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )
        assert chain_with_flag.feature_enabled is True

    def test_e2e_factory_function_with_flags(self, temp_queue):
        """Factory function should respect feature flag dict."""
        # Test with flag OFF
        chain_off = create_audit_chain_with_flag(
            temp_queue,
            tenant_id="test",
            feature_flags={"queue_corruption_detection_enabled": False},
        )
        assert chain_off.feature_enabled is False

        # Test with flag ON
        chain_on = create_audit_chain_with_flag(
            temp_queue,
            tenant_id="test",
            feature_flags={"queue_corruption_detection_enabled": True},
        )
        assert chain_on.feature_enabled is True

        # Test with no flags (default OFF)
        chain_default = create_audit_chain_with_flag(temp_queue, tenant_id="test")
        assert chain_default.feature_enabled is False

    def test_e2e_multi_tenant_isolation(self, temp_queue):
        """Corruption detection should be tenant-scoped."""
        # Create chain for tenant A
        chain_a = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="tenant_a", feature_enabled=True
        )

        # Create chain for tenant B
        chain_b = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="tenant_b", feature_enabled=True
        )

        # Both should have different tenant_ids
        assert chain_a.tenant_id == "tenant_a"
        assert chain_b.tenant_id == "tenant_b"

        # Both should use the same queue file
        assert chain_a.log_file == chain_b.log_file

    def test_e2e_repair_and_verify(self, temp_queue):
        """Repair should result in verifiable chain."""
        # Write corrupted queue
        record1 = {
            "event_type": "event_1",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:00Z",
        }
        record2 = {
            "event_type": "event_2",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "WRONG_HASH",
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",
        }

        with open(temp_queue, "w") as f:
            json.dump(record1, f)
            f.write("\n")
            json.dump(record2, f)
            f.write("\n")

        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )

        # Detect and repair
        report = chain.detect_and_repair_corruption(auto_repair=True)
        assert report.recovery_successful

        # Verify that the repaired file can be loaded
        entries = chain.get_entries()
        assert len(entries) > 0

    def test_e2e_tail_recovery(self, temp_queue):
        """Should be able to extract tail records for recovery."""
        # Write queue with mix of valid and corrupted
        for i in range(10):
            record = {
                "event_type": f"event_{i}",
                "actor": "test",
                "action": "test_action",
                "resource": "test_res",
                "result": "success",
                "prior_hash": f"hash_{i-1}" if i > 0 else "genesis",
                "self_hash": f"hash_{i}",
                "timestamp": f"2026-08-12T10:00:{i:02d}Z",
                "event_id": f"evt_{i}",
            }
            with open(temp_queue, "a") as f:
                json.dump(record, f)
                f.write("\n")

        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )

        # Get tail records
        tail = chain.monitor.get_tail_records(count=3)
        assert len(tail) == 3
        assert tail[0]["event_id"] == "evt_7"
        assert tail[2]["event_id"] == "evt_9"

    def test_e2e_multiple_corruption_types(self, temp_queue):
        """Should detect multiple corruption types in one pass."""
        # Write records with various corruption types
        record1 = {
            "event_type": "event_1",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "genesis",
            "self_hash": "hash_1",
            "timestamp": "2026-08-12T10:00:05Z",  # Newer first
        }
        record2 = {
            "event_type": "event_2",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
            "prior_hash": "WRONG",  # Chain break
            "self_hash": "hash_2",
            "timestamp": "2026-08-12T10:00:01Z",  # Timestamp disorder
            "event_id": "evt_1",
            "session_id": "sess_1",
        }
        record3 = {
            "event_type": "event_3",
            "actor": "test",
            "action": "test_action",
            "resource": "test_res",
            "result": "success",
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

        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=True
        )

        report = chain.detect_and_repair_corruption(auto_repair=False)

        # Should detect multiple corruption types
        assert len(report.corruption_details) >= 2
        corruption_types = {c.corruption_type for c in report.corruption_details}
        assert len(corruption_types) > 1

    def test_e2e_chain_methods_unchanged(self, temp_queue):
        """Wrapper should preserve chain methods."""
        # Record entries through wrapper
        chain = AuditChainWithCorruptionDetection(
            temp_queue, tenant_id="test", feature_enabled=False
        )

        for i in range(3):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="test_actor",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-12T10:00:{i:02d}Z", tenant_id="_default",
            )
            chain.record(entry)

        # Verify methods work
        assert chain.entry_count() == 3
        assert chain.last_hash() != "genesis"
        assert len(chain.get_entries()) == 3
        assert chain.verify_chain() is True

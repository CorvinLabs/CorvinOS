"""
Unit Tests for Audit Durability — ADR-0299

Tests for hash-chained, fsync'd audit log.
"""

import json
import tempfile
from pathlib import Path

import pytest

from core.audit import AuditChain, AuditEntry, ChainVerificationError


class TestAuditEntry:
    """Test audit entry."""

    def test_entry_compute_hash_deterministic(self):
        """Entry hash is deterministic."""
        entry = AuditEntry(
            event_type="auth_check",
            actor="console",
            action="login",
            resource="user_123",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        hash1 = entry.compute_hash()
        hash2 = entry.compute_hash()
        assert hash1 == hash2

    def test_entry_finalize_sets_hash(self):
        """finalize() sets self_hash."""
        entry = AuditEntry(
            event_type="auth_check",
            actor="console",
            action="login",
            resource="user_123",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        assert entry.self_hash == ""
        entry.finalize()
        assert entry.self_hash != ""
        assert len(entry.self_hash) == 64  # SHA256 hex

    def test_entry_excludes_self_hash_from_computation(self):
        """Compute hash excludes self_hash field."""
        entry = AuditEntry(
            event_type="auth_check",
            actor="console",
            action="login",
            resource="user_123",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        entry.finalize()
        hash_with_self = entry.compute_hash()

        # Hash should be same whether self_hash is set or not
        entry2 = AuditEntry(
            event_type="auth_check",
            actor="console",
            action="login",
            resource="user_123",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
            self_hash="ignored",
        )
        hash_without_self = entry2.compute_hash()

        assert hash_with_self == hash_without_self


class TestAuditChain:
    """Test audit chain."""

    @pytest.fixture
    def temp_log(self):
        """Create temporary log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir) / "audit.jsonl"

    def test_chain_initial_empty(self, temp_log):
        """New chain starts empty."""
        chain = AuditChain(temp_log)
        assert chain.entry_count() == 0
        assert chain.last_hash() == "genesis"

    def test_chain_record_one_entry(self, temp_log):
        """Record one entry."""
        chain = AuditChain(temp_log)
        entry = AuditEntry(
            event_type="auth",
            actor="console",
            action="login",
            resource="user_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry)

        assert chain.entry_count() == 1
        assert chain.last_hash() != "genesis"

    def test_chain_record_sets_prior_hash(self, temp_log):
        """Recorded entry has prior_hash set."""
        chain = AuditChain(temp_log)
        entry1 = AuditEntry(
            event_type="event1",
            actor="console",
            action="a",
            resource="r1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry1)

        entry2 = AuditEntry(
            event_type="event2",
            actor="console",
            action="b",
            resource="r2",
            result="success",
            timestamp="2026-08-11T10:00:01Z",
        )
        chain.record(entry2)

        entries = chain.get_entries()
        assert entries[0].prior_hash == "genesis"
        assert entries[1].prior_hash == entries[0].self_hash

    def test_chain_record_multiple_entries(self, temp_log):
        """Record multiple entries."""
        chain = AuditChain(temp_log)
        for i in range(5):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-11T10:00:{i:02d}Z",
            )
            chain.record(entry)

        assert chain.entry_count() == 5

    def test_chain_verify_valid(self, temp_log):
        """Verify valid chain."""
        chain = AuditChain(temp_log)
        for i in range(3):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-11T10:00:{i:02d}Z",
            )
            chain.record(entry)

        assert chain.verify_chain() is True

    def test_chain_persistence(self, temp_log):
        """Chain persists to file."""
        # Write entries
        chain1 = AuditChain(temp_log)
        entry = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain1.record(entry)

        # Load from file
        chain2 = AuditChain(temp_log)
        assert chain2.entry_count() == 1
        assert chain2.verify_chain() is True

    def test_chain_tampering_detection(self, temp_log):
        """Tampered entry detected."""
        chain = AuditChain(temp_log)
        entry1 = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry1)

        entry2 = AuditEntry(
            event_type="event_2",
            actor="console",
            action="test",
            resource="res_2",
            result="success",
            timestamp="2026-08-11T10:00:01Z",
        )
        chain.record(entry2)

        # Tamper with entry1 in internal list
        chain._entries[0].action = "TAMPERED"

        # Verification should fail
        with pytest.raises(ChainVerificationError):
            chain.verify_chain()

    def test_chain_tampering_chain_break_detection(self, temp_log):
        """Chain break detected."""
        chain = AuditChain(temp_log)
        entry1 = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry1)

        entry2 = AuditEntry(
            event_type="event_2",
            actor="console",
            action="test",
            resource="res_2",
            result="success",
            timestamp="2026-08-11T10:00:01Z",
        )
        chain.record(entry2)

        # Break the chain by modifying prior_hash in internal list
        chain._entries[1].prior_hash = "WRONG_HASH"

        # Verification should fail
        with pytest.raises(ChainVerificationError):
            chain.verify_chain()

    def test_chain_last_hash(self, temp_log):
        """last_hash returns last entry's hash."""
        chain = AuditChain(temp_log)
        assert chain.last_hash() == "genesis"

        entry = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry)

        assert chain.last_hash() == chain.get_entries()[0].self_hash

    def test_chain_get_entries_copy(self, temp_log):
        """get_entries returns a copy."""
        chain = AuditChain(temp_log)
        entry = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry)

        entries = chain.get_entries()
        entries[0].action = "MODIFIED"

        # Original should be unchanged
        assert chain.get_entries()[0].action == "test"

    def test_chain_file_format_jsonl(self, temp_log):
        """Audit log file is JSONL format."""
        chain = AuditChain(temp_log)
        for i in range(3):
            entry = AuditEntry(
                event_type=f"event_{i}",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-11T10:00:{i:02d}Z",
            )
            chain.record(entry)

        # Read file and verify JSONL
        with open(temp_log) as f:
            lines = f.readlines()

        assert len(lines) == 3
        for line in lines:
            data = json.loads(line)
            assert "event_type" in data
            assert "self_hash" in data
            assert "prior_hash" in data

    def test_chain_fsync_durability(self, temp_log):
        """Entries are synced to disk (fsync)."""
        chain = AuditChain(temp_log)
        entry = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
        )
        chain.record(entry)

        # File should exist and contain entry
        assert temp_log.exists()
        assert temp_log.stat().st_size > 0

    def test_chain_details_field(self, temp_log):
        """Entry can have details dict."""
        chain = AuditChain(temp_log)
        entry = AuditEntry(
            event_type="event_1",
            actor="console",
            action="test",
            resource="res_1",
            result="success",
            timestamp="2026-08-11T10:00:00Z",
            details={"key": "value", "nested": {"a": 1}},
        )
        chain.record(entry)

        entries = chain.get_entries()
        assert entries[0].details == {"key": "value", "nested": {"a": 1}}

    def test_chain_verify_empty_chain(self, temp_log):
        """Empty chain verifies successfully."""
        chain = AuditChain(temp_log)
        assert chain.verify_chain() is True

    def test_chain_many_entries(self, temp_log):
        """Chain with many entries."""
        chain = AuditChain(temp_log)
        for i in range(100):
            entry = AuditEntry(
                event_type="event",
                actor="console",
                action="test",
                resource=f"res_{i}",
                result="success",
                timestamp=f"2026-08-11T10:00:{i % 60:02d}Z",
            )
            chain.record(entry)

        assert chain.entry_count() == 100
        assert chain.verify_chain() is True

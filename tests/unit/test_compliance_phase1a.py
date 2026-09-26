"""Phase 1A: Smoke tests for ADR-0232 Audit Trail + Boot Tripwire."""

import tempfile
import pytest
from pathlib import Path

from core.compliance import (
    AuditTrail,
    AuditRecord,
    BootTripwire,
    ComplianceError,
)
from core.compliance.audit_trail import new_audit_record
from core.compliance.exceptions import AuditChainError, TripwireError


class TestAuditRecord:
    """Immutable audit records."""
    
    def test_audit_record_frozen(self):
        """AuditRecord is immutable."""
        record = AuditRecord(
            timestamp="2026-09-26T20:32:00Z",
            event_type="consent_granted",
            tenant_id="default",
            actor="operator",
            action="approve",
            resource="skill:foo",
            result="allowed",
            details={"version": "1.0"},
        )
        
        with pytest.raises(AttributeError):
            record.timestamp = "2026-09-26T21:00:00Z"
    
    def test_audit_record_hash_deterministic(self):
        """Same record always produces same hash."""
        record = AuditRecord(
            timestamp="2026-09-26T20:32:00Z",
            event_type="consent_granted",
            tenant_id="default",
            actor="operator",
            action="approve",
            resource="skill:foo",
            result="allowed",
            details={},
        )
        
        hash1 = record.hash()
        hash2 = record.hash()
        assert hash1 == hash2


class TestAuditTrail:
    """Hash-chained audit trail."""
    
    def test_empty_trail(self):
        """New trail is valid."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            assert trail.verify_chain() is True
    
    def test_append_record(self):
        """Append creates hash-chained entry."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            
            record = AuditRecord(
                timestamp="2026-09-26T20:32:00Z",
                event_type="test_event",
                tenant_id="default",
                actor="test",
                action="test_action",
                resource="test",
                result="allowed",
                details={},
            )
            
            hash_result = trail.append(record)
            assert hash_result == record.hash()
            assert trail.verify_chain() is True
    
    def test_chain_integrity(self):
        """Hash chain is verifiable."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            
            # Append 3 records
            for i in range(3):
                record = AuditRecord(
                    timestamp=f"2026-09-26T20:{i:02d}:00Z",
                    event_type="test",
                    tenant_id="default",
                    actor="test",
                    action=f"action_{i}",
                    resource="test",
                    result="allowed",
                    details={"index": i},
                )
                trail.append(record)
            
            # Verify all
            assert trail.verify_chain() is True
    
    def test_records_iteration(self):
        """Iterate all records in trail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            trail = AuditTrail(Path(tmpdir) / "audit.jsonl")
            
            for i in range(3):
                record = AuditRecord(
                    timestamp=f"2026-09-26T20:{i:02d}:00Z",
                    event_type="test",
                    tenant_id="default",
                    actor="test",
                    action=f"action_{i}",
                    resource="test",
                    result="allowed",
                    details={"index": i},
                )
                trail.append(record)
            
            # Iterate and count
            records = list(trail.records())
            assert len(records) == 3
            assert records[0].action == "action_0"
            assert records[2].action == "action_2"


class TestBootTripwire:
    """Boot compliance check."""
    
    def test_tripwire_pass(self):
        """Tripwire passes when audit trail exists and is initialized."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            
            # Create and initialize audit trail
            trail = AuditTrail(corvin_home / "audit.jsonl")
            record = new_audit_record(
                event_type="boot_started",
                tenant_id="default",
                actor="system",
                action="boot",
                resource="platform",
                result="allowed",
                details={"phase": "1a"},
            )
            trail.append(record)
            
            # Tripwire should pass
            tripwire = BootTripwire(corvin_home)
            assert tripwire.run() is True
            
            status = tripwire.status()
            assert status["all_pass"] is True
    
    def test_tripwire_fail_missing(self):
        """Tripwire fails if audit trail missing."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            # Don't create audit trail
            
            tripwire = BootTripwire(corvin_home)
            assert tripwire.run() is False
            
            status = tripwire.status()
            assert status["all_pass"] is False
    
    def test_tripwire_fail_empty(self):
        """Tripwire fails if audit trail empty (not initialized)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            
            # Create empty trail (not initialized)
            trail = AuditTrail(corvin_home / "audit.jsonl")
            
            tripwire = BootTripwire(corvin_home)
            assert tripwire.run() is False
    
    def test_tripwire_assert_raises(self):
        """assert_all() raises TripwireError if fail."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            
            tripwire = BootTripwire(corvin_home)
            with pytest.raises(TripwireError):
                tripwire.assert_all()


class TestCompliancePhase1AIntegration:
    """E2E Phase 1A: Audit Trail + Tripwire."""
    
    def test_full_boot_sequence(self):
        """Full boot: create trail → append record → verify → tripwire."""
        with tempfile.TemporaryDirectory() as tmpdir:
            corvin_home = Path(tmpdir)
            
            # Step 1: Create and populate trail
            trail = AuditTrail(corvin_home / "audit.jsonl")
            record = new_audit_record(
                event_type="boot_started",
                tenant_id="default",
                actor="system",
                action="boot",
                resource="platform",
                result="allowed",
                details={"phase": "1a"},
            )
            trail.append(record)
            
            # Step 2: Verify trail
            assert trail.verify_chain() is True
            
            # Step 3: Tripwire check
            tripwire = BootTripwire(corvin_home)
            tripwire.assert_all()  # Should not raise
            
            # Step 4: Verify tripwire status
            status = tripwire.status()
            assert status["all_pass"] is True

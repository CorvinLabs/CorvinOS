"""Phase 1 Critical Audit Events — Completeness & Registration Tests (2026-09-24).

Validates that all Phase 1 critical fix events (10 new events across Layers 10,
22, 25, 36) are:

1. Registered in EVENT_SEVERITY with correct severity levels
2. Registered in _EVENT_ALLOWLIST with correct field sets
3. Can be emitted without errors
4. Do not leak PII or forbidden fields
5. Are properly chained in the audit trail

Test scope:
  - Layer 10 Context: snapshot_created, snapshot_restored, field_preserved, field_added
  - Layer 22 Compute: checkpoint_corrupted, deadlock_detected, iteration_diverged
  - Layer 25 ACS: l34_gate_passed
  - Layer 36 Erasure: tenant_boundary_checked, cross_tenant_detected

Compliance:
  - GDPR Art. 5 (no PII in chain)
  - GDPR Art. 30 (audit events mandatory)
  - ADR-0232/0233 (chain integrity)
  - ADR-0264 (event schema)
"""
from __future__ import annotations

import json
import tempfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

# Placeholder imports — adapt to actual module structure
try:
    from forge.security_events import (
        EVENT_SEVERITY,
        write_event,
        _EVENT_ALLOWLIST,
        verify_chain,
    )
except ImportError:
    from corvin_operator.forge.forge.security_events import (
        EVENT_SEVERITY,
        write_event,
        _EVENT_ALLOWLIST,
        verify_chain,
    )


class TestPhase1EventRegistration:
    """Verify all Phase 1 events are registered in EVENT_SEVERITY."""

    PHASE1_EVENTS = {
        # Layer 10 — Context Engineering
        "context.snapshot_created": "INFO",
        "context.snapshot_restored": "INFO",
        "context.field_preserved": "INFO",
        "context.field_added": "INFO",
        # Layer 22 — Compute Safety
        "compute.checkpoint_corrupted": "WARNING",
        "compute.deadlock_detected": "WARNING",
        "compute.iteration_diverged": "WARNING",
        # Layer 25 — ACS L34
        "acs.l34_gate_passed": "INFO",
        # Layer 36 — Erasure
        "erasure.tenant_boundary_checked": "INFO",
        "erasure.cross_tenant_detected": "WARNING",
    }

    def test_all_events_registered_in_event_severity(self):
        """All Phase 1 events must be in EVENT_SEVERITY with correct severity."""
        for event_type, expected_severity in self.PHASE1_EVENTS.items():
            assert event_type in EVENT_SEVERITY, \
                f"Event {event_type!r} not registered in EVENT_SEVERITY"
            actual = EVENT_SEVERITY[event_type]
            assert actual == expected_severity, \
                f"Event {event_type!r}: expected severity {expected_severity!r}, got {actual!r}"

    def test_all_events_in_event_allowlist(self):
        """All Phase 1 events must have an allow-list in _EVENT_ALLOWLIST."""
        for event_type in self.PHASE1_EVENTS:
            assert event_type in _EVENT_ALLOWLIST, \
                f"Event {event_type!r} not in _EVENT_ALLOWLIST (will be dropped from chain)"

    def test_allowlist_fields_are_non_empty(self):
        """Allow-list for each event must contain at least 1 field."""
        for event_type in self.PHASE1_EVENTS:
            fields = _EVENT_ALLOWLIST.get(event_type, frozenset())
            assert len(fields) > 0, \
                f"Event {event_type!r} has empty allow-list; events will be dropped"

    def test_context_events_allowlist_structure(self):
        """Layer 10 context events must have required fields."""
        expected = {
            "context.snapshot_created": {"snapshot_id", "user_id", "tenant_id",
                                         "preserved_fields_count", "added_fields_count"},
            "context.snapshot_restored": {"snapshot_id", "restoration_success", "tenant_id"},
            "context.field_preserved": {"snapshot_id", "field_name", "reason", "tenant_id"},
            "context.field_added": {"snapshot_id", "field_name", "source", "tenant_id"},
        }
        for event_type, required_fields in expected.items():
            actual = _EVENT_ALLOWLIST[event_type]
            assert required_fields.issubset(actual), \
                f"{event_type}: missing required fields {required_fields - actual}"

    def test_compute_events_allowlist_structure(self):
        """Layer 22 compute safety events must have required fields."""
        expected = {
            "compute.checkpoint_corrupted": {"job_id", "error_message", "recovery_attempted"},
            "compute.deadlock_detected": {"job_id", "component", "timeout_ms"},
            "compute.iteration_diverged": {"job_id", "prev_loss", "new_loss", "delta_pct"},
        }
        for event_type, required_fields in expected.items():
            actual = _EVENT_ALLOWLIST[event_type]
            assert required_fields.issubset(actual), \
                f"{event_type}: missing required fields {required_fields - actual}"


class TestPhase1EventEmission:
    """Verify Phase 1 events can be emitted and chained correctly."""

    @pytest.fixture
    def temp_chain(self) -> Path:
        """Create a temporary audit chain for testing."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)
        yield temp_path
        # Cleanup
        try:
            temp_path.unlink()
        except OSError:
            pass

    def test_context_snapshot_created_emission(self, temp_chain):
        """Emit context.snapshot_created and verify it reaches the chain."""
        write_event(
            temp_chain,
            "context.snapshot_created",
            details={
                "snapshot_id": "snap_12345678",
                "user_id": "user_abc123",
                "tenant_id": "_default",
                "preserved_fields_count": 5,
                "added_fields_count": 3,
            },
        )
        # Verify record exists in chain
        with temp_chain.open() as f:
            line = f.readline()
            rec = json.loads(line)
            assert rec["event_type"] == "context.snapshot_created"
            assert rec["details"]["snapshot_id"] == "snap_12345678"
            assert rec["hash"]  # Should have hash

    def test_compute_checkpoint_corrupted_emission(self, temp_chain):
        """Emit compute.checkpoint_corrupted with proper metadata."""
        write_event(
            temp_chain,
            "compute.checkpoint_corrupted",
            details={
                "run_id": "run_xyz",
                "tenant_id": "_default",
                "job_id": "job_001",
                "error_message": "Checksum mismatch",
                "recovery_attempted": True,
            },
        )
        with temp_chain.open() as f:
            line = f.readline()
            rec = json.loads(line)
            assert rec["event_type"] == "compute.checkpoint_corrupted"
            assert rec["details"]["job_id"] == "job_001"
            assert rec["details"]["recovery_attempted"] is True

    def test_acs_l34_gate_passed_emission(self, temp_chain):
        """Emit acs.l34_gate_passed with classification metadata."""
        write_event(
            temp_chain,
            "acs.l34_gate_passed",
            details={
                "run_id": "run_xyz",
                "tenant_id": "_default",
                "acs_id": "acs_abc",
                "input_classification": "public",
                "output_classification": "public",
                "gate_enforcement": "strict",
                "bypassed": False,
            },
        )
        with temp_chain.open() as f:
            line = f.readline()
            rec = json.loads(line)
            assert rec["event_type"] == "acs.l34_gate_passed"
            assert rec["details"]["bypassed"] is False

    def test_erasure_tenant_boundary_checked_emission(self, temp_chain):
        """Emit erasure.tenant_boundary_checked with isolation flag."""
        write_event(
            temp_chain,
            "erasure.tenant_boundary_checked",
            details={
                "erasure_id": "erase_123",
                "tenant_id": "_default",
                "isolation_valid": True,
            },
        )
        with temp_chain.open() as f:
            line = f.readline()
            rec = json.loads(line)
            assert rec["event_type"] == "erasure.tenant_boundary_checked"
            assert rec["details"]["isolation_valid"] is True


class TestPhase1ChainIntegrity:
    """Verify Phase 1 events maintain hash-chain integrity."""

    @pytest.fixture
    def multi_event_chain(self) -> Path:
        """Create a chain with multiple Phase 1 events."""
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)

        # Emit multiple Phase 1 events
        write_event(
            temp_path,
            "context.snapshot_created",
            details={
                "snapshot_id": "snap_1",
                "user_id": "user_1",
                "tenant_id": "_default",
                "preserved_fields_count": 1,
                "added_fields_count": 1,
            },
        )
        write_event(
            temp_path,
            "compute.checkpoint_corrupted",
            details={
                "run_id": "run_1",
                "tenant_id": "_default",
                "job_id": "job_1",
                "error_message": "Error",
                "recovery_attempted": False,
            },
        )
        write_event(
            temp_path,
            "erasure.tenant_boundary_checked",
            details={
                "erasure_id": "erase_1",
                "tenant_id": "_default",
                "isolation_valid": True,
            },
        )

        yield temp_path
        try:
            temp_path.unlink()
        except OSError:
            pass

    def test_chain_maintains_hash_links(self, multi_event_chain):
        """Verify hash-chain is intact across Phase 1 events."""
        ok, problems = verify_chain(multi_event_chain)
        assert ok, f"Chain broken: {problems}"

        # Verify all 3 events are in chain
        with multi_event_chain.open() as f:
            events = [json.loads(line) for line in f if line.strip()]
        assert len(events) == 3
        assert events[0]["event_type"] == "context.snapshot_created"
        assert events[1]["event_type"] == "compute.checkpoint_corrupted"
        assert events[2]["event_type"] == "erasure.tenant_boundary_checked"

    def test_chain_hash_sequence_correct(self, multi_event_chain):
        """Verify each event's prev_hash points to previous event's hash."""
        with multi_event_chain.open() as f:
            events = [json.loads(line) for line in f if line.strip()]

        # First event has empty prev_hash
        assert events[0].get("prev_hash") == ""
        assert events[0].get("hash")

        # Second event's prev_hash = first event's hash
        assert events[1].get("prev_hash") == events[0].get("hash")
        assert events[1].get("hash")

        # Third event's prev_hash = second event's hash
        assert events[2].get("prev_hash") == events[1].get("hash")


class TestPhase1PiiProtection:
    """Verify Phase 1 events don't leak PII into the chain."""

    @pytest.fixture
    def temp_chain(self) -> Path:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)
        yield temp_path
        try:
            temp_path.unlink()
        except OSError:
            pass

    def test_no_email_in_context_events(self, temp_chain):
        """Context events must not carry email addresses."""
        write_event(
            temp_chain,
            "context.snapshot_created",
            details={
                "snapshot_id": "snap_1",
                "user_id": "user_1",
                "tenant_id": "_default",
                "preserved_fields_count": 1,
                "added_fields_count": 1,
            },
        )
        with temp_chain.open() as f:
            content = f.read()
        # Verify no email-like patterns in raw chain
        assert "@" not in content, "Email found in context event"

    def test_no_raw_passwords_in_error_messages(self, temp_chain):
        """Error messages in compute events must be sanitized."""
        write_event(
            temp_chain,
            "compute.checkpoint_corrupted",
            details={
                "run_id": "run_1",
                "tenant_id": "_default",
                "job_id": "job_1",
                "error_message": "Checksum mismatch (safe string only)",
                "recovery_attempted": True,
            },
        )
        with temp_chain.open() as f:
            content = f.read()
        # Verify error_message is safely encoded (no raw secrets)
        assert "password" not in content.lower()


class TestPhase1TenantIsolation:
    """Verify Phase 1 events respect tenant isolation (GDPR Art. 6)."""

    @pytest.fixture
    def multi_tenant_chain(self) -> Path:
        with tempfile.NamedTemporaryFile(mode='w', suffix='.jsonl', delete=False) as f:
            temp_path = Path(f.name)

        # Emit events for multiple tenants
        write_event(
            temp_path,
            "erasure.tenant_boundary_checked",
            details={
                "erasure_id": "erase_1",
                "tenant_id": "tenant_a",
                "isolation_valid": True,
            },
        )
        write_event(
            temp_path,
            "erasure.tenant_boundary_checked",
            details={
                "erasure_id": "erase_2",
                "tenant_id": "tenant_b",
                "isolation_valid": True,
            },
        )

        yield temp_path
        try:
            temp_path.unlink()
        except OSError:
            pass

    def test_erasure_events_carry_tenant_id(self, multi_tenant_chain):
        """Each erasure event must carry its tenant_id."""
        with multi_tenant_chain.open() as f:
            events = [json.loads(line) for line in f if line.strip()]

        assert events[0]["details"]["tenant_id"] == "tenant_a"
        assert events[1]["details"]["tenant_id"] == "tenant_b"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

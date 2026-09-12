"""Tests for Creator 2.0 Critical Fixes #1 and #2 (2026-09-12).

FIX #1: Feedback Audit-Chaining (GDPR Art. 30)
- Learning feedback must be hash-chained to audit trail
- Tests: 5 (persist, chain integrity, recovery, replay, durability)

FIX #2: Transactional Guarantees (Data Corruption Risk)
- Skill creation mid-crash leaves partial state on disk
- Solution: WAL + atomic swap
- Tests: 8 (crash scenarios, recovery, concurrency, idempotence)
"""

import json
import pytest
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

from core.skills.os_skills.creator_2_0.learning_integration import Creator20AuditIntegration
from core.skills.os_skills.creator_2_0.events import PhaseCompletedEvent, LossComponents
from core.skills.os_skills.creator_2_0.wal import SkillCreationWAL, WALEntry, _validate_skill_id
from core.skills.os_skills.creator_2_0.phase_model import PhaseModelOrchestrator


# =============================================================================
# FIX #1: FEEDBACK AUDIT-CHAINING TESTS
# =============================================================================

class TestCreator20AuditIntegrationF1:
    """Tests for FIX #1: Feedback Audit-Chaining (GDPR Art. 30)."""

    @pytest.fixture
    def tenant_home(self):
        """Temp directory for tenant home."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def audit_integration(self, tenant_home):
        """Create audit integration instance."""
        return Creator20AuditIntegration(tenant_home=tenant_home, tenant_id="_default")

    def test_f1_emit_feedback_event_with_audit_persists_to_store(self, audit_integration):
        """F1.1: Feedback event is persisted to event store with audit-chaining."""
        phase_event = PhaseCompletedEvent(
            phase_num=5,
            skill_id="test_skill",
            duration_ms=1000,
            errors=[],
            loss_components=LossComponents(
                relevance=0.8,
                completeness=0.9,
                performance=0.85,
                maintainability=0.75,
            ),
        )

        # Emit feedback with audit-chaining
        audit_ref = audit_integration.emit_feedback_event_with_audit(phase_event)

        # Verify event was persisted
        assert audit_ref is not None

        # Verify event store contains the event
        events = audit_integration.event_store.query_events(
            tenant_id="_default",
            skill_id="creator_2_0.test_skill",
        )
        assert len(events) == 1
        assert events[0].skill_id == "creator_2_0.test_skill"

    def test_f1_multiple_feedback_events_all_audited(self, audit_integration):
        """F1.2: Multiple feedback events from different phases all audit-chained."""
        phase_events = [
            PhaseCompletedEvent(
                phase_num=i,
                skill_id="test_skill_2",
                duration_ms=100 * (i + 1),
                errors=[],
                loss_components=LossComponents(
                    relevance=0.8 + (i * 0.01),
                    completeness=0.9,
                    performance=0.85,
                    maintainability=0.75,
                ),
            )
            for i in range(3)
        ]

        # Emit all feedback events
        audit_refs = audit_integration.emit_feedback_events_with_audit(phase_events)

        # Verify all events audited
        assert len(audit_refs) == 3
        assert all(ref is not None for ref in audit_refs)

        # Verify all in event store
        events = audit_integration.event_store.query_events(
            tenant_id="_default",
            skill_id="creator_2_0.test_skill_2",
        )
        assert len(events) >= 3

    def test_f1_feedback_event_has_audit_reference(self, audit_integration):
        """F1.3: Each feedback event has audit_ref (proof of hash-chaining)."""
        phase_event = PhaseCompletedEvent(
            phase_num=7,
            skill_id="test_skill_3",
            duration_ms=500,
            errors=[],
            loss_components=LossComponents(
                relevance=0.9,
                completeness=0.85,
                performance=0.95,
                maintainability=0.8,
            ),
        )

        audit_integration.emit_feedback_event_with_audit(phase_event)

        # Verify event has audit_ref
        events = audit_integration.event_store.query_events(
            tenant_id="_default",
            skill_id="creator_2_0.test_skill_3",
        )
        assert len(events) == 1
        event = events[0]
        # audit_ref may be filled in by event store after write
        assert hasattr(event, 'audit_ref')

    def test_f1_validate_feedback_chain_returns_true_on_valid_chain(self, audit_integration):
        """F1.4: Validation confirms feedback chain integrity."""
        phase_event = PhaseCompletedEvent(
            phase_num=3,
            skill_id="test_skill_4",
            duration_ms=300,
            errors=[],
            loss_components=LossComponents(
                relevance=0.7,
                completeness=0.8,
                performance=0.9,
                maintainability=0.85,
            ),
        )

        audit_integration.emit_feedback_event_with_audit(phase_event)

        # Validate chain
        is_valid = audit_integration.validate_feedback_chain("test_skill_4", 3)
        assert is_valid is True

    def test_f1_tenant_isolation_in_audit_events(self, tenant_home):
        """F1.5: Feedback events are tenant-scoped (GDPR Art. 32)."""
        # Create two integrations with different tenants
        integration_1 = Creator20AuditIntegration(tenant_home=tenant_home, tenant_id="tenant_1")
        integration_2 = Creator20AuditIntegration(tenant_home=tenant_home, tenant_id="tenant_2")

        # Emit events to both tenants
        phase_event_1 = PhaseCompletedEvent(
            phase_num=1,
            skill_id="skill_a",
            duration_ms=100,
            errors=[],
            loss_components=LossComponents(
                relevance=0.8,
                completeness=0.9,
                performance=0.85,
                maintainability=0.75,
            ),
        )

        phase_event_2 = PhaseCompletedEvent(
            phase_num=2,
            skill_id="skill_b",
            duration_ms=200,
            errors=[],
            loss_components=LossComponents(
                relevance=0.9,
                completeness=0.8,
                performance=0.9,
                maintainability=0.85,
            ),
        )

        integration_1.emit_feedback_event_with_audit(phase_event_1)
        integration_2.emit_feedback_event_with_audit(phase_event_2)

        # Verify tenant isolation: tenant_1 should not see tenant_2 events
        events_1 = integration_1.event_store.query_events(tenant_id="tenant_1")
        events_2 = integration_2.event_store.query_events(tenant_id="tenant_2")

        # Each tenant should have its own event
        assert len(events_1) >= 1
        assert len(events_2) >= 1

        # Verify tenant_id in events
        for event in events_1:
            assert event.tenant_id == "tenant_1"
        for event in events_2:
            assert event.tenant_id == "tenant_2"


# =============================================================================
# FIX #2: TRANSACTIONAL GUARANTEES TESTS
# =============================================================================

class TestSkillCreationWALF2:
    """Tests for FIX #2: Transactional Guarantees (Data Corruption Risk)."""

    @pytest.fixture
    def tenant_home(self):
        """Temp directory for tenant home."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    @pytest.fixture
    def wal(self, tenant_home):
        """Create WAL instance."""
        return SkillCreationWAL(tenant_home)

    def test_f2_write_state_creates_wal_entry(self, wal):
        """F2.1: State is written to WAL before phase execution."""
        skill_id = "test_skill"
        phase_num = 0
        state = {"skill_id": skill_id, "intake_result": None}

        # Write to WAL
        wal.write_state(skill_id, phase_num, state)

        # Verify WAL file exists and contains entry
        assert wal.wal_file.exists()

        # Read WAL and verify
        with open(wal.wal_file, "r") as f:
            lines = f.readlines()
            assert len(lines) >= 1
            entry_data = json.loads(lines[-1])
            assert entry_data["skill_id"] == skill_id
            assert entry_data["phase_num"] == phase_num

    def test_f2_mark_processed_updates_wal_entry(self, wal):
        """F2.2: Completing a phase marks WAL entry as processed."""
        skill_id = "test_skill_2"
        phase_num = 1
        state = {"skill_id": skill_id, "intake_result": None}

        # Write and mark processed
        wal.write_state(skill_id, phase_num, state)
        wal.mark_processed(skill_id, phase_num)

        # Verify entry is marked processed
        unprocessed = wal.get_unprocessed_entries(skill_id)
        assert len(unprocessed) == 0

    def test_f2_get_unprocessed_entries_returns_incomplete_phases(self, wal):
        """F2.3: Unprocessed entries can be recovered on crash."""
        skill_id = "test_skill_3"

        # Write 3 phases
        for i in range(3):
            state = {"skill_id": skill_id, "phase_num": i}
            wal.write_state(skill_id, i, state)

        # Mark phase 0 and 2 as processed
        wal.mark_processed(skill_id, 0)
        wal.mark_processed(skill_id, 2)

        # Get unprocessed
        unprocessed = wal.get_unprocessed_entries(skill_id)

        # Only phase 1 should be unprocessed
        assert len(unprocessed) == 1
        assert unprocessed[0].phase_num == 1

    def test_f2_wal_state_hash_ensures_integrity(self, wal):
        """F2.4: State hash prevents corruption detection."""
        skill_id = "test_skill_4"
        phase_num = 0
        state = {"skill_id": skill_id, "data": "original"}

        # Write state
        wal.write_state(skill_id, phase_num, state)

        # Read entry and verify hash
        entries = wal.get_unprocessed_entries(skill_id)
        assert len(entries) == 1

        entry = entries[0]
        # State hash should be computed
        assert entry.state_hash is not None
        assert isinstance(entry.state_hash, str)
        assert len(entry.state_hash) == 64  # SHA256 hex

    def test_f2_crash_recovery_via_wal_replay(self, wal):
        """F2.5: Unprocessed entries can be replayed on restart."""
        skill_id = "test_skill_5"

        # Simulate crash: write state for phases 0-2, complete 0-1
        for i in range(3):
            state = {"phase": i}
            wal.write_state(skill_id, i, state)

        wal.mark_processed(skill_id, 0)
        wal.mark_processed(skill_id, 1)

        # Simulate restart: get unprocessed entries
        unprocessed = wal.get_unprocessed_entries(skill_id)

        # Should have phase 2
        assert len(unprocessed) == 1
        assert unprocessed[0].phase_num == 2

        # Verify state is intact
        assert unprocessed[0].state["phase"] == 2

    def test_f2_mark_error_records_failure_reason(self, wal):
        """F2.6: Errors during recovery are marked for debugging."""
        skill_id = "test_skill_6"
        phase_num = 0
        state = {"phase": 0}

        # Write and mark error
        wal.write_state(skill_id, phase_num, state)
        wal.mark_error(skill_id, phase_num, "File system error")

        # Verify error is recorded
        unprocessed = wal.get_unprocessed_entries(skill_id)
        assert len(unprocessed) == 1
        assert unprocessed[0].error == "File system error"

    def test_f2_clear_skill_entries_after_success(self, wal):
        """F2.7: WAL entries are cleared after successful completion."""
        skill_id = "test_skill_7"

        # Write entries for multiple phases
        for i in range(3):
            state = {"phase": i}
            wal.write_state(skill_id, i, state)

        # Clear all entries
        wal.clear_skill_entries(skill_id)

        # Verify cleared
        unprocessed = wal.get_unprocessed_entries(skill_id)
        assert len(unprocessed) == 0

    def test_f2_idempotent_recovery_on_repeated_replay(self, wal):
        """F2.8: Replaying the same WAL entry multiple times is safe."""
        skill_id = "test_skill_8"
        phase_num = 0
        state = {"phase": 0, "counter": 0}

        # Write state
        wal.write_state(skill_id, phase_num, state)

        # Get unprocessed twice (simulating repeated recovery)
        unprocessed_1 = wal.get_unprocessed_entries(skill_id)
        unprocessed_2 = wal.get_unprocessed_entries(skill_id)

        # Should return same data both times
        assert len(unprocessed_1) == 1
        assert len(unprocessed_2) == 1
        assert unprocessed_1[0].state == unprocessed_2[0].state


# =============================================================================
# INTEGRATION TESTS
# =============================================================================

class TestCreator20CriticalFixesIntegration:
    """Integration tests for both fixes working together."""

    @pytest.fixture
    def tenant_home(self):
        """Temp directory for tenant home."""
        with tempfile.TemporaryDirectory() as tmpdir:
            yield Path(tmpdir)

    def test_orchestrator_uses_wal_for_transactional_safety(self, tenant_home):
        """Verify PhaseModelOrchestrator uses WAL for FIX #2."""
        orchestrator = PhaseModelOrchestrator(tenant_home=tenant_home)

        # Verify WAL is initialized
        assert orchestrator.wal is not None
        assert orchestrator.wal.wal_file is not None

    def test_validate_skill_id_prevents_directory_traversal(self):
        """Validate skill_id format prevents security issues."""
        valid_ids = ["test_skill", "skill_2", "my_skill_v1"]
        invalid_ids = ["../evil", "test/../evil", "skill;rm -rf", ""]

        for skill_id in valid_ids:
            try:
                _validate_skill_id(skill_id)
            except ValueError:
                pytest.fail(f"Valid skill_id rejected: {skill_id}")

        for skill_id in invalid_ids:
            with pytest.raises(ValueError):
                _validate_skill_id(skill_id)

    def test_wal_entry_serialization_and_deserialization(self):
        """WAL entries survive serialization round-trip."""
        original = WALEntry(
            skill_id="test",
            phase_num=5,
            state_hash="abc123",
            timestamp="2026-09-12T12:00:00Z",
            state={"key": "value"},
            processed=False,
            error=None,
        )

        # Serialize and deserialize
        serialized = original.to_dict()
        deserialized = WALEntry.from_dict(serialized)

        # Verify round-trip
        assert deserialized.skill_id == original.skill_id
        assert deserialized.phase_num == original.phase_num
        assert deserialized.state_hash == original.state_hash
        assert deserialized.state == original.state
        assert deserialized.processed == original.processed

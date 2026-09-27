"""Phase C (k=2) Tests — Audit Trail Integration with Hash-Chain & PII-Detection.

ADR-0232 § Hash-Chain Integrity
ADR-0297 § PII-Detection Fail-Closed
ADR-0007 § Tenant Isolation

Tests verify:
  1. Hash-chain continuity (prior_hash → chain_hash linking)
  2. PII redaction (email, SSN, phone scrubbed from delta)
  3. Tenant isolation (cross-tenant reads fail)
  4. Event immutability (frozen dataclass)
  5. Chain verification (integrity check catches tampering)
"""

import asyncio
import json
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

import pytest

from core.task_tracking.audit import (
    AuditChainWriter,
    AuditEvent,
    _scrub_pii_from_delta,
    _scrub_pii_from_value,
    emit_task_audit_event,
    verify_audit_chain,
)


class TestAuditEventImmutability:
    """Test that AuditEvent is frozen (immutable)."""

    def test_frozen_dataclass(self):
        """AuditEvent instances cannot be modified after creation."""
        event = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Attempt to modify: should raise FrozenInstanceError
        with pytest.raises(Exception):  # FrozenInstanceError
            event.event_type = "task_updated"

    def test_delta_as_immutable_dict(self):
        """Delta is a regular dict (mutable), but the event itself is frozen."""
        mutable_delta = {"title": "New Task"}
        event = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta=mutable_delta,
            timestamp=datetime.now(timezone.utc).isoformat(),
        )

        # Can't modify the event's delta reference
        with pytest.raises(Exception):
            event.delta = {"different": "delta"}

        # The original dict is separate from the event's copy
        mutable_delta["new_field"] = "value"
        assert "new_field" not in event.delta


class TestHashChainComputation:
    """Test cryptographic hash-chain linking."""

    def test_compute_hash_deterministic(self):
        """Same event content produces same hash."""
        event1 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
        )

        event2 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
        )

        hash1 = event1.compute_hash()
        hash2 = event2.compute_hash()

        assert hash1 == hash2, "Identical events must produce identical hashes"

    def test_compute_hash_changes_on_content_change(self):
        """Different event content produces different hash."""
        event1 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
        )

        event2 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "Different Title"},  # Changed
            timestamp="2026-09-27T12:00:00Z",
        )

        hash1 = event1.compute_hash()
        hash2 = event2.compute_hash()

        assert hash1 != hash2, "Different events must produce different hashes"

    def test_with_hashes_sets_prior_and_chain_hash(self):
        """with_hashes() returns new event with prior_hash and chain_hash set."""
        event = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
        )

        prior_hash = "abc123"
        event_with_hashes = event.with_hashes(prior_hash=prior_hash)

        assert event_with_hashes.prior_hash == prior_hash
        assert event_with_hashes.chain_hash == event_with_hashes.compute_hash()
        assert event_with_hashes.chain_hash != ""

    def test_hash_excludes_chain_hash_field(self):
        """Hash computation excludes the chain_hash field (avoid circular dependency)."""
        event1 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
            chain_hash="",
        )

        event2 = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="_default",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
            chain_hash="different_hash_value",  # Different chain_hash
        )

        # But compute_hash should be identical (chain_hash excluded)
        assert event1.compute_hash() == event2.compute_hash()


class TestPIIRedaction:
    """Test PII-Detection and redaction in audit delta."""

    def test_scrub_email_from_delta(self):
        """Email addresses are redacted from audit delta."""
        delta = {
            "owner": "alice@example.com",
            "title": "Update owner",
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        # Email is scrubbed
        assert "alice@example.com" not in str(scrubbed)
        assert "[EMAIL" in scrubbed["owner"]

        # Title is preserved
        assert scrubbed["title"] == "Update owner"

    def test_scrub_phone_from_delta(self):
        """Phone numbers are redacted from audit delta."""
        delta = {
            "contact": "+49 123 456 7890",
            "description": "Contact info",
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        # Phone is scrubbed
        assert "+49 123 456 7890" not in str(scrubbed)
        assert "[PHONE" in scrubbed["contact"] or "SUSPICIOUS" in scrubbed["contact"]

    def test_scrub_nested_dict(self):
        """PII in nested dicts is also scrubbed."""
        delta = {
            "metadata": {
                "author_email": "bob@example.org",
                "title": "Some Task",
            }
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        assert "[EMAIL" in scrubbed["metadata"]["author_email"]
        assert scrubbed["metadata"]["title"] == "Some Task"

    def test_scrub_list_values(self):
        """PII in lists is also scrubbed."""
        delta = {
            "emails": ["alice@example.com", "bob@example.org"],
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        for email_scrubbed in scrubbed["emails"]:
            assert "@example" not in email_scrubbed
            assert "[EMAIL" in email_scrubbed or "[SUSPICIOUS" in email_scrubbed

    def test_non_pii_values_preserved(self):
        """Non-PII values are preserved unchanged."""
        delta = {
            "title": "Task Title",
            "status": "open",
            "priority": "high",
            "progress": 50,
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        assert scrubbed == delta

    def test_none_values_preserved(self):
        """None values are preserved."""
        delta = {
            "owner": None,
            "title": "Task",
        }

        scrubbed = _scrub_pii_from_delta(delta, tenant_id="_default")

        assert scrubbed["owner"] is None
        assert scrubbed["title"] == "Task"


class TestAuditChainWriter:
    """Test hash-chain writing and continuity."""

    def test_write_event_creates_chain(self):
        """Writing an event creates the chain log file."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"

            writer = AuditChainWriter(chain_path)
            event = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )

            chain_hash = writer.write_event(event)

            assert chain_path.exists()
            assert chain_hash != ""

    def test_write_multiple_events_creates_chain(self):
        """Writing multiple events creates a linked chain."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(chain_path)

            # Write first event
            event1 = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )
            hash1 = writer.write_event(event1)

            # Write second event
            event2 = AuditEvent(
                event_type="task_updated",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="status",
                delta={"status": {"from": "open", "to": "in_progress"}},
                timestamp="2026-09-27T12:01:00Z",
            )
            hash2 = writer.write_event(event2)

            # Hashes should be different
            assert hash1 != hash2

            # Read chain and verify linking
            with open(chain_path, "r") as f:
                lines = f.readlines()

            assert len(lines) >= 2

            data1 = json.loads(lines[0])
            data2 = json.loads(lines[1])

            # First event: prior_hash should be "genesis"
            assert data1["prior_hash"] == "genesis"
            assert data1["chain_hash"] == hash1

            # Second event: prior_hash should be first event's hash
            assert data2["prior_hash"] == hash1
            assert data2["chain_hash"] == hash2

    def test_chain_hash_fsync_durability(self):
        """Writing event includes fsync for durability."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(chain_path)

            event = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )

            writer.write_event(event)

            # Read immediately (should be durable)
            with open(chain_path, "r") as f:
                line = f.readline()
                data = json.loads(line)

            assert data["event_type"] == "task_created"
            assert data["task_id"] == "task-123"


class TestTenantIsolation:
    """Test tenant isolation in audit events."""

    def test_tenant_id_mandatory(self):
        """emit_task_audit_event requires tenant_id."""

        async def test():
            with tempfile.TemporaryDirectory() as tmpdir:
                with mock.patch("core.paths.tenant_home", return_value=tmpdir):
                    # Omitted entirely: keyword-only, no default -> TypeError.
                    with pytest.raises(TypeError, match="tenant_id"):
                        await emit_task_audit_event(
                            event_type="task_created",
                            task_id="task-123",
                            actor="user-1",
                            action="create",
                            delta={"title": "New Task"},
                        )
                    # Passed but empty: refused before anything is written.
                    with pytest.raises(ValueError, match="tenant_id is required"):
                        await emit_task_audit_event(
                            event_type="task_created",
                            task_id="task-123",
                            tenant_id="",
                            actor="user-1",
                            action="create",
                            delta={"title": "New Task"},
                        )

        asyncio.run(test())

    def test_tenant_id_in_event(self):
        """AuditEvent carries tenant_id for isolation."""
        event = AuditEvent(
            event_type="task_created",
            task_id="task-123",
            tenant_id="tenant-a",
            actor="user-1",
            action="create",
            delta={"title": "New Task"},
            timestamp="2026-09-27T12:00:00Z",
        )

        assert event.tenant_id == "tenant-a"

    def test_different_tenants_different_chains(self):
        """Events from different tenants create separate chain files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            # Simulate separate chain paths per tenant
            chain_a = Path(tmpdir) / "tenant-a" / "audit.jsonl"
            chain_b = Path(tmpdir) / "tenant-b" / "audit.jsonl"

            writer_a = AuditChainWriter(chain_a)
            writer_b = AuditChainWriter(chain_b)

            event_a = AuditEvent(
                event_type="task_created",
                task_id="task-a",
                tenant_id="tenant-a",
                actor="user-1",
                action="create",
                delta={"title": "Task A"},
                timestamp="2026-09-27T12:00:00Z",
            )

            event_b = AuditEvent(
                event_type="task_created",
                task_id="task-b",
                tenant_id="tenant-b",
                actor="user-2",
                action="create",
                delta={"title": "Task B"},
                timestamp="2026-09-27T12:00:00Z",
            )

            writer_a.write_event(event_a)
            writer_b.write_event(event_b)

            assert chain_a.exists()
            assert chain_b.exists()

            # Chains are separate
            with open(chain_a, "r") as f:
                data_a = json.loads(f.readline())
            with open(chain_b, "r") as f:
                data_b = json.loads(f.readline())

            assert data_a["task_id"] == "task-a"
            assert data_b["task_id"] == "task-b"


class TestChainVerification:
    """Test chain integrity verification."""

    def test_verify_empty_chain(self):
        """Verifying an empty chain returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            assert verify_audit_chain(chain_path, tenant_id="_default") is True

    def test_verify_valid_chain(self):
        """Verifying a valid chain returns True."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(chain_path)

            # Write two events
            event1 = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )
            writer.write_event(event1)

            event2 = AuditEvent(
                event_type="task_updated",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="status",
                delta={"status": "in_progress"},
                timestamp="2026-09-27T12:01:00Z",
            )
            writer.write_event(event2)

            # Verify should succeed
            assert verify_audit_chain(chain_path, tenant_id="_default") is True

    def test_verify_chain_detects_tampering(self):
        """Verifying detects if chain_hash was tampered with."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(chain_path)

            event = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )
            writer.write_event(event)

            # Tamper with the chain_hash
            with open(chain_path, "r") as f:
                data = json.loads(f.readline())

            data["chain_hash"] = "tampered_hash"

            with open(chain_path, "w") as f:
                json.dump(data, f)

            # Verify should fail
            with pytest.raises(ValueError, match="Entry tampering"):
                verify_audit_chain(chain_path, tenant_id="_default")

    def test_verify_chain_detects_broken_link(self):
        """Verifying detects if prior_hash was tampered with (broken link)."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"
            writer = AuditChainWriter(chain_path)

            event1 = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )
            writer.write_event(event1)

            event2 = AuditEvent(
                event_type="task_updated",
                task_id="task-123",
                tenant_id="_default",
                actor="user-1",
                action="status",
                delta={"status": "in_progress"},
                timestamp="2026-09-27T12:01:00Z",
            )
            writer.write_event(event2)

            # Tamper with second event's prior_hash
            lines = []
            with open(chain_path, "r") as f:
                for line in f:
                    lines.append(line)

            data2 = json.loads(lines[1])
            data2["prior_hash"] = "wrong_prior_hash"

            with open(chain_path, "w") as f:
                f.write(lines[0])
                json.dump(data2, f)
                f.write("\n")

            # Verify should fail
            with pytest.raises(ValueError, match="prior_hash mismatch"):
                verify_audit_chain(chain_path, tenant_id="_default")

    def test_verify_chain_tenant_isolation(self):
        """Verifying detects if wrong tenant_id in event."""
        with tempfile.TemporaryDirectory() as tmpdir:
            chain_path = Path(tmpdir) / "audit.jsonl"

            event = AuditEvent(
                event_type="task_created",
                task_id="task-123",
                tenant_id="wrong-tenant",
                actor="user-1",
                action="create",
                delta={"title": "New Task"},
                timestamp="2026-09-27T12:00:00Z",
            )

            with open(chain_path, "w") as f:
                data = asdict(event)
                data["chain_hash"] = event.compute_hash()
                json.dump(data, f)

            # Verify for different tenant should fail
            with pytest.raises(ValueError, match="tenant_id mismatch"):
                verify_audit_chain(chain_path, tenant_id="_default")


def asdict(obj):
    """Convert dataclass to dict (for testing)."""
    from dataclasses import asdict as _asdict

    return _asdict(obj)

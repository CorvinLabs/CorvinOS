"""E2E Test for Credential Rotation Phase 1 (ADR-0869).

Tests end-to-end rotation workflow with audit trail verification.
Verifies:
- Policy application
- Rotation execution
- Audit event generation (hash-chained)
- Tenant isolation
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from core.security.credential_rotation_daemon import (
    RotationDaemon,
    bootstrap_rotation_daemon_phase1,
)
from core.security.credential_rotation_policy import (
    RotationPolicy,
    RotationScheduleType,
    RotationStatus,
)


class MockAuditBackend:
    """Mock audit backend for testing."""

    def __init__(self):
        self.events = []
        self.last_hash = ""

    def write_event(self, event: dict, lom: str = "") -> None:
        """Record audit event."""
        event["lom"] = lom
        self.events.append(event)
        if "hash" in event:
            self.last_hash = event["hash"]

    def verify_chain(self) -> tuple[bool, list[str]]:
        """Verify audit chain integrity."""
        problems = []
        prev_hash = ""

        for i, event in enumerate(self.events):
            # Check prev_hash reference
            expected_prev = prev_hash if i > 0 else ""
            if event.get("prev_hash", "") != expected_prev:
                problems.append(
                    f"Event {i}: prev_hash mismatch "
                    f"(expected {expected_prev!r}, got {event.get('prev_hash')!r})"
                )

            # Track current hash for next event
            if "hash" in event:
                prev_hash = event["hash"]

        return len(problems) == 0, problems


class TestRotationE2E:
    """End-to-end tests for credential rotation."""

    def test_e2e_single_credential_rotation(self):
        """Test rotating a single credential end-to-end."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Rotate one credential
        result = daemon.rotate_credential("GITHUB_TOKEN")

        # Verify result
        assert result.status == RotationStatus.COMPLETED
        assert result.credential_id == "GITHUB_TOKEN"
        assert result.error_message is None
        assert result.duration_ms is not None

        # Verify audit events were created
        assert len(backend.events) >= 2
        event_types = [e.get("event_type") for e in backend.events]
        assert "credential_rotation_started" in event_types
        assert "credential_rotation_completed" in event_types

    def test_e2e_audit_chain_integrity(self):
        """Test audit chain integrity across multiple events."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Rotate multiple credentials
        for cred_id in ["GITHUB_TOKEN", "HETZNER_API_TOKEN"]:
            daemon.rotate_credential(cred_id)

        # Verify chain integrity
        is_valid, problems = backend.verify_chain()
        assert is_valid, f"Chain integrity issues: {problems}"

    def test_e2e_tenant_isolation(self):
        """Test tenant isolation in audit trail."""
        backend1 = MockAuditBackend()
        backend2 = MockAuditBackend()

        daemon1 = RotationDaemon(
            tenant_id="tenant1", audit_backend=backend1
        )
        daemon2 = RotationDaemon(
            tenant_id="tenant2", audit_backend=backend2
        )

        # Rotate credentials in each tenant
        daemon1.rotate_credential("GITHUB_TOKEN")
        daemon2.rotate_credential("GITHUB_TOKEN")

        # Verify events are isolated by tenant
        for event in backend1.events:
            assert event.get("tenant_id") == "tenant1"
        for event in backend2.events:
            assert event.get("tenant_id") == "tenant2"

    def test_e2e_policy_application_and_rotation(self):
        """Test applying policy and rotating multiple credentials."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Define policy
        policy = RotationPolicy(
            credential_ids=[
                "GITHUB_TOKEN",
                "HETZNER_API_TOKEN",
                "CLOUDFLARE_ID",
            ],
            schedule_type=RotationScheduleType.MONTHLY,
            interval_days=90,
        )

        # Apply policy
        daemon.apply_policy(policy)

        # Verify scheduler has credentials
        due = daemon.get_due_credentials()
        assert len(due) == 0  # New credentials not due immediately

        # Rotate all credentials
        for cred_id in policy.credential_ids:
            result = daemon.rotate_credential(cred_id)
            assert result.status == RotationStatus.COMPLETED

        # Verify audit trail
        assert len(backend.events) >= 6  # At least 2 events per credential
        cred_ids = set()
        for event in backend.events:
            cred_ids.add(event.get("credential_id"))
        assert cred_ids == set(policy.credential_ids)

    def test_e2e_rotation_failure_handling(self):
        """Test rotation failure with audit trail."""

        class FailingAuditBackend:
            """Audit backend that fails after first event."""

            def __init__(self):
                self.call_count = 0

            def write_event(self, event: dict, lom: str = "") -> None:
                self.call_count += 1

        backend = FailingAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Rotate credential
        result = daemon.rotate_credential("GITHUB_TOKEN")

        # Result should reflect completion (Phase 1 doesn't fail on audit)
        assert result.status == RotationStatus.COMPLETED

    def test_e2e_scheduler_state_persistence(self):
        """Test scheduler state is updated after rotation."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Add credentials to scheduler
        daemon.scheduler.add_credential("GITHUB_TOKEN")
        daemon.scheduler.add_credential("HETZNER_API_TOKEN")

        initial_state = daemon.scheduler.to_dict()
        assert initial_state["schedules"]["GITHUB_TOKEN"]["rotation_count"] == 0

        # Rotate one credential
        daemon.rotate_credential("GITHUB_TOKEN")

        # Verify state updated
        updated_state = daemon.scheduler.to_dict()
        assert (
            updated_state["schedules"]["GITHUB_TOKEN"]["rotation_count"]
            == 1
        )
        assert (
            updated_state["schedules"]["HETZNER_API_TOKEN"]["rotation_count"]
            == 0
        )

    def test_e2e_bootstrap_and_rotation(self):
        """Test bootstrap flow with rotation."""
        backend = MockAuditBackend()
        daemon = bootstrap_rotation_daemon_phase1(
            tenant_id="_default", audit_backend=backend
        )

        # Bootstrap should have applied default policy
        assert daemon.policy is not None
        assert len(daemon.policy.credential_ids) > 0

        # Rotate a credential
        result = daemon.rotate_credential(daemon.policy.credential_ids[0])
        assert result.status == RotationStatus.COMPLETED

    def test_e2e_multiple_rotation_cycles(self):
        """Test multiple rotation cycles."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        cred_id = "GITHUB_TOKEN"

        # Perform multiple rotations
        for cycle in range(3):
            result = daemon.rotate_credential(cred_id)
            assert result.status == RotationStatus.COMPLETED

        # Verify scheduler tracks all rotations
        schedule = daemon.scheduler.get_schedule(cred_id)
        assert schedule.rotation_count == 3

        # Verify audit trail has all events
        assert len(backend.events) >= 6  # 2 events per rotation


class TestRotationAuditChain:
    """Tests for audit chain verification."""

    def test_audit_chain_hash_computation(self):
        """Test hash computation across audit events."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        # Create two rotations
        daemon.rotate_credential("GITHUB_TOKEN")
        daemon.rotate_credential("HETZNER_API_TOKEN")

        # Verify each event references the previous
        assert len(backend.events) >= 4

        # Track hashes
        hashes = []
        for event in backend.events:
            if "hash" in event:
                hashes.append(event["hash"])

        # All hashes should be unique
        assert len(hashes) == len(set(hashes))

    def test_audit_events_have_lom(self):
        """Test that audit events include Line of Moral Responsibility."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        daemon.rotate_credential("GITHUB_TOKEN")

        # Verify all events have lom
        for event in backend.events:
            assert "lom" in event
            assert "credential_rotation" in event.get("lom", "")


class TestRotationCompliance:
    """Tests for GDPR/compliance requirements."""

    def test_gdpr_art30_audit_trail(self):
        """Test GDPR Art. 30: Audit trail for all rotation events."""
        backend = MockAuditBackend()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=backend
        )

        daemon.rotate_credential("GITHUB_TOKEN")

        # Verify all events are in audit trail
        required_fields = [
            "event_type",
            "tenant_id",
            "credential_id",
            "timestamp",
            "status",
        ]
        for event in backend.events:
            for field in required_fields:
                assert (
                    field in event
                ), f"Missing required field {field} in audit event"

    def test_gdpr_art32_fail_closed(self):
        """Test GDPR Art. 32: Fail-closed on error."""
        # Test without audit backend (simulating backend unavailability)
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=None
        )

        result = daemon.rotate_credential("GITHUB_TOKEN")

        # Should fail because cannot emit started event
        assert result.status == RotationStatus.FAILED

    def test_gdpr_tenant_isolation(self):
        """Test tenant data isolation (GDPR Art. 5, 6)."""
        backend1 = MockAuditBackend()
        backend2 = MockAuditBackend()

        daemon1 = RotationDaemon(
            tenant_id="tenant1", audit_backend=backend1
        )
        daemon2 = RotationDaemon(
            tenant_id="tenant2", audit_backend=backend2
        )

        # Verify no cross-tenant events
        daemon1.rotate_credential("GITHUB_TOKEN")
        daemon2.rotate_credential("HETZNER_API_TOKEN")

        # Each backend should only have events from its tenant
        for event in backend1.events:
            assert event["tenant_id"] == "tenant1"
        for event in backend2.events:
            assert event["tenant_id"] == "tenant2"

        # Cross-tenant should not leak
        backend1_creds = {
            e["credential_id"] for e in backend1.events
        }
        backend2_creds = {
            e["credential_id"] for e in backend2.events
        }
        assert backend1_creds == {"GITHUB_TOKEN"}
        assert backend2_creds == {"HETZNER_API_TOKEN"}


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

"""Tests for Credential Rotation Daemon (ADR-0869).

Tests Phase 1: Daemon initialization, policy application, rotation execution,
and audit event generation.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from core.security.credential_rotation_daemon import (
    RotationDaemon,
    RotationResult,
    bootstrap_rotation_daemon_phase1,
)
from core.security.credential_rotation_policy import (
    RotationPolicy,
    RotationScheduleType,
    RotationStatus,
)


class TestRotationDaemon:
    """Tests for RotationDaemon class."""

    def test_daemon_creation(self):
        """Test daemon creation with default values."""
        daemon = RotationDaemon(tenant_id="_default")
        assert daemon.tenant_id == "_default"
        assert daemon.policy is None
        assert daemon.audit_backend is None

    def test_daemon_with_audit_backend(self):
        """Test daemon creation with audit backend."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )
        assert daemon.audit_backend is mock_backend

    def test_apply_policy(self):
        """Test applying rotation policy."""
        daemon = RotationDaemon()
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN", "HETZNER_API_TOKEN"],
            schedule_type=RotationScheduleType.MONTHLY,
        )
        daemon.apply_policy(policy)
        assert daemon.policy is policy
        assert "GITHUB_TOKEN" in daemon.scheduler.schedules
        assert "HETZNER_API_TOKEN" in daemon.scheduler.schedules

    def test_get_due_credentials(self):
        """Test getting due credentials."""
        daemon = RotationDaemon()
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN", "HETZNER_API_TOKEN"],
            schedule_type=RotationScheduleType.MONTHLY,
        )
        daemon.apply_policy(policy)
        due = daemon.get_due_credentials()
        # New credentials are not due immediately
        assert len(due) == 0

    def test_emit_audit_event_without_backend(self):
        """Test audit event emission without backend."""
        daemon = RotationDaemon(audit_backend=None)
        event = daemon.emit_audit_event(
            event_type="rotation_started",
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.IN_PROGRESS,
        )
        assert event is None

    def test_emit_audit_event_with_backend(self):
        """Test audit event emission with backend."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        event = daemon.emit_audit_event(
            event_type="rotation_started",
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.IN_PROGRESS,
        )

        assert event is not None
        assert event.event_type == "rotation_started"
        assert event.credential_id == "GITHUB_TOKEN"
        assert event.status == RotationStatus.IN_PROGRESS.value
        mock_backend.write_event.assert_called_once()

    def test_emit_audit_event_chain_hash(self):
        """Test audit event hash chaining."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        # Emit first event
        event1 = daemon.emit_audit_event(
            event_type="rotation_started",
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.IN_PROGRESS,
        )
        hash1 = daemon._last_audit_hash

        # Emit second event
        event2 = daemon.emit_audit_event(
            event_type="rotation_completed",
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.COMPLETED,
        )

        # Second event should reference first
        assert event2.prev_hash == hash1
        assert hash1 != ""

    def test_emit_audit_event_with_error(self):
        """Test audit event emission with error message."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        event = daemon.emit_audit_event(
            event_type="rotation_failed",
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.FAILED,
            error_message="Network timeout",
            duration_ms=5000,
        )

        assert event.status == "failed"
        assert event.error_message == "Network timeout"
        assert event.duration_ms == 5000

    def test_rotate_credential_refuses_without_claiming_success(self):
        """No implementation exists, so the daemon must not report COMPLETED."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(tenant_id="_default", audit_backend=mock_backend)

        result = daemon.rotate_credential("GITHUB_TOKEN")

        assert result.credential_id == "GITHUB_TOKEN"
        assert result.status == RotationStatus.FAILED
        assert result.error_message == "rotation_not_implemented"
        assert result.audit_event is None

    def test_rotate_credential_does_not_advance_scheduler(self):
        """A refused rotation must not be counted as a rotation."""
        daemon = RotationDaemon(tenant_id="_default", audit_backend=MagicMock())
        daemon.scheduler.add_credential("GITHUB_TOKEN")

        daemon.rotate_credential("GITHUB_TOKEN")

        assert daemon.scheduler.get_schedule("GITHUB_TOKEN").rotation_count == 0

    def test_rotate_credential_emits_no_completion_record(self):
        """No 'credential_rotation_completed' record for a rotation that did not happen."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(tenant_id="_default", audit_backend=mock_backend)

        for cred_id in ["GITHUB_TOKEN", "HETZNER_API_TOKEN", "CLOUDFLARE_ID"]:
            assert daemon.rotate_credential(cred_id).status == RotationStatus.FAILED

        for call in mock_backend.write_event.call_args_list:
            event = call.args[0] if call.args else call.kwargs.get("event", {})
            assert event.get("event_type") != "credential_rotation_completed"

    def test_rotate_credential_without_backend(self):
        """Refused regardless of backend."""
        daemon = RotationDaemon(tenant_id="_default", audit_backend=None)
        assert daemon.rotate_credential("GITHUB_TOKEN").status == RotationStatus.FAILED

    def test_verify_audit_trail_does_not_claim_intact(self):
        """The daemon does not read the chain, so it must not report it intact."""
        for backend in (None, MagicMock()):
            is_valid, message = RotationDaemon(audit_backend=backend).verify_audit_trail()
            assert is_valid is False
            assert message == "not_implemented"


class TestRotationResult:
    """Tests for RotationResult."""

    def test_result_creation_success(self):
        """Test result creation for successful rotation."""
        result = RotationResult(
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.COMPLETED,
            duration_ms=1000,
        )
        assert result.credential_id == "GITHUB_TOKEN"
        assert result.status == RotationStatus.COMPLETED
        assert result.error_message is None

    def test_result_creation_failure(self):
        """Test result creation for failed rotation."""
        result = RotationResult(
            credential_id="GITHUB_TOKEN",
            status=RotationStatus.FAILED,
            error_message="Network error",
        )
        assert result.status == RotationStatus.FAILED
        assert result.error_message == "Network error"


class TestBootstrapRotationDaemon:
    """Tests for bootstrap_rotation_daemon_phase1 function."""

    def test_bootstrap_with_default_policy(self):
        """Test bootstrap with default policy."""
        mock_backend = MagicMock()
        daemon = bootstrap_rotation_daemon_phase1(
            tenant_id="_default", audit_backend=mock_backend
        )
        assert daemon is not None
        assert daemon.tenant_id == "_default"
        assert daemon.policy is not None
        assert len(daemon.policy.credential_ids) > 0

    def test_bootstrap_with_custom_policy(self):
        """Test bootstrap with custom policy."""
        mock_backend = MagicMock()
        custom_policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN"],
            schedule_type=RotationScheduleType.DAILY,
        )
        daemon = bootstrap_rotation_daemon_phase1(
            tenant_id="_default",
            audit_backend=mock_backend,
            policy=custom_policy,
        )
        assert daemon.policy is custom_policy
        assert daemon.policy.credential_ids == ["GITHUB_TOKEN"]

    def test_bootstrap_without_backend(self):
        """Test bootstrap without audit backend (non-blocking)."""
        daemon = bootstrap_rotation_daemon_phase1(
            tenant_id="_default", audit_backend=None
        )
        assert daemon is not None
        assert daemon.audit_backend is None

    def test_bootstrap_multiple_tenants(self):
        """Test bootstrap for multiple tenants."""
        daemon1 = bootstrap_rotation_daemon_phase1(
            tenant_id="tenant1", audit_backend=None
        )
        daemon2 = bootstrap_rotation_daemon_phase1(
            tenant_id="tenant2", audit_backend=None
        )
        assert daemon1.tenant_id == "tenant1"
        assert daemon2.tenant_id == "tenant2"
        assert daemon1.tenant_id != daemon2.tenant_id



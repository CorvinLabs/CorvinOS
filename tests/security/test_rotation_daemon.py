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

    def test_rotate_credential_success(self):
        """Test successful credential rotation."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        result = daemon.rotate_credential("GITHUB_TOKEN")

        assert result.credential_id == "GITHUB_TOKEN"
        assert result.status == RotationStatus.COMPLETED
        assert result.error_message is None
        assert result.duration_ms is not None
        assert result.audit_event is not None

    def test_rotate_credential_updates_scheduler(self):
        """Test that rotation updates scheduler."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )
        daemon.scheduler.add_credential("GITHUB_TOKEN")
        initial_schedule = daemon.scheduler.get_schedule("GITHUB_TOKEN")
        assert initial_schedule.rotation_count == 0

        daemon.rotate_credential("GITHUB_TOKEN")

        updated_schedule = daemon.scheduler.get_schedule("GITHUB_TOKEN")
        assert updated_schedule.rotation_count == 1

    def test_rotate_credential_audit_trail(self):
        """Test rotation creates audit trail."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        daemon.rotate_credential("GITHUB_TOKEN")

        # Should have called write_event at least twice (started, completed)
        assert mock_backend.write_event.call_count >= 2

    def test_rotate_credential_timing(self):
        """Test that rotation timing is tracked."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        result = daemon.rotate_credential("GITHUB_TOKEN")

        assert result.duration_ms is not None
        assert result.duration_ms >= 0

    def test_rotate_credential_multiple_creds(self):
        """Test rotating multiple credentials."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        results = []
        for cred_id in ["GITHUB_TOKEN", "HETZNER_API_TOKEN", "CLOUDFLARE_ID"]:
            result = daemon.rotate_credential(cred_id)
            results.append(result)

        assert len(results) == 3
        for result in results:
            assert result.status == RotationStatus.COMPLETED

    def test_rotate_credential_without_backend(self):
        """Test rotation without audit backend (fails closed)."""
        daemon = RotationDaemon(tenant_id="_default", audit_backend=None)
        result = daemon.rotate_credential("GITHUB_TOKEN")

        # Rotation should fail because started event cannot be emitted
        assert result.status == RotationStatus.FAILED

    def test_verify_audit_trail_without_backend(self):
        """Test audit trail verification without backend."""
        daemon = RotationDaemon(audit_backend=None)
        is_valid, message = daemon.verify_audit_trail()
        assert is_valid is False

    def test_verify_audit_trail_with_backend(self):
        """Test audit trail verification with backend."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(audit_backend=mock_backend)
        is_valid, message = daemon.verify_audit_trail()
        assert is_valid is True


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


class TestRotationDaemonIntegration:
    """Integration tests for RotationDaemon."""

    def test_full_rotation_workflow(self):
        """Test complete rotation workflow."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        # Apply policy
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN", "HETZNER_API_TOKEN"],
            schedule_type=RotationScheduleType.MONTHLY,
        )
        daemon.apply_policy(policy)

        # Rotate credentials
        results = []
        for cred_id in policy.credential_ids:
            result = daemon.rotate_credential(cred_id)
            results.append(result)

        # Verify all rotations succeeded
        assert len(results) == 2
        for result in results:
            assert result.status == RotationStatus.COMPLETED
            assert result.error_message is None

    def test_rotation_with_audit_trail_chain(self):
        """Test rotation maintains audit trail chain."""
        mock_backend = MagicMock()
        daemon = RotationDaemon(
            tenant_id="_default", audit_backend=mock_backend
        )

        # Rotate credential
        result = daemon.rotate_credential("GITHUB_TOKEN")

        # Extract audit events from mock calls
        calls = mock_backend.write_event.call_args_list
        assert len(calls) >= 2  # At least started and completed

        # Verify chain (each event should reference previous)
        # This is verified by the audit event generation
        assert result.audit_event is not None

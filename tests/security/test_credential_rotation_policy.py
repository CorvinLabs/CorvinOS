"""Tests for Credential Rotation Policy Framework (ADR-0869).

Tests Phase 1: Policy definition, scheduling, and audit trail schema.
"""

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from core.security.credential_rotation_policy import (
    RotationAudit,
    RotationPolicy,
    RotationSchedule,
    RotationScheduleType,
    RotationScheduler,
    RotationStatus,
)


class TestRotationPolicy:
    """Tests for RotationPolicy class."""

    def test_policy_creation(self):
        """Test policy creation with default values."""
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN", "CLOUDFLARE_ID"],
            schedule_type=RotationScheduleType.MONTHLY,
            interval_days=90,
        )
        assert policy.credential_ids == ["GITHUB_TOKEN", "CLOUDFLARE_ID"]
        assert policy.schedule_type == RotationScheduleType.MONTHLY
        assert policy.interval_days == 90
        assert policy.backup_enabled is True
        assert policy.rollback_on_error is True

    def test_policy_immutability(self):
        """Test that policy is immutable (frozen dataclass)."""
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN"],
            schedule_type=RotationScheduleType.MONTHLY,
        )
        with pytest.raises(Exception):  # AttributeError on frozen dataclass
            policy.credential_ids = ["OTHER_TOKEN"]

    def test_policy_to_dict(self):
        """Test policy serialization to dictionary."""
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN"],
            schedule_type=RotationScheduleType.WEEKLY,
            interval_days=7,
            backup_enabled=False,
        )
        policy_dict = policy.to_dict()
        assert policy_dict["credential_ids"] == ["GITHUB_TOKEN"]
        assert policy_dict["schedule_type"] == "weekly"
        assert policy_dict["interval_days"] == 7
        assert policy_dict["backup_enabled"] is False

    def test_policy_from_dict(self):
        """Test policy deserialization from dictionary."""
        data = {
            "credential_ids": ["GITHUB_TOKEN", "HETZNER_API_TOKEN"],
            "schedule_type": "daily",
            "interval_days": 1,
            "backup_enabled": True,
            "max_retries": 5,
        }
        policy = RotationPolicy.from_dict(data)
        assert policy.credential_ids == ["GITHUB_TOKEN", "HETZNER_API_TOKEN"]
        assert policy.schedule_type == RotationScheduleType.DAILY
        assert policy.interval_days == 1
        assert policy.max_retries == 5

    def test_policy_custom_cron(self):
        """Test policy with custom cron schedule."""
        policy = RotationPolicy(
            credential_ids=["GITHUB_TOKEN"],
            schedule_type=RotationScheduleType.CUSTOM_CRON,
            schedule_cron="0 2 * * 1",  # Every Monday at 2 AM
        )
        assert policy.schedule_type == RotationScheduleType.CUSTOM_CRON
        assert policy.schedule_cron == "0 2 * * 1"

    def test_policy_serialization_round_trip(self):
        """Test policy serialize/deserialize round trip."""
        original = RotationPolicy(
            credential_ids=["GITHUB_TOKEN", "PYPI_TOKEN"],
            schedule_type=RotationScheduleType.MONTHLY,
            interval_days=90,
            max_retries=3,
        )
        policy_dict = original.to_dict()
        restored = RotationPolicy.from_dict(policy_dict)
        assert restored.credential_ids == original.credential_ids
        assert restored.schedule_type == original.schedule_type
        assert restored.interval_days == original.interval_days
        assert restored.max_retries == original.max_retries


class TestRotationSchedule:
    """Tests for RotationSchedule class."""

    def test_schedule_creation(self):
        """Test schedule record creation."""
        schedule = RotationSchedule(
            credential_id="GITHUB_TOKEN",
            last_rotation_utc="2026-09-19T12:00:00Z",
        )
        assert schedule.credential_id == "GITHUB_TOKEN"
        assert schedule.last_rotation_utc == "2026-09-19T12:00:00Z"
        assert schedule.rotation_count == 0

    def test_schedule_to_dict(self):
        """Test schedule serialization."""
        schedule = RotationSchedule(
            credential_id="GITHUB_TOKEN",
            rotation_count=3,
            error_count=1,
        )
        schedule_dict = schedule.to_dict()
        assert schedule_dict["credential_id"] == "GITHUB_TOKEN"
        assert schedule_dict["rotation_count"] == 3
        assert schedule_dict["error_count"] == 1

    def test_schedule_from_dict(self):
        """Test schedule deserialization."""
        data = {
            "credential_id": "GITHUB_TOKEN",
            "last_rotation_utc": "2026-09-19T12:00:00Z",
            "rotation_count": 5,
            "error_count": 0,
        }
        schedule = RotationSchedule.from_dict(data)
        assert schedule.credential_id == "GITHUB_TOKEN"
        assert schedule.rotation_count == 5
        assert schedule.error_count == 0


class TestRotationAudit:
    """Tests for RotationAudit immutable event."""

    def test_audit_creation(self):
        """Test audit event creation."""
        event = RotationAudit(
            event_type="rotation_started",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="in_progress",
            prev_hash="abc123",
        )
        assert event.event_type == "rotation_started"
        assert event.credential_id == "GITHUB_TOKEN"
        assert event.status == "in_progress"

    def test_audit_hash_computation(self):
        """Test audit event hash computation."""
        event = RotationAudit(
            event_type="rotation_started",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="in_progress",
            prev_hash="abc123",
        )
        hash1 = event.compute_hash()
        hash2 = event.compute_hash()
        assert hash1 == hash2  # Hash is deterministic
        assert len(hash1) == 64  # SHA256 hex digest

    def test_audit_different_events_different_hash(self):
        """Test that different events produce different hashes."""
        event1 = RotationAudit(
            event_type="rotation_started",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="in_progress",
            prev_hash="abc123",
        )
        event2 = RotationAudit(
            event_type="rotation_completed",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="completed",
            prev_hash="abc123",
        )
        assert event1.compute_hash() != event2.compute_hash()

    def test_audit_hash_chain(self):
        """Test hash-chain integrity."""
        # Create first event
        event1 = RotationAudit(
            event_type="rotation_started",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="in_progress",
            prev_hash="",  # First event has no previous
        )
        hash1 = event1.compute_hash()

        # Create second event, referencing first
        event2 = RotationAudit(
            event_type="rotation_completed",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:05:00Z",
            status="completed",
            prev_hash=hash1,  # Chain to first event
        )

        # Chain is verified by prev_hash reference
        assert event2.prev_hash == hash1
        assert event1.prev_hash == ""

    def test_audit_to_dict(self):
        """Test audit serialization."""
        event = RotationAudit(
            event_type="rotation_failed",
            tenant_id="_default",
            credential_id="GITHUB_TOKEN",
            timestamp="2026-09-19T12:00:00Z",
            status="failed",
            prev_hash="abc123",
            error_message="Network timeout",
            duration_ms=5000,
        )
        event_dict = event.to_dict()
        assert event_dict["event_type"] == "rotation_failed"
        assert event_dict["error_message"] == "Network timeout"
        assert event_dict["duration_ms"] == 5000


class TestRotationScheduler:
    """Tests for RotationScheduler."""

    def test_scheduler_creation(self):
        """Test scheduler creation."""
        scheduler = RotationScheduler(
            tenant_id="_default", interval_days=90
        )
        assert scheduler.tenant_id == "_default"
        assert scheduler.interval_days == 90
        assert len(scheduler.schedules) == 0

    def test_add_credential(self):
        """Test adding credential to schedule."""
        scheduler = RotationScheduler()
        scheduler.add_credential("GITHUB_TOKEN")
        assert "GITHUB_TOKEN" in scheduler.schedules
        schedule = scheduler.schedules["GITHUB_TOKEN"]
        assert schedule.credential_id == "GITHUB_TOKEN"
        assert schedule.rotation_count == 0

    def test_add_multiple_credentials(self):
        """Test adding multiple credentials."""
        scheduler = RotationScheduler()
        scheduler.add_credential("GITHUB_TOKEN")
        scheduler.add_credential("HETZNER_API_TOKEN")
        scheduler.add_credential("CLOUDFLARE_ID")
        assert len(scheduler.schedules) == 3

    def test_is_due_for_rotation_new_credential(self):
        """Test rotation due check for new credential."""
        scheduler = RotationScheduler(interval_days=90)
        scheduler.add_credential("GITHUB_TOKEN")
        # New credential should not be due immediately
        assert not scheduler.is_due_for_rotation("GITHUB_TOKEN")

    def test_is_due_for_rotation_overdue(self):
        """Test rotation due check for overdue credential."""
        scheduler = RotationScheduler(interval_days=1)
        scheduler.add_credential("GITHUB_TOKEN")
        # Manually set next rotation to past
        old_schedule = scheduler.schedules["GITHUB_TOKEN"]
        past_time = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        scheduler.schedules["GITHUB_TOKEN"] = RotationSchedule(
            credential_id="GITHUB_TOKEN",
            last_rotation_utc=None,
            next_rotation_due_utc=past_time,
        )
        assert scheduler.is_due_for_rotation("GITHUB_TOKEN")

    def test_mark_rotated(self):
        """Test marking credential as rotated."""
        scheduler = RotationScheduler(interval_days=90)
        scheduler.add_credential("GITHUB_TOKEN")
        initial_schedule = scheduler.schedules["GITHUB_TOKEN"]
        assert initial_schedule.rotation_count == 0

        scheduler.mark_rotated("GITHUB_TOKEN")
        updated_schedule = scheduler.schedules["GITHUB_TOKEN"]
        assert updated_schedule.rotation_count == 1
        assert updated_schedule.last_rotation_utc is not None

    def test_mark_error(self):
        """Test marking rotation as failed."""
        scheduler = RotationScheduler()
        scheduler.add_credential("GITHUB_TOKEN")
        initial_schedule = scheduler.schedules["GITHUB_TOKEN"]
        assert initial_schedule.error_count == 0

        scheduler.mark_error("GITHUB_TOKEN")
        updated_schedule = scheduler.schedules["GITHUB_TOKEN"]
        assert updated_schedule.error_count == 1

    def test_get_due_credentials(self):
        """Test getting list of due credentials."""
        scheduler = RotationScheduler(interval_days=1)
        scheduler.add_credential("GITHUB_TOKEN")
        scheduler.add_credential("HETZNER_API_TOKEN")

        # Manually mark one as overdue
        past_time = (
            datetime.now(timezone.utc) - timedelta(days=1)
        ).isoformat()
        scheduler.schedules["GITHUB_TOKEN"] = RotationSchedule(
            credential_id="GITHUB_TOKEN",
            next_rotation_due_utc=past_time,
        )

        due = scheduler.get_due_credentials()
        assert "GITHUB_TOKEN" in due
        assert "HETZNER_API_TOKEN" not in due

    def test_scheduler_to_dict(self):
        """Test scheduler serialization."""
        scheduler = RotationScheduler(
            tenant_id="_default", interval_days=90
        )
        scheduler.add_credential("GITHUB_TOKEN")
        scheduler.mark_rotated("GITHUB_TOKEN")

        scheduler_dict = scheduler.to_dict()
        assert scheduler_dict["tenant_id"] == "_default"
        assert scheduler_dict["interval_days"] == 90
        assert "GITHUB_TOKEN" in scheduler_dict["schedules"]

    def test_scheduler_from_dict(self):
        """Test scheduler deserialization."""
        data = {
            "tenant_id": "_default",
            "interval_days": 90,
            "schedules": {
                "GITHUB_TOKEN": {
                    "credential_id": "GITHUB_TOKEN",
                    "rotation_count": 5,
                    "error_count": 0,
                }
            },
        }
        scheduler = RotationScheduler.from_dict(data)
        assert scheduler.tenant_id == "_default"
        assert scheduler.interval_days == 90
        assert "GITHUB_TOKEN" in scheduler.schedules
        assert scheduler.schedules["GITHUB_TOKEN"].rotation_count == 5

    def test_scheduler_serialization_round_trip(self):
        """Test scheduler serialize/deserialize round trip."""
        scheduler1 = RotationScheduler(
            tenant_id="_default", interval_days=90
        )
        scheduler1.add_credential("GITHUB_TOKEN")
        scheduler1.add_credential("HETZNER_API_TOKEN")
        scheduler1.mark_rotated("GITHUB_TOKEN")

        scheduler_dict = scheduler1.to_dict()
        scheduler2 = RotationScheduler.from_dict(scheduler_dict)

        assert scheduler2.tenant_id == scheduler1.tenant_id
        assert scheduler2.interval_days == scheduler1.interval_days
        assert len(scheduler2.schedules) == len(scheduler1.schedules)
        assert (
            scheduler2.schedules["GITHUB_TOKEN"].rotation_count
            == scheduler1.schedules["GITHUB_TOKEN"].rotation_count
        )

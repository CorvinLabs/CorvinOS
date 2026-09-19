"""Credential Rotation Policy Framework — Phase 1 (ADR-0869).

Provides policy management for credential rotation:
- RotationPolicy: defines which credentials rotate, schedule, hooks
- RotationScheduler: manages rotation timing
- RotationAudit: immutable audit events for compliance

Compliance:
  - GDPR Art. 30: Audit trail for all rotation events (hash-chained)
  - GDPR Art. 32: Fail-closed on error (never partial rotation)
  - Tenant-scoped: isolation per tenant
"""

from __future__ import annotations

import hashlib
import json
import logging
import sys
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

_LOM_FILE = "core/security/credential_rotation_policy.py"


def _lom(func_name: str) -> str:
    """Line of Moral Responsibility for the CALLER's current line (ADR-0537)."""
    return f"{_LOM_FILE}:{func_name}:L{sys._getframe(1).f_lineno}"


class RotationScheduleType(Enum):
    """Rotation schedule type."""

    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    CUSTOM_CRON = "custom_cron"


class RotationStatus(Enum):
    """Rotation execution status."""

    PENDING = "pending"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class RotationPolicy:
    """Immutable rotation policy definition.

    Defines which credentials rotate, on what schedule, and what hooks to run.
    """

    credential_ids: List[str]  # Credentials managed by this policy
    schedule_type: RotationScheduleType  # Schedule type (daily/weekly/monthly/custom)
    schedule_cron: Optional[str] = None  # Cron expression (if CUSTOM_CRON)
    interval_days: int = 90  # Rotation interval in days
    pre_rotation_hook: Optional[Callable[[], bool]] = None  # Hook before rotation
    post_rotation_hook: Optional[Callable[[], bool]] = None  # Hook after rotation
    backup_enabled: bool = True  # Create backup before rotation
    rollback_on_error: bool = True  # Rollback to backup on error
    max_retries: int = 3  # Max retry attempts
    retry_delay_seconds: int = 60  # Delay between retries

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (without callables)."""
        return {
            "credential_ids": self.credential_ids,
            "schedule_type": self.schedule_type.value,
            "schedule_cron": self.schedule_cron,
            "interval_days": self.interval_days,
            "backup_enabled": self.backup_enabled,
            "rollback_on_error": self.rollback_on_error,
            "max_retries": self.max_retries,
            "retry_delay_seconds": self.retry_delay_seconds,
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> RotationPolicy:
        """Create policy from dictionary."""
        return RotationPolicy(
            credential_ids=data.get("credential_ids", []),
            schedule_type=RotationScheduleType(data.get("schedule_type", "monthly")),
            schedule_cron=data.get("schedule_cron"),
            interval_days=data.get("interval_days", 90),
            backup_enabled=data.get("backup_enabled", True),
            rollback_on_error=data.get("rollback_on_error", True),
            max_retries=data.get("max_retries", 3),
            retry_delay_seconds=data.get("retry_delay_seconds", 60),
        )


@dataclass(frozen=True)
class RotationSchedule:
    """Immutable rotation schedule record.

    Tracks when each credential was last rotated and when next rotation is due.
    """

    credential_id: str
    last_rotation_utc: Optional[str] = None  # ISO timestamp of last rotation
    next_rotation_due_utc: Optional[str] = None  # ISO timestamp when next rotation is due
    rotation_count: int = 0  # How many times rotated
    error_count: int = 0  # How many failures

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        return asdict(self)

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> RotationSchedule:
        """Create from dictionary."""
        return RotationSchedule(
            credential_id=data.get("credential_id", ""),
            last_rotation_utc=data.get("last_rotation_utc"),
            next_rotation_due_utc=data.get("next_rotation_due_utc"),
            rotation_count=data.get("rotation_count", 0),
            error_count=data.get("error_count", 0),
        )


@dataclass(frozen=True)
class RotationAudit:
    """Immutable audit event for credential rotation (hash-chained)."""

    event_type: str  # "rotation_policy_created", "rotation_started", etc.
    tenant_id: str
    credential_id: str
    timestamp: str  # ISO timestamp
    status: str  # "pending", "in_progress", "completed", "failed"
    prev_hash: str  # Hash of previous event (for chain)
    hash: str = ""  # SHA256 hash of this event
    lom: str = ""  # Line of Moral Responsibility
    error_message: Optional[str] = None
    duration_ms: Optional[int] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary (JSON-serializable)."""
        return {
            "event_type": self.event_type,
            "tenant_id": self.tenant_id,
            "credential_id": self.credential_id,
            "timestamp": self.timestamp,
            "status": self.status,
            "prev_hash": self.prev_hash,
            "hash": self.hash,
            "lom": self.lom,
            "error_message": self.error_message,
            "duration_ms": self.duration_ms,
        }

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event (for chain integrity)."""
        payload = json.dumps(
            {
                "event_type": self.event_type,
                "tenant_id": self.tenant_id,
                "credential_id": self.credential_id,
                "timestamp": self.timestamp,
                "status": self.status,
                "prev_hash": self.prev_hash,
                "error_message": self.error_message,
                "duration_ms": self.duration_ms,
            },
            sort_keys=True,
            default=str,
        )
        return hashlib.sha256(payload.encode()).hexdigest()


class RotationScheduler:
    """Manages rotation scheduling for a set of credentials.

    Tracks when each credential should be rotated and enforces interval
    constraints.
    """

    def __init__(self, tenant_id: str = "_default", interval_days: int = 90):
        """Initialize scheduler.

        Args:
            tenant_id: Tenant scope
            interval_days: Default interval between rotations
        """
        self.tenant_id = tenant_id
        self.interval_days = interval_days
        self.schedules: Dict[str, RotationSchedule] = {}

    def add_credential(self, credential_id: str) -> None:
        """Add credential to schedule.

        Args:
            credential_id: Credential identifier
        """
        if credential_id not in self.schedules:
            now = datetime.now(timezone.utc).isoformat()
            next_due = (
                datetime.now(timezone.utc) + timedelta(days=self.interval_days)
            ).isoformat()
            self.schedules[credential_id] = RotationSchedule(
                credential_id=credential_id,
                last_rotation_utc=None,
                next_rotation_due_utc=next_due,
                rotation_count=0,
                error_count=0,
            )
            logger.info(f"Added {credential_id} to rotation schedule")

    def is_due_for_rotation(self, credential_id: str) -> bool:
        """Check if credential is due for rotation.

        Args:
            credential_id: Credential identifier

        Returns:
            True if rotation is due, False otherwise
        """
        if credential_id not in self.schedules:
            return False

        schedule = self.schedules[credential_id]
        if schedule.next_rotation_due_utc is None:
            return False

        now_utc = datetime.now(timezone.utc).isoformat()
        return schedule.next_rotation_due_utc <= now_utc

    def mark_rotated(self, credential_id: str) -> None:
        """Mark credential as rotated (update schedule).

        Args:
            credential_id: Credential identifier
        """
        if credential_id not in self.schedules:
            self.add_credential(credential_id)

        old_schedule = self.schedules[credential_id]
        now = datetime.now(timezone.utc).isoformat()
        next_due = (
            datetime.now(timezone.utc) + timedelta(days=self.interval_days)
        ).isoformat()

        self.schedules[credential_id] = RotationSchedule(
            credential_id=credential_id,
            last_rotation_utc=now,
            next_rotation_due_utc=next_due,
            rotation_count=old_schedule.rotation_count + 1,
            error_count=old_schedule.error_count,
        )
        logger.info(
            f"Marked {credential_id} as rotated "
            f"(count={self.schedules[credential_id].rotation_count})"
        )

    def mark_error(self, credential_id: str) -> None:
        """Mark credential rotation as failed (update error count).

        Args:
            credential_id: Credential identifier
        """
        if credential_id not in self.schedules:
            self.add_credential(credential_id)

        old_schedule = self.schedules[credential_id]
        self.schedules[credential_id] = RotationSchedule(
            credential_id=credential_id,
            last_rotation_utc=old_schedule.last_rotation_utc,
            next_rotation_due_utc=old_schedule.next_rotation_due_utc,
            rotation_count=old_schedule.rotation_count,
            error_count=old_schedule.error_count + 1,
        )
        logger.warning(
            f"Marked {credential_id} rotation as failed "
            f"(error_count={self.schedules[credential_id].error_count})"
        )

    def get_schedule(self, credential_id: str) -> Optional[RotationSchedule]:
        """Get rotation schedule for a credential.

        Args:
            credential_id: Credential identifier

        Returns:
            RotationSchedule if found, None otherwise
        """
        return self.schedules.get(credential_id)

    def get_due_credentials(self) -> List[str]:
        """Get list of credentials due for rotation.

        Returns:
            List of credential IDs due for rotation
        """
        return [
            cred_id
            for cred_id in self.schedules
            if self.is_due_for_rotation(cred_id)
        ]

    def to_dict(self) -> Dict[str, Any]:
        """Export schedule state to dictionary.

        Returns:
            Dictionary representation of all schedules
        """
        return {
            "tenant_id": self.tenant_id,
            "interval_days": self.interval_days,
            "schedules": {
                cred_id: schedule.to_dict()
                for cred_id, schedule in self.schedules.items()
            },
        }

    @staticmethod
    def from_dict(data: Dict[str, Any]) -> RotationScheduler:
        """Load scheduler state from dictionary.

        Args:
            data: Dictionary representation

        Returns:
            RotationScheduler instance with loaded state
        """
        scheduler = RotationScheduler(
            tenant_id=data.get("tenant_id", "_default"),
            interval_days=data.get("interval_days", 90),
        )
        for cred_id, schedule_data in data.get("schedules", {}).items():
            scheduler.schedules[cred_id] = RotationSchedule.from_dict(schedule_data)
        return scheduler

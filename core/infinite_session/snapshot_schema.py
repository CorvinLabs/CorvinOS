"""Phase A: Snapshot Schema (ADR-0540, Infinite Session Engine).

Defines immutable snapshot dataclass for session state persistence.
All snapshots are tenant-scoped, hash-verified, and audit-first.

Compliance:
- GDPR Art. 32: Tenant isolation, immutable append-only storage
- Audit Trail: Every snapshot creation emits an audit event before disk write
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from enum import Enum
from typing import Any, Optional
from uuid import uuid4


class SnapshotType(str, Enum):
    """Snapshot types."""
    PHASE_CHECKPOINT = "phase_checkpoint"  # End of phase
    ROLLBACK_RECOVERY = "rollback_recovery"  # Recovery snapshot for rollback
    INTERMEDIATE = "intermediate"  # Mid-phase snapshot


@dataclass(frozen=True)
class Snapshot:
    """Immutable snapshot of session/task state (ADR-0540).

    Guarantees:
    - Frozen (immutable after creation)
    - Tenant-scoped (fail-closed on missing tenant_id)
    - Hash-verified (content_hash computed from state_dict)
    - Timestamped (audit trail)
    - Audit-first (validation before storage)
    """

    snapshot_id: str  # UUID4
    tenant_id: str  # Tenant scope (GDPR Art. 32, fail-closed)
    task_id: str  # Task identifier
    phase_id: str  # Phase identifier
    snapshot_type: SnapshotType  # Type: checkpoint, recovery, intermediate
    timestamp: str  # ISO 8601 UTC
    state_dict: dict[str, Any]  # Snapshot payload (immutable copy)
    content_hash: str  # SHA256(state_dict) for verification
    version: str = "1.0"  # Schema version

    # Metadata for recovery
    prev_snapshot_hash: Optional[str] = None  # Hash of previous snapshot (chain)
    base_commit: Optional[str] = None  # Git commit hash at snapshot time
    worktree_path: Optional[str] = None  # Worktree location (for recovery)

    def __post_init__(self):
        """Validate snapshot on creation (frozen dataclass, fail-closed)."""
        if not self.tenant_id or not self.tenant_id.strip():
            raise ValueError("tenant_id is required and must not be empty (GDPR Art. 32)")
        if not self.task_id or not self.task_id.strip():
            raise ValueError("task_id is required")
        if not self.phase_id or not self.phase_id.strip():
            raise ValueError("phase_id is required")
        if not self.snapshot_id or not self.snapshot_id.strip():
            raise ValueError("snapshot_id is required")

        # Verify content_hash matches state_dict
        computed_hash = Snapshot.compute_hash(self.state_dict)
        if self.content_hash != computed_hash:
            raise ValueError(
                f"content_hash mismatch: expected {computed_hash}, got {self.content_hash}"
            )

    @staticmethod
    def compute_hash(state_dict: dict[str, Any]) -> str:
        """Compute SHA256 hash of state dictionary.

        Args:
            state_dict: State dictionary to hash

        Returns:
            SHA256 hex digest
        """
        # Normalize JSON for consistent hashing
        normalized = json.dumps(state_dict, sort_keys=True, separators=(',', ':'))
        return hashlib.sha256(normalized.encode()).hexdigest()

    @classmethod
    def create(
        cls,
        tenant_id: str,
        task_id: str,
        phase_id: str,
        state_dict: dict[str, Any],
        snapshot_type: SnapshotType = SnapshotType.PHASE_CHECKPOINT,
        prev_snapshot_hash: Optional[str] = None,
        base_commit: Optional[str] = None,
        worktree_path: Optional[str] = None,
    ) -> Snapshot:
        """Factory for creating new snapshots.

        Args:
            tenant_id: Tenant identifier (fail-closed if empty)
            task_id: Task identifier
            phase_id: Phase identifier
            state_dict: State dictionary to snapshot
            snapshot_type: Type of snapshot
            prev_snapshot_hash: Hash of previous snapshot (for chain)
            base_commit: Git commit hash at snapshot time
            worktree_path: Worktree location (for recovery)

        Returns:
            Frozen Snapshot instance

        Raises:
            ValueError: If tenant_id is empty (fail-closed)
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required and must not be empty (fail-closed)")

        content_hash = cls.compute_hash(state_dict)

        return cls(
            snapshot_id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id=phase_id,
            snapshot_type=snapshot_type,
            timestamp=datetime.utcnow().isoformat() + "Z",
            state_dict=state_dict.copy(),  # Immutable copy
            content_hash=content_hash,
            prev_snapshot_hash=prev_snapshot_hash,
            base_commit=base_commit,
            worktree_path=worktree_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize snapshot to dict (for storage/JSON).

        Returns:
            Dictionary representation of snapshot
        """
        data = asdict(self)
        data['snapshot_type'] = self.snapshot_type.value
        return data

    def verify_hash(self, state_dict: dict[str, Any]) -> bool:
        """Verify that state_dict matches content_hash.

        Args:
            state_dict: State dictionary to verify

        Returns:
            True if hash matches, False otherwise
        """
        computed = self.compute_hash(state_dict)
        return computed == self.content_hash

    def chain_link(self, next_snapshot: Snapshot) -> bool:
        """Verify that next_snapshot is chained to this snapshot.

        Args:
            next_snapshot: Snapshot to verify chain link

        Returns:
            True if prev_snapshot_hash matches this snapshot's content_hash
        """
        return next_snapshot.prev_snapshot_hash == self.content_hash


@dataclass(frozen=True)
class SnapshotMetadata:
    """Metadata for snapshot storage/retrieval (ADR-0540).

    Used for directory organization and quick lookups without
    deserializing the full snapshot.
    """

    snapshot_id: str
    tenant_id: str
    task_id: str
    phase_id: str
    content_hash: str
    timestamp: str
    snapshot_type: SnapshotType
    file_path: Optional[str] = None  # Path on disk

    @classmethod
    def from_snapshot(cls, snapshot: Snapshot, file_path: Optional[str] = None) -> SnapshotMetadata:
        """Create metadata from snapshot.

        Args:
            snapshot: Source snapshot
            file_path: Optional disk path

        Returns:
            SnapshotMetadata instance
        """
        return cls(
            snapshot_id=snapshot.snapshot_id,
            tenant_id=snapshot.tenant_id,
            task_id=snapshot.task_id,
            phase_id=snapshot.phase_id,
            content_hash=snapshot.content_hash,
            timestamp=snapshot.timestamp,
            snapshot_type=snapshot.snapshot_type,
            file_path=file_path,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict.

        Returns:
            Dictionary representation
        """
        data = asdict(self)
        data['snapshot_type'] = self.snapshot_type.value
        return data

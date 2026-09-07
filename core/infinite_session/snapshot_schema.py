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
import re
from dataclasses import dataclass, asdict, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional
from uuid import uuid4

from core.infinite_session.paths import validate_id
from core.tenants import validate_tenant_id


def utc_now_iso() -> str:
    """ISO-8601 UTC timestamp with microseconds and a ``Z`` suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


def _detect_pii_risk(value: Any) -> bool:
    """Detect if a value contains potential PII (email, SSN, phone, etc.).

    Args:
        value: Value to check

    Returns:
        True if PII-like patterns detected, False otherwise
    """
    if not isinstance(value, str):
        return False

    # Check for common PII patterns
    pii_patterns = [
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',  # Email
        r'\b\d{3}-\d{2}-\d{4}\b',  # SSN
        r'\b\d{10}\b',  # Phone (10 digits)
        r'\b\d{3}[-\s]?\d{3}[-\s]?\d{4}\b',  # Phone (various formats)
        r'\b4[0-9]{12}(?:[0-9]{3})?\b',  # Credit card (Visa)
        r'\b5[1-5][0-9]{14}\b',  # Credit card (Mastercard)
    ]

    for pattern in pii_patterns:
        if re.search(pattern, value):
            return True
    return False


def scrub_pii_from_text(text: str) -> str:
    """Scrub PII patterns from text (for logging).

    Args:
        text: Text to scrub

    Returns:
        Text with PII patterns replaced by placeholders
    """
    if not isinstance(text, str):
        return str(text)

    scrubbed = text
    # Replace emails
    scrubbed = re.sub(
        r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b',
        '<EMAIL>',
        scrubbed
    )
    # Replace SSNs
    scrubbed = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '<SSN>', scrubbed)
    # Replace phone numbers
    scrubbed = re.sub(r'\b\d{10}\b', '<PHONE>', scrubbed)
    scrubbed = re.sub(r'\b\d{3}[-\s]?\d{3}[-\s]?\d{4}\b', '<PHONE>', scrubbed)
    # Replace credit cards
    scrubbed = re.sub(r'\b4[0-9]{12}(?:[0-9]{3})?\b', '<CREDITCARD>', scrubbed)
    scrubbed = re.sub(r'\b5[1-5][0-9]{14}\b', '<CREDITCARD>', scrubbed)

    return scrubbed


def _check_dict_for_pii(state_dict: dict[str, Any], max_depth: int = 10) -> bool:
    """Recursively check if a dictionary contains PII.

    Fail-closed: exhausting ``max_depth`` counts as PII (a deeper structure
    cannot be vouched for), dict KEYS are scanned like values, and lists nest
    (round-2 review, R2-B3).

    Returns:
        True if PII detected (or the structure could not be fully scanned).
    """
    if max_depth <= 0:
        return True

    def _scan(value: Any, depth: int) -> bool:
        if depth <= 0:
            return True
        if isinstance(value, dict):
            for k, v in value.items():
                if _detect_pii_risk(k) or _scan(v, depth - 1):
                    return True
            return False
        if isinstance(value, (list, tuple, set)):
            return any(_scan(item, depth - 1) for item in value)
        return bool(_detect_pii_risk(value))

    return _scan(state_dict, max_depth)


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
    - Size-bounded (max 50MB to prevent DoS)
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

    # Keyed chain MAC over EVERY other field (R4-F2). ``content_hash`` covers
    # only ``state_dict``, so until 2026-09-07 the LINK fields —
    # ``prev_snapshot_hash``, ``snapshot_id``, ``task_id``, ``tenant_id``,
    # ``timestamp`` — were freely rewritable: a middle snapshot could be
    # deleted and its successor re-pointed at its predecessor, and
    # ``verify_snapshot_chain`` still returned ``(True, "")`` with ``/health``
    # reporting healthy. This is the same defence ``rollback_manager`` already
    # applies to its transaction log: HMAC-SHA256 under the per-tenant
    # ``CryptoBinding`` key, so an attacker who can edit the file cannot
    # re-sign it. Assigned by :meth:`EventStore.write_snapshot`; ``None`` means
    # UNSIGNED (a chain written before this scheme) and never verifies.
    chain_mac: Optional[str] = None

    # Size limit constants
    MAX_SNAPSHOT_SIZE_BYTES = 50 * 1024 * 1024  # 50MB

    def __post_init__(self):
        """Validate snapshot on creation (frozen dataclass, fail-closed)."""
        if not self.tenant_id or not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id is required and must not be empty (GDPR Art. 32)")
        validate_tenant_id(self.tenant_id)
        validate_id(self.task_id, "task_id")
        validate_id(self.phase_id, "phase_id")
        validate_id(self.snapshot_id, "snapshot_id")
        if not isinstance(self.state_dict, dict):
            raise ValueError("state_dict must be a dict")

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
            task_id: Task identifier (must not contain path traversal)
            phase_id: Phase identifier (must not contain path traversal)
            state_dict: State dictionary to snapshot
            snapshot_type: Type of snapshot
            prev_snapshot_hash: Hash of previous snapshot (for chain)
            base_commit: Git commit hash at snapshot time
            worktree_path: Worktree location (for recovery)

        Returns:
            Frozen Snapshot instance

        Raises:
            ValueError: If tenant_id is empty, or identifiers contain path traversal (fail-closed)
        """
        if not tenant_id or not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id is required and must not be empty (fail-closed)")
        validate_tenant_id(tenant_id)
        validate_id(task_id, "task_id")
        validate_id(phase_id, "phase_id")
        if not isinstance(state_dict, dict):
            raise ValueError("state_dict must be a dict")

        # Check for PII in state_dict (GDPR Art. 5 - data minimization)
        if _check_dict_for_pii(state_dict):
            raise ValueError(
                "state_dict contains potential PII (email, SSN, phone, credit card). "
                "Scrub sensitive data before snapshotting (GDPR Art. 5, fail-closed)"
            )

        # Check snapshot size (prevent DoS from oversized snapshots)
        serialized = json.dumps(state_dict, sort_keys=True, separators=(',', ':'))
        size_bytes = len(serialized.encode())
        if size_bytes > cls.MAX_SNAPSHOT_SIZE_BYTES:
            raise ValueError(
                f"Snapshot size {size_bytes} bytes exceeds maximum {cls.MAX_SNAPSHOT_SIZE_BYTES} "
                f"(50MB limit, fail-closed)"
            )

        content_hash = cls.compute_hash(state_dict)

        return cls(
            snapshot_id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            phase_id=phase_id,
            snapshot_type=snapshot_type,
            timestamp=utc_now_iso(),
            state_dict=json.loads(serialized),  # Deep, JSON-clean copy
            content_hash=content_hash,
            prev_snapshot_hash=prev_snapshot_hash,
            base_commit=base_commit,
            worktree_path=worktree_path,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Snapshot:
        """Reconstruct a snapshot from its stored dict (re-validates the hash)."""
        payload = dict(data)
        payload["snapshot_type"] = SnapshotType(payload["snapshot_type"])
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        """Serialize snapshot to dict (for storage/JSON).

        Returns:
            Dictionary representation of snapshot
        """
        data = asdict(self)
        data['snapshot_type'] = self.snapshot_type.value
        return data

    def mac_payload(self) -> dict[str, Any]:
        """The bytes the :attr:`chain_mac` commits to: every field but itself.

        Deliberately the whole record — identity (``snapshot_id``,
        ``tenant_id``, ``task_id``), position (``prev_snapshot_hash``,
        ``timestamp``) and content (``state_dict``, ``content_hash``) — so no
        part of a stored snapshot can be edited without invalidating the MAC.
        """
        data = self.to_dict()
        data.pop("chain_mac", None)
        return data

    def signed(self, chain_mac: str) -> "Snapshot":
        """Return a copy carrying ``chain_mac`` (the record is frozen)."""
        return replace(self, chain_mac=chain_mac)

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
    seq: int = 0  # Monotonic position in the task chain (assigned by EventStore)
    prev_snapshot_hash: Optional[str] = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SnapshotMetadata:
        """Reconstruct metadata from its stored dict."""
        payload = dict(data)
        payload["snapshot_type"] = SnapshotType(payload["snapshot_type"])
        return cls(**payload)

    @classmethod
    def from_snapshot(
        cls, snapshot: Snapshot, file_path: Optional[str] = None, seq: int = 0
    ) -> SnapshotMetadata:
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
            seq=seq,
            prev_snapshot_hash=snapshot.prev_snapshot_hash,
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict.

        Returns:
            Dictionary representation
        """
        data = asdict(self)
        data['snapshot_type'] = self.snapshot_type.value
        return data

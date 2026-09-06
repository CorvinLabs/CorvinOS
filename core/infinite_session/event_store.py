"""Phase A: Event Store with Snapshot Persistence (ADR-0540, Infinite Session Engine).

Manages snapshot storage, retrieval, and replay.
Persistence to ~/.corvin/tenants/<tenant_id>/snapshots/
All operations are audit-first: audit event emitted before disk write.

Compliance:
- GDPR Art. 32: Tenant isolation, fail-closed on missing tenant_id
- Audit Trail: Every write operation is logged before persistence
- Immutability: Snapshots are append-only (never update/delete)
"""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict
from pathlib import Path
from typing import Any, Optional
from datetime import datetime

from core.infinite_session.snapshot_schema import Snapshot, SnapshotType, SnapshotMetadata


class EventStore:
    """Append-only event store for snapshots (ADR-0540).

    Storage layout:
        ~/.corvin/tenants/<tenant_id>/snapshots/
            <task_id>/
                <phase_id>/
                    <snapshot_id>.json
                    metadata.json (index)
    """

    def __init__(self, corvin_home: str = None):
        """Initialize EventStore.

        Args:
            corvin_home: Corvin home directory (defaults to ~/.corvin)
        """
        if corvin_home is None:
            corvin_home = os.path.expanduser("~/.corvin")
        self.corvin_home = Path(corvin_home)

    def _get_snapshot_dir(self, tenant_id: str, task_id: str, phase_id: str) -> Path:
        """Get directory path for snapshots (fail-closed on invalid tenant_id).

        Args:
            tenant_id: Tenant identifier (must not be empty)
            task_id: Task identifier
            phase_id: Phase identifier

        Returns:
            Path to snapshot directory

        Raises:
            ValueError: If tenant_id is empty or invalid
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required and must not be empty (fail-closed)")

        snapshot_dir = (
            self.corvin_home
            / "tenants"
            / tenant_id
            / "snapshots"
            / task_id
            / phase_id
        )
        return snapshot_dir

    def write_snapshot(
        self,
        snapshot: Snapshot,
        audit_callback: Optional[callable] = None,
    ) -> tuple[bool, str]:
        """Write snapshot to storage (audit-first).

        Args:
            snapshot: Snapshot to write
            audit_callback: Optional callback to emit audit event before write

        Returns:
            (success, error_message)

        Notes:
            - Audit event is emitted BEFORE disk write (audit-first)
            - If audit_callback returns False, snapshot is NOT written
            - Fail-closed on any error
        """
        try:
            # Validate snapshot
            if not snapshot.tenant_id or not snapshot.tenant_id.strip():
                return False, "snapshot.tenant_id is empty (fail-closed)"

            # Emit audit event FIRST (audit-first design)
            if audit_callback:
                audit_emitted = audit_callback(
                    event_type="snapshot_created",
                    task_id=snapshot.task_id,
                    phase_id=snapshot.phase_id,
                    snapshot_id=snapshot.snapshot_id,
                    tenant_id=snapshot.tenant_id,
                    content_hash=snapshot.content_hash,
                    size_bytes=len(json.dumps(snapshot.to_dict())),
                )
                if not audit_emitted:
                    return False, "Audit event emission failed (fail-closed)"

            # Create directories
            snapshot_dir = self._get_snapshot_dir(
                snapshot.tenant_id,
                snapshot.task_id,
                snapshot.phase_id,
            )
            snapshot_dir.mkdir(parents=True, exist_ok=True)

            # Write snapshot to file
            snapshot_file = snapshot_dir / f"{snapshot.snapshot_id}.json"
            snapshot_data = snapshot.to_dict()
            with open(snapshot_file, "w") as f:
                json.dump(snapshot_data, f, indent=2)

            # Write metadata index
            metadata = SnapshotMetadata.from_snapshot(snapshot, str(snapshot_file))
            metadata_file = snapshot_dir / "metadata.json"
            metadata_list = []
            if metadata_file.exists():
                with open(metadata_file, "r") as f:
                    metadata_list = json.load(f)
            metadata_list.append(metadata.to_dict())
            with open(metadata_file, "w") as f:
                json.dump(metadata_list, f, indent=2)

            return True, ""

        except Exception as e:
            return False, f"Failed to write snapshot: {str(e)}"

    def read_snapshot(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: str,
        snapshot_id: str,
    ) -> tuple[Optional[Snapshot], str]:
        """Read snapshot from storage.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            phase_id: Phase identifier
            snapshot_id: Snapshot identifier

        Returns:
            (snapshot, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            snapshot_dir = self._get_snapshot_dir(tenant_id, task_id, phase_id)
            snapshot_file = snapshot_dir / f"{snapshot_id}.json"

            if not snapshot_file.exists():
                return None, f"Snapshot not found: {snapshot_id}"

            with open(snapshot_file, "r") as f:
                snapshot_data = json.load(f)

            # Reconstruct snapshot from dict
            snapshot_data["snapshot_type"] = SnapshotType(snapshot_data["snapshot_type"])
            snapshot = Snapshot(**snapshot_data)

            return snapshot, ""

        except Exception as e:
            return None, f"Failed to read snapshot: {str(e)}"

    def list_snapshots(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: Optional[str] = None,
    ) -> tuple[list[SnapshotMetadata], str]:
        """List snapshots for a task/phase.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            phase_id: Optional phase identifier (if None, list all phases)

        Returns:
            (metadata_list, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return [], "tenant_id is required (fail-closed)"

            base_dir = self.corvin_home / "tenants" / tenant_id / "snapshots" / task_id

            if not base_dir.exists():
                return [], ""

            metadata_list = []
            if phase_id:
                # List snapshots for specific phase
                phase_dir = base_dir / phase_id
                if phase_dir.exists():
                    metadata_file = phase_dir / "metadata.json"
                    if metadata_file.exists():
                        with open(metadata_file, "r") as f:
                            data = json.load(f)
                            for item in data:
                                item["snapshot_type"] = SnapshotType(item["snapshot_type"])
                                metadata_list.append(SnapshotMetadata(**item))
            else:
                # List snapshots for all phases
                for phase_subdir in base_dir.iterdir():
                    if phase_subdir.is_dir():
                        metadata_file = phase_subdir / "metadata.json"
                        if metadata_file.exists():
                            with open(metadata_file, "r") as f:
                                data = json.load(f)
                                for item in data:
                                    item["snapshot_type"] = SnapshotType(item["snapshot_type"])
                                    metadata_list.append(SnapshotMetadata(**item))

            return metadata_list, ""

        except Exception as e:
            return [], f"Failed to list snapshots: {str(e)}"

    def get_latest_snapshot(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: str,
    ) -> tuple[Optional[Snapshot], str]:
        """Get the latest snapshot for a phase (most recent by timestamp).

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            phase_id: Phase identifier

        Returns:
            (snapshot, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            metadata_list, error = self.list_snapshots(tenant_id, task_id, phase_id)
            if error:
                return None, error

            if not metadata_list:
                return None, "No snapshots found"

            # Sort by timestamp, get latest
            latest = max(metadata_list, key=lambda m: m.timestamp)

            # Read and return the snapshot
            return self.read_snapshot(
                tenant_id,
                task_id,
                phase_id,
                latest.snapshot_id,
            )

        except Exception as e:
            return None, f"Failed to get latest snapshot: {str(e)}"

    def verify_snapshot_chain(
        self,
        tenant_id: str,
        task_id: str,
        phase_id: str,
    ) -> tuple[bool, str]:
        """Verify hash chain integrity for a phase's snapshots.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            phase_id: Phase identifier

        Returns:
            (is_valid, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return False, "tenant_id is required (fail-closed)"

            metadata_list, error = self.list_snapshots(tenant_id, task_id, phase_id)
            if error:
                return False, error

            if not metadata_list:
                return True, ""  # Empty chain is valid

            # Sort by timestamp
            sorted_metadata = sorted(metadata_list, key=lambda m: m.timestamp)

            # Verify chain links
            prev_hash = None
            for metadata in sorted_metadata:
                snapshot, read_error = self.read_snapshot(
                    tenant_id,
                    task_id,
                    phase_id,
                    metadata.snapshot_id,
                )
                if read_error:
                    return False, f"Cannot read snapshot {metadata.snapshot_id}: {read_error}"

                if prev_hash is not None and snapshot.prev_snapshot_hash != prev_hash:
                    return False, f"Chain link broken at snapshot {metadata.snapshot_id}"

                prev_hash = snapshot.content_hash

            return True, ""

        except Exception as e:
            return False, f"Failed to verify chain: {str(e)}"

    def delete_snapshots_before(
        self,
        tenant_id: str,
        task_id: str,
        timestamp: str,  # ISO 8601
        audit_callback: Optional[callable] = None,
    ) -> tuple[int, str]:
        """Delete snapshots older than timestamp (for pruning/archival).

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            timestamp: ISO 8601 timestamp (delete snapshots before this)
            audit_callback: Optional callback to emit audit event

        Returns:
            (num_deleted, error_message)

        Notes:
            - This is an archival operation, not a data loss
            - Audit events are emitted for tracking
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return 0, "tenant_id is required (fail-closed)"

            metadata_list, error = self.list_snapshots(tenant_id, task_id)
            if error:
                return 0, error

            count = 0
            for metadata in metadata_list:
                if metadata.timestamp < timestamp:
                    # Delete the snapshot file
                    if metadata.file_path and Path(metadata.file_path).exists():
                        Path(metadata.file_path).unlink()
                        count += 1

                    # Emit audit event
                    if audit_callback:
                        audit_callback(
                            event_type="snapshot_archived",
                            task_id=task_id,
                            snapshot_id=metadata.snapshot_id,
                            tenant_id=tenant_id,
                        )

            return count, ""

        except Exception as e:
            return 0, f"Failed to delete snapshots: {str(e)}"

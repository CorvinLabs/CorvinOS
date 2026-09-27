"""Snapshot Manager — Save/restore Control Plane state (ADR-2029 Stream 4).

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
console's snapshot routes answer 501 and use a different module
(``corvin_console/control_plane``); nothing constructs this class outside tests.

Defused 2026-09-27 (adversarial review):

* ``restore_snapshot`` returned ``status: "restored"`` while applying NOTHING —
  no component takes a snapshot's state and puts it back. It now verifies the
  snapshot (tenant, checksum), records ``snapshot_restore_failed`` with
  ``reason=not_implemented`` and raises :class:`SnapshotRestoreNotImplemented`.
* audit went to whatever object was injected (tests passed an in-memory list);
  by default it now commits to the tenant's core chain through
  ``audit_chain_provider.get_audit_chain_writer`` (the forge writer) and a
  record that does not commit raises. Free-text snapshot names are not audited.
* the id counter restarted at 0 per instance, so after
  ``load_snapshots_from_disk`` the next snapshot overwrote ``snap_000000`` on
  disk; ids now skip every id already in memory or on disk.
* ``get_audit_log`` always answered ``[]``; it now reads the tenant's chain.
"""

from dataclasses import dataclass, asdict, field
from typing import Dict, List, Optional, Any
from datetime import datetime
import json
import gzip
import hashlib
import logging
import os

logger = logging.getLogger(__name__)

#: Content-free field sets (ids, hashes, sizes, codes). EVENT_SEVERITY entries
#: for ``security_events.py`` (owner please add): snapshot_created INFO,
#: snapshot_deleted INFO, snapshot_restore_failed WARNING.
SNAPSHOT_EVENT_ALLOWLISTS: Dict[str, frozenset] = {
    name: frozenset({"snapshot_id", "checksum", "size_bytes", "tenant_id",
                     "reason", "event_id", "expected", "calculated"})
    for name in ("snapshot_created", "snapshot_deleted", "snapshot_restore_failed")
}


class SnapshotRestoreNotImplemented(NotImplementedError):
    """No component applies snapshot state; a restore is never reported done."""


@dataclass(frozen=True)
class Snapshot:
    """Immutable Control Plane snapshot."""

    snapshot_id: str
    timestamp: str  # ISO 8601
    name: str
    description: str
    intent_state: Dict[str, Any]
    plugin_state: Dict[str, Any]
    subsystem_state: Dict[str, Any]
    override_state: Dict[str, Any]
    checksum: str  # SHA256
    tenant_id: str
    created_by: str
    size_bytes: int = 0


class SnapshotManager:
    """Manages Control Plane snapshots with integrity verification.

    Responsibilities:
    - Create snapshots of Control Plane state (atomic)
    - Restore from snapshots (with checksum verification)
    - List and retrieve snapshot metadata
    - Persist snapshots to compressed storage
    - Compute and verify checksums (SHA256)
    - Enforce tenant isolation
    """

    def __init__(self, audit_backend=None, storage_path: str = ""):
        """Initialize snapshot manager.

        Args:
            audit_backend: ``None`` → the tenant's core audit chain (default).
                An injected backend (tests) must expose ``write_event_dict``
                (the ``AuditChainWriter`` API) or an async ``log_event``.
            storage_path: Directory for snapshot storage
        """
        if not storage_path:
            raise ValueError("storage_path is required")
        self.audit = audit_backend
        self.storage_path = storage_path
        self.snapshots: Dict[str, Snapshot] = {}
        self._snapshot_counter = 0

        # Ensure storage directory exists
        os.makedirs(storage_path, exist_ok=True)

    async def create_snapshot(
        self,
        control_plane_state: Dict[str, Any],
        name: str,
        description: str,
        creator_id: str,
        tenant_id: str,
    ) -> Dict:
        """Create a snapshot of Control Plane state.

        Args:
            control_plane_state: Dict containing intent, plugin, subsystem, override state
            name: Human-readable snapshot name
            description: Long-form description
            creator_id: User ID creating snapshot
            tenant_id: Tenant scope

        Returns:
            Status dict with snapshot_id, checksum, created_at

        Raises:
            ValueError: If control_plane_state is invalid
        """
        if not isinstance(control_plane_state, dict):
            raise ValueError("control_plane_state must be a dict")

        snapshot_id = self._next_snapshot_id()

        # Extract state components (fail-closed if missing)
        intent_state = control_plane_state.get("intent", {})
        plugin_state = control_plane_state.get("plugins", {})
        subsystem_state = control_plane_state.get("subsystems", {})
        override_state = control_plane_state.get("overrides", {})

        # Calculate checksum (deterministic JSON)
        snapshot_content = {
            "intent": intent_state,
            "plugins": plugin_state,
            "subsystems": subsystem_state,
            "overrides": override_state,
        }
        state_json = json.dumps(snapshot_content, sort_keys=True)
        checksum = hashlib.sha256(state_json.encode()).hexdigest()

        # Create snapshot object
        timestamp = datetime.utcnow().isoformat() + "Z"
        snapshot = Snapshot(
            snapshot_id=snapshot_id,
            timestamp=timestamp,
            name=name,
            description=description,
            intent_state=intent_state,
            plugin_state=plugin_state,
            subsystem_state=subsystem_state,
            override_state=override_state,
            checksum=checksum,
            tenant_id=tenant_id,
            created_by=creator_id,
            size_bytes=len(state_json.encode()),
        )

        # Store in memory
        self.snapshots[snapshot_id] = snapshot

        # Persist to disk (compressed)
        self._save_snapshot_to_disk(snapshot)

        # Log audit event (the free-text name/description is never audited)
        await self._audit_event(
            "snapshot_created", tenant_id, user_id=creator_id,
            details={
                "snapshot_id": snapshot_id,
                "checksum": checksum,
                "size_bytes": snapshot.size_bytes,
            },
        )

        logger.info(f"Snapshot {snapshot_id} created: {name} ({snapshot.size_bytes} bytes)")

        return {
            "snapshot_id": snapshot_id,
            "checksum": checksum,
            "created_at": timestamp,
            "size_bytes": snapshot.size_bytes,
        }

    async def restore_snapshot(
        self,
        snapshot_id: str,
        tenant_id: str,
        approver_id: str,
    ) -> Dict:
        """Restore Control Plane state from snapshot.

        Args:
            snapshot_id: ID of snapshot to restore
            tenant_id: Tenant scope
            approver_id: User ID approving restoration

        Raises:
            ValueError: If snapshot not found, corrupted, or checksum mismatch
            SnapshotRestoreNotImplemented: ALWAYS for a valid snapshot — no
                component applies snapshot state, so nothing is restored and
                success is never reported.
        """
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        snapshot = self.snapshots[snapshot_id]

        # Verify tenant access
        if snapshot.tenant_id != tenant_id:
            raise ValueError(f"Access denied to snapshot {snapshot_id}")

        # Verify checksum before restore
        state_json = json.dumps(
            {
                "intent": snapshot.intent_state,
                "plugins": snapshot.plugin_state,
                "subsystems": snapshot.subsystem_state,
                "overrides": snapshot.override_state,
            },
            sort_keys=True,
        )
        calculated_checksum = hashlib.sha256(state_json.encode()).hexdigest()

        if calculated_checksum != snapshot.checksum:
            await self._audit_event(
                "snapshot_restore_failed", tenant_id, user_id=approver_id,
                details={
                    "snapshot_id": snapshot_id,
                    "reason": "checksum_mismatch",
                    "expected": snapshot.checksum,
                    "calculated": calculated_checksum,
                },
            )
            raise ValueError("Snapshot checksum mismatch (data corrupted)")

        # The snapshot is intact — but nothing can apply it. Record the refusal
        # and fail; never report a restore that did not happen.
        await self._audit_event(
            "snapshot_restore_failed", tenant_id, user_id=approver_id,
            details={
                "snapshot_id": snapshot_id,
                "reason": "not_implemented",
                "checksum": snapshot.checksum,
            },
        )
        raise SnapshotRestoreNotImplemented(
            f"restore of {snapshot_id} is not implemented: no component applies "
            "snapshot state (nothing was restored)"
        )

    async def list_snapshots(self, tenant_id: str) -> List[Dict]:
        """List all snapshots for a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            List of snapshot metadata dicts
        """
        snapshots = []
        for snapshot in self.snapshots.values():
            if snapshot.tenant_id == tenant_id:
                snapshots.append({
                    "snapshot_id": snapshot.snapshot_id,
                    "name": snapshot.name,
                    "description": snapshot.description,
                    "created_at": snapshot.timestamp,
                    "created_by": snapshot.created_by,
                    "checksum": snapshot.checksum,
                    "size_bytes": snapshot.size_bytes,
                })
        # Sort by timestamp (most recent first)
        snapshots.sort(key=lambda x: x["created_at"], reverse=True)
        return snapshots

    def get_snapshot_details(self, snapshot_id: str, tenant_id: str) -> Dict:
        """Get full snapshot details.

        Args:
            snapshot_id: ID of snapshot
            tenant_id: Tenant scope

        Returns:
            Full snapshot dict

        Raises:
            ValueError: If snapshot not found or access denied
        """
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        snapshot = self.snapshots[snapshot_id]

        # Verify tenant access
        if snapshot.tenant_id != tenant_id:
            raise ValueError(f"Access denied to snapshot {snapshot_id}")

        return asdict(snapshot)

    async def delete_snapshot(self, snapshot_id: str, tenant_id: str, approver_id: str) -> Dict:
        """Delete a snapshot permanently.

        Args:
            snapshot_id: ID of snapshot to delete
            tenant_id: Tenant scope
            approver_id: User ID approving deletion

        Returns:
            Status dict with deletion confirmation

        Raises:
            ValueError: If snapshot not found or access denied
        """
        if snapshot_id not in self.snapshots:
            raise ValueError(f"Snapshot {snapshot_id} not found")

        snapshot = self.snapshots[snapshot_id]

        # Verify tenant access
        if snapshot.tenant_id != tenant_id:
            raise ValueError(f"Access denied to snapshot {snapshot_id}")

        # Delete from storage
        self._delete_snapshot_from_disk(snapshot_id)

        # Delete from memory
        del self.snapshots[snapshot_id]

        # Log audit event
        await self._audit_event(
            "snapshot_deleted", tenant_id, user_id=approver_id,
            details={"snapshot_id": snapshot_id, "checksum": snapshot.checksum},
        )

        logger.info(f"Snapshot {snapshot_id} deleted by {approver_id}")
        return {
            "snapshot_id": snapshot_id,
            "status": "deleted",
        }

    def get_storage_stats(self, tenant_id: str) -> Dict:
        """Get storage statistics for a tenant.

        Args:
            tenant_id: Tenant scope

        Returns:
            Stats dict with snapshot counts and total size
        """
        tenant_snapshots = [s for s in self.snapshots.values() if s.tenant_id == tenant_id]
        total_size = sum(s.size_bytes for s in tenant_snapshots)

        return {
            "tenant_id": tenant_id,
            "snapshot_count": len(tenant_snapshots),
            "total_size_bytes": total_size,
            "total_size_mb": total_size / (1024 * 1024),
        }

    def _save_snapshot_to_disk(self, snapshot: Snapshot):
        """Save snapshot to disk (compressed JSON).

        Args:
            snapshot: Snapshot object to save
        """
        snapshot_dict = asdict(snapshot)
        snapshot_json = json.dumps(snapshot_dict, indent=2).encode()

        filename = os.path.join(self.storage_path, f"snapshot_{snapshot.snapshot_id}.json.gz")

        try:
            with gzip.open(filename, "wb") as f:
                f.write(snapshot_json)
            logger.debug(f"Snapshot persisted to {filename}")
        except IOError as e:
            logger.error(f"Failed to persist snapshot {snapshot.snapshot_id}: {e}")
            raise

    def _delete_snapshot_from_disk(self, snapshot_id: str):
        """Delete snapshot from disk.

        Args:
            snapshot_id: ID of snapshot to delete
        """
        filename = os.path.join(self.storage_path, f"snapshot_{snapshot_id}.json.gz")

        try:
            if os.path.exists(filename):
                os.remove(filename)
                logger.debug(f"Snapshot removed from disk: {filename}")
        except IOError as e:
            logger.error(f"Failed to delete snapshot {snapshot_id}: {e}")
            raise

    async def get_snapshot(self, snapshot_id: str, tenant_id: str) -> Optional[Dict]:
        """Get snapshot details (alias for get_snapshot_details).

        Args:
            snapshot_id: ID of snapshot
            tenant_id: Tenant scope

        Returns:
            Snapshot dict or None if not found

        Raises:
            ValueError: If access denied
        """
        if snapshot_id not in self.snapshots:
            return None

        snapshot = self.snapshots[snapshot_id]

        # Verify tenant access
        if snapshot.tenant_id != tenant_id:
            raise ValueError(f"Access denied to snapshot {snapshot_id}")

        return asdict(snapshot)

    async def get_audit_log(self, tenant_id: str) -> List[Dict]:
        """Snapshot records of ``tenant_id`` from its core audit chain.

        Raises:
            NotImplementedError: an injected (non-chain) audit backend is in use
                — there is no chain to read, and an empty list would claim
                "no events".
        """
        if self.audit is not None:
            raise NotImplementedError("audit log is only readable from the core chain")
        from corvin_operator.bridges.shared.paths import tenant_audit_chain

        chain = tenant_audit_chain(tenant_id)
        if not chain.exists():
            return []
        out: List[Dict] = []
        with open(chain, "r") as f:
            for line in f:
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                details = rec.get("details") or {}
                if (str(rec.get("event_type", "")).startswith("snapshot_")
                        and details.get("tenant_id") == tenant_id):
                    out.append(rec)
        return out

    # ── internals ────────────────────────────────────────────────────────

    def _next_snapshot_id(self) -> str:
        """Next free ``snap_NNNNNN`` — never one in memory or on disk."""
        while True:
            candidate = f"snap_{self._snapshot_counter:06d}"
            self._snapshot_counter += 1
            on_disk = os.path.exists(
                os.path.join(self.storage_path, f"snapshot_{candidate}.json.gz"))
            if candidate not in self.snapshots and not on_disk:
                return candidate

    async def _audit_event(self, event_type: str, tenant_id: str, *,
                           user_id: Optional[str], details: Dict[str, Any]) -> None:
        """Commit one record; raise if it does not (fail-closed)."""
        backend = self.audit
        if backend is None:
            from core.compliance.audit_chain_provider import get_audit_chain_writer
            from core.compliance.audit_chain_writer import _forge

            se, _ = _forge()
            for name, fields in SNAPSHOT_EVENT_ALLOWLISTS.items():
                se.register_event_allowlist(name, fields)
            backend = get_audit_chain_writer(tenant_id)
        if hasattr(backend, "write_event_dict"):
            backend.write_event_dict(event_type=event_type, tenant_id=tenant_id,
                                     user_id=user_id, details=dict(details))
        else:  # legacy injected test backend
            payload = dict(details)
            payload["tenant_id"] = tenant_id
            await backend.log_event(event_type, payload)

    def load_snapshots_from_disk(self):
        """Load all snapshots from disk into memory (on startup).

        Raises:
            IOError: If disk load fails
        """
        if not os.path.exists(self.storage_path):
            logger.info(f"Snapshot storage path not found: {self.storage_path}")
            return

        for filename in os.listdir(self.storage_path):
            if filename.startswith("snapshot_") and filename.endswith(".json.gz"):
                try:
                    filepath = os.path.join(self.storage_path, filename)
                    with gzip.open(filepath, "rb") as f:
                        snapshot_dict = json.loads(f.read().decode())
                        snapshot = Snapshot(**snapshot_dict)
                        self.snapshots[snapshot.snapshot_id] = snapshot
                        logger.debug(f"Loaded snapshot from disk: {snapshot.snapshot_id}")
                except (IOError, json.JSONDecodeError) as e:
                    logger.error(f"Failed to load snapshot {filename}: {e}")

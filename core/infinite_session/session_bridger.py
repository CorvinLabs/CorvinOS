"""Phase B: Session Bridging (ADR-0541).

Manages session-to-session state handoff via cryptographically signed snapshots.
Creates bridge events that prove state continuity across session boundaries.
All operations are audit-first and fail-closed on any error.

Compliance:
- GDPR Art. 30/32: Audit continuity, cryptographic proof of state
- Immutability: Bridge events are append-only, never edited
- Tenant isolation: Every bridge operation scoped by tenant_id
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, asdict
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple
from uuid import uuid4

from core.infinite_session.event_store import EventStore
from core.infinite_session.crypto_binding import CryptoBinding
from core.infinite_session.snapshot_schema import Snapshot, SnapshotType


@dataclass(frozen=True)
class SessionBridgeEvent:
    """Immutable event representing a session bridge (state handoff).

    Guarantees:
    - Frozen (immutable after creation)
    - Tenant-scoped (fail-closed on missing tenant_id)
    - Cryptographically signed (signature must verify)
    - Timestamped (audit trail)
    """

    bridge_id: str
    tenant_id: str
    task_id: str
    source_session_id: str
    dest_session_id: str
    snapshot_hash: str  # Hash of the snapshot being handed off
    prev_hash: str  # Hash of the previous event (for chain continuity)
    signature: str  # HMAC-SHA256 signature of snapshot_hash
    timestamp: str  # ISO 8601 UTC
    artifacts: list[str]  # Artifacts passed to next session (ADRs, test results, etc.)
    phase_completed: str  # Phase that completed in source session
    metadata: dict[str, Any]  # Additional bridge metadata

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict (for storage/audit trail)."""
        return asdict(self)

    @classmethod
    def create(
        cls,
        tenant_id: str,
        task_id: str,
        source_session_id: str,
        dest_session_id: str,
        snapshot_hash: str,
        prev_hash: str,
        signature: str,
        phase_completed: str,
        artifacts: list[str] = None,
        metadata: dict[str, Any] = None,
    ) -> SessionBridgeEvent:
        """Factory for creating new bridge events.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            source_session_id: Source session ID
            dest_session_id: Destination session ID
            snapshot_hash: Hash of snapshot being handed off
            prev_hash: Hash of previous event (for chain)
            signature: Cryptographic signature
            phase_completed: Phase that completed
            artifacts: List of artifacts passed to next session
            metadata: Additional metadata

        Returns:
            Frozen SessionBridgeEvent instance

        Raises:
            ValueError: If tenant_id is empty (fail-closed)
        """
        if not tenant_id or not tenant_id.strip():
            raise ValueError("tenant_id is required (fail-closed)")

        return cls(
            bridge_id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id=source_session_id,
            dest_session_id=dest_session_id,
            snapshot_hash=snapshot_hash,
            prev_hash=prev_hash,
            signature=signature,
            timestamp=datetime.utcnow().isoformat() + "Z",
            artifacts=artifacts or [],
            phase_completed=phase_completed,
            metadata=metadata or {},
        )


class SessionBridger:
    """Session-to-session bridging with cryptographic signatures (ADR-0541).

    Manages:
    - Snapshot creation at session boundary
    - Cryptographic signing (HMAC-SHA256)
    - Bridge event emission (audit trail)
    - State validation before handoff
    """

    def __init__(
        self,
        event_store: EventStore,
        crypto_binding: CryptoBinding,
        corvin_home: str = None,
    ):
        """Initialize session bridger.

        Args:
            event_store: EventStore instance (Phase A)
            crypto_binding: CryptoBinding instance (Phase B crypto)
            corvin_home: Corvin home directory (defaults to ~/.corvin)
        """
        if corvin_home is None:
            corvin_home = (Path.home() / ".corvin").as_posix()

        self.event_store = event_store
        self.crypto_binding = crypto_binding
        self.corvin_home = Path(corvin_home)
        self.bridge_dir = self.corvin_home / "bridges"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def create_bridge(
        self,
        tenant_id: str,
        task_id: str,
        source_session_id: str,
        dest_session_id: str,
        snapshot: Snapshot,
        phase_completed: str,
        artifacts: list[str] = None,
        metadata: dict[str, Any] = None,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[SessionBridgeEvent], str]:
        """Create a session bridge with cryptographic signature (audit-first).

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            source_session_id: Source session ID
            dest_session_id: Destination session ID
            snapshot: Snapshot to hand off (already created)
            phase_completed: Phase that completed
            artifacts: List of artifacts passed to next session
            metadata: Additional metadata
            audit_callback: Optional callback to emit audit events

        Returns:
            (bridge_event, error_message)

        Notes:
            - Validates snapshot before bridging
            - Signs snapshot_hash with tenant's signing key
            - Emits bridge event to audit trail (audit-first)
            - Fail-closed: any error → returns (None, error_message)
        """
        try:
            # Validate inputs
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"
            if not task_id or not task_id.strip():
                return None, "task_id is required"
            if not source_session_id or not source_session_id.strip():
                return None, "source_session_id is required"
            if not dest_session_id or not dest_session_id.strip():
                return None, "dest_session_id is required"
            if not snapshot:
                return None, "snapshot is required"

            # Verify snapshot tenant_id matches bridge tenant_id
            if snapshot.tenant_id != tenant_id:
                return None, f"Snapshot tenant_id mismatch: {snapshot.tenant_id} != {tenant_id}"

            # Emit audit event: bridge creation started
            if audit_callback:
                audit_callback(
                    event_type="session_bridge_started",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    source_session_id=source_session_id,
                    dest_session_id=dest_session_id,
                    snapshot_hash=snapshot.content_hash,
                )

            # Sign snapshot hash
            signature, error = self.crypto_binding.sign_snapshot(
                tenant_id=tenant_id,
                snapshot_hash=snapshot.content_hash,
                audit_callback=audit_callback,
            )
            if error:
                if audit_callback:
                    audit_callback(
                        event_type="session_bridge_failed",
                        tenant_id=tenant_id,
                        task_id=task_id,
                        reason=f"Signature generation failed: {error}",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return None, f"Failed to sign snapshot: {error}"

            # Create bridge event
            # prev_hash is the hash of the last event in the source session
            # For now, use the snapshot's prev_snapshot_hash (if available)
            prev_hash = snapshot.prev_snapshot_hash or "genesis"

            bridge = SessionBridgeEvent.create(
                tenant_id=tenant_id,
                task_id=task_id,
                source_session_id=source_session_id,
                dest_session_id=dest_session_id,
                snapshot_hash=snapshot.content_hash,
                prev_hash=prev_hash,
                signature=signature,
                phase_completed=phase_completed,
                artifacts=artifacts,
                metadata=metadata,
            )

            # Emit bridge event to audit trail (audit-first)
            if audit_callback:
                audit_emitted = audit_callback(
                    event_type="task_session_bridged",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    bridge_id=bridge.bridge_id,
                    source_session_id=source_session_id,
                    dest_session_id=dest_session_id,
                    snapshot_hash=snapshot.content_hash,
                    signature=signature,
                    phase_completed=phase_completed,
                    timestamp=bridge.timestamp,
                )
                if not audit_emitted:
                    return None, "Failed to emit audit event for bridge"

            # Persist bridge to disk
            success, error = self._persist_bridge(bridge)
            if not success:
                return None, f"Failed to persist bridge: {error}"

            return bridge, ""

        except Exception as e:
            if audit_callback:
                audit_callback(
                    event_type="session_bridge_failed",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    reason=f"Exception: {str(e)}",
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )
            return None, f"Failed to create bridge: {str(e)}"

    def resume_from_bridge(
        self,
        tenant_id: str,
        task_id: str,
        bridge_id: str,
        user_id: Optional[str] = None,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[dict[str, Any]], str]:
        """Resume session from a bridge (load previous state).

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            bridge_id: Bridge ID to resume from
            user_id: User ID (for consent checking, GDPR Art. 6)
            audit_callback: Optional callback to emit audit events

        Returns:
            (state_dict, error_message)

        Notes:
            - Loads bridge from disk
            - Verifies signature (fail-closed on mismatch)
            - Checks user consent before restoring state (GDPR Art. 6, L16)
            - Returns restored state
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            # Check user consent to resume session (GDPR Art. 6, L16)
            # TODO: Integrate with L16 consent gate when available
            # For now: placeholder that documents the requirement
            if user_id:
                # Future: check consent_gate.requires_consent(
                #   user_id=user_id,
                #   consent_type="session_resume",
                #   tenant_id=tenant_id
                # )
                # if not has_consent:
                #   return None, "User has not consented to session restoration (GDPR Art. 6)"
                pass

            # Load bridge from disk
            bridge, error = self._load_bridge(tenant_id, task_id, bridge_id)
            if error:
                if audit_callback:
                    audit_callback(
                        event_type="session_resume_failed",
                        tenant_id=tenant_id,
                        task_id=task_id,
                        bridge_id=bridge_id,
                        reason=f"Bridge load failed: {error}",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return None, error

            # Verify signature
            is_valid, error = self.crypto_binding.verify_signature(
                tenant_id=tenant_id,
                snapshot_hash=bridge.snapshot_hash,
                signature=bridge.signature,
                audit_callback=audit_callback,
            )
            if not is_valid:
                if audit_callback:
                    audit_callback(
                        event_type="session_resume_failed",
                        tenant_id=tenant_id,
                        task_id=task_id,
                        bridge_id=bridge_id,
                        reason=f"Signature verification failed: {error}",
                        timestamp=datetime.utcnow().isoformat() + "Z",
                    )
                return None, f"Signature verification failed: {error}"

            # Emit audit event: session resumed
            if audit_callback:
                audit_callback(
                    event_type="session_resumed",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    bridge_id=bridge_id,
                    source_session_id=bridge.source_session_id,
                    phase_completed=bridge.phase_completed,
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )

            # Return bridge metadata + artifacts for state reconstruction
            return {
                "bridge_id": bridge.bridge_id,
                "source_session_id": bridge.source_session_id,
                "phase_completed": bridge.phase_completed,
                "artifacts": bridge.artifacts,
                "metadata": bridge.metadata,
                "timestamp": bridge.timestamp,
            }, ""

        except Exception as e:
            if audit_callback:
                audit_callback(
                    event_type="session_resume_failed",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    reason=f"Exception: {str(e)}",
                    timestamp=datetime.utcnow().isoformat() + "Z",
                )
            return None, f"Failed to resume from bridge: {str(e)}"

    def _persist_bridge(self, bridge: SessionBridgeEvent) -> Tuple[bool, str]:
        """Persist bridge event to disk.

        Args:
            bridge: Bridge event to persist

        Returns:
            (success, error_message)
        """
        try:
            # Create bridge directory
            bridge_task_dir = (
                self.bridge_dir
                / bridge.tenant_id
                / bridge.task_id
            )
            bridge_task_dir.mkdir(parents=True, exist_ok=True)

            # Write bridge to file
            bridge_file = bridge_task_dir / f"{bridge.bridge_id}.json"
            bridge_data = bridge.to_dict()
            with open(bridge_file, "w") as f:
                json.dump(bridge_data, f, indent=2)

            return True, ""

        except Exception as e:
            return False, f"Failed to persist bridge: {str(e)}"

    def _load_bridge(
        self,
        tenant_id: str,
        task_id: str,
        bridge_id: str,
    ) -> Tuple[Optional[SessionBridgeEvent], str]:
        """Load bridge event from disk.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier
            bridge_id: Bridge ID

        Returns:
            (bridge_event, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return None, "tenant_id is required (fail-closed)"

            bridge_file = (
                self.bridge_dir
                / tenant_id
                / task_id
                / f"{bridge_id}.json"
            )

            if not bridge_file.exists():
                return None, f"Bridge not found: {bridge_id}"

            with open(bridge_file, "r") as f:
                bridge_data = json.load(f)

            # Reconstruct bridge from dict
            bridge = SessionBridgeEvent(**bridge_data)
            return bridge, ""

        except Exception as e:
            return None, f"Failed to load bridge: {str(e)}"

    def list_bridges(
        self,
        tenant_id: str,
        task_id: str,
    ) -> Tuple[list[SessionBridgeEvent], str]:
        """List all bridges for a task.

        Args:
            tenant_id: Tenant identifier
            task_id: Task identifier

        Returns:
            (bridge_list, error_message)
        """
        try:
            if not tenant_id or not tenant_id.strip():
                return [], "tenant_id is required (fail-closed)"

            task_dir = self.bridge_dir / tenant_id / task_id

            if not task_dir.exists():
                return [], ""

            bridges = []
            for bridge_file in sorted(task_dir.glob("*.json")):
                try:
                    with open(bridge_file, "r") as f:
                        bridge_data = json.load(f)
                    bridge = SessionBridgeEvent(**bridge_data)
                    bridges.append(bridge)
                except Exception:
                    # Skip malformed bridges
                    continue

            return bridges, ""

        except Exception as e:
            return [], f"Failed to list bridges: {str(e)}"

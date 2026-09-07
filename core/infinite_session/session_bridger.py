"""Phase B: Session Bridging (ADR-0541).

A *bridge* is the signed hand-off record between two sessions of one task:
"session A finished phase P at snapshot H; session B may resume from H".

Security model:
- The signature covers the CANONICAL JSON of the WHOLE bridge event minus the
  ``signature`` field — every field (ids, sessions, hashes, phase, artifacts,
  metadata, timestamp) is bound; changing any of them invalidates the bridge.
- ``resume_from_bridge`` re-verifies the signature, checks the bridge's tenant
  and task against the request, loads the referenced snapshot through the
  tenant-bound :class:`EventStore` and checks its ``content_hash`` — the
  recovered ``state_dict`` is only returned when all of that holds.
- Bridges are stored at ``<tenant_root>/bridges/<task_id>/<bridge_id>.json``;
  ids are validated and the path is resolve-checked before any open.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional, Tuple
from uuid import uuid4

from core.infinite_session.crypto_binding import CryptoBinding
from core.infinite_session.event_store import EventStore
from core.infinite_session.paths import safe_child, validate_id
from core.infinite_session.snapshot_schema import Snapshot
from core.tenants import validate_tenant_id


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class SessionBridgeEvent:
    """Immutable, signed hand-off record (see module docstring)."""

    bridge_id: str
    tenant_id: str
    task_id: str
    source_session_id: str
    dest_session_id: str
    snapshot_id: str
    snapshot_hash: str
    prev_hash: str
    signature: str  # HMAC-SHA256 over canonical JSON of every other field
    timestamp: str
    artifacts: list[str]
    phase_completed: str
    metadata: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def signed_payload(self) -> dict[str, Any]:
        """Every field except ``signature`` — the bytes the HMAC binds."""
        data = self.to_dict()
        data.pop("signature", None)
        return data

    @classmethod
    def create(
        cls,
        tenant_id: str,
        task_id: str,
        source_session_id: str,
        dest_session_id: str,
        snapshot_id: str,
        snapshot_hash: str,
        prev_hash: str,
        phase_completed: str,
        artifacts: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        signature: str = "",
    ) -> "SessionBridgeEvent":
        validate_tenant_id(tenant_id)
        validate_id(task_id, "task_id")
        validate_id(snapshot_id, "snapshot_id")
        for name, value in (
            ("source_session_id", source_session_id),
            ("dest_session_id", dest_session_id),
            ("phase_completed", phase_completed),
        ):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} is required")
        return cls(
            bridge_id=str(uuid4()),
            tenant_id=tenant_id,
            task_id=task_id,
            source_session_id=source_session_id,
            dest_session_id=dest_session_id,
            snapshot_id=snapshot_id,
            snapshot_hash=snapshot_hash,
            prev_hash=prev_hash,
            signature=signature,
            timestamp=_now(),
            artifacts=list(artifacts or []),
            phase_completed=phase_completed,
            metadata=dict(metadata or {}),
        )


class SessionBridger:
    """Session-to-session bridging with whole-event signatures (ADR-0541)."""

    def __init__(self, event_store: EventStore, crypto_binding: CryptoBinding):
        self.event_store = event_store
        self.crypto_binding = crypto_binding
        self.tenant_id = event_store.tenant_id
        self.bridge_dir = event_store.root_dir.parent / "bridges"
        self.bridge_dir.mkdir(parents=True, exist_ok=True)

    def _bind(self, tenant_id: Any) -> Optional[str]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return "tenant_id is required (fail-closed)"
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            return str(exc)
        if tenant_id != self.tenant_id:
            return f"Tenant mismatch: bridger is bound to {self.tenant_id!r}, got {tenant_id!r}"
        return None

    # ── create ───────────────────────────────────────────────────────────

    def create_bridge(
        self,
        tenant_id: str,
        task_id: str,
        source_session_id: str,
        dest_session_id: str,
        snapshot: Snapshot,
        phase_completed: str,
        artifacts: Optional[list[str]] = None,
        metadata: Optional[dict[str, Any]] = None,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[SessionBridgeEvent], str]:
        """Sign and persist a bridge for ``snapshot`` (audit-first, fail-closed).

        The snapshot must already be persisted in the bound EventStore under the
        same tenant/task — a bridge can never point at state that is not stored.
        """
        error = self._bind(tenant_id)
        if error:
            return None, error
        if snapshot is None:
            return None, "snapshot is required"
        if snapshot.tenant_id != tenant_id:
            return None, f"Snapshot tenant_id mismatch: {snapshot.tenant_id} != {tenant_id}"
        if snapshot.task_id != task_id:
            return None, f"Snapshot task_id mismatch: {snapshot.task_id} != {task_id}"

        stored, read_error = self.event_store.read_snapshot(tenant_id, task_id, snapshot.snapshot_id)
        if read_error or stored is None or stored.content_hash != snapshot.content_hash:
            return None, "snapshot is not persisted in the event store (fail-closed)"

        if audit_callback:
            audit_callback(
                event_type="session_bridge_started",
                tenant_id=tenant_id,
                task_id=task_id,
                source_session_id=source_session_id,
                dest_session_id=dest_session_id,
                snapshot_hash=snapshot.content_hash,
            )

        try:
            unsigned = SessionBridgeEvent.create(
                tenant_id=tenant_id,
                task_id=task_id,
                source_session_id=source_session_id,
                dest_session_id=dest_session_id,
                snapshot_id=snapshot.snapshot_id,
                snapshot_hash=snapshot.content_hash,
                prev_hash=snapshot.prev_snapshot_hash or "genesis",
                phase_completed=phase_completed,
                artifacts=artifacts,
                metadata=metadata,
            )
        except ValueError as exc:
            return None, str(exc)

        signature, error = self.crypto_binding.sign_payload(
            tenant_id, unsigned.signed_payload(), audit_callback=audit_callback
        )
        if error or not signature:
            if audit_callback:
                audit_callback(
                    event_type="session_bridge_failed", tenant_id=tenant_id,
                    task_id=task_id, reason="signature generation failed", timestamp=_now(),
                )
            return None, f"Failed to sign bridge: {error}"

        bridge = SessionBridgeEvent(**{**unsigned.to_dict(), "signature": signature})

        if audit_callback:
            emitted = audit_callback(
                event_type="task_session_bridged",
                tenant_id=tenant_id,
                task_id=task_id,
                bridge_id=bridge.bridge_id,
                source_session_id=source_session_id,
                dest_session_id=dest_session_id,
                snapshot_hash=snapshot.content_hash,
                phase_completed=phase_completed,
                timestamp=bridge.timestamp,
            )
            if not emitted:
                return None, "Failed to emit audit event for bridge"

        ok, error = self._persist_bridge(bridge)
        if not ok:
            return None, f"Failed to persist bridge: {error}"
        return bridge, ""

    # ── resume ───────────────────────────────────────────────────────────

    def verify_bridge(
        self,
        tenant_id: str,
        bridge: SessionBridgeEvent,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[bool, str]:
        """Signature + binding checks for a loaded bridge (no state load)."""
        error = self._bind(tenant_id)
        if error:
            return False, error
        if bridge.tenant_id != tenant_id:
            return False, "Bridge tenant_id mismatch (fail-closed)"
        return self.crypto_binding.verify_payload(
            tenant_id, bridge.signed_payload(), bridge.signature, audit_callback=audit_callback
        )

    def resume_from_bridge(
        self,
        tenant_id: str,
        task_id: str,
        bridge_id: str,
        audit_callback: Optional[callable] = None,
    ) -> Tuple[Optional[dict[str, Any]], str]:
        """Load, fully verify and return the state handed off by ``bridge_id``.

        Returns ``{"bridge_id", "source_session_id", "dest_session_id",
        "phase_completed", "artifacts", "metadata", "timestamp",
        "snapshot_id", "snapshot_hash", "state_dict"}`` or ``(None, reason)``.
        """
        def _fail(reason: str) -> Tuple[None, str]:
            if audit_callback:
                audit_callback(
                    event_type="session_resume_failed", tenant_id=tenant_id or "<invalid>",
                    task_id=task_id, bridge_id=bridge_id, reason=reason, timestamp=_now(),
                )
            return None, reason

        error = self._bind(tenant_id)
        if error:
            return _fail(error)

        bridge, error = self._load_bridge(tenant_id, task_id, bridge_id)
        if error or bridge is None:
            return _fail(f"Bridge load failed: {error}")
        if bridge.tenant_id != tenant_id or bridge.task_id != task_id or bridge.bridge_id != bridge_id:
            return _fail("Bridge binding mismatch (tenant/task/id) (fail-closed)")

        ok, error = self.crypto_binding.verify_payload(
            tenant_id, bridge.signed_payload(), bridge.signature, audit_callback=audit_callback
        )
        if not ok:
            return _fail(f"Signature verification failed: {error}")

        snapshot, error = self.event_store.read_snapshot(tenant_id, task_id, bridge.snapshot_id)
        if error or snapshot is None:
            return _fail(f"Referenced snapshot unavailable: {error}")
        if snapshot.content_hash != bridge.snapshot_hash:
            return _fail("Referenced snapshot hash mismatch (fail-closed)")

        if audit_callback:
            audit_callback(
                event_type="session_resumed", tenant_id=tenant_id, task_id=task_id,
                bridge_id=bridge_id, source_session_id=bridge.source_session_id,
                phase_completed=bridge.phase_completed, timestamp=_now(),
            )
        return {
            "bridge_id": bridge.bridge_id,
            "source_session_id": bridge.source_session_id,
            "dest_session_id": bridge.dest_session_id,
            "phase_completed": bridge.phase_completed,
            "artifacts": list(bridge.artifacts),
            "metadata": dict(bridge.metadata),
            "timestamp": bridge.timestamp,
            "snapshot_id": snapshot.snapshot_id,
            "snapshot_hash": snapshot.content_hash,
            "state_dict": dict(snapshot.state_dict),
        }, ""

    # ── storage ──────────────────────────────────────────────────────────

    def _bridge_file(self, task_id: str, bridge_id: str) -> Path:
        validate_id(bridge_id, "bridge_id")
        return safe_child(self.bridge_dir, task_id, f"{bridge_id}.json")

    def _persist_bridge(self, bridge: SessionBridgeEvent) -> Tuple[bool, str]:
        try:
            target = self._bridge_file(bridge.task_id, bridge.bridge_id)
            target.parent.mkdir(parents=True, exist_ok=True)
            tmp = target.with_name(target.name + ".tmp")
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(bridge.to_dict(), fh, indent=2, sort_keys=True)
            tmp.replace(target)
            return True, ""
        except (OSError, ValueError) as exc:
            return False, str(exc)

    def _load_bridge(
        self, tenant_id: str, task_id: str, bridge_id: str
    ) -> Tuple[Optional[SessionBridgeEvent], str]:
        error = self._bind(tenant_id)
        if error:
            return None, error
        try:
            target = self._bridge_file(task_id, bridge_id)
        except ValueError as exc:
            return None, str(exc)
        if not target.exists():
            return None, f"Bridge not found: {bridge_id}"
        try:
            with open(target, "r", encoding="utf-8") as fh:
                return SessionBridgeEvent(**json.load(fh)), ""
        except (OSError, ValueError, TypeError) as exc:
            return None, f"Failed to load bridge: {exc}"

    def list_bridges(self, tenant_id: str, task_id: str) -> Tuple[list[SessionBridgeEvent], str]:
        error = self._bind(tenant_id)
        if error:
            return [], error
        try:
            task_dir = safe_child(self.bridge_dir, task_id)
        except ValueError as exc:
            return [], str(exc)
        if not task_dir.exists():
            return [], ""
        bridges: list[SessionBridgeEvent] = []
        for bridge_file in sorted(task_dir.glob("*.json")):
            try:
                with open(bridge_file, "r", encoding="utf-8") as fh:
                    bridges.append(SessionBridgeEvent(**json.load(fh)))
            except (OSError, ValueError, TypeError) as exc:
                return [], f"Malformed bridge {bridge_file.name}: {exc} (fail-closed)"
        bridges.sort(key=lambda b: b.timestamp)
        return bridges, ""

"""Immutable Audit Trail with Hash-Chain Validation.

Every DataHub Creator event (skill generation, weight update, feedback, optimization)
is logged to an immutable, hash-chained JSONL file. The chain is verified on boot
(fail-closed if broken).

Tenant-scoped: all queries filtered by tenant_id.
"""
from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event with hash-chain linkage."""

    event_type: str  # "skill_generated", "weight_updated", "feedback_received", etc.
    tenant_id: str
    timestamp: str  # ISO 8601
    skill_id: Optional[str] = None
    skill_version: Optional[str] = None
    payload: dict = field(default_factory=dict)
    prev_hash: str = ""  # SHA256 of previous event
    hash: str = ""  # SHA256 of this event

    def compute_hash(self) -> str:
        """Compute SHA256 of this event (excluding hash field)."""
        # Create a dict without the hash field for hashing
        data = asdict(self)
        data.pop("hash", None)

        json_str = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(json_str.encode()).hexdigest()

    def __post_init__(self):
        """Validate hash chain (frozen dataclass, so use object.__setattr__)."""
        if not self.hash:
            computed = self.compute_hash()
            object.__setattr__(self, "hash", computed)


class AuditTrail:
    """Immutable audit event log with hash-chain validation."""

    def __init__(self, tenant_id: str, chain_path: Path):
        """
        Initialize audit trail.

        Args:
            tenant_id: Tenant identifier (fail-closed if None)
            chain_path: Path to JSONL audit log (e.g., ~/.corvin/tenants/_default/audit.jsonl)

        Raises:
            ValueError: if tenant_id is None or chain is broken
        """
        if not tenant_id:
            raise ValueError("tenant_id must not be None (fail-closed)")

        self.tenant_id = tenant_id
        self.chain_path = Path(chain_path)
        self.chain_path.parent.mkdir(parents=True, exist_ok=True)

        # Verify chain integrity on boot (fail-closed)
        self._verify_chain()

    def _verify_chain(self) -> None:
        """Verify hash-chain integrity. Raises ValueError if broken."""
        if not self.chain_path.exists():
            return  # Empty chain is valid

        prev_hash = ""
        with open(self.chain_path, 'r') as f:
            for line_num, line in enumerate(f, 1):
                if not line.strip():
                    continue

                try:
                    event_data = json.loads(line)
                    event = AuditEvent(**event_data)

                    # Verify prev_hash link
                    if event.prev_hash != prev_hash:
                        raise ValueError(
                            f"Chain broken at line {line_num}: "
                            f"expected prev_hash={prev_hash}, got {event.prev_hash}"
                        )

                    # Verify event hash
                    computed_hash = event.compute_hash()
                    if event.hash != computed_hash:
                        raise ValueError(
                            f"Event hash mismatch at line {line_num}: "
                            f"expected {computed_hash}, got {event.hash}"
                        )

                    prev_hash = event.hash

                except (json.JSONDecodeError, TypeError) as e:
                    raise ValueError(f"Malformed event at line {line_num}: {e}")

        logger.info(f"Audit chain verified: {line_num} events, chain intact")

    def write_event(
        self,
        event_type: str,
        skill_id: Optional[str] = None,
        skill_version: Optional[str] = None,
        payload: Optional[dict] = None,
    ) -> AuditEvent:
        """
        Write an immutable event to the audit trail.

        Args:
            event_type: Event type (e.g., "skill_generated")
            skill_id: Optional skill identifier
            skill_version: Optional skill version
            payload: Optional event-specific data (dict, will be JSON-serialized)

        Returns:
            The written AuditEvent

        Raises:
            ValueError: if write fails (fail-closed)
        """
        # Read last event to get prev_hash
        prev_hash = ""
        if self.chain_path.exists():
            with open(self.chain_path, 'r') as f:
                lines = f.readlines()
                if lines:
                    last_line = lines[-1].strip()
                    if last_line:
                        last_event = json.loads(last_line)
                        prev_hash = last_event.get("hash", "")

        # Create new event
        event = AuditEvent(
            event_type=event_type,
            tenant_id=self.tenant_id,
            timestamp=datetime.now(timezone.utc).isoformat(),
            skill_id=skill_id,
            skill_version=skill_version,
            payload=payload or {},
            prev_hash=prev_hash,
        )

        # Write to chain
        try:
            with open(self.chain_path, 'a') as f:
                f.write(json.dumps(asdict(event), default=str) + '\n')
            logger.debug(f"Audit event written: {event_type} (hash={event.hash[:8]}...)")
        except Exception as e:
            raise ValueError(f"Failed to write audit event: {e}")

        return event

    def query_events(
        self,
        event_type: Optional[str] = None,
        skill_id: Optional[str] = None,
        since: Optional[datetime] = None,
        limit: int = 100,
    ) -> list[AuditEvent]:
        """
        Query audit events with optional filtering.

        All queries are tenant-scoped (filtered by self.tenant_id).

        Args:
            event_type: Filter by event type
            skill_id: Filter by skill_id
            since: Filter by timestamp (ISO 8601 or datetime)
            limit: Max number of results

        Returns:
            List of matching AuditEvent objects (up to limit)
        """
        results = []

        if not self.chain_path.exists():
            return results

        with open(self.chain_path, 'r') as f:
            for line in f:
                if not line.strip():
                    continue

                try:
                    event_data = json.loads(line)
                    event = AuditEvent(**event_data)

                    # Tenant filter (mandatory)
                    if event.tenant_id != self.tenant_id:
                        continue

                    # Apply filters
                    if event_type and event.event_type != event_type:
                        continue
                    if skill_id and event.skill_id != skill_id:
                        continue
                    if since:
                        since_iso = since.isoformat() if isinstance(since, datetime) else since
                        if event.timestamp < since_iso:
                            continue

                    results.append(event)

                    if len(results) >= limit:
                        break

                except (json.JSONDecodeError, TypeError):
                    logger.warning(f"Skipping malformed event in audit trail")

        return results

    def export_jsonl(self, since: Optional[datetime] = None) -> str:
        """Export audit trail as JSONL (for compliance reports)."""
        events = self.query_events(since=since, limit=999999)
        return '\n'.join(
            json.dumps(asdict(e), default=str) for e in events
        )

    def verify_integrity(self) -> tuple[bool, str]:
        """
        Verify audit trail integrity.

        Returns:
            (is_valid, message)
        """
        try:
            self._verify_chain()
            return True, "Audit chain intact"
        except ValueError as e:
            return False, str(e)

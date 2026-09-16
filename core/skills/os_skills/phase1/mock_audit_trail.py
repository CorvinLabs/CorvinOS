"""Mock AuditTrail for Phase 1 development and testing.

In production, this would integrate with core/compliance/audit_chain.py.
For Phase 1, we use a simple file-based mock that:
- Writes events to a JSON file (one event per line)
- Computes hash chain (each event includes prev_hash)
- Validates tenant isolation (all reads filtered by tenant_id)
- Fails closed (write error → entire operation rejected)

Production integration path:
- Replace MockAuditTrail with real AuditTrail from core/compliance/
- Real version ensures hash-chain integrity, audit-chain verification
- Boot tripwire (ADR-0232) validates chain before any Skill runs
"""

from __future__ import annotations

from typing import Optional
from pathlib import Path
import json
import logging
import hashlib
import threading

try:
    from .base_skill import AuditTrail, SkillExecutedEvent, SkillConfigUpdatedEvent
except ImportError:
    from base_skill import AuditTrail, SkillExecutedEvent, SkillConfigUpdatedEvent

logger = logging.getLogger(__name__)


class MockAuditTrail(AuditTrail):
    """Mock audit trail: JSON lines file with hash chain.

    File format (one event per line):
    ```
    {
        "tenant_id": "_default",
        "timestamp": "2026-09-16T12:00:00.000Z",
        "event_type": "skill_executed",
        "skill_id": "os.health_monitor",
        ...
        "prev_hash": "sha256(...)",
        "hash": "sha256(...)"
    }
    ```

    Invariants:
    - All writes are append-only
    - Hash chain is unbroken (every event references previous event's hash)
    - Reads are tenant-scoped (filtered by tenant_id)
    - Thread-safe (lock-protected writes)
    """

    def __init__(self, audit_file: Optional[Path] = None):
        """Initialize mock audit trail.

        Args:
            audit_file: Path to audit log file (default: ~/.corvin/audit.jsonl)
        """
        if audit_file is None:
            audit_file = Path.home() / ".corvin" / "audit.jsonl"

        self.audit_file = Path(audit_file) if not isinstance(audit_file, Path) else audit_file
        self.audit_file.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.Lock()  # For append-only safety
        self._last_hash: Optional[str] = None

        # Load last hash from existing file
        self._reload_last_hash()

    def write_event(self, event: SkillExecutedEvent) -> bool:
        """Write skill execution event to audit trail.

        Fail-closed: if write fails, return False (skill execution rejected).
        """
        try:
            with self._lock:
                # Get previous hash
                prev_hash = self._last_hash

                # Create event with hash chain
                event_dict = {
                    "tenant_id": event.tenant_id,
                    "timestamp": event.timestamp,
                    "event_type": "skill_executed",
                    "skill_id": event.skill_id,
                    "version": event.version,
                    "input_hash": event.input_hash,
                    "output_hash": event.output_hash,
                    "status": event.status.value,
                    "latency_ms": event.latency_ms,
                    "lom": event.lom,
                    "lom_hash": event.lom_hash,
                    "error_message": event.error_message,
                    "prev_hash": prev_hash,
                }

                # Compute hash of this event
                payload = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
                event_hash = hashlib.sha256(payload.encode()).hexdigest()
                event_dict["hash"] = event_hash

                # Append to file
                with open(self.audit_file, "a") as f:
                    f.write(json.dumps(event_dict) + "\n")

                # Update last hash
                self._last_hash = event_hash

                logger.debug(
                    f"Audit event written: {event.skill_id} ({event.status.value})",
                    extra={"tenant_id": event.tenant_id}
                )

                return True

        except Exception as e:
            logger.error(
                f"Audit trail write FAILED: {e}",
                extra={"skill_id": event.skill_id, "tenant_id": event.tenant_id}
            )
            return False

    def write_config_event(self, event: SkillConfigUpdatedEvent) -> bool:
        """Write skill config update event to audit trail."""
        try:
            with self._lock:
                prev_hash = self._last_hash

                event_dict = {
                    "tenant_id": event.tenant_id,
                    "timestamp": event.timestamp,
                    "event_type": "skill_config_updated",
                    "skill_id": event.skill_id,
                    "version": event.version,
                    "param_name": event.param_name,
                    "value_before_hash": event.value_before_hash,
                    "value_after_hash": event.value_after_hash,
                    "reason": event.reason,
                    "confidence_delta": event.confidence_delta,
                    "lom": event.lom,
                    "lom_hash": event.lom_hash,
                    "prev_hash": prev_hash,
                }

                payload = json.dumps(event_dict, sort_keys=True, separators=(",", ":"))
                event_hash = hashlib.sha256(payload.encode()).hexdigest()
                event_dict["hash"] = event_hash

                with open(self.audit_file, "a") as f:
                    f.write(json.dumps(event_dict) + "\n")

                self._last_hash = event_hash
                return True

        except Exception as e:
            logger.error(f"Config audit trail write FAILED: {e}")
            return False

    def read_events(self, tenant_id: str, limit: Optional[int] = None) -> list[dict]:
        """Read audit events for a tenant (filtered by tenant_id).

        Args:
            tenant_id: Filter events by tenant
            limit: Max events to return (default: all)

        Returns:
            List of events (ordered by timestamp, most recent first)
        """
        events = []

        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        if event.get("tenant_id") == tenant_id:
                            events.append(event)
        except FileNotFoundError:
            pass  # No events yet

        # Most recent first
        events.reverse()

        if limit:
            events = events[:limit]

        return events

    def verify_chain(self, tenant_id: str) -> bool:
        """Verify hash chain integrity for a tenant.

        Returns:
            True if chain is unbroken, False if any link is broken
        """
        events = []

        try:
            with open(self.audit_file, "r") as f:
                for line in f:
                    if line.strip():
                        event = json.loads(line)
                        if event.get("tenant_id") == tenant_id:
                            events.append(event)
        except FileNotFoundError:
            return True  # No events = valid empty chain

        # Check chain integrity
        prev_hash = None
        for event in events:
            if event.get("prev_hash") != prev_hash:
                logger.error(f"Chain broken at event {event.get('timestamp')}: prev_hash mismatch")
                return False

            # Verify hash
            event_copy = event.copy()
            event_hash = event_copy.pop("hash")
            payload = json.dumps(event_copy, sort_keys=True, separators=(",", ":"))
            computed_hash = hashlib.sha256(payload.encode()).hexdigest()

            if computed_hash != event_hash:
                logger.error(f"Chain broken at event {event.get('timestamp')}: hash mismatch")
                return False

            prev_hash = event_hash

        logger.info(f"Audit chain verified for tenant {tenant_id}: {len(events)} events")
        return True

    def _reload_last_hash(self) -> None:
        """Load last event hash from file (for chain continuity)."""
        try:
            with open(self.audit_file, "r") as f:
                lines = f.readlines()
                if lines:
                    last_event = json.loads(lines[-1])
                    self._last_hash = last_event.get("hash")
        except FileNotFoundError:
            self._last_hash = None

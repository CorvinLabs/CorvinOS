"""Audit backend provider - ADR-0232/0233.

Singleton registry for audit event persistence + chain verification.
Implements appendix-only, hash-chained audit trail with tenant isolation.
"""

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional, Protocol
import threading

_logger = logging.getLogger(__name__)

# Thread-safe singleton
_lock = threading.Lock()
_active_backend: Optional['AuditBackend'] = None


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event with hash-chain binding."""
    tenant_id: str
    timestamp: str
    event_type: str
    payload: dict
    prev_hash: str = ""

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this event."""
        event_dict = asdict(self)
        # Exclude the hash from computation
        event_dict.pop('prev_hash', None)
        json_str = json.dumps(event_dict, sort_keys=True)
        return hashlib.sha256(json_str.encode()).hexdigest()


class AuditBackend(Protocol):
    """Protocol for audit backend implementations."""

    async def write_event(self, event: AuditEvent) -> bool:
        """Write an event to the audit trail.

        Args:
            event: The audit event to write

        Returns:
            True if write succeeded, False otherwise
        """
        ...

    async def verify_chain(self, tenant_id: str, since_timestamp: Optional[str] = None) -> bool:
        """Verify hash-chain integrity for a tenant.

        Args:
            tenant_id: Tenant to verify
            since_timestamp: Optional start timestamp

        Returns:
            True if chain is valid, False if tampered/broken
        """
        ...

    async def read_events(self, tenant_id: str, event_type: Optional[str] = None) -> list[AuditEvent]:
        """Read events from audit trail.

        Args:
            tenant_id: Filter by tenant
            event_type: Optional filter by event type

        Returns:
            List of audit events (appendix-only view)
        """
        ...

    async def enforce_retention(self, tenant_id: str, max_age_days: int) -> int:
        """Enforce retention policy (delete old events).

        Args:
            tenant_id: Tenant to clean
            max_age_days: Delete events older than this

        Returns:
            Number of events deleted
        """
        ...

    async def health_check(self) -> bool:
        """Check backend health."""
        ...


class DefaultAuditBackend:
    """Default in-process audit backend with file persistence."""

    def __init__(self, storage_path: Optional[Path] = None):
        """Initialize the audit backend.

        Args:
            storage_path: Path to store audit.jsonl (default: ~/.corvin/audit.jsonl)
        """
        if storage_path is None:
            storage_path = Path.home() / ".corvin" / "audit.jsonl"

        self.storage_path = storage_path
        self.storage_path.parent.mkdir(parents=True, exist_ok=True)

        # In-memory cache of last hash per tenant for atomic writes
        self._last_hash_cache: dict[str, str] = {}
        self._cache_lock = threading.Lock()
        self._dropped_events = 0  # Atomic counter for backpressure

    async def write_event(self, event: AuditEvent) -> bool:
        """Write event atomically with hash-chain verification."""
        try:
            # Get last hash for this tenant
            with self._cache_lock:
                prev_hash = self._last_hash_cache.get(event.tenant_id, "")

            # Recompute hash with correct prev_hash
            event_with_chain = AuditEvent(
                tenant_id=event.tenant_id,
                timestamp=event.timestamp,
                event_type=event.event_type,
                payload=event.payload,
                prev_hash=prev_hash
            )
            new_hash = event_with_chain.compute_hash()

            # Atomic append to file
            event_line = json.dumps({
                **asdict(event_with_chain),
                "hash": new_hash
            })

            # Thread-safe write
            with self._cache_lock:
                with open(self.storage_path, 'a') as f:
                    f.write(event_line + '\n')
                self._last_hash_cache[event.tenant_id] = new_hash

            _logger.debug(f"Audit event written: {event.event_type} for {event.tenant_id}")
            return True
        except Exception as e:
            _logger.error(f"Failed to write audit event: {e}")
            with self._cache_lock:
                self._dropped_events += 1
            return False

    async def verify_chain(self, tenant_id: str, since_timestamp: Optional[str] = None) -> bool:
        """Verify hash-chain integrity."""
        try:
            if not self.storage_path.exists():
                return True  # Empty chain is valid

            prev_hash = ""
            event_count = 0

            with open(self.storage_path, 'r') as f:
                for line in f:
                    try:
                        event_data = json.loads(line)
                        if event_data.get('tenant_id') != tenant_id:
                            continue

                        if since_timestamp and event_data.get('timestamp') < since_timestamp:
                            continue

                        event_count += 1
                        current_hash = event_data.get('hash', '')
                        current_prev = event_data.get('prev_hash', '')

                        # Verify hash chain link
                        if current_prev != prev_hash:
                            _logger.error(f"Chain broken at event {event_count}")
                            return False

                        prev_hash = current_hash
                    except json.JSONDecodeError:
                        _logger.error(f"Invalid JSON in audit trail")
                        return False

            _logger.info(f"Chain verified for {tenant_id}: {event_count} events")
            return True
        except Exception as e:
            _logger.error(f"Chain verification failed: {e}")
            return False

    async def read_events(self, tenant_id: str, event_type: Optional[str] = None) -> list[AuditEvent]:
        """Read events from audit trail (appendix-only view)."""
        events = []
        try:
            if not self.storage_path.exists():
                return events

            with open(self.storage_path, 'r') as f:
                for line in f:
                    try:
                        event_data = json.loads(line)
                        if event_data.get('tenant_id') != tenant_id:
                            continue
                        if event_type and event_data.get('event_type') != event_type:
                            continue

                        # Remove hash fields for return (hash-chain is internal)
                        event_data_copy = event_data.copy()
                        event_data_copy.pop('hash', None)
                        events.append(AuditEvent(**event_data_copy))
                    except (json.JSONDecodeError, TypeError):
                        continue
        except Exception as e:
            _logger.error(f"Failed to read audit events: {e}")

        return events

    async def enforce_retention(self, tenant_id: str, max_age_days: int) -> int:
        """Enforce retention policy."""
        if not self.storage_path.exists():
            return 0

        try:
            deleted_count = 0
            cutoff_timestamp = datetime.now(timezone.utc)

            # Read all events
            all_events = []
            with open(self.storage_path, 'r') as f:
                for line in f:
                    try:
                        all_events.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue

            # Filter out old events for this tenant
            kept_events = []
            for event in all_events:
                if event.get('tenant_id') == tenant_id:
                    event_time = datetime.fromisoformat(event.get('timestamp', ''))
                    age_days = (cutoff_timestamp - event_time).days
                    if age_days > max_age_days:
                        deleted_count += 1
                        continue
                kept_events.append(event)

            # Rewrite file with kept events
            with self._cache_lock:
                with open(self.storage_path, 'w') as f:
                    for event in kept_events:
                        f.write(json.dumps(event) + '\n')

            _logger.info(f"Deleted {deleted_count} events for {tenant_id}")
            return deleted_count
        except Exception as e:
            _logger.error(f"Retention enforcement failed: {e}")
            return 0

    async def health_check(self) -> bool:
        """Check backend health."""
        try:
            # Try to write a test event
            test_event = AuditEvent(
                tenant_id="__health_check__",
                timestamp=datetime.now(timezone.utc).isoformat(),
                event_type="health_check",
                payload={}
            )
            return await self.write_event(test_event)
        except Exception:
            return False


def get_active() -> AuditBackend:
    """Get the currently active audit backend."""
    global _active_backend
    with _lock:
        if _active_backend is None:
            _active_backend = DefaultAuditBackend()
        return _active_backend


def set_active(backend: AuditBackend) -> None:
    """Set the active audit backend (for testing)."""
    global _active_backend
    with _lock:
        _active_backend = backend

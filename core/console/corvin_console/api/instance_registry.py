"""Instance registry for peer discovery and heartbeat (Phase 9a, ADR-0451).

Manages multi-instance synchronization via:
- Instance registration (add self to known peers)
- Heartbeat updates (periodic last_heartbeat refresh)
- Stale cleanup (remove instances with no heartbeat > 30s)
- Peer listing (query active instances)

Storage: JSON Lines file at ~/.corvin/instances.json (one JSON object per line).
Tenant isolated: separate registry per tenant_id (future).
"""

from __future__ import annotations

import json
import logging
import time
import uuid
from dataclasses import dataclass, asdict
from pathlib import Path
from threading import Lock
from typing import Optional, List

logger = logging.getLogger(__name__)

# Default heartbeat intervals (seconds)
HEARTBEAT_INTERVAL_S = 5
STALE_THRESHOLD_S = 30


@dataclass
class InstanceRecord:
    """Single peer instance record."""

    instance_id: str
    endpoint_id: str  # Target endpoint name (e.g., "ubuntu-host", "windows-dev")
    url: Optional[str] = None  # HTTP(S) address (optional, derived from endpoint_id)
    last_heartbeat: float = 0  # Unix timestamp
    status: str = "online"  # "online" or "offline"
    metadata: dict = None  # Custom metadata (engine version, region, etc.)

    def __post_init__(self):
        if self.metadata is None:
            self.metadata = {}
        if self.last_heartbeat == 0:
            self.last_heartbeat = time.time()

    def to_dict(self) -> dict:
        """Serialize to dict."""
        return asdict(self)

    def is_stale(self, threshold_s: int = STALE_THRESHOLD_S) -> bool:
        """Check if instance has gone stale (no heartbeat > threshold)."""
        return (time.time() - self.last_heartbeat) > threshold_s


class InstanceRegistry:
    """Thread-safe registry of peer instances for multi-instance coordination.

    File format: one JSON object per line (JSON Lines).
    Each line: {"instance_id": "...", "endpoint_id": "...", "last_heartbeat": ..., ...}
    """

    def __init__(self, registry_path: Optional[Path] = None):
        """Initialize registry.

        Args:
            registry_path: Path to instances.json. Defaults to ~/.corvin/instances.json.
        """
        self.registry_path = registry_path or self._default_registry_path()
        self.lock = Lock()

    @staticmethod
    def _default_registry_path() -> Path:
        """Get default registry path: ~/.corvin/instances.json."""
        corvin_home = Path.home() / ".corvin"
        corvin_home.mkdir(parents=True, exist_ok=True)
        return corvin_home / "instances.json"

    def register(self, instance_id: str, endpoint_id: str, **metadata) -> InstanceRecord:
        """Register or update an instance in the registry.

        Args:
            instance_id: Unique instance identifier (UUID)
            endpoint_id: Target endpoint name (e.g., "ubuntu-host")
            **metadata: Optional metadata (version, region, etc.)

        Returns:
            The created/updated InstanceRecord.
        """
        with self.lock:
            # Read existing records
            records = self._read_all()

            # Update or create record
            record = None
            for r in records:
                if r.instance_id == instance_id:
                    r.last_heartbeat = time.time()
                    r.status = "online"
                    if metadata:
                        r.metadata.update(metadata)
                    record = r
                    break

            if not record:
                record = InstanceRecord(
                    instance_id=instance_id,
                    endpoint_id=endpoint_id,
                    metadata=metadata or {},
                )
                records.append(record)

            # Write back
            self._write_all(records)
            return record

    def heartbeat(self, instance_id: str) -> bool:
        """Update heartbeat timestamp for an instance.

        Args:
            instance_id: Instance to update

        Returns:
            True if updated, False if instance not found.
        """
        with self.lock:
            records = self._read_all()
            for r in records:
                if r.instance_id == instance_id:
                    r.last_heartbeat = time.time()
                    r.status = "online"
                    self._write_all(records)
                    return True
            return False

    def list_active(self, threshold_s: int = STALE_THRESHOLD_S) -> List[InstanceRecord]:
        """List all active (non-stale) instances.

        Args:
            threshold_s: Stale threshold in seconds.

        Returns:
            List of active InstanceRecord objects.
        """
        with self.lock:
            records = self._read_all()
            return [r for r in records if not r.is_stale(threshold_s)]

    def list_all(self) -> List[InstanceRecord]:
        """List all instances, including stale ones."""
        with self.lock:
            return self._read_all()

    def cleanup(self, threshold_s: int = STALE_THRESHOLD_S) -> int:
        """Remove stale instances and return count removed.

        Args:
            threshold_s: Stale threshold in seconds.

        Returns:
            Number of instances removed.
        """
        with self.lock:
            records = self._read_all()
            before_count = len(records)
            records = [r for r in records if not r.is_stale(threshold_s)]
            removed_count = before_count - len(records)

            if removed_count > 0:
                self._write_all(records)
                logger.info(f"InstanceRegistry cleanup: removed {removed_count} stale instances")

            return removed_count

    def get(self, instance_id: str) -> Optional[InstanceRecord]:
        """Get a specific instance by ID."""
        with self.lock:
            records = self._read_all()
            for r in records:
                if r.instance_id == instance_id:
                    return r
            return None

    def remove(self, instance_id: str) -> bool:
        """Remove an instance from the registry.

        Args:
            instance_id: Instance to remove.

        Returns:
            True if removed, False if not found.
        """
        with self.lock:
            records = self._read_all()
            before_count = len(records)
            records = [r for r in records if r.instance_id != instance_id]
            removed = len(records) < before_count

            if removed:
                self._write_all(records)

            return removed

    def _read_all(self) -> List[InstanceRecord]:
        """Read all records from file (not thread-safe, use lock outside)."""
        if not self.registry_path.exists():
            return []

        records = []
        try:
            with open(self.registry_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                        records.append(InstanceRecord(**data))
                    except Exception as exc:
                        logger.warning(f"Skipping malformed registry line: {exc}")
        except Exception as exc:
            logger.error(f"Failed to read instance registry: {exc}")

        return records

    def _write_all(self, records: List[InstanceRecord]) -> None:
        """Write all records to file (not thread-safe, use lock outside)."""
        try:
            self.registry_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.registry_path, "w") as f:
                for record in records:
                    f.write(json.dumps(record.to_dict()) + "\n")
        except Exception as exc:
            logger.error(f"Failed to write instance registry: {exc}")


# Global registry singleton (per-tenant would use different path)
_global_registry: Optional[InstanceRegistry] = None


def get_registry() -> InstanceRegistry:
    """Get or create global instance registry."""
    global _global_registry
    if _global_registry is None:
        _global_registry = InstanceRegistry()
    return _global_registry

"""AuditChainWriter — hash-chained audit logging (Phase 0).

Implements:
1. Append-only hash-chained audit log
2. GDPR Art. 30/32 requirements (record-keeping, integrity)
3. Tamper detection via continuous verification
4. RFC 3161 timestamp server integration (future)
"""

from __future__ import annotations

import hashlib
import json
import threading
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4


@dataclass(frozen=True)
class AuditEvent:
    """Immutable audit event for hash-chaining."""

    event_id: str
    event_type: str  # "access", "modify", "delete", "auth", "error", etc.
    tenant_id: str
    user_id: Optional[str]
    timestamp: str  # ISO 8601
    details: dict[str, Any] = field(default_factory=dict)
    severity: Optional[str] = None  # "info", "warning", "error", "critical"

    def to_json(self) -> str:
        """Serialize to JSON (deterministic for hashing)."""
        data = asdict(self)
        return json.dumps(data, sort_keys=True, separators=(",", ":"))


class AuditChainWriter:
    """Hash-chained audit logger (GDPR Art. 30, 32).

    Guarantees:
    - Events are immutable (append-only)
    - Hash-chained for tamper detection
    - Atomic writes (no partial records)
    - Fail-closed (raise exception on write failure)
    """

    GENESIS_HASH = hashlib.sha256(b"audit.chain.genesis").hexdigest()
    VERSION = "1.0"

    def __init__(self, log_path: str | Path):
        """Initialize audit chain writer.

        Args:
            log_path: Path to audit.jsonl file
        """
        self.log_path = Path(log_path)
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self._lock = threading.RLock()
        self._last_hash = self.GENESIS_HASH
        self._event_count = 0

        # Load existing chain state on init
        self._load_chain_state()

    def _load_chain_state(self) -> None:
        """Load the last hash from existing chain."""
        if not self.log_path.exists():
            return

        try:
            with open(self.log_path, "r") as f:
                lines = f.readlines()

            if lines:
                last_line = lines[-1].strip()
                if last_line:
                    last_entry = json.loads(last_line)
                    self._last_hash = last_entry.get("hash", self.GENESIS_HASH)
                    self._event_count = len(lines)

        except (json.JSONDecodeError, IOError) as e:
            raise ValueError(f"Failed to load audit chain: {e}")

    def write_event(self, event: AuditEvent) -> str:
        """Write audit event with hash-chaining.

        Args:
            event: AuditEvent to write

        Returns:
            Hash of the written event

        Raises:
            IOError: If write fails (fail-closed)
        """
        with self._lock:
            # Serialize event
            event_json = event.to_json()

            # Compute hash: H(prev_hash || event_json)
            combined = (self._last_hash + event_json).encode("utf-8")
            event_hash = hashlib.sha256(combined).hexdigest()

            # Create audit record
            record = {
                "event_id": event.event_id,
                "event_type": event.event_type,
                "tenant_id": event.tenant_id,
                "user_id": event.user_id,
                "timestamp": event.timestamp,
                "details": event.details,
                "severity": event.severity,
                "hash": event_hash,
                "prev_hash": self._last_hash,
                "sequence": self._event_count,
            }

            # Append to file (atomic single write)
            try:
                with open(self.log_path, "a") as f:
                    f.write(json.dumps(record) + "\n")
                    f.flush()  # Ensure data is written to disk

                # Update in-memory state
                self._last_hash = event_hash
                self._event_count += 1

                return event_hash

            except IOError as e:
                # Fail-closed: raise exception, don't silently fail
                raise IOError(f"Failed to write audit event: {e}")

    def write_event_dict(
        self,
        event_type: str,
        tenant_id: str,
        user_id: Optional[str] = None,
        details: Optional[dict] = None,
        severity: Optional[str] = None,
    ) -> str:
        """Convenience method to write event from dict."""
        event = AuditEvent(
            event_id=str(uuid4()),
            event_type=event_type,
            tenant_id=tenant_id,
            user_id=user_id,
            timestamp=datetime.utcnow().isoformat(),
            details=details or {},
            severity=severity,
        )
        return self.write_event(event)

    def verify_chain(self) -> bool:
        """Verify hash chain integrity.

        Returns:
            True if chain is valid, False if corrupted
        """
        if not self.log_path.exists():
            return True  # Empty chain is valid

        try:
            with open(self.log_path, "r") as f:
                lines = f.readlines()

            prev_hash = self.GENESIS_HASH

            for line_idx, line in enumerate(lines):
                if not line.strip():
                    continue

                entry = json.loads(line)
                stored_hash = entry.get("hash")
                expected_prev = entry.get("prev_hash")

                # Verify previous hash
                if expected_prev != prev_hash:
                    print(
                        f"ERROR: Chain broken at line {line_idx}: "
                        f"expected prev={prev_hash}, got {expected_prev}"
                    )
                    return False

                # Reconstruct event and recompute hash
                event = AuditEvent(
                    event_id=entry["event_id"],
                    event_type=entry["event_type"],
                    tenant_id=entry["tenant_id"],
                    user_id=entry.get("user_id"),
                    timestamp=entry["timestamp"],
                    details=entry.get("details", {}),
                    severity=entry.get("severity"),
                )
                event_json = event.to_json()

                combined = (prev_hash + event_json).encode("utf-8")
                computed_hash = hashlib.sha256(combined).hexdigest()

                # Compare
                if computed_hash != stored_hash:
                    print(
                        f"ERROR: Hash mismatch at line {line_idx}: "
                        f"expected {stored_hash}, computed {computed_hash}"
                    )
                    return False

                prev_hash = stored_hash

            return True

        except (json.JSONDecodeError, IOError) as e:
            print(f"ERROR: Failed to verify chain: {e}")
            return False

    def get_last_hash(self) -> str:
        """Get the hash of the most recent event."""
        return self._last_hash

    def get_event_count(self) -> int:
        """Get total event count."""
        return self._event_count

    def read_events(self, tenant_id: Optional[str] = None, limit: int = 1000) -> list[AuditEvent]:
        """Read audit events, optionally filtered by tenant."""
        if not self.log_path.exists():
            return []

        events = []

        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    entry = json.loads(line)

                    if tenant_id and entry.get("tenant_id") != tenant_id:
                        continue

                    event = AuditEvent(
                        event_id=entry["event_id"],
                        event_type=entry["event_type"],
                        tenant_id=entry["tenant_id"],
                        user_id=entry.get("user_id"),
                        timestamp=entry["timestamp"],
                        details=entry.get("details", {}),
                        severity=entry.get("severity"),
                    )
                    events.append(event)

                    if len(events) >= limit:
                        break

            return events

        except (json.JSONDecodeError, IOError):
            return []

    def get_stats(self) -> dict[str, Any]:
        """Get audit chain statistics."""
        events_by_type = {}
        events_by_tenant = {}

        try:
            with open(self.log_path, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    entry = json.loads(line)
                    event_type = entry.get("event_type")
                    tenant_id = entry.get("tenant_id")

                    events_by_type[event_type] = events_by_type.get(event_type, 0) + 1
                    events_by_tenant[tenant_id] = events_by_tenant.get(tenant_id, 0) + 1

        except (json.JSONDecodeError, IOError):
            pass

        return {
            "total_events": self._event_count,
            "events_by_type": events_by_type,
            "events_by_tenant": events_by_tenant,
            "last_hash": self._last_hash,
            "log_path": str(self.log_path),
            "chain_verified": self.verify_chain(),
        }

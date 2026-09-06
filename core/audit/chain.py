"""
Hash-Chained Audit Log — ADR-0299

Immutable audit trail with SHA256 hash chain.
Every entry links to prior entry via hash. Tampering detected immediately.
"""

import hashlib
import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Any, Optional


@dataclass
class AuditEntry:
    """One audit log entry."""

    event_type: str
    actor: str
    action: str
    resource: str
    result: str  # success | failure
    timestamp: str
    tenant_id: str  # Multi-tenant isolation (GDPR Art. 5, 32)
    details: Optional[dict[str, Any]] = None
    prior_hash: str = "genesis"  # Hash of prior entry
    self_hash: str = ""  # Hash of this entry (computed)

    def compute_hash(self) -> str:
        """Compute SHA256 hash of this entry (excluding self_hash)."""
        # Create dict without self_hash
        content = {k: v for k, v in asdict(self).items() if k != "self_hash"}

        # JSON serialize (deterministic)
        json_str = json.dumps(content, sort_keys=True, separators=(",", ":"))

        # SHA256
        return hashlib.sha256(json_str.encode()).hexdigest()

    def finalize(self) -> None:
        """Compute and set self_hash."""
        self.self_hash = self.compute_hash()


class ChainVerificationError(Exception):
    """Raised when audit chain verification fails."""

    pass


class AuditChain:
    """Hash-chained audit log with fsync durability."""

    def __init__(self, log_file: Path):
        """Initialize audit chain with file path."""
        self.log_file = Path(log_file)
        self._entries: list[AuditEntry] = []
        self._load_existing()

    def _load_existing(self) -> None:
        """Load existing entries from file."""
        if not self.log_file.exists():
            return

        try:
            with open(self.log_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    data = json.loads(line)
                    entry = AuditEntry(
                        event_type=data["event_type"],
                        actor=data["actor"],
                        action=data["action"],
                        resource=data["resource"],
                        result=data["result"],
                        timestamp=data["timestamp"],
                        # Serialized by asdict() since ADR-0007 but never read
                        # back: every reload of a chain raised
                        # ChainVerificationError ("missing tenant_id").
                        tenant_id=data.get("tenant_id", "_default"),
                        details=data.get("details"),
                        prior_hash=data.get("prior_hash", "genesis"),
                        self_hash=data.get("self_hash", ""),
                    )
                    self._entries.append(entry)
        except Exception as e:
            raise ChainVerificationError(f"Failed to load audit chain: {e}")

    def record(self, entry: AuditEntry) -> None:
        """Record an entry in the audit chain."""
        # Set prior_hash to last entry's hash
        if self._entries:
            entry.prior_hash = self._entries[-1].self_hash
        else:
            entry.prior_hash = "genesis"

        # Compute self_hash
        entry.finalize()

        # Append to entries
        self._entries.append(entry)

        # Write to file (fsync for durability)
        self._write_entry_to_file(entry)

    def _write_entry_to_file(self, entry: AuditEntry) -> None:
        """Write entry to audit log file with fsync."""
        # Ensure directory exists
        self.log_file.parent.mkdir(parents=True, exist_ok=True)

        # Append JSON line
        with open(self.log_file, "a") as f:
            json.dump(asdict(entry), f)
            f.write("\n")
            f.flush()
            os.fsync(f.fileno())  # Force OS to sync to disk

    def verify_chain(self) -> bool:
        """Verify integrity of entire audit chain."""
        if not self._entries:
            return True

        prior_hash = "genesis"

        for entry in self._entries:
            # Verify prior_hash matches
            if entry.prior_hash != prior_hash:
                raise ChainVerificationError(
                    f"Chain broken at {entry.event_type}: "
                    f"prior_hash {entry.prior_hash} != expected {prior_hash}"
                )

            # Verify self_hash is correct
            expected_hash = entry.compute_hash()
            if entry.self_hash != expected_hash:
                raise ChainVerificationError(
                    f"Entry tampering detected at {entry.event_type}: "
                    f"self_hash {entry.self_hash} != expected {expected_hash}"
                )

            prior_hash = entry.self_hash

        return True

    def get_entries(self) -> list[AuditEntry]:
        """Get all entries (immutable copy)."""
        import copy
        return copy.deepcopy(self._entries)

    def last_hash(self) -> str:
        """Get hash of last entry (for attestation)."""
        if self._entries:
            return self._entries[-1].self_hash
        return "genesis"

    def entry_count(self) -> int:
        """Get number of entries."""
        return len(self._entries)

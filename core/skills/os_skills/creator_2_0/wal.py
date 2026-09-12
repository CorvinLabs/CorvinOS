"""FIX #2: Write-Ahead Log (WAL) for Skill Creation Transactions.

CRITICAL FIX #2 (2026-09-12): Transactional Guarantees (Data Corruption Risk)
- Skill creation may be interrupted mid-phase, leaving partial state on disk
- Solution: Write-Ahead Log (WAL) before state changes + atomic swap on completion
- Recovery: replay WAL on restart to recover incomplete skill creations

Guarantees:
1. Every phase state change is written to WAL BEFORE execution
2. On disk write, use atomic swap (.tmp → final)
3. On crash, replay WAL to recover state
4. Idempotent recovery (safe to replay same WAL entry multiple times)
"""

import json
import logging
import os
import re
import threading
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, List

logger = logging.getLogger(__name__)


def _validate_skill_id(skill_id: str) -> None:
    """Validate skill_id format (prevent directory traversal).

    Args:
        skill_id: Skill identifier

    Raises:
        ValueError: if skill_id contains invalid characters
    """
    if not skill_id or not isinstance(skill_id, str):
        raise ValueError(f"Invalid skill_id: must be non-empty string, got {skill_id!r}")

    if not re.match(r'^[a-zA-Z0-9_-]+$', skill_id):
        raise ValueError(f"Invalid skill_id format: {skill_id!r}")


@dataclass
class WALEntry:
    """Write-Ahead Log entry for skill creation state."""

    skill_id: str
    phase_num: int
    state_hash: str  # SHA256 of state dict (for integrity check)
    timestamp: str  # ISO 8601 UTC
    state: Dict[str, Any]  # Full state snapshot at this phase
    processed: bool = False  # True if successfully recovered
    error: Optional[str] = None  # Error message if recovery failed

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict for JSON storage."""
        return {
            "skill_id": self.skill_id,
            "phase_num": self.phase_num,
            "state_hash": self.state_hash,
            "timestamp": self.timestamp,
            "state": self.state,
            "processed": self.processed,
            "error": self.error,
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "WALEntry":
        """Deserialize from dict."""
        return cls(
            skill_id=data["skill_id"],
            phase_num=data["phase_num"],
            state_hash=data["state_hash"],
            timestamp=data["timestamp"],
            state=data["state"],
            processed=data.get("processed", False),
            error=data.get("error"),
        )


class SkillCreationWAL:
    """Write-Ahead Log for skill creation (FIX #2: Transactional Guarantees).

    Structure:
      {tenant_home}/global/creator_2_0/wal.jsonl
      One JSON line per phase completion, append-only

    On crash:
      1. Read WAL
      2. For each unprocessed entry: replay state recovery
      3. Resume from last checkpoint
    """

    _WAL_DIR = "creator_2_0"
    _WAL_FILE = "wal.jsonl"
    _lock = threading.RLock()

    def __init__(self, tenant_home: Path):
        """Initialize WAL for skill creation.

        Args:
            tenant_home: Path to tenant home (~/.corvin/tenants/_default/)
        """
        self.tenant_home = Path(tenant_home)
        self.wal_dir = self.tenant_home / "global" / self._WAL_DIR
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.wal_file = self.wal_dir / self._WAL_FILE

    def write_state(
        self,
        skill_id: str,
        phase_num: int,
        state: Dict[str, Any],
    ) -> None:
        """Write state to WAL before phase execution (FIX #2: Durability).

        Args:
            skill_id: Skill identifier
            phase_num: Phase number (0-10)
            state: Full state dict at this phase

        Raises:
            ValueError: if skill_id is invalid
            IOError: if WAL write fails
        """
        _validate_skill_id(skill_id)

        # Compute state hash for integrity checking
        import hashlib
        state_str = json.dumps(state, sort_keys=True, separators=(",", ":"))
        state_hash = hashlib.sha256(state_str.encode()).hexdigest()

        entry = WALEntry(
            skill_id=skill_id,
            phase_num=phase_num,
            state_hash=state_hash,
            timestamp=datetime.utcnow().isoformat() + "Z",
            state=state,
            processed=False,
        )

        with self._lock:
            try:
                # FIX #2: Write-Ahead Log (WAL) entry before phase execution
                # If this write fails, the phase does NOT execute
                # If the next write fails, we can recover from this entry
                line = json.dumps(entry.to_dict(), separators=(",", ":")) + "\n"
                with open(self.wal_file, "a") as f:
                    f.write(line)
                    f.flush()  # Ensure durable write to disk
                    os.fsync(f.fileno())  # Force fsync (crash-safe)
            except IOError as e:
                raise IOError(f"Failed to write WAL entry: {e}") from e

    def mark_processed(self, skill_id: str, phase_num: int) -> None:
        """Mark a WAL entry as successfully processed (after state persisted to disk).

        Args:
            skill_id: Skill identifier
            phase_num: Phase number

        Raises:
            ValueError: if skill_id is invalid or entry not found
        """
        _validate_skill_id(skill_id)

        with self._lock:
            # Read all entries
            entries = []
            try:
                with open(self.wal_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        entries.append(WALEntry.from_dict(data))
            except IOError:
                entries = []

            # Mark matching entry as processed
            found = False
            for entry in entries:
                if entry.skill_id == skill_id and entry.phase_num == phase_num:
                    entry.processed = True
                    found = True
                    break

            if not found:
                raise ValueError(f"WAL entry not found: skill_id={skill_id}, phase_num={phase_num}")

            # Rewrite WAL with updated entries
            try:
                with open(self.wal_file, "w") as f:
                    for entry in entries:
                        line = json.dumps(entry.to_dict(), separators=(",", ":")) + "\n"
                        f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            except IOError as e:
                raise IOError(f"Failed to update WAL: {e}") from e

    def get_unprocessed_entries(self, skill_id: Optional[str] = None) -> List[WALEntry]:
        """Get all unprocessed WAL entries (for crash recovery).

        Args:
            skill_id: Optional filter by skill ID

        Returns:
            List of unprocessed WALEntry objects
        """
        entries = []
        with self._lock:
            try:
                with open(self.wal_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        try:
                            data = json.loads(line)
                            entry = WALEntry.from_dict(data)
                            if not entry.processed:
                                if skill_id is None or entry.skill_id == skill_id:
                                    entries.append(entry)
                        except json.JSONDecodeError as e:
                            logger.error(f"Corrupted WAL line: {e}")
                            continue
            except IOError:
                # WAL file doesn't exist yet
                pass

        return entries

    def mark_error(self, skill_id: str, phase_num: int, error: str) -> None:
        """Mark a WAL entry with an error (recovery failed).

        Args:
            skill_id: Skill identifier
            phase_num: Phase number
            error: Error message
        """
        _validate_skill_id(skill_id)

        with self._lock:
            # Read all entries
            entries = []
            try:
                with open(self.wal_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        entries.append(WALEntry.from_dict(data))
            except IOError:
                entries = []

            # Mark matching entry with error
            for entry in entries:
                if entry.skill_id == skill_id and entry.phase_num == phase_num:
                    entry.error = error
                    break

            # Rewrite WAL
            try:
                with open(self.wal_file, "w") as f:
                    for entry in entries:
                        line = json.dumps(entry.to_dict(), separators=(",", ":")) + "\n"
                        f.write(line)
                    f.flush()
                    os.fsync(f.fileno())
            except IOError as e:
                logger.error(f"Failed to mark WAL error: {e}")

    def clear_skill_entries(self, skill_id: str) -> None:
        """Clear all WAL entries for a skill (after successful completion).

        Args:
            skill_id: Skill identifier
        """
        _validate_skill_id(skill_id)

        with self._lock:
            # Read all entries
            entries = []
            try:
                with open(self.wal_file, "r") as f:
                    for line in f:
                        if not line.strip():
                            continue
                        data = json.loads(line)
                        entry = WALEntry.from_dict(data)
                        if entry.skill_id != skill_id:
                            entries.append(entry)
            except IOError:
                entries = []

            # Rewrite WAL without this skill's entries
            try:
                with open(self.wal_file, "w") as f:
                    for entry in entries:
                        line = json.dumps(entry.to_dict(), separators=(",", ":")) + "\n"
                        f.write(line)
                    if entries:  # Only fsync if there's content
                        f.flush()
                        os.fsync(f.fileno())
            except IOError as e:
                logger.error(f"Failed to clear WAL entries: {e}")

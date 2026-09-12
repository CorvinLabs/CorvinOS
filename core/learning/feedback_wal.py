"""Phase 4: Feedback Write-Ahead Log (WAL) — Durable, crash-recoverable feedback persistence.

ADR-0661/0662: Feedback → Learning Loop (Write-Ahead Log Pattern)

Feedback events are written to WAL BEFORE daemon processes them, ensuring:
1. **Durability:** Feedback survives daemon crash
2. **Idempotency:** Daemon can safely re-process from WAL on restart
3. **Ordering:** FIFO processing order preserved
4. **Batching:** Daemon polls WAL (0.1s cadence), batches bursts smoothly
5. **Audit trail:** WAL is SSOT until daemon marks processed

Flow:
  1. FeedbackSink.record() → WAL.append(feedback) [blocking, durable]
  2. Daemon polls WAL.get_unprocessed() every 0.1s
  3. Daemon processes feedback → updates weights
  4. Daemon marks WAL entry as processed
  5. Audit trail written after processing (secondary)
  6. On daemon crash: resume from last processed marker

GDPR/Compliance:
- User feedback scrubbed (masked user_id, no reason text)
- WAL stored in tenant-scoped directory
- Retention policy: 90 days (ADR-0665)
"""

import asyncio
import hashlib
import json
import logging
import os
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from uuid import uuid4

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class FeedbackWALEntry:
    """Immutable WAL entry (frozen dataclass)."""

    entry_id: str  # UUID4
    feedback_id: str  # Reference to original feedback event
    skill_id: str
    task_id: str
    tenant_id: str
    timestamp: str  # ISO 8601 UTC
    signal: float  # [-1.0, +1.0]
    processed: bool = False
    processed_at: Optional[str] = None
    process_error: Optional[str] = None
    data_sources: List[str] = field(default_factory=list)  # Phase 4: attribution metadata

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict."""
        return asdict(self)

    def mark_processed(
        self, error: Optional[str] = None
    ) -> "FeedbackWALEntry":
        """Return a new entry with processed flag set (immutability)."""
        return FeedbackWALEntry(
            entry_id=self.entry_id,
            feedback_id=self.feedback_id,
            skill_id=self.skill_id,
            task_id=self.task_id,
            tenant_id=self.tenant_id,
            timestamp=self.timestamp,
            signal=self.signal,
            processed=True,
            processed_at=datetime.utcnow().isoformat(),
            process_error=error,
        )


class FeedbackWAL:
    """Write-Ahead Log for feedback events.

    Stores feedback durably before daemon processes; enables crash recovery.
    """

    def __init__(
        self, wal_dir: Path, tenant_id: str = "_default", retention_days: int = 90
    ):
        self.wal_dir = wal_dir
        self.tenant_id = tenant_id
        self.retention_days = retention_days

        # Tenant-scoped WAL directory (GDPR Art. 32)
        self.tenant_wal_dir = wal_dir / f"feedback_wal_{tenant_id}"
        self.tenant_wal_dir.mkdir(parents=True, exist_ok=True)

        # WAL file (append-only)
        self.wal_file = self.tenant_wal_dir / "feedback.jsonl"

        # In-memory cache of last-read markers (for fast lookup)
        self.last_processed_entry_id: Optional[str] = None
        self._load_processing_state()

        logger.info(
            f"FeedbackWAL initialized: {self.wal_file} (tenant={tenant_id})"
        )

    def _load_processing_state(self) -> None:
        """Load the last processed entry ID from disk (recovery on restart)."""
        state_file = self.tenant_wal_dir / "processing_state.json"
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                    self.last_processed_entry_id = state.get(
                        "last_processed_entry_id"
                    )
                    logger.info(
                        f"Recovered processing state: {self.last_processed_entry_id}"
                    )
            except Exception as e:
                logger.error(f"Error loading processing state: {e}")

    async def append(self, feedback: Dict[str, Any]) -> FeedbackWALEntry:
        """
        Append feedback to WAL (blocking, durable).

        Returns: WAL entry ID for tracking.
        """
        entry_id = str(uuid4())
        # Use provided timestamp or current time
        timestamp = feedback.get("timestamp")
        if timestamp is None:
            timestamp = datetime.utcnow().isoformat()

        entry = FeedbackWALEntry(
            entry_id=entry_id,
            feedback_id=feedback.get("feedback_id", f"fb-{entry_id}"),
            skill_id=feedback.get("skill_id"),
            task_id=feedback.get("task_id"),
            tenant_id=feedback.get("tenant_id", self.tenant_id),
            timestamp=timestamp,
            signal=feedback.get("signal", 0.0),
            processed=False,
            data_sources=feedback.get("data_sources", []),  # Phase 4: attribution
        )

        # Write to WAL (append-only, durable)
        try:
            with open(self.wal_file, "a") as f:
                f.write(json.dumps(entry.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())  # Force durability

            logger.debug(f"Appended to WAL: {entry_id}")
            return entry
        except Exception as e:
            logger.error(f"Error appending to WAL: {e}")
            raise

    async def get_unprocessed(self, batch_size: int = 100) -> List[FeedbackWALEntry]:
        """
        Get unprocessed feedback entries from WAL.

        Returns: list of unprocessed entries (FIFO), up to batch_size.
        """
        if not self.wal_file.exists():
            return []

        unprocessed = []
        try:
            with open(self.wal_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    entry_dict = json.loads(line)
                    entry = FeedbackWALEntry(**entry_dict)

                    # Skip if already processed
                    if entry.processed:
                        self.last_processed_entry_id = entry.entry_id
                        continue

                    # Stop after batch_size
                    if len(unprocessed) >= batch_size:
                        break

                    unprocessed.append(entry)

            return unprocessed
        except Exception as e:
            logger.error(f"Error reading WAL: {e}")
            return []

    async def mark_processed(
        self, entry_id: str, error: Optional[str] = None
    ) -> bool:
        """
        Mark a WAL entry as processed (rewrite WAL with updated entry).

        This is a bit expensive (O(n) rewrite), but simple and correct.
        For high throughput, could use a separate processed-entries file.
        """
        if not self.wal_file.exists():
            return False

        try:
            # Read entire WAL
            entries = []
            with open(self.wal_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue
                    entry_dict = json.loads(line)
                    entry = FeedbackWALEntry(**entry_dict)

                    # Update the target entry
                    if entry.entry_id == entry_id:
                        entry = entry.mark_processed(error)
                        self.last_processed_entry_id = entry_id

                    entries.append(entry)

            # Rewrite WAL (atomic: write to temp, then rename)
            temp_file = self.wal_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                for entry in entries:
                    f.write(json.dumps(entry.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())

            # Atomic rename
            temp_file.replace(self.wal_file)

            # Update processing state (recovery marker)
            self._save_processing_state()

            logger.debug(f"Marked processed: {entry_id}")
            return True
        except Exception as e:
            logger.error(f"Error marking processed: {e}")
            return False

    def _save_processing_state(self) -> None:
        """Persist processing state for crash recovery."""
        state_file = self.tenant_wal_dir / "processing_state.json"
        try:
            with open(state_file, "w") as f:
                json.dump(
                    {
                        "last_processed_entry_id": self.last_processed_entry_id,
                        "timestamp": datetime.utcnow().isoformat(),
                    },
                    f,
                )
        except Exception as e:
            logger.error(f"Error saving processing state: {e}")

    async def retention_cleanup(self) -> int:
        """
        Archive processed feedback older than retention_days.

        Returns: count of entries archived.
        """
        if not self.wal_file.exists():
            return 0

        cutoff_time = datetime.now(timezone.utc) - timedelta(
            days=self.retention_days
        )
        archived_count = 0

        try:
            entries_to_keep = []
            with open(self.wal_file, "r") as f:
                for line in f:
                    if not line.strip():
                        continue

                    entry_dict = json.loads(line)
                    entry = FeedbackWALEntry(**entry_dict)

                    # Parse timestamp (handle both naive and aware datetimes)
                    timestamp_str = entry.timestamp.replace("Z", "+00:00")
                    timestamp = datetime.fromisoformat(timestamp_str)

                    # Make naive if needed (for comparison with cutoff_time)
                    if timestamp.tzinfo is None and cutoff_time.tzinfo is not None:
                        timestamp = timestamp.replace(tzinfo=timezone.utc)
                    elif timestamp.tzinfo is not None and cutoff_time.tzinfo is None:
                        cutoff_time = cutoff_time.replace(tzinfo=timezone.utc)

                    # Keep if recent or unprocessed
                    if timestamp >= cutoff_time or not entry.processed:
                        entries_to_keep.append(entry)
                    else:
                        archived_count += 1

            # Rewrite WAL with kept entries
            temp_file = self.wal_file.with_suffix(".tmp")
            with open(temp_file, "w") as f:
                for entry in entries_to_keep:
                    f.write(json.dumps(entry.to_dict()) + "\n")
                f.flush()
                os.fsync(f.fileno())

            temp_file.replace(self.wal_file)
            logger.info(
                f"Archived {archived_count} feedback entries (>= {self.retention_days} days old)"
            )
            return archived_count
        except Exception as e:
            logger.error(f"Retention cleanup error: {e}")
            return 0

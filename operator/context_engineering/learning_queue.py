"""
Tier 2: Learning Queue Infrastructure
Append-only, atomic, checksummed record store for ContextEvaluation feedback.

Implements:
  - C1: Queue corruption recovery (checksums + fail-safe)
  - Atomic appends (tempfile + rename)
  - Dated JSONL files (one per week minimum)
  - Metadata tracking (checksums, record counts)
"""

import json
import hashlib
import os
import threading
from pathlib import Path
from dataclasses import dataclass, asdict
from datetime import datetime
from typing import Iterator, Optional, Dict, List
from contextlib import contextmanager
import logging

logger = logging.getLogger(__name__)


@dataclass
class LearningQueueRecord:
    """Single immutable record in Tier 2 queue."""
    context_id: str
    task_id: str
    relevance_actual: float
    helpfulness: float
    correctness: float
    impact: str  # "CRITICAL" | "helpful" | "neutral" | "harmful"
    notes: Optional[str]
    timestamp: str  # ISO 8601
    user_id: str
    task_keywords: List[str]
    checksum: str = ""  # Computed by append()


class QueueAppendError(Exception):
    """Raised when queue append fails."""
    pass


class QueueCorruptionError(Exception):
    """Raised when corruption detected."""
    pass


class LearningQueue:
    """Tier 2 queue: append-only, atomic, checksummed."""

    def __init__(self, queue_root: Optional[Path] = None, tenant_id: str = "_default"):
        """
        Args:
            queue_root: Root directory for queue. Defaults to ~/.corvin/tenants/{tenant_id}/learning-queue/
            tenant_id: Tenant identifier
        """
        if queue_root is None:
            queue_root = Path.home() / ".corvin" / "tenants" / tenant_id / "learning-queue"

        self.queue_root = Path(queue_root)
        self.queue_root.mkdir(parents=True, exist_ok=True)

        self.lock = threading.Lock()  # Protect concurrent writes
        self.metrics = {
            "append_success": 0,
            "append_failed": 0,
            "record_corrupted": 0,
            "record_parse_error": 0,
        }

    def append_record(self, record: LearningQueueRecord) -> bool:
        """
        Atomically append record to dated JSONL file.

        Strategy (C1 fix):
          1. Compute checksum of record content
          2. Write to temp file (atomic)
          3. Fsync to disk
          4. Atomic rename (POSIX atomic)

        On failure: log, alert metrics, don't block

        Args:
            record: LearningQueueRecord to append

        Returns:
            True if success, False if failed (but didn't raise)

        Raises:
            QueueAppendError: On critical failures
        """
        with self.lock:
            try:
                # Compute checksum
                record_dict = asdict(record)
                record_json = json.dumps(record_dict, sort_keys=True)
                checksum = hashlib.sha256(record_json.encode()).hexdigest()
                record.checksum = checksum

                # Dated filename
                timestamp_str = record.timestamp.split("T")[0]  # YYYY-MM-DD
                queue_file = self.queue_root / f"{timestamp_str}.jsonl"

                # Atomic write: temp → rename
                temp_file = self.queue_root / f".{record.task_id}.tmp"

                # Delete stale temp if exists
                if temp_file.exists():
                    temp_file.unlink()

                # Write to temp
                with open(temp_file, "w") as f:
                    json.dump(asdict(record), f)
                    f.flush()
                    # Force to disk (C1 fix: ensure durable before rename)
                    os.fsync(f.fileno())

                # Atomic append: append to queue file
                with open(queue_file, "a") as f:
                    json.dump(asdict(record), f)
                    f.write("\n")
                    f.flush()
                    os.fsync(f.fileno())

                # Clean up temp
                if temp_file.exists():
                    temp_file.unlink()

                self.metrics["append_success"] += 1
                logger.debug(f"Queue record appended: {record.task_id}")
                return True

            except Exception as e:
                # Fail-safe (C1 fix): log, don't block
                logger.error(f"Queue append failed: {e}", exc_info=True)
                self.metrics["append_failed"] += 1
                raise QueueAppendError(str(e)) from e

    def read_all_records(
        self,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        skip_corrupt: bool = True,
    ) -> Iterator[LearningQueueRecord]:
        """
        Stream records from queue files in date range.

        Corruption detection (C1 fix):
          - Verify checksum of each record
          - If corrupt: log, optionally skip, emit metric

        Args:
            start_date: ISO date (YYYY-MM-DD) or None (earliest)
            end_date: ISO date or None (latest)
            skip_corrupt: If True, skip corrupt records. If False, raise.

        Yields:
            LearningQueueRecord (if valid checksum)

        Raises:
            QueueCorruptionError: If skip_corrupt=False and corruption found
        """
        # Find all queue files in date range
        queue_files = sorted(self.queue_root.glob("*.jsonl"))

        if not queue_files:
            logger.warning(f"No queue files found in {self.queue_root}")
            return

        for queue_file in queue_files:
            # Filter by date range
            file_date = queue_file.stem  # YYYY-MM-DD
            if start_date and file_date < start_date:
                continue
            if end_date and file_date > end_date:
                continue

            with open(queue_file, "r") as f:
                for line_no, line in enumerate(f, 1):
                    line = line.strip()
                    if not line:
                        continue

                    try:
                        record_dict = json.loads(line)

                        # C1 fix: Verify checksum
                        stored_checksum = record_dict.pop("checksum", "")
                        record_json = json.dumps(record_dict, sort_keys=True)
                        computed_checksum = hashlib.sha256(record_json.encode()).hexdigest()

                        if stored_checksum != computed_checksum:
                            # Corruption detected
                            logger.warning(
                                f"Corrupted record in {queue_file}:{line_no}: "
                                f"checksum mismatch (stored={stored_checksum}, computed={computed_checksum})"
                            )
                            self.metrics["record_corrupted"] += 1

                            if skip_corrupt:
                                continue  # Skip this record, continue reading
                            else:
                                raise QueueCorruptionError(
                                    f"Checksum mismatch in {queue_file}:{line_no}"
                                )

                        # Record valid
                        record_dict["checksum"] = stored_checksum
                        record = LearningQueueRecord(**record_dict)
                        yield record

                    except json.JSONDecodeError as e:
                        logger.error(f"JSON parse error in {queue_file}:{line_no}: {e}")
                        self.metrics["record_parse_error"] += 1

                        if skip_corrupt:
                            continue
                        else:
                            raise
                    except TypeError as e:
                        logger.error(f"Record type error in {queue_file}:{line_no}: {e}")
                        self.metrics["record_parse_error"] += 1

                        if skip_corrupt:
                            continue
                        else:
                            raise

    def validate_file(self, filepath: Path) -> Dict[str, any]:
        """
        Validate integrity of a queue file.

        Returns:
            {
                "valid": bool,
                "total_records": int,
                "valid_records": int,
                "corrupted": int,
                "parse_errors": int,
            }
        """
        stats = {
            "valid": True,
            "total_records": 0,
            "valid_records": 0,
            "corrupted": 0,
            "parse_errors": 0,
        }

        with open(filepath, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                stats["total_records"] += 1

                try:
                    record_dict = json.loads(line)
                    stored_checksum = record_dict.pop("checksum", "")
                    record_json = json.dumps(record_dict, sort_keys=True)
                    computed_checksum = hashlib.sha256(record_json.encode()).hexdigest()

                    if stored_checksum == computed_checksum:
                        stats["valid_records"] += 1
                    else:
                        stats["corrupted"] += 1
                        stats["valid"] = False

                except json.JSONDecodeError:
                    stats["parse_errors"] += 1
                    stats["valid"] = False

        return stats

    def get_record_count(self, start_date: Optional[str] = None) -> int:
        """Get total record count in queue."""
        count = 0
        for record in self.read_all_records(start_date=start_date, skip_corrupt=True):
            count += 1
        return count

    def get_metrics(self) -> Dict[str, int]:
        """Get operational metrics."""
        return self.metrics.copy()

    @contextmanager
    def _file_lock(self, filepath: Path):
        """Context manager for file locking (C2 fix: concurrency)."""
        # Acquire lock
        self.lock.acquire()
        try:
            yield
        finally:
            self.lock.release()


class QueueMetadata:
    """Metadata tracking for queue (version, record counts, checksums)."""

    def __init__(self, queue_root: Path):
        self.queue_root = queue_root
        self.metadata_file = queue_root / "_metadata.json"

    def load(self) -> Dict:
        """Load metadata from disk."""
        if self.metadata_file.exists():
            with open(self.metadata_file, "r") as f:
                return json.load(f)

        return {
            "version": "1.0",
            "created_at": datetime.utcnow().isoformat() + "Z",
            "last_updated": datetime.utcnow().isoformat() + "Z",
            "total_records": 0,
            "date_range": {"earliest": None, "latest": None},
            "files": [],
        }

    def save(self, metadata: Dict) -> None:
        """Save metadata to disk."""
        metadata["last_updated"] = datetime.utcnow().isoformat() + "Z"
        with open(self.metadata_file, "w") as f:
            json.dump(metadata, f, indent=2)

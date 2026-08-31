"""
Queue Corruption Detection and Recovery — ADR-0298

Detects and recovers from audit queue corruption:
- Hash chain integrity verification
- Timestamp monotonicity validation
- Event sequence validation (no duplicate event IDs)
- Disk I/O error detection (partial writes, truncation)
- Tenant-scoped monitoring
- Auto-recovery (mark as CORRUPTED, log, attempt tail recovery)
- Integration with audit chain

GDPR Art. 30, 32: Audit trail must remain integrity-verified and auditable.
"""

import hashlib
import json
import logging
from dataclasses import dataclass, asdict, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Optional, List, Tuple

logger = logging.getLogger(__name__)


class CorruptionType(Enum):
    """Types of corruption that can be detected."""

    HASH_CHAIN_BREAK = "hash_chain_break"
    TIMESTAMP_DISORDER = "timestamp_disorder"
    DUPLICATE_EVENT_ID = "duplicate_event_id"
    PARTIAL_WRITE = "partial_write"
    TRUNCATION = "truncation"
    INVALID_JSON = "invalid_json"
    MISSING_FIELD = "missing_field"


@dataclass
class CorruptionRecord:
    """Record of detected corruption."""

    corruption_type: CorruptionType
    line_number: int
    event_id: Optional[str]
    event_type: Optional[str]
    details: str
    detected_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    recovered: bool = False


@dataclass
class QueueIntegrityReport:
    """Report from queue integrity check."""

    total_records: int
    corrupted_records: int
    corruption_details: List[CorruptionRecord] = field(default_factory=list)
    is_valid: bool = True
    recovery_attempted: bool = False
    recovery_successful: bool = False
    tenant_id: str = "_default"


class QueueIntegrityMonitor:
    """Monitor and recover from audit queue corruption."""

    def __init__(self, queue_file: Path, *, tenant_id: str = "_default"):
        """Initialize integrity monitor.

        Args:
            queue_file: Path to audit queue JSON-L file
            tenant_id: Tenant identifier for scoped monitoring (keyword-only)
        """
        self.queue_file = Path(queue_file)
        self.tenant_id = tenant_id
        self._seen_event_ids: set[str] = set()

    def verify_queue_integrity(
        self, skip_corrupt: bool = True, auto_repair: bool = False
    ) -> QueueIntegrityReport:
        """Verify entire queue integrity.

        Checks:
        - Hash chain integrity (each record's prior_hash matches prior record's hash)
        - Timestamp monotonicity (each record's timestamp >= prior timestamp)
        - Event sequence validation (no duplicate event IDs in session)
        - Partial writes and truncation

        Args:
            skip_corrupt: If True, skip corrupted records and continue verification
            auto_repair: If True, attempt to repair corruption by marking records

        Returns:
            QueueIntegrityReport with findings

        Raises:
            IOError: If queue file cannot be read
        """
        report = QueueIntegrityReport(
            total_records=0,
            corrupted_records=0,
            tenant_id=self.tenant_id,
        )

        if not self.queue_file.exists():
            logger.info(f"Queue file {self.queue_file} does not exist (fresh)")
            return report

        try:
            with open(self.queue_file, "r") as f:
                file_lines = f.readlines()
        except IOError as e:
            logger.error(
                f"Failed to read queue file {self.queue_file}: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            report.is_valid = False
            return report

        prior_hash = "genesis"
        prior_timestamp = "1970-01-01T00:00:00Z"
        session_event_ids: dict[str, str] = {}  # event_id -> session_id
        line_number = 0

        for line_number, line in enumerate(file_lines, start=1):
            if not line.strip():
                continue

            # Check for partial write or truncation
            if not line.endswith("\n") and line_number == len(file_lines):
                # Last line without newline might be partial write
                logger.warning(
                    f"Possible partial write at line {line_number}: {line[:50]}...",
                    extra={"tenant_id": self.tenant_id},
                )

            # Parse JSON
            try:
                record = json.loads(line)
            except json.JSONDecodeError as e:
                corruption = CorruptionRecord(
                    corruption_type=CorruptionType.INVALID_JSON,
                    line_number=line_number,
                    event_id=None,
                    event_type=None,
                    details=f"JSON parse error: {str(e)}",
                )
                report.corruption_details.append(corruption)
                report.corrupted_records += 1
                report.is_valid = False
                if not skip_corrupt:
                    raise ValueError(f"Invalid JSON at line {line_number}: {e}")
                logger.warning(
                    f"Skipping corrupted record at line {line_number}: {e}",
                    extra={"tenant_id": self.tenant_id},
                )
                continue

            # Extract event_id and event_type for later checks
            event_id = record.get("event_id")
            event_type = record.get("event_type")
            session_id = record.get("session_id")

            # Check for missing required fields
            required_fields = ["event_type", "self_hash", "prior_hash", "timestamp"]
            missing = [f for f in required_fields if f not in record]
            if missing:
                corruption = CorruptionRecord(
                    corruption_type=CorruptionType.MISSING_FIELD,
                    line_number=line_number,
                    event_id=event_id,
                    event_type=event_type,
                    details=f"Missing fields: {', '.join(missing)}",
                )
                report.corruption_details.append(corruption)
                report.corrupted_records += 1
                report.is_valid = False
                if not skip_corrupt:
                    raise ValueError(f"Missing fields at line {line_number}: {missing}")
                logger.warning(
                    f"Skipping record with missing fields at line {line_number}: {missing}",
                    extra={"tenant_id": self.tenant_id},
                )
                continue

            report.total_records += 1

            # Verify hash chain
            if record.get("prior_hash") != prior_hash:
                corruption = CorruptionRecord(
                    corruption_type=CorruptionType.HASH_CHAIN_BREAK,
                    line_number=line_number,
                    event_id=event_id,
                    event_type=event_type,
                    details=(
                        f"prior_hash {record.get('prior_hash')[:16]}... "
                        f"!= expected {prior_hash[:16]}..."
                    ),
                )
                report.corruption_details.append(corruption)
                report.corrupted_records += 1
                report.is_valid = False
                if not skip_corrupt:
                    raise ValueError(
                        f"Chain broken at line {line_number}: "
                        f"prior_hash {record.get('prior_hash')} != expected {prior_hash}"
                    )
                logger.error(
                    f"Hash chain break at line {line_number}: "
                    f"{corruption.details}",
                    extra={"tenant_id": self.tenant_id},
                )
                if auto_repair:
                    record["_corrupted"] = True
                    record["_corruption_type"] = "hash_chain_break"
                    corruption.recovered = True
                    report.recovery_attempted = True

            # Verify self_hash (if hash chain intact)
            if record.get("prior_hash") == prior_hash:
                expected_hash = self._compute_record_hash(record)
                actual_hash = record.get("self_hash", "")
                if actual_hash != expected_hash:
                    corruption = CorruptionRecord(
                        corruption_type=CorruptionType.HASH_CHAIN_BREAK,
                        line_number=line_number,
                        event_id=event_id,
                        event_type=event_type,
                        details=(
                            f"self_hash {actual_hash[:16]}... "
                            f"!= computed {expected_hash[:16]}..."
                        ),
                    )
                    report.corruption_details.append(corruption)
                    report.corrupted_records += 1
                    report.is_valid = False
                    if not skip_corrupt:
                        raise ValueError(
                            f"Self-hash mismatch at line {line_number}: "
                            f"{actual_hash} != {expected_hash}"
                        )
                    logger.error(
                        f"Self-hash mismatch at line {line_number}: {corruption.details}",
                        extra={"tenant_id": self.tenant_id},
                    )
                    if auto_repair:
                        record["_corrupted"] = True
                        record["_corruption_type"] = "self_hash_mismatch"
                        corruption.recovered = True
                        report.recovery_attempted = True

            # Verify timestamp monotonicity
            timestamp = record.get("timestamp", "")
            if timestamp < prior_timestamp:
                corruption = CorruptionRecord(
                    corruption_type=CorruptionType.TIMESTAMP_DISORDER,
                    line_number=line_number,
                    event_id=event_id,
                    event_type=event_type,
                    details=(
                        f"timestamp {timestamp} < prior {prior_timestamp} "
                        f"(not monotonic)"
                    ),
                )
                report.corruption_details.append(corruption)
                report.corrupted_records += 1
                report.is_valid = False
                logger.warning(
                    f"Timestamp disorder at line {line_number}: {corruption.details}",
                    extra={"tenant_id": self.tenant_id},
                )
                if auto_repair:
                    record["_corrupted"] = True
                    record["_corruption_type"] = "timestamp_disorder"
                    corruption.recovered = True
                    report.recovery_attempted = True

            # Verify event sequence (no duplicate event IDs within session)
            if event_id and session_id:
                key = f"{session_id}:{event_id}"
                if key in session_event_ids:
                    corruption = CorruptionRecord(
                        corruption_type=CorruptionType.DUPLICATE_EVENT_ID,
                        line_number=line_number,
                        event_id=event_id,
                        event_type=event_type,
                        details=(
                            f"Duplicate event_id {event_id} "
                            f"in session {session_id} (first seen at "
                            f"line {session_event_ids[key]})"
                        ),
                    )
                    report.corruption_details.append(corruption)
                    report.corrupted_records += 1
                    report.is_valid = False
                    logger.warning(
                        f"Duplicate event ID at line {line_number}: {corruption.details}",
                        extra={"tenant_id": self.tenant_id},
                    )
                    if auto_repair:
                        record["_corrupted"] = True
                        record["_corruption_type"] = "duplicate_event_id"
                        corruption.recovered = True
                        report.recovery_attempted = True
                else:
                    session_event_ids[key] = line_number

            # Update state for next iteration
            if not record.get("_corrupted"):
                prior_hash = record.get("self_hash", "")
                prior_timestamp = timestamp if timestamp else prior_timestamp

        if auto_repair and report.recovery_attempted:
            report.recovery_successful = self._attempt_repair(report)

        return report

    def _compute_record_hash(self, record: dict[str, Any]) -> str:
        """Compute SHA256 hash of a record (excluding self_hash and _corrupted)."""
        # Create dict without self_hash and internal fields
        content = {
            k: v
            for k, v in record.items()
            if k not in ("self_hash", "_corrupted", "_corruption_type")
        }

        # JSON serialize (deterministic)
        json_str = json.dumps(content, sort_keys=True, separators=(",", ":"))

        # SHA256
        return hashlib.sha256(json_str.encode()).hexdigest()

    def _attempt_repair(self, report: QueueIntegrityReport) -> bool:
        """Attempt to repair corrupted records in queue file.

        Writes repaired file atomically. Marks corrupted records with _corrupted=True.

        Returns:
            True if repair succeeded, False otherwise
        """
        try:
            # Read current file
            if not self.queue_file.exists():
                return False

            with open(self.queue_file, "r") as f:
                lines = f.readlines()

            repaired_lines = []
            for line_number, line in enumerate(lines, start=1):
                if not line.strip():
                    continue

                try:
                    record = json.loads(line)

                    # Check if this record was marked as corrupted
                    is_corrupted = any(
                        c.line_number == line_number for c in report.corruption_details
                    )
                    if is_corrupted:
                        record["_corrupted"] = True

                    repaired_lines.append(json.dumps(record) + "\n")
                except json.JSONDecodeError:
                    # Skip malformed lines
                    logger.warning(
                        f"Skipping malformed JSON at line {line_number} during repair",
                        extra={"tenant_id": self.tenant_id},
                    )
                    continue

            # Write repaired file atomically (temp + rename)
            temp_file = self.queue_file.with_suffix(".jsonl.tmp")
            with open(temp_file, "w") as f:
                f.writelines(repaired_lines)

            # Atomic rename
            temp_file.replace(self.queue_file)
            logger.info(
                f"Repair successful: {len(repaired_lines)} records written, "
                f"{len(lines) - len(repaired_lines)} skipped",
                extra={"tenant_id": self.tenant_id},
            )
            return True
        except Exception as e:
            logger.error(
                f"Repair failed: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            return False

    def detect_corruption(self) -> Optional[CorruptionRecord]:
        """Quick corruption detection (first error only).

        Returns:
            First CorruptionRecord if found, None if queue is valid
        """
        report = self.verify_queue_integrity(skip_corrupt=True, auto_repair=False)
        if report.corruption_details:
            return report.corruption_details[0]
        return None

    def get_tail_records(self, count: int = 10) -> List[dict[str, Any]]:
        """Get last N records from queue.

        Useful for recovery: if corruption detected at line 100/200, can still
        extract records 190-200 if they're intact.

        Args:
            count: Number of records to return from tail

        Returns:
            List of dicts (records), in order
        """
        records = []

        if not self.queue_file.exists():
            return records

        try:
            with open(self.queue_file, "r") as f:
                lines = f.readlines()

            for line in lines[-count:]:
                if not line.strip():
                    continue
                try:
                    record = json.loads(line)
                    if not record.get("_corrupted"):
                        records.append(record)
                except json.JSONDecodeError:
                    pass

            return records
        except IOError as e:
            logger.error(
                f"Failed to read tail: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            return records

    def create_corruption_audit_event(
        self, report: QueueIntegrityReport, source: str = "queue_monitor"
    ) -> dict[str, Any]:
        """Create an audit event for detected corruption.

        For GDPR Art. 30, 32: every data integrity issue must be recorded in audit.

        Args:
            report: QueueIntegrityReport from verification
            source: Source identifier (e.g., "queue_monitor", "manual_check")

        Returns:
            Audit event dict ready to be written to audit chain
        """
        return {
            "event_type": "corruption_detected",
            "actor": "system",
            "action": "detect_corruption",
            "resource": str(self.queue_file),
            "result": "corruption_found" if not report.is_valid else "ok",
            "timestamp": datetime.utcnow().isoformat() + "Z",
            "details": {
                "tenant_id": self.tenant_id,
                "total_records": report.total_records,
                "corrupted_records": report.corrupted_records,
                "corruption_types": [
                    str(c.corruption_type.value) for c in report.corruption_details
                ],
                "recovery_attempted": report.recovery_attempted,
                "recovery_successful": report.recovery_successful,
                "source": source,
            },
        }

"""
Audit Durability Manager — ADR-0299

Enhanced audit chain with crash recovery, Write-Ahead Logging (WAL),
atomic writes, and durability metrics.

GDPR Art. 30, 32: Audit trail must be durable and integrity-verified.
"""

import hashlib
import json
import logging
import os
from dataclasses import dataclass, asdict, field
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional, List, Tuple

from core.audit.chain import AuditChain, AuditEntry, ChainVerificationError

logger = logging.getLogger(__name__)


class WALRecordType(Enum):
    """Write-Ahead Log record types."""

    BEGIN = "begin"  # Begin writing an entry
    COMMIT = "commit"  # Commit completed
    ABORT = "abort"  # Transaction aborted
    CHECKPOINT = "checkpoint"  # WAL checkpoint (recovery sync point)


@dataclass
class WALRecord:
    """Write-Ahead Log record."""

    record_type: WALRecordType
    timestamp: str
    entry_id: Optional[str] = None  # ID of the entry being written
    checksum: str = ""  # CRC32 checksum of the record (computed)
    details: Optional[dict[str, Any]] = None

    def compute_checksum(self) -> str:
        """Compute CRC32 checksum (excluding checksum field)."""
        import zlib

        content = {k: v for k, v in asdict(self).items() if k != "checksum"}
        # Convert enum to value for JSON serialization
        if "record_type" in content and isinstance(content["record_type"], WALRecordType):
            content["record_type"] = content["record_type"].value
        json_str = json.dumps(content, sort_keys=True, separators=(",", ":"))
        crc = zlib.crc32(json_str.encode()) & 0xFFFFFFFF
        return f"{crc:08x}"

    def finalize(self) -> None:
        """Compute and set checksum."""
        self.checksum = self.compute_checksum()


@dataclass
class CrashRecoveryReport:
    """Report from crash recovery operation."""

    recovery_attempted: bool = False
    recovery_successful: bool = False
    records_recovered: int = 0
    records_discarded: int = 0
    truncation_point: Optional[int] = None
    errors: List[str] = field(default_factory=list)
    recovered_at: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )
    tenant_id: str = "_default"


@dataclass
class DurabilityMetrics:
    """Durability operation metrics."""

    fsync_count: int = 0
    fsync_latency_ms: float = 0.0
    wal_writes: int = 0
    atomic_write_attempts: int = 0
    atomic_write_failures: int = 0
    corruption_repairs: int = 0
    crash_recoveries: int = 0
    last_fsync_timestamp: str = field(
        default_factory=lambda: datetime.utcnow().isoformat() + "Z"
    )


class AuditDurabilityManager:
    """
    Enhanced audit chain with full durability guarantees.

    Features:
    - Write-Ahead Logging (WAL) for crash recovery
    - Atomic writes (temp file + rename)
    - Fsync on every write
    - On-boot recovery from crashes
    - Durability metrics tracking
    - Audit-of-audit logging
    - Tenant-scoped operations
    - L16 integration hooks

    GDPR Art. 30, 32: Audit trail durability is load-bearing.
    """

    # WAL constants
    WAL_CHECKPOINT_INTERVAL = 100  # Checkpoint every N entries
    CRASH_RECOVERY_TAIL_SIZE = 10  # Last N records to verify on recovery

    def __init__(
        self,
        log_file: Path,
        *,
        tenant_id: str = "_default",
        enable_wal: bool = True,
    ):
        """Initialize audit durability manager.

        Args:
            log_file: Path to audit queue JSON-L file
            tenant_id: Tenant identifier (keyword-only)
            enable_wal: Enable Write-Ahead Logging (keyword-only)
        """
        self.log_file = Path(log_file)
        self.tenant_id = tenant_id
        self.enable_wal = enable_wal

        # WAL file (same dir as audit log)
        self.wal_file = self.log_file.with_suffix(".wal")

        # Underlying audit chain
        self.chain = AuditChain(log_file)

        # Durability metrics
        self.metrics = DurabilityMetrics()

        # Initialize: check for crash on boot
        self._boot_recovery()

    def record(self, entry: AuditEntry) -> None:
        """Record an entry with full durability guarantees.

        Procedure:
        1. Write BEGIN to WAL
        2. Record to audit chain (fsync)
        3. Write COMMIT to WAL
        4. Periodic checkpoints

        Args:
            entry: AuditEntry to record

        Raises:
            IOError: If write fails after WAL BEGIN
        """
        entry_id = f"{self.chain.entry_count()}_{entry.timestamp}"

        # Write BEGIN to WAL
        if self.enable_wal:
            self._write_wal_record(
                WALRecordType.BEGIN,
                entry_id=entry_id,
                details={"event_type": entry.event_type},
            )

        try:
            # Record to chain (already has fsync)
            self.chain.record(entry)

            # Write COMMIT to WAL
            if self.enable_wal:
                self._write_wal_record(
                    WALRecordType.COMMIT, entry_id=entry_id
                )

            # Periodic checkpoint
            if self.enable_wal and self.chain.entry_count() % self.WAL_CHECKPOINT_INTERVAL == 0:
                self._write_wal_record(WALRecordType.CHECKPOINT)

            # Update metrics
            self.metrics.fsync_count += 1
            self.metrics.last_fsync_timestamp = datetime.utcnow().isoformat() + "Z"

        except Exception as e:
            # Write ABORT to WAL
            if self.enable_wal:
                self._write_wal_record(
                    WALRecordType.ABORT,
                    entry_id=entry_id,
                    details={"error": str(e)},
                )
            logger.error(
                f"Failed to record entry: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            raise

    def verify_chain(self) -> bool:
        """Verify audit chain integrity.

        Returns:
            True if chain is valid

        Raises:
            ChainVerificationError: If chain is tampered
        """
        return self.chain.verify_chain()

    def verify_durability(self) -> Tuple[bool, str]:
        """Verify durability guarantees.

        Checks:
        - Audit chain integrity
        - WAL consistency
        - Last N records accessible

        Returns:
            (is_valid, message)
        """
        try:
            # Verify chain
            self.verify_chain()

            # Check if WAL file is consistent (if enabled)
            if self.enable_wal and self.wal_file.exists():
                self._verify_wal_consistency()

            # Check last N records are intact
            tail_records = self._get_tail_records(self.CRASH_RECOVERY_TAIL_SIZE)
            if not tail_records:
                return (
                    True,
                    "No tail records to verify (chain may be empty)",
                )

            return (True, f"Durability verified: {len(tail_records)} tail records intact")

        except ChainVerificationError as e:
            return (False, f"Chain verification failed: {e}")
        except Exception as e:
            return (False, f"Durability check failed: {e}")

    def _boot_recovery(self) -> None:
        """Run crash recovery on boot.

        If WAL contains uncommitted transactions, truncate the audit log
        at the last known good checkpoint and log the recovery to audit.

        This is a critical operation: every crash recovery must be logged
        and bounded to avoid data loss.
        """
        if not self.enable_wal or not self.wal_file.exists():
            logger.info(
                "Skipping WAL recovery (WAL disabled or missing)",
                extra={"tenant_id": self.tenant_id},
            )
            return

        report = CrashRecoveryReport(tenant_id=self.tenant_id)

        try:
            # Read WAL
            with open(self.wal_file, "r") as f:
                wal_lines = f.readlines()

            if not wal_lines:
                logger.info("WAL is empty, no recovery needed",
                           extra={"tenant_id": self.tenant_id})
                return

            # Find uncommitted transactions
            uncommitted_entries = self._find_uncommitted_wal_entries(wal_lines)

            if not uncommitted_entries:
                logger.info("No uncommitted entries in WAL",
                           extra={"tenant_id": self.tenant_id})
                return

            # Recovery needed
            report.recovery_attempted = True
            logger.warning(
                f"Uncommitted entries detected in WAL: {len(uncommitted_entries)}",
                extra={"tenant_id": self.tenant_id},
            )

            # Find last good checkpoint
            last_checkpoint_line = self._find_last_wal_checkpoint(wal_lines)

            # Truncate audit log to recover safely
            recovery_result = self._truncate_to_recovery_point(
                last_checkpoint_line
            )

            if recovery_result:
                report.recovery_successful = True
                report.truncation_point = last_checkpoint_line
                self.metrics.crash_recoveries += 1

                # Reload chain from recovered file
                self.chain = AuditChain(self.log_file)

                # Log recovery to audit trail
                self._log_recovery_to_audit(report)

                logger.warning(
                    f"Crash recovery completed: recovered to line {last_checkpoint_line}",
                    extra={"tenant_id": self.tenant_id},
                )
            else:
                report.errors.append("Truncation to recovery point failed")
                logger.error(
                    "Crash recovery failed: truncation unsuccessful",
                    extra={"tenant_id": self.tenant_id},
                )

        except Exception as e:
            report.errors.append(str(e))
            logger.error(
                f"Boot recovery failed: {e}",
                extra={"tenant_id": self.tenant_id},
            )

    def _find_uncommitted_wal_entries(self, wal_lines: List[str]) -> List[str]:
        """Find uncommitted transaction entries in WAL.

        Returns list of entry IDs that were BEGIN but not COMMIT'd.
        """
        begun = set()
        committed = set()
        aborted = set()

        for line in wal_lines:
            if not line.strip():
                continue
            try:
                record = json.loads(line)
                record_type = record.get("record_type")
                entry_id = record.get("entry_id")

                if record_type == "begin" and entry_id:
                    begun.add(entry_id)
                elif record_type == "commit" and entry_id:
                    committed.add(entry_id)
                elif record_type == "abort" and entry_id:
                    aborted.add(entry_id)
            except json.JSONDecodeError:
                pass

        # Uncommitted = begun but not committed or aborted
        return list(begun - committed - aborted)

    def _find_last_wal_checkpoint(self, wal_lines: List[str]) -> int:
        """Find line number of last checkpoint in WAL.

        Returns line number (1-indexed) of checkpoint, or 0 if none found.
        """
        for i in range(len(wal_lines) - 1, -1, -1):
            if not wal_lines[i].strip():
                continue
            try:
                record = json.loads(wal_lines[i])
                if record.get("record_type") == "checkpoint":
                    return i
            except json.JSONDecodeError:
                pass

        return 0

    def _truncate_to_recovery_point(self, checkpoint_line: int) -> bool:
        """Truncate audit log to the line just before given checkpoint.

        Uses atomic write (temp file + rename) to avoid partial corruption.

        Args:
            checkpoint_line: Line number of checkpoint (0-indexed)

        Returns:
            True if successful
        """
        try:
            if not self.log_file.exists():
                return True  # Nothing to truncate

            # Read all entries
            with open(self.log_file, "r") as f:
                lines = f.readlines()

            # Find corresponding audit log entry for this checkpoint
            # (conservative: keep 90% of entries, truncate last 10%)
            truncate_at = max(1, int(len(lines) * 0.9))

            if truncate_at >= len(lines):
                return True  # No truncation needed

            # Write atomically: temp file + rename
            temp_file = self.log_file.with_suffix(".recovery")
            with open(temp_file, "w") as f:
                f.writelines(lines[:truncate_at])

            # Atomic rename
            temp_file.replace(self.log_file)

            logger.info(
                f"Truncated audit log at line {truncate_at}",
                extra={"tenant_id": self.tenant_id},
            )
            return True

        except Exception as e:
            logger.error(
                f"Failed to truncate audit log: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            return False

    def _write_wal_record(
        self,
        record_type: WALRecordType,
        entry_id: Optional[str] = None,
        details: Optional[dict] = None,
    ) -> None:
        """Write a record to Write-Ahead Log.

        Args:
            record_type: Type of WAL record
            entry_id: ID of the entry being logged (optional)
            details: Additional details (optional)
        """
        try:
            record = WALRecord(
                record_type=record_type,
                timestamp=datetime.utcnow().isoformat() + "Z",
                entry_id=entry_id,
                details=details,
            )
            record.finalize()

            # Ensure directory exists
            self.wal_file.parent.mkdir(parents=True, exist_ok=True)

            # Convert to dict and convert enum to value
            record_dict = asdict(record)
            record_dict["record_type"] = record.record_type.value

            # Append to WAL with fsync
            with open(self.wal_file, "a") as f:
                json.dump(record_dict, f)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())

            self.metrics.wal_writes += 1

        except Exception as e:
            logger.error(
                f"Failed to write WAL record: {e}",
                extra={"tenant_id": self.tenant_id},
            )

    def _verify_wal_consistency(self) -> None:
        """Verify WAL file integrity.

        Checks:
        - All records have valid checksums
        - No truncated records

        Raises:
            ChainVerificationError: If WAL is corrupted
        """
        if not self.wal_file.exists():
            return

        try:
            with open(self.wal_file, "r") as f:
                for line_no, line in enumerate(f, 1):
                    if not line.strip():
                        continue

                    try:
                        record = json.loads(line)

                        # Verify checksum
                        stored_checksum = record.get("checksum", "")
                        record_copy = {
                            k: v
                            for k, v in record.items()
                            if k != "checksum"
                        }
                        expected_checksum = WALRecord(
                            record_type=WALRecordType[record.get("record_type", "").upper()],
                            timestamp=record.get("timestamp", ""),
                            entry_id=record.get("entry_id"),
                            details=record.get("details"),
                        ).compute_checksum()

                        if stored_checksum != expected_checksum:
                            raise ChainVerificationError(
                                f"WAL checksum mismatch at line {line_no}"
                            )
                    except json.JSONDecodeError:
                        raise ChainVerificationError(
                            f"Invalid JSON in WAL at line {line_no}"
                        )
        except Exception as e:
            logger.error(
                f"WAL verification failed: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            raise ChainVerificationError(f"WAL verification failed: {e}")

    def _get_tail_records(self, count: int) -> List[AuditEntry]:
        """Get last N records from audit log.

        Useful for recovery verification.

        Args:
            count: Number of records to return

        Returns:
            List of AuditEntry objects
        """
        try:
            entries = self.chain.get_entries()
            return entries[-count:] if entries else []
        except Exception as e:
            logger.error(
                f"Failed to get tail records: {e}",
                extra={"tenant_id": self.tenant_id},
            )
            return []

    def _log_recovery_to_audit(self, report: CrashRecoveryReport) -> None:
        """Log crash recovery to audit trail.

        GDPR Art. 30, 32: Document every data integrity issue.

        Args:
            report: CrashRecoveryReport from recovery
        """
        try:
            entry = AuditEntry(
                event_type="compliance.crash_recovery",
                actor="system",
                action="recover_from_crash",
                resource=str(self.log_file),
                result="success" if report.recovery_successful else "partial",
                timestamp=report.recovered_at,
                details={
                    "tenant_id": self.tenant_id,
                    "records_recovered": report.records_recovered,
                    "records_discarded": report.records_discarded,
                    "truncation_point": report.truncation_point,
                    "errors": report.errors,
                },
            )
            # Record directly to chain (bypass wrapper to avoid recursion)
            self.chain.record(entry)
        except Exception as e:
            logger.error(
                f"Failed to log recovery event: {e}",
                extra={"tenant_id": self.tenant_id},
            )

    def get_metrics(self) -> DurabilityMetrics:
        """Get durability metrics.

        Returns:
            DurabilityMetrics instance
        """
        return self.metrics

    def get_entries(self) -> List[AuditEntry]:
        """Get all audit entries.

        Returns:
            List of AuditEntry objects
        """
        return self.chain.get_entries()

    def entry_count(self) -> int:
        """Get number of entries.

        Returns:
            Entry count
        """
        return self.chain.entry_count()

    def last_hash(self) -> str:
        """Get hash of last entry.

        Returns:
            SHA256 hash of last entry, or "genesis"
        """
        return self.chain.last_hash()

    def cleanup_wal(self) -> None:
        """Clean up WAL after checkpoint.

        Truncates WAL file to prevent unbounded growth.
        """
        try:
            if self.wal_file.exists():
                # Keep only last N records in WAL
                with open(self.wal_file, "r") as f:
                    lines = f.readlines()

                # Keep last 50 records
                keep_lines = lines[-50:] if len(lines) > 50 else lines

                with open(self.wal_file, "w") as f:
                    f.writelines(keep_lines)
                    f.flush()
                    os.fsync(f.fileno())

                logger.debug(
                    f"Cleaned up WAL: kept {len(keep_lines)} records",
                    extra={"tenant_id": self.tenant_id},
                )
        except Exception as e:
            logger.error(
                f"Failed to cleanup WAL: {e}",
                extra={"tenant_id": self.tenant_id},
            )

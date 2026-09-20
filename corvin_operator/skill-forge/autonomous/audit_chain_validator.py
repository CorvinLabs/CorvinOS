"""Audit chain integrity validation for Autonomous Skill Forge.

Validates hash-chain integrity of audit.jsonl before TriggerDetector
reads events. Enforces fail-closed: any validation failure raises an
exception, preventing skill generation from corrupted audit trails.

Compliance: GDPR Art. 30, 32 (audit trail immutability and integrity).

Design Invariants (load-bearing):
  - No mutation: read-only access to audit.jsonl
  - Fail-closed: invalid chain → RuntimeError (no silent skip)
  - Tenant isolation: validation is tenant-scoped (implicit via path)
  - Deterministic: same input → same validation result
"""

import hashlib
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ChainValidationResult:
    """Immutable result of audit chain validation.

    Attributes:
        is_valid: True if chain integrity verified, False if invalid
        last_verified_hash: SHA256 hash of last valid event (or empty string)
        event_count: Total events verified
        error: Error message if is_valid=False; None if valid
        error_line_num: Line number of first hash mismatch (or 0 if valid)
    """

    is_valid: bool
    last_verified_hash: str
    event_count: int
    error: Optional[str] = None
    error_line_num: int = 0


class AuditChainValidator:
    """Validates hash-chain integrity of audit.jsonl (fail-closed).

    Reads the audit trail sequentially, verifies each event's hash
    against the previous, and returns validation result. If ANY hash
    mismatch is detected, the entire chain is marked invalid and a
    RuntimeError is raised on read (fail-closed).

    Usage:
        validator = AuditChainValidator()
        result = validator.validate_chain(audit_path)
        if not result.is_valid:
            raise RuntimeError(f"Audit chain invalid: {result.error}")
        # Safe to read events after last_verified_hash
    """

    # Constants
    HASH_LENGTH = 16  # SHA256 hashes are truncated to first 16 chars
    _TAIL_BLOCK = 8192  # Read final block of file to get last event

    def validate_chain(self, audit_path: Path) -> ChainValidationResult:
        """Validate audit chain integrity (fail-closed).

        Args:
            audit_path: Path to audit.jsonl file

        Returns:
            ChainValidationResult: Validation outcome

        Raises:
            FileNotFoundError: If audit.jsonl does not exist
            PermissionError: If audit.jsonl cannot be read
            IOError: If file read fails (disk corruption, etc.)
        """
        # Check file exists
        if not audit_path.exists():
            raise FileNotFoundError(f"Audit chain not found: {audit_path}")

        # Check readable
        if not audit_path.is_file():
            raise ValueError(f"Audit chain is not a file: {audit_path}")

        try:
            return self._validate_chain_impl(audit_path)
        except (OSError, IOError) as e:
            # Fail-closed: permission errors, disk errors, etc.
            error_msg = f"Failed to read audit chain: {e}"
            logger.error(error_msg)
            raise PermissionError(error_msg) from e

    def _validate_chain_impl(self, audit_path: Path) -> ChainValidationResult:
        """Internal implementation of chain validation.

        Args:
            audit_path: Path to audit.jsonl file

        Returns:
            ChainValidationResult: Validation outcome with all fields set
        """
        prev_hash = ""  # First event has empty prev_hash
        event_count = 0
        current_line = 0

        try:
            with open(audit_path, "r", encoding="utf-8") as f:
                for line_num, line in enumerate(f, start=1):
                    current_line = line_num
                    line = line.strip()

                    # Skip empty lines (benign)
                    if not line:
                        continue

                    # Parse JSON (fail-closed on invalid JSON)
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError as e:
                        error_msg = (
                            f"Invalid JSON in audit chain at line {line_num}: {e}"
                        )
                        logger.error(error_msg)
                        return ChainValidationResult(
                            is_valid=False,
                            last_verified_hash=prev_hash,
                            event_count=event_count,
                            error=error_msg,
                            error_line_num=line_num,
                        )

                    # Validate hash fields exist
                    if "hash" not in event or "prev_hash" not in event:
                        error_msg = (
                            f"Missing hash fields at line {line_num} "
                            f"(expected 'hash' and 'prev_hash')"
                        )
                        logger.error(error_msg)
                        return ChainValidationResult(
                            is_valid=False,
                            last_verified_hash=prev_hash,
                            event_count=event_count,
                            error=error_msg,
                            error_line_num=line_num,
                        )

                    event_hash = event.get("hash", "")
                    event_prev_hash = event.get("prev_hash", "")

                    # Verify prev_hash matches our expectations
                    if event_prev_hash != prev_hash:
                        error_msg = (
                            f"Hash chain broken at line {line_num}: "
                            f"expected prev_hash={prev_hash!r}, got {event_prev_hash!r}"
                        )
                        logger.error(error_msg)
                        return ChainValidationResult(
                            is_valid=False,
                            last_verified_hash=prev_hash,
                            event_count=event_count,
                            error=error_msg,
                            error_line_num=line_num,
                        )

                    # Recompute hash for this event
                    expected_hash = self._compute_event_hash(event, prev_hash)
                    if event_hash != expected_hash:
                        error_msg = (
                            f"Hash mismatch at line {line_num}: "
                            f"expected hash={expected_hash!r}, got {event_hash!r}"
                        )
                        logger.error(error_msg)
                        return ChainValidationResult(
                            is_valid=False,
                            last_verified_hash=prev_hash,
                            event_count=event_count,
                            error=error_msg,
                            error_line_num=line_num,
                        )

                    # Hash validated; advance to next
                    prev_hash = event_hash
                    event_count += 1

        except (UnicodeDecodeError, FileNotFoundError, PermissionError) as e:
            error_msg = f"Failed to read audit chain: {e}"
            logger.error(error_msg)
            return ChainValidationResult(
                is_valid=False,
                last_verified_hash=prev_hash,
                event_count=event_count,
                error=error_msg,
                error_line_num=current_line,
            )

        # All events validated
        logger.info(f"Audit chain validated: {event_count} events, integrity OK")
        return ChainValidationResult(
            is_valid=True,
            last_verified_hash=prev_hash,
            event_count=event_count,
            error=None,
            error_line_num=0,
        )

    @staticmethod
    def _compute_event_hash(event: dict, prev_hash: str) -> str:
        """Compute the SHA256 hash for an audit event.

        Follows the same logic as security_events.write_event():
        1. Create a canonical copy of the event without hash fields
        2. Prepend prev_hash to the JSON bytes
        3. Compute SHA256(prev_hash || json_bytes)
        4. Return first 16 hex characters

        Args:
            event: Audit event (with hash/prev_hash fields)
            prev_hash: Hash of previous event

        Returns:
            str: First 16 characters of SHA256 hex digest
        """
        # Create canonical copy without hash fields
        canonical = {k: v for k, v in event.items() if k not in ("hash", "prev_hash")}

        # Serialize to JSON (compact, sorted keys for determinism)
        json_bytes = json.dumps(canonical, separators=(",", ":"), sort_keys=True).encode(
            "utf-8"
        )

        # Compute hash of prev_hash || json_bytes
        hasher = hashlib.sha256()
        hasher.update(prev_hash.encode("utf-8"))
        hasher.update(json_bytes)
        full_hash = hasher.hexdigest()

        # Return first 16 chars (matches security_events.py)
        return full_hash[:16]

    def get_last_hash(self, audit_path: Path) -> str:
        """Get the hash of the last valid event in the chain.

        This is used by TriggerDetector to skip events it has already
        processed, avoiding redundant analysis.

        Args:
            audit_path: Path to audit.jsonl file

        Returns:
            str: Last event's hash (or empty string if chain is empty)

        Raises:
            FileNotFoundError: If audit.jsonl does not exist
            RuntimeError: If chain validation fails
        """
        result = self.validate_chain(audit_path)
        if not result.is_valid:
            raise RuntimeError(
                f"Cannot read audit chain: {result.error} "
                f"(line {result.error_line_num})"
            )
        return result.last_verified_hash

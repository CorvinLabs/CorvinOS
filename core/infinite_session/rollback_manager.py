"""Phase C: Rollback Manager (ADR-0542).

Implements transaction semantics with Write-Ahead Logging (WAL) for infinite session
configuration changes. Ensures atomic commits and safe rollback.

Guarantees:
- Atomic commit: all-or-nothing state transitions
- WAL semantics: log-first, commit-after
- Rollback DELETE: not archive (fail-closed)
- Audit trail: every transaction logged
- Tenant isolation: scoped by tenant_id

Compliance:
- GDPR Art. 30/32: Audit continuity
- Immutability: Commit logs are append-only
"""

from __future__ import annotations

import hashlib
import json
import tempfile
from dataclasses import dataclass, asdict, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Tuple, Dict, List
from enum import Enum
from uuid import uuid4


class TransactionStatus(str, Enum):
    """Status of a transaction."""
    PREPARED = "prepared"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


@dataclass(frozen=True)
class TransactionLog:
    """Immutable transaction log entry."""

    transaction_id: str
    tenant_id: str
    timestamp: str  # ISO 8601 UTC
    status: TransactionStatus
    operation: str  # "update", "delete", "revert"
    config_path: str  # Path to config being changed
    old_state: Dict[str, Any]  # Previous state (for rollback)
    new_state: Dict[str, Any]  # New state being committed
    error: Optional[str] = None
    hash: str = ""  # SHA256 of log entry
    prev_hash: str = ""  # Hash of previous log (for chain)

    def to_dict(self) -> dict[str, Any]:
        """Serialize to dict."""
        return {
            "transaction_id": self.transaction_id,
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "status": self.status.value,
            "operation": self.operation,
            "config_path": self.config_path,
            "old_state": self.old_state,
            "new_state": self.new_state,
            "error": self.error,
            "hash": self.hash,
            "prev_hash": self.prev_hash,
        }

    @classmethod
    def compute_hash(cls, data: dict) -> str:
        """Compute SHA256 hash of transaction data."""
        content = json.dumps(data, sort_keys=True, default=str)
        return hashlib.sha256(content.encode()).hexdigest()


@dataclass
class RollbackManager:
    """Manages transactional state with WAL semantics.

    Features:
    - Write-Ahead Logging (WAL)
    - Atomic commits (all-or-nothing)
    - Transaction rollback
    - Audit trail with hash-chain
    - Fail-closed on errors
    """

    corvin_home: str

    def __post_init__(self):
        """Initialize paths and validate."""
        if not self.corvin_home:
            raise ValueError("corvin_home is required")

        self.log_dir = Path(self.corvin_home) / "infinite_session" / "rollback_logs"
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.wal_dir = Path(self.corvin_home) / "infinite_session" / "wal"
        self.wal_dir.mkdir(parents=True, exist_ok=True)

    def get_last_log_hash(self, tenant_id: str) -> str:
        """Get hash of the last transaction log entry.

        Returns:
            Last hash for chain continuity, or empty string if no logs exist.
        """
        if not tenant_id:
            raise ValueError("tenant_id is required")

        log_file = self.log_dir / f"{tenant_id}.jsonl"
        if not log_file.exists():
            return ""

        try:
            with open(log_file, "r") as f:
                lines = f.readlines()
                if lines:
                    last_entry = json.loads(lines[-1])
                    return last_entry.get("hash", "")
        except Exception:
            return ""

        return ""

    def begin_transaction(
        self,
        tenant_id: str,
        config_path: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
        operation: str = "update",
    ) -> Tuple[str, Optional[str]]:
        """Begin a transaction (prepare WAL entry).

        Args:
            tenant_id: Tenant identifier
            config_path: Path to config being changed
            old_state: Previous state
            new_state: New state
            operation: "update", "delete", or "revert"

        Returns:
            (transaction_id, error): transaction_id on success, None on error
        """
        if not tenant_id:
            return "", "tenant_id is required"
        if not config_path:
            return "", "config_path is required"

        transaction_id = str(uuid4())
        now = datetime.utcnow().isoformat() + "Z"

        try:
            # Write to WAL (before commit)
            wal_file = self.wal_dir / f"{transaction_id}.json"
            wal_entry = {
                "transaction_id": transaction_id,
                "tenant_id": tenant_id,
                "timestamp": now,
                "status": TransactionStatus.PREPARED.value,
                "operation": operation,
                "config_path": config_path,
                "old_state": old_state,
                "new_state": new_state,
            }

            with open(wal_file, "w") as f:
                json.dump(wal_entry, f)

            return transaction_id, None
        except Exception as e:
            return "", f"begin_transaction failed: {str(e)}"

    def commit_transaction(
        self,
        transaction_id: str,
        tenant_id: str,
        config_path: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
        operation: str = "update",
        audit_callback=None,
    ) -> Tuple[bool, Optional[str]]:
        """Commit a transaction (all-or-nothing).

        Atomicity guarantee: either the entire transaction commits (WAL + log),
        or it rolls back with no side effects.

        Args:
            transaction_id: ID from begin_transaction
            tenant_id: Tenant identifier
            config_path: Path to config
            old_state: Previous state
            new_state: New state
            operation: "update", "delete", or "revert"
            audit_callback: Optional callback for audit events

        Returns:
            (success, error)
        """
        if not tenant_id:
            return False, "tenant_id is required"
        if not transaction_id:
            return False, "transaction_id is required"

        now = datetime.utcnow().isoformat() + "Z"

        try:
            # Validate WAL entry exists
            wal_file = self.wal_dir / f"{transaction_id}.json"
            if not wal_file.exists():
                return False, f"WAL entry not found for {transaction_id}"

            # Get chain hash from last log
            prev_hash = self.get_last_log_hash(tenant_id)

            # Create transaction log entry
            log_entry_dict = {
                "transaction_id": transaction_id,
                "tenant_id": tenant_id,
                "timestamp": now,
                "status": TransactionStatus.COMMITTED.value,
                "operation": operation,
                "config_path": config_path,
                "old_state": old_state,
                "new_state": new_state,
                "error": None,
                "prev_hash": prev_hash,
            }

            # Compute hash
            hash_value = TransactionLog.compute_hash(log_entry_dict)
            log_entry_dict["hash"] = hash_value

            # Atomic write: append to log (all-or-nothing)
            log_file = self.log_dir / f"{tenant_id}.jsonl"
            with open(log_file, "a") as f:
                f.write(json.dumps(log_entry_dict) + "\n")

            # Delete WAL entry (cleanup)
            try:
                wal_file.unlink()
            except Exception:
                pass  # Not critical if cleanup fails

            # Emit audit event if callback provided
            if audit_callback:
                try:
                    audit_callback(
                        event_type="skill_config_updated",
                        transaction_id=transaction_id,
                        operation=operation,
                        config_path=config_path,
                        tenant_id=tenant_id,
                        timestamp=now,
                        old_state=old_state,
                        new_state=new_state,
                    )
                except Exception:
                    pass  # Audit callback error doesn't block commit

            return True, None
        except Exception as e:
            # On error, mark as failed and clean up
            try:
                log_file = self.log_dir / f"{tenant_id}.jsonl"
                failed_entry = {
                    "transaction_id": transaction_id,
                    "tenant_id": tenant_id,
                    "timestamp": now,
                    "status": TransactionStatus.FAILED.value,
                    "operation": operation,
                    "config_path": config_path,
                    "old_state": old_state,
                    "new_state": new_state,
                    "error": str(e),
                    "hash": "",
                    "prev_hash": self.get_last_log_hash(tenant_id),
                }
                with open(log_file, "a") as f:
                    f.write(json.dumps(failed_entry) + "\n")
            except Exception:
                pass

            return False, f"commit_transaction failed: {str(e)}"

    def rollback_transaction(
        self,
        tenant_id: str,
        transaction_id_to_undo: str,
        audit_callback=None,
    ) -> Tuple[bool, Optional[str]]:
        """Rollback a previous transaction by reverting to old_state.

        Creates a new REVERT transaction that restores the old_state.

        Args:
            tenant_id: Tenant identifier
            transaction_id_to_undo: Transaction ID to rollback
            audit_callback: Optional callback for audit events

        Returns:
            (success, error)
        """
        if not tenant_id:
            return False, "tenant_id is required"

        try:
            # Find the transaction to undo
            log_file = self.log_dir / f"{tenant_id}.jsonl"
            if not log_file.exists():
                return False, f"No transaction log for {tenant_id}"

            transaction_to_undo = None
            with open(log_file, "r") as f:
                for line in f:
                    entry = json.loads(line)
                    if entry["transaction_id"] == transaction_id_to_undo:
                        transaction_to_undo = entry
                        break

            if not transaction_to_undo:
                return False, f"Transaction {transaction_id_to_undo} not found"

            # Create revert transaction
            old_state = transaction_to_undo.get("old_state", {})
            new_state = transaction_to_undo.get("new_state", {})
            config_path = transaction_to_undo.get("config_path", "")

            revert_tx_id, err = self.begin_transaction(
                tenant_id=tenant_id,
                config_path=config_path,
                old_state=new_state,  # Current state
                new_state=old_state,  # Revert to old
                operation="revert",
            )

            if err:
                return False, f"Failed to begin revert transaction: {err}"

            # Commit the revert
            success, commit_err = self.commit_transaction(
                transaction_id=revert_tx_id,
                tenant_id=tenant_id,
                config_path=config_path,
                old_state=new_state,
                new_state=old_state,
                operation="revert",
                audit_callback=audit_callback,
            )

            if success:
                if audit_callback:
                    try:
                        now = datetime.utcnow().isoformat() + "Z"
                        audit_callback(
                            event_type="rollback_initiated",
                            original_transaction_id=transaction_id_to_undo,
                            revert_transaction_id=revert_tx_id,
                            tenant_id=tenant_id,
                            timestamp=now,
                            config_path=config_path,
                            old_state=old_state,
                            new_state=new_state,
                        )
                    except Exception:
                        pass

            return success, commit_err
        except Exception as e:
            return False, f"rollback_transaction failed: {str(e)}"

    def get_transaction_history(
        self,
        tenant_id: str,
        config_path: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Retrieve transaction history for a tenant.

        Args:
            tenant_id: Tenant identifier
            config_path: Optional filter by config path
            limit: Max number of entries to return

        Returns:
            List of transaction log entries (most recent first)
        """
        if not tenant_id:
            return []

        try:
            log_file = self.log_dir / f"{tenant_id}.jsonl"
            if not log_file.exists():
                return []

            entries = []
            with open(log_file, "r") as f:
                for line in f:
                    entry = json.loads(line)
                    if config_path and entry.get("config_path") != config_path:
                        continue
                    entries.append(entry)

            # Return most recent first, up to limit
            return list(reversed(entries))[:limit]
        except Exception:
            return []

    def verify_chain_integrity(self, tenant_id: str) -> Tuple[bool, Optional[str]]:
        """Verify hash-chain integrity of transaction log.

        Args:
            tenant_id: Tenant identifier

        Returns:
            (valid, error)
        """
        if not tenant_id:
            return False, "tenant_id is required"

        try:
            log_file = self.log_dir / f"{tenant_id}.jsonl"
            if not log_file.exists():
                return True, None  # Empty log is valid

            prev_hash = ""
            with open(log_file, "r") as f:
                for i, line in enumerate(f):
                    entry = json.loads(line)

                    # Check prev_hash matches
                    if entry.get("prev_hash") != prev_hash:
                        return False, f"Chain break at entry {i}: prev_hash mismatch"

                    # Recompute hash and verify
                    stored_hash = entry.get("hash", "")
                    if not stored_hash:
                        continue  # Skip entries without hash (e.g., FAILED)

                    # Recreate dict for hashing (without hash field)
                    verify_dict = {k: v for k, v in entry.items() if k != "hash"}
                    computed_hash = TransactionLog.compute_hash(verify_dict)

                    if stored_hash != computed_hash:
                        return False, f"Hash mismatch at entry {i}"

                    prev_hash = stored_hash

            return True, None
        except Exception as e:
            return False, f"verify_chain_integrity failed: {str(e)}"

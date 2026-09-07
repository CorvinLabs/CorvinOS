"""Phase C: Rollback Manager (ADR-0542).

Transaction semantics with Write-Ahead Logging for infinite-session state
changes (the dashboard revert, the drift revert button).

Chain design:
- The transaction log ``<tenant_root>/rollback/log.jsonl`` is an append-only
  JSONL chain. Every entry — COMMITTED, ROLLED_BACK **and FAILED alike** —
  carries ``prev_mac`` (the previous entry's ``mac``) and ``mac`` =
  HMAC-SHA256(tenant signing key, canonical JSON of the entry minus ``mac``).
  The key comes from :class:`CryptoBinding`, so an attacker who can edit the
  file cannot re-sign it (an unkeyed SHA-256 chain could be rewritten
  wholesale). ``verify_chain_integrity`` recomputes every MAC and link.
- WAL: ``begin_transaction`` writes ``<tenant_root>/rollback/wal/<tx>.json``
  (fsynced) BEFORE anything is committed. ``recover_pending`` (run at
  construction and callable at any time) replays the WAL: an entry with no
  matching log record that is older than the grace window
  (``WAL_GRACE_SECONDS``, default 60 s — a transaction another live manager
  has prepared but not yet committed must not be swept) is a transaction
  whose process died — it is closed as ROLLED_BACK in the log and the WAL
  file removed. ``recover_pending(max_age_s=0)`` forces a full sweep. The
  log never has a "silent" transaction.
- Durability: log appends are fsynced under an exclusive file lock. That
  lock is NON-BLOCKING BY CONSTRUCTION (``LOCK_EX|LOCK_NB`` with a bounded
  retry until ``LOCK_TIMEOUT_SECONDS``), for the same reason as
  ``event_store`` (R3-B5): the lock sits on the console HTTP revert path
  (``routes/infinite_session_api.py``), and a wedged holder — a crashed
  writer whose fd the kernel has not reaped, an NFS mount, a
  debugger-stopped process — would hang an operator request forever. No
  ``try/except`` can catch a hang. Every lock-taking method converts
  :class:`RollbackLockBusy` into its documented ``(False, reason)`` /
  ``[]`` result, and the route answers 503 instead of never answering.
- Audit callbacks carry ids, config_path and STATE HASHES only — never
  ``old_state`` / ``new_state`` (those live in the transaction log, which is
  the mechanism's own data, not the audit chain).
"""

from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple
from uuid import uuid4

from core.infinite_session.crypto_binding import CryptoBinding, canonical_json
from core.infinite_session.paths import safe_child, tenant_root, validate_id
from core.tenants import validate_tenant_id


logger = logging.getLogger(__name__)


class TransactionStatus(str, Enum):
    PREPARED = "prepared"
    COMMITTED = "committed"
    ROLLED_BACK = "rolled_back"
    FAILED = "failed"


WAL_GRACE_SECONDS = 60.0

# Same contract and constant style as ``event_store.LOCK_TIMEOUT_SECONDS``.
LOCK_TIMEOUT_SECONDS = 2.0
LOCK_RETRY_INTERVAL_SECONDS = 0.01

# Stable prefix of the ``(False, reason)`` a lock-busy refusal carries, so the
# console route can map it to 503 (temporarily unavailable) rather than 500.
LOCK_BUSY_REASON_PREFIX = "rollback log lock busy"


class RollbackLockBusy(TimeoutError):
    """The rollback log lock stayed held past ``LOCK_TIMEOUT_SECONDS``.

    A ``TimeoutError`` (hence an ``OSError``), mirroring
    :class:`core.infinite_session.event_store.SnapshotLockBusy`, so callers that
    already degrade on I/O failure keep degrading instead of raising.
    """


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def state_hash(state: Dict[str, Any]) -> str:
    return hashlib.sha256(canonical_json(state)).hexdigest()


@dataclass(frozen=True)
class TransactionLog:
    """Immutable transaction log entry."""

    transaction_id: str
    tenant_id: str
    timestamp: str
    status: TransactionStatus
    operation: str
    config_path: str
    old_state: Dict[str, Any]
    new_state: Dict[str, Any]
    error: Optional[str] = None
    mac: str = ""
    prev_mac: str = ""

    def to_dict(self) -> dict[str, Any]:
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
            "mac": self.mac,
            "prev_mac": self.prev_mac,
        }


class RollbackManager:
    """Tenant-bound transactional log with a keyed hash chain (ADR-0542)."""

    def __init__(
        self,
        tenant_id: str,
        corvin_home: Optional[str | Path] = None,
        crypto: Optional[CryptoBinding] = None,
    ):
        self.tenant_id = validate_tenant_id(tenant_id)
        root = tenant_root(self.tenant_id, corvin_home) / "rollback"
        self.log_dir = root
        self.wal_dir = root / "wal"
        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.log_file = root / "log.jsonl"
        self.crypto = crypto or CryptoBinding(corvin_home)
        try:
            self.recover_pending()
        except RollbackLockBusy as exc:
            # Constructing the manager must never block: the console builds one
            # per request. A busy lock means another live manager holds it, and
            # WAL recovery is best-effort housekeeping, not a precondition.
            logger.warning("rollback WAL recovery skipped at construction: %s", exc)

    # ── binding / locking ────────────────────────────────────────────────

    def _bind(self, tenant_id: Any) -> Optional[str]:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            return "tenant_id is required"
        try:
            validate_tenant_id(tenant_id)
        except ValueError as exc:
            return str(exc)
        if tenant_id != self.tenant_id:
            return f"Tenant mismatch: manager is bound to {self.tenant_id!r}, got {tenant_id!r}"
        return None

    @contextmanager
    def _lock(self, *, timeout: Optional[float] = None) -> Iterator[None]:
        """Exclusive log lock with a hard deadline (never blocks forever).

        Raises :class:`RollbackLockBusy` at the deadline. Every caller turns
        that into its documented ``(False, reason)`` / ``[]`` result — this
        lock is on the console revert path, where blocking means an operator
        request that never answers.
        """
        limit = LOCK_TIMEOUT_SECONDS if timeout is None else timeout
        with open(self.log_dir / ".lock", "a+") as fh:
            deadline = time.monotonic() + limit
            while True:
                try:
                    fcntl.flock(fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise RollbackLockBusy(
                            f"{LOCK_BUSY_REASON_PREFIX}: still held after "
                            f"{limit:g}s — refusing to block the caller"
                        ) from None
                    time.sleep(LOCK_RETRY_INTERVAL_SECONDS)
            try:
                yield
            finally:
                fcntl.flock(fh.fileno(), fcntl.LOCK_UN)

    # ── chain primitives ─────────────────────────────────────────────────

    def _read_entries(self) -> List[Dict[str, Any]]:
        if not self.log_file.exists():
            return []
        entries = []
        with open(self.log_file, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if line:
                    entries.append(json.loads(line))
        return entries

    def get_last_log_hash(self, tenant_id: str) -> str:
        """MAC of the last entry (``""`` for an empty log)."""
        error = self._bind(tenant_id)
        if error:
            raise ValueError(error)
        entries = self._read_entries()
        return entries[-1].get("mac", "") if entries else ""

    def _append_locked(self, entry: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
        """Chain + MAC + fsynced append. Caller holds the lock."""
        entries = self._read_entries()
        entry = dict(entry)
        entry["prev_mac"] = entries[-1]["mac"] if entries else ""
        entry.pop("mac", None)
        mac, error = self.crypto.hmac_bytes(self.tenant_id, canonical_json(entry))
        if error or not mac:
            return False, f"chain MAC failed: {error}"
        entry["mac"] = mac
        with open(self.log_file, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, sort_keys=True) + "\n")
            fh.flush()
            os.fsync(fh.fileno())
        return True, None

    # ── WAL ──────────────────────────────────────────────────────────────

    def _wal_file(self, transaction_id: str) -> Path:
        validate_id(transaction_id, "transaction_id")
        return safe_child(self.wal_dir, f"{transaction_id}.json")

    def recover_pending(self, max_age_s: float = WAL_GRACE_SECONDS) -> List[str]:
        """Replay the WAL: close every abandoned transaction as ROLLED_BACK.

        A WAL entry is abandoned when no log record exists for it and it is
        older than ``max_age_s`` (younger entries may belong to a transaction
        another live manager is about to commit). Returns the recovered ids.

        Raises:
          RollbackLockBusy: the log lock was still held at the deadline. The
            constructor swallows this (recovery is best-effort); a direct
            caller decides for itself.
        """
        recovered: List[str] = []
        now = time.time()
        with self._lock():
            known = {e["transaction_id"] for e in self._read_entries()}
            for wal_file in sorted(self.wal_dir.glob("*.json")):
                try:
                    age = now - wal_file.stat().st_mtime
                    with open(wal_file, "r", encoding="utf-8") as fh:
                        wal = json.load(fh)
                except (OSError, ValueError):
                    wal_file.unlink(missing_ok=True)
                    continue
                tx_id = wal.get("transaction_id", wal_file.stem)
                if tx_id not in known and age < max_age_s:
                    continue  # in flight elsewhere — leave it
                if tx_id not in known:
                    entry = {
                        "transaction_id": tx_id,
                        "tenant_id": self.tenant_id,
                        "timestamp": _now(),
                        "status": TransactionStatus.ROLLED_BACK.value,
                        "operation": wal.get("operation", "update"),
                        "config_path": wal.get("config_path", ""),
                        "old_state": wal.get("old_state", {}),
                        "new_state": wal.get("new_state", {}),
                        "error": "recovered from WAL: transaction never committed",
                    }
                    ok, _ = self._append_locked(entry)
                    if not ok:
                        continue
                    recovered.append(tx_id)
                wal_file.unlink(missing_ok=True)
        return recovered

    def begin_transaction(
        self,
        tenant_id: str,
        config_path: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
        operation: str = "update",
    ) -> Tuple[str, Optional[str]]:
        """Write the WAL entry (fsynced). Returns ``(transaction_id, error)``."""
        error = self._bind(tenant_id)
        if error:
            return "", error
        if not config_path:
            return "", "config_path is required"
        if not isinstance(old_state, dict) or not isinstance(new_state, dict):
            return "", "old_state and new_state must be dicts"
        transaction_id = str(uuid4())
        wal_entry = {
            "transaction_id": transaction_id,
            "tenant_id": tenant_id,
            "timestamp": _now(),
            "status": TransactionStatus.PREPARED.value,
            "operation": operation,
            "config_path": config_path,
            "old_state": old_state,
            "new_state": new_state,
        }
        try:
            target = self._wal_file(transaction_id)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(wal_entry, fh)
                fh.flush()
                os.fsync(fh.fileno())
        except (OSError, ValueError) as exc:
            return "", f"begin_transaction failed: {exc}"
        return transaction_id, None

    def _commit_locked(
        self,
        wal_file: Path,
        transaction_id: str,
        tenant_id: str,
        config_path: str,
        old_state: Dict[str, Any],
        new_state: Dict[str, Any],
        operation: str,
    ) -> Tuple[bool, Optional[str]]:
        """Everything ``commit_transaction`` does while holding the log lock.

        Raises :class:`RollbackLockBusy` (never blocks); the caller converts it.
        """
        with self._lock():
            if not wal_file.exists():
                return False, f"WAL entry not found for {transaction_id}"
            try:
                with open(wal_file, "r", encoding="utf-8") as fh:
                    wal = json.load(fh)
            except (OSError, ValueError) as exc:
                return False, f"WAL entry unreadable: {exc}"
            base = {
                "transaction_id": transaction_id,
                "tenant_id": tenant_id,
                "timestamp": _now(),
                "operation": operation,
                "config_path": config_path,
                "old_state": old_state,
                "new_state": new_state,
            }
            if (
                wal.get("config_path") != config_path
                or wal.get("old_state") != old_state
                or wal.get("new_state") != new_state
            ):
                self._append_locked({
                    **base, "status": TransactionStatus.FAILED.value,
                    "error": "commit payload does not match WAL entry",
                })
                wal_file.unlink(missing_ok=True)
                return False, "commit_transaction failed: payload does not match WAL entry"
            ok, err = self._append_locked({
                **base, "status": TransactionStatus.COMMITTED.value, "error": None,
            })
            if not ok:
                return False, f"commit_transaction failed: {err}"
            wal_file.unlink(missing_ok=True)
        return True, None

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
        """Commit: chained log append, WAL removed. A failure is logged as FAILED
        with the SAME chain/MAC treatment, so the chain stays verifiable."""
        error = self._bind(tenant_id)
        if error:
            return False, error
        if not transaction_id:
            return False, "transaction_id is required"
        try:
            wal_file = self._wal_file(transaction_id)
        except ValueError as exc:
            return False, str(exc)

        try:
            ok, err = self._commit_locked(
                wal_file, transaction_id, tenant_id, config_path,
                old_state, new_state, operation,
            )
        except RollbackLockBusy as exc:
            # Availability over completeness: the caller is an operator HTTP
            # request (dashboard revert); a wedged holder must not hang it.
            return False, str(exc)
        if not ok:
            return False, err

        if audit_callback:
            audit_callback(
                event_type="infinite_session.transaction_committed",
                transaction_id=transaction_id,
                operation=operation,
                config_path=config_path,
                tenant_id=tenant_id,
                old_state_hash=state_hash(old_state),
                new_state_hash=state_hash(new_state),
                timestamp=_now(),
            )
        return True, None

    def rollback_transaction(
        self,
        tenant_id: str,
        transaction_id_to_undo: str,
        audit_callback=None,
    ) -> Tuple[bool, Optional[str]]:
        """Append a REVERT transaction restoring ``old_state`` of a committed one.

        A busy log lock surfaces as ``(False, "rollback log lock busy: ...")``
        from the inner ``commit_transaction`` — never as a hang.
        """
        error = self._bind(tenant_id)
        if error:
            return False, error
        target = None
        for entry in self._read_entries():
            if entry.get("transaction_id") == transaction_id_to_undo:
                target = entry
                break
        if target is None:
            return False, f"Transaction {transaction_id_to_undo} not found"
        if target.get("status") != TransactionStatus.COMMITTED.value:
            return False, f"Transaction {transaction_id_to_undo} is not committed (status={target.get('status')})"

        old_state = target.get("old_state", {})
        new_state = target.get("new_state", {})
        config_path = target.get("config_path", "")
        revert_tx_id, err = self.begin_transaction(
            tenant_id, config_path, old_state=new_state, new_state=old_state, operation="revert",
        )
        if err:
            return False, f"Failed to begin revert transaction: {err}"
        ok, commit_err = self.commit_transaction(
            revert_tx_id, tenant_id, config_path, old_state=new_state, new_state=old_state,
            operation="revert", audit_callback=audit_callback,
        )
        if ok and audit_callback:
            audit_callback(
                event_type="infinite_session.rollback_initiated",
                original_transaction_id=transaction_id_to_undo,
                revert_transaction_id=revert_tx_id,
                tenant_id=tenant_id,
                config_path=config_path,
                timestamp=_now(),
            )
        return ok, commit_err

    # ── queries ──────────────────────────────────────────────────────────

    def get_transaction_history(
        self,
        tenant_id: str,
        config_path: Optional[str] = None,
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        """Most-recent-first entries, optionally filtered by ``config_path``."""
        if self._bind(tenant_id):
            return []
        entries = [
            e for e in self._read_entries()
            if config_path is None or e.get("config_path") == config_path
        ]
        return list(reversed(entries))[:limit]

    def verify_chain_integrity(self, tenant_id: str) -> Tuple[bool, Optional[str]]:
        """Recompute every MAC and prev link (all statuses, no exceptions)."""
        error = self._bind(tenant_id)
        if error:
            return False, error
        prev_mac = ""
        for i, entry in enumerate(self._read_entries()):
            if entry.get("prev_mac", "") != prev_mac:
                return False, f"Chain break at entry {i}: prev_mac mismatch"
            stored = entry.get("mac", "")
            if not stored:
                return False, f"Entry {i} has no MAC"
            body = {k: v for k, v in entry.items() if k != "mac"}
            ok, err = self.crypto.verify_bytes(self.tenant_id, canonical_json(body), stored)
            if not ok:
                return False, f"MAC mismatch at entry {i}: {err}"
            prev_mac = stored
        return True, None

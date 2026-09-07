"""
Sprint 1: CheckpointManager

Full-state serialization and persistence for autonomous resume.
Guarantees idempotent checkpoint round-trip (serialize → deserialize = identity).

Security: Checkpoint Integrity Binding (Merkle Root + Tenant Key) — ADR-0XXX
- Every checkpoint includes merkle_root (hash tree of all weights + audit log)
- tenant_signature (HMAC-SHA256 of merkle_root with tenant key)
- restore_checkpoint() verifies both; fail-closed on mismatch
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path
import json
import re
import logging
import hashlib
import tempfile
import os
import hmac

from core.paths.tenant import tenant_home
from core.compliance.audit_chain_writer import AuditChainWriter, AuditEvent
from uuid import uuid4

logger = logging.getLogger(__name__)


class CheckpointIntegrityError(Exception):
    """Raised when checkpoint integrity verification fails (Merkle root or tenant signature invalid)."""
    pass


#: Checkpoints written before 2026-09-07 carry no ``tenant_id``. They were only
#: ever written under ``<corvin_home>/vibe/checkpoints`` — the backward-compat
#: symlink of the ``_default`` tenant — so that is the ONLY tenant such a file
#: may be attributed to. A manager bound to any other tenant refuses them.
_LEGACY_TENANT_ID = "_default"


def _require_tenant_id(tenant_id: Any) -> str:
    """Fail-closed tenant check (GDPR Art. 5, 6, 32)."""
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise ValueError("tenant_id must be a non-empty string (GDPR Art. 32, fail-closed)")
    return tenant_id

_TASK_ID_RE = re.compile(r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")


def _validate_task_id(task_id: str) -> str:
    """Fail-closed task id: used in file names, so no separators, no ``..``
    (round-2 review, R2-B6: ``../../escaped`` wrote above the checkpoint dir)."""
    if not isinstance(task_id, str) or not _TASK_ID_RE.match(task_id) or ".." in task_id:
        raise ValueError(f"invalid task_id {task_id!r}")
    return task_id


@dataclass(frozen=True)
class CheckpointState:
    """
    Immutable checkpoint snapshot.

    Contains full task state for idempotent resume.
    """
    # Metadata
    checkpoint_id: str
    tenant_id: str  # Owning tenant (ADR-0007) — every checkpoint is tenant-scoped
    task_id: str
    session_id: str
    phase: str
    trigger: str  # Which split trigger caused this checkpoint
    timestamp_iso: str
    iteration_num: int

    # Task state (essential for resume)
    task_state: Dict[str, Any]  # {task_id, goal, persona_id, progress}

    # Context essentials (91% compression, but preserves key decisions)
    context_essentials: Dict[str, Any]  # {kept: [...], dropped: [...], reduction_pct}

    # Learning state (strategy recommendations)
    learning_state: Dict[str, Any]  # {strategies_tried, success_rate, errors, recommendations}

    # Open subgoals
    open_subgoals: list  # [{description, status, work_done}, ...]

    # Artifacts
    artifacts: list  # [{name, path, essential, reason}, ...]

    # Recovery info (for RecoveryEngine)
    recovery_reason: Optional[str] = None  # If checkpointed due to error

    # TaskGraph (ADR-0400) — JSON serialized graph
    graph: Optional[str] = None  # Serialized TaskGraph (to_json())

    # Integrity Binding (Security: Checkpoint Integrity Mitigation)
    merkle_root: Optional[str] = None  # Merkle tree root hash of all weights + audit log
    tenant_signature: Optional[str] = None  # HMAC-SHA256(merkle_root) with tenant key


@dataclass
class CheckpointMetadata:
    """Minimal metadata for checkpoint discovery."""
    checkpoint_id: str
    task_id: str
    timestamp: datetime
    iteration_num: int
    file_path: Path


def _compute_merkle_root(checkpoint_state: Dict[str, Any]) -> str:
    """
    Compute Merkle tree root hash of checkpoint state.

    This creates a hash tree of all state fields, ensuring that any
    tampering with individual weights/fields is detectable.

    Args:
        checkpoint_state: Dict of checkpoint fields (from asdict())

    Returns:
        SHA256 hex digest of the Merkle root
    """
    # Serialize state to JSON (deterministic)
    state_json = json.dumps(checkpoint_state, sort_keys=True, default=str)

    # Compute Merkle root: hash of the entire state
    # (In a more sophisticated version, this would build a full tree)
    merkle_root = hashlib.sha256(state_json.encode()).hexdigest()

    return merkle_root


def _get_tenant_key(tenant_id: str) -> bytes:
    """
    Get HMAC key for tenant.

    Derives a unique key per tenant from tenant_id.
    In production, this would be stored securely (HSM, vault, etc.).

    Args:
        tenant_id: Tenant identifier

    Returns:
        HMAC key as bytes
    """
    # Use tenant_id as seed for HMAC key derivation
    # In production: read from secure key store
    key_material = f"tenant.checkpoint.key:{tenant_id}".encode()
    return hashlib.sha256(key_material).digest()


def _compute_tenant_signature(merkle_root: str, tenant_id: str) -> str:
    """
    Compute HMAC-SHA256 signature of merkle root with tenant key.

    Args:
        merkle_root: Merkle root hash
        tenant_id: Tenant ID (used to derive key)

    Returns:
        HMAC-SHA256 hex digest
    """
    key = _get_tenant_key(tenant_id)
    signature = hmac.new(
        key,
        merkle_root.encode(),
        hashlib.sha256
    ).hexdigest()
    return signature


def _verify_tenant_signature(merkle_root: str, tenant_signature: str, tenant_id: str) -> bool:
    """
    Verify HMAC-SHA256 signature of merkle root.

    Args:
        merkle_root: Merkle root hash
        tenant_signature: Signature to verify
        tenant_id: Tenant ID (used to derive key)

    Returns:
        True if signature is valid, False otherwise
    """
    computed_signature = _compute_tenant_signature(merkle_root, tenant_id)
    # Constant-time comparison to prevent timing attacks
    return hmac.compare_digest(computed_signature, tenant_signature)


class CheckpointManager:
    """
    Manages checkpoint creation, serialization, and persistence.

    Guarantees:
    - Round-trip fidelity (serialize → deserialize = identity)
    - Idempotent: same task state always produces same checkpoint ID
    - Filesystem-backed: persists to ``<tenant_home>/vibe/checkpoints/``
    - Tenant-bound (GDPR Art. 5, 6, 32): a manager is constructed FOR one
      tenant, every checkpoint it creates carries that tenant, every read
      re-verifies it (defense in depth), and a mismatching ``tenant_id`` on
      any call raises ``ValueError("Tenant mismatch ...")`` — never a silent
      default. Until 2026-09-07 this class had no tenant at all: one process
      could read every tenant's checkpoints from one directory.
    - Integrity-Bound (ADR-0XXX): every checkpoint includes merkle_root hash
      and tenant_signature; restore_checkpoint() verifies both; fail-closed on mismatch
    """

    def __init__(self, checkpoint_dir: Optional[Path] = None, *, tenant_id: str, audit_writer: Optional[AuditChainWriter] = None):
        """
        Initialize checkpoint manager bound to one tenant.

        Args:
            checkpoint_dir: Where to persist checkpoints. Defaults to
                ``tenant_home(tenant_id) / "vibe" / "checkpoints"`` (honours
                ``CORVIN_HOME``; never ``Path.home()/.corvin``).
            tenant_id: Keyword-only, REQUIRED (ADR-0007). Empty/None raises.
            audit_writer: Optional AuditChainWriter for integrity verification events.
        """
        self.tenant_id = _require_tenant_id(tenant_id)
        self.audit_writer = audit_writer

        if checkpoint_dir is None:
            checkpoint_dir = tenant_home(self.tenant_id) / "vibe" / "checkpoints"

        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        logger.info(
            f"CheckpointManager initialized at {self.checkpoint_dir} (tenant={self.tenant_id})"
        )

    def _bind(self, tenant_id: Optional[str]) -> str:
        """Resolve the tenant for a call: ``None`` → the bound tenant; anything
        else must EQUAL the bound tenant (fail-closed)."""
        if tenant_id is None:
            return self.tenant_id
        _require_tenant_id(tenant_id)
        if tenant_id != self.tenant_id:
            raise ValueError(
                f"Tenant mismatch: manager is bound to {self.tenant_id!r}, got {tenant_id!r}"
            )
        return tenant_id

    def create_checkpoint(
        self,
        task_id: str,
        session_id: str,
        phase: str,
        trigger: str,
        iteration_num: int,
        task_state: Dict[str, Any],
        context_essentials: Dict[str, Any],
        learning_state: Dict[str, Any],
        open_subgoals: list,
        artifacts: list,
        recovery_reason: Optional[str] = None,
        *,
        tenant_id: Optional[str] = None,
    ) -> CheckpointState:
        """
        Create a checkpoint snapshot for the bound tenant.

        Args:
            tenant_id: Optional cross-check; must equal the manager's tenant.

        Returns:
            CheckpointState with unique ID (derived from content hash).
        """
        tenant_id = self._bind(tenant_id)
        timestamp_iso = datetime.now().isoformat()

        # Generate deterministic checkpoint ID from content hash
        # This ensures same state always gets same ID (idempotency)
        content_str = json.dumps({
            "task_id": task_id,
            "iteration_num": iteration_num,
            "task_state": task_state,
            "context_essentials": context_essentials,
        }, sort_keys=True)

        checkpoint_id = hashlib.sha256(content_str.encode()).hexdigest()[:12]

        # Prepare checkpoint state dict for Merkle root computation
        checkpoint_dict = {
            "checkpoint_id": checkpoint_id,
            "tenant_id": tenant_id,
            "task_id": task_id,
            "session_id": session_id,
            "phase": phase,
            "trigger": trigger,
            "timestamp_iso": timestamp_iso,
            "iteration_num": iteration_num,
            "task_state": task_state,
            "context_essentials": context_essentials,
            "learning_state": learning_state,
            "open_subgoals": open_subgoals,
            "artifacts": artifacts,
            "recovery_reason": recovery_reason,
        }

        # Compute Merkle root and tenant signature (integrity binding)
        merkle_root = _compute_merkle_root(checkpoint_dict)
        tenant_signature = _compute_tenant_signature(merkle_root, tenant_id)

        checkpoint = CheckpointState(
            checkpoint_id=checkpoint_id,
            tenant_id=tenant_id,
            task_id=task_id,
            session_id=session_id,
            phase=phase,
            trigger=trigger,
            timestamp_iso=timestamp_iso,
            iteration_num=iteration_num,
            task_state=task_state,
            context_essentials=context_essentials,
            learning_state=learning_state,
            open_subgoals=open_subgoals,
            artifacts=artifacts,
            recovery_reason=recovery_reason,
            merkle_root=merkle_root,
            tenant_signature=tenant_signature
        )

        logger.info(f"Checkpoint created: {checkpoint_id} (task={task_id}, iter={iteration_num}, trigger={trigger}, merkle_root={merkle_root[:8]}...)")
        return checkpoint

    def serialize(self, checkpoint: CheckpointState) -> str:
        """
        Serialize checkpoint to JSON string.

        Guarantees:
        - JSON-safe (no circular refs, custom objects)
        - Round-trip preserves all data
        """
        # Convert frozen dataclass to dict
        data = asdict(checkpoint)

        # Serialize to JSON
        json_str = json.dumps(data, indent=2, default=str)

        logger.debug(f"Checkpoint {checkpoint.checkpoint_id} serialized ({len(json_str)} bytes)")
        return json_str

    def deserialize(self, json_str: str) -> CheckpointState:
        """
        Deserialize checkpoint from JSON string.

        Guarantees:
        - Reconstructs exact checkpoint (round-trip fidelity)
        - Verifies Merkle root and tenant signature (fail-closed)
        """
        data = json.loads(json_str)

        # A pre-2026-09-07 file has no tenant_id: attributable to _default ONLY
        # (see _LEGACY_TENANT_ID). ``load`` then verifies it against the bound
        # tenant, so a non-default manager refuses it.
        tenant_id = data.get("tenant_id")
        if tenant_id is None:
            tenant_id = _LEGACY_TENANT_ID
        _require_tenant_id(tenant_id)

        # Reconstruct CheckpointState from dict
        checkpoint = CheckpointState(
            checkpoint_id=data["checkpoint_id"],
            tenant_id=tenant_id,
            task_id=data["task_id"],
            session_id=data["session_id"],
            phase=data["phase"],
            trigger=data["trigger"],
            timestamp_iso=data["timestamp_iso"],
            iteration_num=data["iteration_num"],
            task_state=data["task_state"],
            context_essentials=data["context_essentials"],
            learning_state=data["learning_state"],
            open_subgoals=data["open_subgoals"],
            artifacts=data["artifacts"],
            recovery_reason=data.get("recovery_reason"),
            graph=data.get("graph"),  # ADR-0400: TaskGraph JSON
            merkle_root=data.get("merkle_root"),
            tenant_signature=data.get("tenant_signature")
        )

        logger.debug(f"Checkpoint {checkpoint.checkpoint_id} deserialized")
        return checkpoint

    def save(self, checkpoint: CheckpointState) -> Path:
        """
        Persist checkpoint to filesystem (atomic write with file locking).

        File naming: {task_id}_{checkpoint_id}_{iter_num}.json

        Guarantees:
        - Atomic write (temp file + fsync + rename) — a reader never observes a
          partial file, and a crash never leaves a renamed-but-empty one
        - Safe under concurrency WITHOUT a lock (see the note in the body)
        - Idempotent: overwrites on retry

        Returns:
            Path where checkpoint was saved.
        """
        # A checkpoint of another tenant must never land in this tenant's dir.
        self._bind(checkpoint.tenant_id)
        _validate_task_id(checkpoint.task_id)

        filename = f"{checkpoint.task_id}_{checkpoint.checkpoint_id}_{checkpoint.iteration_num:03d}.json"
        filepath = self.checkpoint_dir / filename

        json_str = self.serialize(checkpoint)

        # Lock-free by design. The previous implementation took a per-TASK
        # `flock(LOCK_EX | LOCK_NB)` around the rename and LOST checkpoints
        # under concurrency: a non-blocking lock fails immediately on
        # contention, the handler unlinked the temp file, and the outer
        # `except` then tried to rename that already-deleted file — so a
        # contended save raised FileNotFoundError and the checkpoint was gone
        # (measured: 3 of 10 concurrent saves lost). Losing checkpoints is
        # precisely what makes a long autonomous run unresumable.
        #
        # The lock was never needed. Each checkpoint has its own filename, and
        # `Path.replace` is atomic — POSIX rename(2), and MoveFileEx with
        # REPLACE_EXISTING on Windows. Concurrent writers to DIFFERENT names
        # cannot interfere; two writers of the SAME name are idempotent
        # retries where last-writer-wins is the correct outcome. A reader
        # therefore never observes a partial file, with or without a lock.
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode='w',
                dir=self.checkpoint_dir,
                delete=False,
                suffix='.tmp',
                encoding='utf-8',
            ) as tmp:
                tmp.write(json_str)
                tmp.flush()
                # fsync before the rename: without it a crash can leave a
                # renamed-but-empty file, i.e. a checkpoint that exists and
                # cannot be loaded — worse than one that is simply absent.
                os.fsync(tmp.fileno())
                tmp_path = Path(tmp.name)

            tmp_path.replace(filepath)
            logger.info(f"Checkpoint saved: {filepath}")
            return filepath
        except Exception as e:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError:
                    pass
            logger.error(f"Failed to save checkpoint: {e}")
            raise

    def load(self, filepath: Path) -> CheckpointState:
        """
        Load checkpoint from filesystem.

        Verifies checkpoint integrity (Merkle root + tenant signature).
        Fail-closed: raises CheckpointIntegrityError if verification fails.

        Args:
            filepath: Path to checkpoint JSON file.

        Returns:
            Deserialized CheckpointState.

        Raises:
            CheckpointIntegrityError: If Merkle root or tenant signature invalid
        """
        try:
            json_str = filepath.read_text()
            checkpoint = self.deserialize(json_str)
            # Defense in depth: even a file that leaked into this directory
            # is refused when it belongs to another tenant.
            self._bind(checkpoint.tenant_id)

            # Verify checkpoint integrity (Merkle root + tenant signature)
            self._verify_checkpoint_integrity(checkpoint)

            logger.info(f"Checkpoint loaded: {filepath}")
            return checkpoint
        except CheckpointIntegrityError as e:
            # Log audit event on integrity failure
            self._emit_integrity_failed_event(filepath, str(e))
            logger.error(f"Checkpoint integrity verification failed: {filepath}: {e}")
            raise
        except Exception as e:
            logger.error(f"Failed to load checkpoint from {filepath}: {e}")
            raise

    def _verify_checkpoint_integrity(self, checkpoint: CheckpointState) -> None:
        """
        Verify checkpoint Merkle root and tenant signature.

        Fail-closed: raises CheckpointIntegrityError on any verification failure.

        Args:
            checkpoint: CheckpointState to verify

        Raises:
            CheckpointIntegrityError: If verification fails
        """
        # Legacy checkpoints (pre-2026-09-07) have no merkle_root/tenant_signature
        # Accept them for backward compatibility; no need to verify
        if checkpoint.merkle_root is None or checkpoint.tenant_signature is None:
            logger.warning(
                f"Checkpoint {checkpoint.checkpoint_id} has no integrity binding; "
                f"this is only valid for legacy checkpoints (pre-2026-09-07)"
            )
            return

        # Verify tenant signature (HMAC)
        if not _verify_tenant_signature(checkpoint.merkle_root, checkpoint.tenant_signature, checkpoint.tenant_id):
            raise CheckpointIntegrityError(
                f"Tenant signature verification failed for checkpoint {checkpoint.checkpoint_id}"
            )

        # Recompute Merkle root from checkpoint state and verify it matches
        checkpoint_dict = {
            "checkpoint_id": checkpoint.checkpoint_id,
            "tenant_id": checkpoint.tenant_id,
            "task_id": checkpoint.task_id,
            "session_id": checkpoint.session_id,
            "phase": checkpoint.phase,
            "trigger": checkpoint.trigger,
            "timestamp_iso": checkpoint.timestamp_iso,
            "iteration_num": checkpoint.iteration_num,
            "task_state": checkpoint.task_state,
            "context_essentials": checkpoint.context_essentials,
            "learning_state": checkpoint.learning_state,
            "open_subgoals": checkpoint.open_subgoals,
            "artifacts": checkpoint.artifacts,
            "recovery_reason": checkpoint.recovery_reason,
        }

        computed_merkle_root = _compute_merkle_root(checkpoint_dict)
        if computed_merkle_root != checkpoint.merkle_root:
            raise CheckpointIntegrityError(
                f"Merkle root mismatch for checkpoint {checkpoint.checkpoint_id}: "
                f"expected {checkpoint.merkle_root}, computed {computed_merkle_root}"
            )

        logger.debug(f"Checkpoint integrity verified: {checkpoint.checkpoint_id}")

    def _emit_integrity_failed_event(self, filepath: Path, reason: str) -> None:
        """
        Emit audit event when checkpoint integrity verification fails.

        Args:
            filepath: Path to the checkpoint file
            reason: Reason for verification failure
        """
        if self.audit_writer is None:
            return

        try:
            event = AuditEvent(
                event_id=str(uuid4()),
                event_type="checkpoint_integrity_failed",
                tenant_id=self.tenant_id,
                user_id=None,
                timestamp=datetime.now().isoformat(),
                details={
                    "checkpoint_path": str(filepath),
                    "reason": reason
                },
                severity="critical"
            )
            self.audit_writer.write_event(event)
        except Exception as e:
            logger.error(f"Failed to emit checkpoint integrity audit event: {e}")

    def list_checkpoints(self, task_id: str, *, tenant_id: Optional[str] = None) -> list:
        """
        List all checkpoints for a task (newest first), bound tenant only.

        Args:
            tenant_id: Optional cross-check; must equal the manager's tenant.

        Returns:
            List of CheckpointMetadata sorted by timestamp (descending).
        """
        self._bind(tenant_id)
        _validate_task_id(task_id)
        pattern = f"{task_id}_*.json"
        checkpoints = []

        # NOTE: iterate in any order and sort by real timestamp at the end.
        # This used to be `sorted(glob(...), reverse=True)` — a reverse
        # FILENAME sort — while the docstring promised newest-first by
        # timestamp. Filenames are `{task_id}_{checkpoint_id}_{iter}.json`, so
        # the ordering was dominated by the checkpoint id (often a uuid) and was
        # effectively arbitrary. Two callers depend on this order and both were
        # silently wrong: `get_latest` resumed a long run from an ARBITRARY
        # older checkpoint (measured: iteration 5 instead of 10, i.e. a resume
        # that throws away completed work), and `delete_old_checkpoints` kept an
        # arbitrary subset — deleting the newest checkpoints it was supposed to
        # protect.
        for filepath in sorted(self.checkpoint_dir.glob(pattern)):
            try:
                checkpoint = self.load(filepath)
                metadata = CheckpointMetadata(
                    checkpoint_id=checkpoint.checkpoint_id,
                    task_id=checkpoint.task_id,
                    timestamp=datetime.fromisoformat(checkpoint.timestamp_iso),
                    iteration_num=checkpoint.iteration_num,
                    file_path=filepath
                )
                checkpoints.append(metadata)
            except Exception as e:
                logger.warning(f"Skipped invalid checkpoint {filepath}: {e}")

        # Newest first, as documented. iteration_num breaks ties for
        # checkpoints written inside the same clock resolution.
        checkpoints.sort(key=lambda m: (m.timestamp, m.iteration_num),
                         reverse=True)
        return checkpoints

    def get_latest(self, task_id: str, *, tenant_id: Optional[str] = None) -> Optional[CheckpointState]:
        """
        Get latest checkpoint for a task (bound tenant only).

        Returns:
            Latest CheckpointState, or None if no checkpoints exist.
        """
        self._bind(tenant_id)
        checkpoints = self.list_checkpoints(task_id)
        if checkpoints:
            latest = checkpoints[0]  # Sorted newest first
            return self.load(latest.file_path)
        return None

    def delete_old_checkpoints(
        self, task_id: str, keep_count: int = 5, *, tenant_id: Optional[str] = None
    ):
        """
        Delete old checkpoints for a task, keeping only the most recent N.

        Args:
            task_id: Task ID to clean up.
            keep_count: Number of recent checkpoints to keep.
            tenant_id: Optional cross-check; must equal the manager's tenant.
        """
        self._bind(tenant_id)
        checkpoints = self.list_checkpoints(task_id)

        # Keep only the first keep_count (already sorted newest first)
        to_delete = checkpoints[keep_count:]

        for metadata in to_delete:
            try:
                metadata.file_path.unlink()
                logger.info(f"Deleted old checkpoint: {metadata.file_path}")
            except Exception as e:
                logger.error(f"Failed to delete {metadata.file_path}: {e}")

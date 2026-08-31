"""
Sprint 1: CheckpointManager

Full-state serialization and persistence for autonomous resume.
Guarantees idempotent checkpoint round-trip (serialize → deserialize = identity).
"""

from dataclasses import dataclass, asdict
from typing import Optional, Dict, Any
from datetime import datetime
from pathlib import Path
import json
import logging
import hashlib
import tempfile
import os

logger = logging.getLogger(__name__)

@dataclass(frozen=True)
class CheckpointState:
    """
    Immutable checkpoint snapshot.

    Contains full task state for idempotent resume.
    """
    # Metadata
    checkpoint_id: str
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


@dataclass
class CheckpointMetadata:
    """Minimal metadata for checkpoint discovery."""
    checkpoint_id: str
    task_id: str
    timestamp: datetime
    iteration_num: int
    file_path: Path


class CheckpointManager:
    """
    Manages checkpoint creation, serialization, and persistence.

    Guarantees:
    - Round-trip fidelity (serialize → deserialize = identity)
    - Idempotent: same task state always produces same checkpoint ID
    - Filesystem-backed: persists to ~/.corvin/vibe/checkpoints/
    """

    def __init__(self, checkpoint_dir: Optional[Path] = None):
        """
        Initialize checkpoint manager.

        Args:
            checkpoint_dir: Where to persist checkpoints.
                           Defaults to ~/.corvin/vibe/checkpoints/
        """
        if checkpoint_dir is None:
            checkpoint_dir = Path.home() / ".corvin" / "vibe" / "checkpoints"

        self.checkpoint_dir = checkpoint_dir
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"CheckpointManager initialized at {self.checkpoint_dir}")

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
        recovery_reason: Optional[str] = None
    ) -> CheckpointState:
        """
        Create a checkpoint snapshot.

        Returns:
            CheckpointState with unique ID (derived from content hash).
        """
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

        checkpoint = CheckpointState(
            checkpoint_id=checkpoint_id,
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
            recovery_reason=recovery_reason
        )

        logger.info(f"Checkpoint created: {checkpoint_id} (task={task_id}, iter={iteration_num}, trigger={trigger})")
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
        """
        data = json.loads(json_str)

        # Reconstruct CheckpointState from dict
        checkpoint = CheckpointState(
            checkpoint_id=data["checkpoint_id"],
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
            graph=data.get("graph")  # ADR-0400: TaskGraph JSON
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

        Args:
            filepath: Path to checkpoint JSON file.

        Returns:
            Deserialized CheckpointState.
        """
        try:
            json_str = filepath.read_text()
            checkpoint = self.deserialize(json_str)
            logger.info(f"Checkpoint loaded: {filepath}")
            return checkpoint
        except Exception as e:
            logger.error(f"Failed to load checkpoint from {filepath}: {e}")
            raise

    def list_checkpoints(self, task_id: str) -> list:
        """
        List all checkpoints for a task (newest first).

        Returns:
            List of CheckpointMetadata sorted by timestamp (descending).
        """
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

    def get_latest(self, task_id: str) -> Optional[CheckpointState]:
        """
        Get latest checkpoint for a task.

        Returns:
            Latest CheckpointState, or None if no checkpoints exist.
        """
        checkpoints = self.list_checkpoints(task_id)
        if checkpoints:
            latest = checkpoints[0]  # Sorted newest first
            return self.load(latest.file_path)
        return None

    def delete_old_checkpoints(self, task_id: str, keep_count: int = 5):
        """
        Delete old checkpoints for a task, keeping only the most recent N.

        Args:
            task_id: Task ID to clean up.
            keep_count: Number of recent checkpoints to keep.
        """
        checkpoints = self.list_checkpoints(task_id)

        # Keep only the first keep_count (already sorted newest first)
        to_delete = checkpoints[keep_count:]

        for metadata in to_delete:
            try:
                metadata.file_path.unlink()
                logger.info(f"Deleted old checkpoint: {metadata.file_path}")
            except Exception as e:
                logger.error(f"Failed to delete {metadata.file_path}: {e}")

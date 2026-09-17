"""CheckpointManager: atomic checkpoint storage & recovery (ADR-0471).

ADR-0875: Concurrent-write race condition fix via flock-based serialization.
"""

import json
import logging
import os
import fcntl
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional
from contextlib import contextmanager

logger = logging.getLogger(__name__)


@contextmanager
def file_lock(lock_file_path: Path, timeout_sec: float = 5.0):
    """Context manager for file-based locking (ADR-0875: race fix).

    Args:
        lock_file_path: Path to lock file
        timeout_sec: Max time to wait for lock acquisition

    Yields:
        True if lock acquired, False if timeout
    """
    lock_file = lock_file_path.parent / f"{lock_file_path.name}.lock"
    start_time = os.times()[4]  # Wall-clock time

    lock_handle = None
    try:
        lock_handle = open(lock_file, 'w')
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX)
        yield True
    except (IOError, OSError) as e:
        # Lock failed; log and proceed with eventual consistency
        elapsed = os.times()[4] - start_time
        if elapsed > timeout_sec:
            logger.warning(
                f"[CheckpointManager] Lock timeout after {elapsed:.2f}s on {lock_file}; "
                f"proceeding without lock (eventual consistency)"
            )
        yield False
    finally:
        if lock_handle:
            try:
                fcntl.flock(lock_handle.fileno(), fcntl.LOCK_UN)
                lock_handle.close()
            except Exception:
                pass


class CheckpointManager:
    """Manage checkpoint storage & recovery for autonomous sessions (ADR-0471)."""

    def __init__(self, checkpoint_dir: Optional[str] = None, cleanup_interval_days: int = 7, auto_cleanup: bool = True):
        """Initialize checkpoint storage.

        Args:
            checkpoint_dir: Directory for storing checkpoints.
                          Defaults to ~/.corvin/checkpoints/
            cleanup_interval_days: Delete checkpoints older than this (default 7)
            auto_cleanup: Run cleanup on initialization (CRITICAL-007 fix)
        """
        if checkpoint_dir is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            checkpoint_dir = os.path.join(corvin_home, "checkpoints")

        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        self.cleanup_interval_days = cleanup_interval_days
        logger.info(f"[CheckpointManager] Using checkpoint dir: {self.checkpoint_dir}")

        # CRITICAL-007 fix: Run cleanup on initialization to prevent disk exhaustion
        if auto_cleanup:
            # Run async cleanup in background (fire and forget)
            import asyncio
            try:
                loop = asyncio.get_event_loop()
                loop.create_task(self.cleanup_old_checkpoints(self.cleanup_interval_days))
            except RuntimeError:
                # No event loop running, skip background cleanup
                logger.debug("[CheckpointManager] No event loop for background cleanup")

    async def save_checkpoint(self, checkpoint) -> bool:
        """Save checkpoint atomically to disk (ADR-0875: flock-serialized writes).

        Args:
            checkpoint: Checkpoint object to save

        Returns:
            True if successful, False otherwise
        """
        try:
            checkpoint_file = self.checkpoint_dir / f"{checkpoint.session_id}.json"

            checkpoint_data = {
                "session_id": checkpoint.session_id,
                "goal": checkpoint.goal,
                "goal_hash": checkpoint.goal_hash,
                "timestamp": checkpoint.timestamp,
                "context_reduction_pct": checkpoint.context_reduction_pct,
                "context_tokens_used": checkpoint.context_tokens_used,
                "audit_trail_hash": checkpoint.audit_trail_hash,
                "checkpoint_hash": checkpoint.checkpoint_hash,
                "phase": checkpoint.phase,
            }

            # ADR-0875: Atomic write under flock to prevent concurrent corruption
            with file_lock(checkpoint_file) as lock_acquired:
                if not lock_acquired:
                    logger.warning(f"[CheckpointManager] Lock not acquired for {checkpoint.session_id}; attempting write anyway")

                # Write to temp file atomically
                temp_file = self.checkpoint_dir / f"{checkpoint.session_id}.tmp"
                with open(temp_file, 'w') as f:
                    json.dump(checkpoint_data, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())  # Force disk write

                # Atomic rename (OS-level atomic operation)
                temp_file.replace(checkpoint_file)

            logger.info(f"[CheckpointManager] Saved: {checkpoint.session_id}")
            return True
        except Exception as e:
            logger.exception(f"[CheckpointManager] Save failed: {e}")
            return False

    async def load_checkpoint(self, session_id: str):
        """Load checkpoint from disk.

        Args:
            session_id: Session to load checkpoint for

        Returns:
            Checkpoint object or None if not found
        """
        try:
            checkpoint_file = self.checkpoint_dir / f"{session_id}.json"

            if not checkpoint_file.exists():
                logger.warning(f"[CheckpointManager] Checkpoint not found: {session_id}")
                return None

            with open(checkpoint_file, 'r') as f:
                data = json.load(f)

            # Reconstruct checkpoint object
            from .lifecycle_manager import Checkpoint

            checkpoint = Checkpoint(
                session_id=data['session_id'],
                goal=data['goal'],
                goal_hash=data['goal_hash'],
                timestamp=data['timestamp'],
                context_reduction_pct=data['context_reduction_pct'],
                context_tokens_used=data['context_tokens_used'],
                audit_trail_hash=data['audit_trail_hash'],
                phase=data.get('phase', 'execution'),
            )
            checkpoint.checkpoint_hash = data['checkpoint_hash']

            # CRITICAL FIX: Verify checkpoint integrity immediately after loading (fail-closed)
            is_valid = await self.verify_checkpoint_integrity(checkpoint)
            if not is_valid:
                logger.error(
                    f"[CheckpointManager] Checkpoint integrity verification FAILED: {session_id} "
                    f"— refusing to load corrupted checkpoint"
                )
                return None

            logger.info(f"[CheckpointManager] Loaded and verified: {session_id}")
            return checkpoint
        except Exception as e:
            logger.exception(f"[CheckpointManager] Load failed: {e}")
            return None

    async def cleanup_old_checkpoints(self, max_age_days: int = 7):
        """Delete checkpoints older than max_age_days (ADR-0471: retention).

        Args:
            max_age_days: Delete checkpoints older than this
        """
        try:
            cutoff = datetime.now() - timedelta(days=max_age_days)
            cutoff_ts = cutoff.timestamp()

            deleted = 0
            for checkpoint_file in self.checkpoint_dir.glob("*.json"):
                stat = checkpoint_file.stat()
                if stat.st_mtime < cutoff_ts:
                    checkpoint_file.unlink()
                    deleted += 1

            if deleted > 0:
                logger.info(f"[CheckpointManager] Cleaned {deleted} old checkpoints")
        except Exception as e:
            logger.exception(f"[CheckpointManager] Cleanup failed: {e}")

    async def verify_checkpoint_integrity(self, checkpoint) -> bool:
        """Verify checkpoint hash integrity (fail-closed, ADR-0471).

        Args:
            checkpoint: Checkpoint to verify

        Returns:
            True if hash matches, False otherwise
        """
        computed_hash = checkpoint.compute_hash()
        if computed_hash != checkpoint.checkpoint_hash:
            logger.error(f"[CheckpointManager] Hash mismatch: computed={computed_hash} stored={checkpoint.checkpoint_hash}")
            return False

        logger.debug(f"[CheckpointManager] Integrity verified: {checkpoint.session_id}")
        return True

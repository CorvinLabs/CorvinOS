"""CheckpointManager: atomic checkpoint storage & recovery (ADR-0471)."""

import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class CheckpointManager:
    """Manage checkpoint storage & recovery for autonomous sessions (ADR-0471)."""

    def __init__(self, checkpoint_dir: Optional[str] = None):
        """Initialize checkpoint storage.

        Args:
            checkpoint_dir: Directory for storing checkpoints.
                          Defaults to ~/.corvin/checkpoints/
        """
        if checkpoint_dir is None:
            corvin_home = os.getenv("CORVIN_HOME", os.path.expanduser("~/.corvin"))
            checkpoint_dir = os.path.join(corvin_home, "checkpoints")

        self.checkpoint_dir = Path(checkpoint_dir)
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"[CheckpointManager] Using checkpoint dir: {self.checkpoint_dir}")

    async def save_checkpoint(self, checkpoint) -> bool:
        """Save checkpoint atomically to disk.

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

            # Write atomically (temp file → rename)
            temp_file = self.checkpoint_dir / f"{checkpoint.session_id}.tmp"
            with open(temp_file, 'w') as f:
                json.dump(checkpoint_data, f, indent=2)

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

            logger.info(f"[CheckpointManager] Loaded: {session_id}")
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

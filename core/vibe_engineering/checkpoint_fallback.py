"""
Checkpoint Fallback & Graceful Degradation

Ensures task execution continues even if checkpointing fails.
Falls back to in-memory state when persistence unavailable.

Integration: Wrap CheckpointManager calls with this layer for production resilience.
"""

from dataclasses import dataclass
from typing import Optional, Dict, Any, List
from datetime import datetime
import logging

from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState
from core.vibe_engineering.recovery_engine import RecoveryEngine

logger = logging.getLogger(__name__)


@dataclass
class CheckpointResult:
    """Result of checkpoint operation (success or fallback)."""
    success: bool
    checkpoint_id: Optional[str] = None
    mode: str = "filesystem"  # "filesystem" or "memory"
    error: Optional[str] = None
    recovery_available: bool = False  # Can resume from this checkpoint?


class CheckpointFallback:
    """
    Graceful degradation layer for checkpointing.

    If filesystem checkpoint fails:
    1. Log warning (not critical)
    2. Keep in-memory copy (task continues)
    3. Next checkpoint attempt (retry)
    4. If persistent failures: degrade to memory-only mode

    Guarantees:
    - Task execution NEVER blocked by checkpoint failure
    - Memory footprint bounded (keep last N checkpoints)
    - Filesystem recovery available when healthy again
    """

    def __init__(
        self,
        checkpoint_manager: CheckpointManager,
        recovery_engine: RecoveryEngine,
        fallback_memory_limit: int = 10  # Keep up to 10 in-memory checkpoints
    ):
        self.manager = checkpoint_manager
        self.recovery = recovery_engine
        self.fallback_memory_limit = fallback_memory_limit

        # In-memory fallback store
        self.memory_checkpoints: Dict[str, CheckpointState] = {}
        self.persistence_failures: int = 0
        self.persistence_healthy: bool = True

        logger.info("CheckpointFallback initialized (graceful degradation mode)")

    def save_with_fallback(self, checkpoint: CheckpointState) -> CheckpointResult:
        """
        Save checkpoint with graceful fallback to memory if filesystem fails.

        Args:
            checkpoint: CheckpointState to persist.

        Returns:
            CheckpointResult with success/mode/error info.
        """
        # Try filesystem first
        try:
            path = self.manager.save(checkpoint)
            self.persistence_failures = 0
            self.persistence_healthy = True

            logger.info(f"Checkpoint saved (filesystem): {checkpoint.checkpoint_id}")
            return CheckpointResult(
                success=True,
                checkpoint_id=checkpoint.checkpoint_id,
                mode="filesystem",
                recovery_available=True
            )

        except Exception as e:
            # Filesystem failed — fall back to memory
            self.persistence_failures += 1
            logger.warning(
                f"Checkpoint filesystem failed (attempt {self.persistence_failures}): {e}. "
                f"Degrading to in-memory mode."
            )

            # Store in memory
            self.memory_checkpoints[checkpoint.checkpoint_id] = checkpoint

            # Prune old checkpoints (keep most recent N)
            if len(self.memory_checkpoints) > self.fallback_memory_limit:
                oldest = min(
                    self.memory_checkpoints.keys(),
                    key=lambda k: self.memory_checkpoints[k].timestamp_iso
                )
                del self.memory_checkpoints[oldest]
                logger.debug(f"Pruned old checkpoint from memory: {oldest}")

            # Decide health status
            if self.persistence_failures >= 3:
                self.persistence_healthy = False
                logger.error(
                    f"Checkpointing in persistent failure mode (>{self.persistence_failures} failures). "
                    f"Check disk space, file permissions, or filesystem health."
                )

            return CheckpointResult(
                success=True,  # Logical success (degraded)
                checkpoint_id=checkpoint.checkpoint_id,
                mode="memory",
                error=str(e),
                recovery_available=True  # In-memory recovery still works
            )

    def load_with_fallback(self, checkpoint_id: str) -> Optional[CheckpointState]:
        """
        Load checkpoint from filesystem, fallback to memory if missing.

        Args:
            checkpoint_id: Checkpoint to load.

        Returns:
            CheckpointState, or None if not found anywhere.
        """
        # Try memory first (faster, always available)
        if checkpoint_id in self.memory_checkpoints:
            logger.info(f"Checkpoint loaded (memory): {checkpoint_id}")
            return self.memory_checkpoints[checkpoint_id]

        # Try filesystem
        try:
            checkpoints = self.manager.list_checkpoints(checkpoint_id.split('_')[0])
            for meta in checkpoints:
                if meta.checkpoint_id == checkpoint_id:
                    loaded = self.manager.load(meta.file_path)
                    logger.info(f"Checkpoint loaded (filesystem): {checkpoint_id}")
                    return loaded
        except Exception as e:
            logger.warning(f"Failed to load checkpoint from filesystem: {e}")

        logger.error(f"Checkpoint not found: {checkpoint_id}")
        return None

    def recovery_possible(self) -> bool:
        """
        Check if recovery is possible (filesystem healthy or memory fallback available).

        Returns:
            True if at least one checkpoint exists.
        """
        return len(self.memory_checkpoints) > 0 or self.persistence_healthy

    def get_status(self) -> Dict[str, Any]:
        """
        Get fallback status for monitoring.

        Returns:
            Dict with mode, health, memory usage, etc.
        """
        return {
            "mode": "degraded" if not self.persistence_healthy else "normal",
            "persistence_healthy": self.persistence_healthy,
            "persistence_failures": self.persistence_failures,
            "memory_checkpoints": len(self.memory_checkpoints),
            "memory_limit": self.fallback_memory_limit,
            "recovery_available": self.recovery_possible()
        }

    def reset_health(self):
        """
        Reset persistence health status (e.g., after operator fixes disk space).

        Called after manual recovery or operator intervention.
        """
        self.persistence_failures = 0
        self.persistence_healthy = True
        logger.info("Persistence health reset (manual recovery)")

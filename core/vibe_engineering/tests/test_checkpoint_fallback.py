"""
Checkpoint Fallback Tests

Tests for graceful degradation when checkpointing fails.
Verifies task execution continues even if persistence is unavailable.
"""

import pytest
import tempfile
from pathlib import Path
from unittest.mock import Mock, patch

from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState
from core.vibe_engineering.recovery_engine import RecoveryEngine
from core.vibe_engineering.checkpoint_fallback import CheckpointFallback, CheckpointResult


class TestCheckpointFallback:
    """Test graceful degradation layer."""

    def setup_method(self):
        self.tmpdir = tempfile.mkdtemp()
        self.manager = CheckpointManager(Path(self.tmpdir))
        self.recovery = RecoveryEngine()
        self.fallback = CheckpointFallback(self.manager, self.recovery)

    def create_test_checkpoint(self, iter_num: int = 1) -> CheckpointState:
        return CheckpointState(
            checkpoint_id=f"ckpt_{iter_num}",
            task_id="task_fallback",
            session_id="session_fallback",
            phase="execution",
            trigger="test",
            timestamp_iso="2026-08-24T15:00:00",
            iteration_num=iter_num,
            task_state={"goal": "test", "progress": 0.5},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={},
            open_subgoals=[],
            artifacts=[]
        )

    def test_save_success_filesystem(self):
        """Successful save to filesystem."""
        cp = self.create_test_checkpoint()
        result = self.fallback.save_with_fallback(cp)

        assert result.success is True
        assert result.mode == "filesystem"
        assert result.recovery_available is True
        assert result.error is None

    def test_save_fallback_to_memory_on_failure(self):
        """Fallback to memory when filesystem write fails."""
        cp = self.create_test_checkpoint()

        # Mock filesystem failure
        with patch.object(self.manager, 'save', side_effect=OSError("Disk full")):
            result = self.fallback.save_with_fallback(cp)

        assert result.success is True  # Logical success (degraded)
        assert result.mode == "memory"  # Fell back to memory
        assert result.recovery_available is True  # Still recoverable
        assert "Disk full" in result.error

    def test_load_prefers_memory_over_filesystem(self):
        """Load from memory is preferred (faster, always available)."""
        cp = self.create_test_checkpoint()

        # Save to both (memory + filesystem)
        self.fallback.save_with_fallback(cp)

        # Load should come from memory
        loaded = self.fallback.load_with_fallback(cp.checkpoint_id)

        assert loaded is not None
        assert loaded.checkpoint_id == cp.checkpoint_id

    def test_fallback_memory_limit(self):
        """In-memory store bounded by limit."""
        # Set small limit
        self.fallback.fallback_memory_limit = 3

        # Save 5 checkpoints (all to memory due to mocked failure)
        with patch.object(self.manager, 'save', side_effect=OSError("Simulated failure")):
            for i in range(5):
                cp = self.create_test_checkpoint(iter_num=i)
                self.fallback.save_with_fallback(cp)

        # Should only keep 3 most recent
        assert len(self.fallback.memory_checkpoints) <= 3

    def test_persistence_failure_tracking(self):
        """Track persistence failures and mark unhealthy after threshold."""
        cp = self.create_test_checkpoint()

        assert self.fallback.persistence_healthy is True
        assert self.fallback.persistence_failures == 0

        # Simulate 3 consecutive failures
        with patch.object(self.manager, 'save', side_effect=OSError("Error")):
            for i in range(3):
                cp = self.create_test_checkpoint(iter_num=i)
                self.fallback.save_with_fallback(cp)

        assert self.fallback.persistence_failures == 3
        assert self.fallback.persistence_healthy is False  # Marked unhealthy

    def test_recovery_possible_with_memory_checkpoints(self):
        """Recovery is possible even if filesystem fails (in-memory fallback exists)."""
        cp = self.create_test_checkpoint()

        # Save to memory only
        with patch.object(self.manager, 'save', side_effect=OSError("Error")):
            self.fallback.save_with_fallback(cp)

        # Recovery should still be possible
        assert self.fallback.recovery_possible() is True

    def test_recovery_not_possible_without_checkpoints(self):
        """Recovery not possible if no checkpoints saved anywhere."""
        # No saves made
        assert self.fallback.recovery_possible() is False

    def test_get_status_reflects_degradation(self):
        """Status report shows degradation mode."""
        cp = self.create_test_checkpoint()

        # Simulate failures
        with patch.object(self.manager, 'save', side_effect=OSError("Error")):
            for i in range(3):
                self.fallback.save_with_fallback(self.create_test_checkpoint(iter_num=i))

        status = self.fallback.get_status()

        assert status["mode"] == "degraded"
        assert status["persistence_healthy"] is False
        assert status["persistence_failures"] == 3
        assert status["memory_checkpoints"] == 3
        assert status["recovery_available"] is True

    def test_reset_health_after_manual_recovery(self):
        """Operator can reset health status after fixing issue."""
        cp = self.create_test_checkpoint()

        # Simulate failures to mark unhealthy
        with patch.object(self.manager, 'save', side_effect=OSError("Error")):
            self.fallback.save_with_fallback(cp)
            self.fallback.save_with_fallback(cp)
            self.fallback.save_with_fallback(cp)

        assert self.fallback.persistence_healthy is False

        # Operator fixes issue (e.g., frees disk space)
        self.fallback.reset_health()

        assert self.fallback.persistence_healthy is True
        assert self.fallback.persistence_failures == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

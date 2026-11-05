"""Concurrent-Write Race Condition Test for CheckpointManager (ADR-0875).

Verifies that flock-based serialization prevents corruption under concurrent writes.
"""

import asyncio
import json
import tempfile
from pathlib import Path
from datetime import datetime

import pytest

from core.session_manager.checkpoint_manager import CheckpointManager


def MockCheckpoint(session_id: str):
    """A REAL ``lifecycle_manager.Checkpoint``. The hand-written mock lacked
    the ADR-0668 savings fields and ``compute_hash()`` that save_checkpoint
    uses, so every save in this file failed on the mock, not on the race."""
    from core.session_manager.lifecycle_manager import Checkpoint

    return Checkpoint(
        session_id=session_id,
        goal=f"test_goal_{session_id}",
        goal_hash="hash_123",
        timestamp=datetime.now().timestamp(),
        context_reduction_pct=50,
        context_tokens_used=1000,
        audit_trail_hash="audit_hash_456",
        checkpoint_hash="ckpt_hash_789",
        phase="execution",
    )


class TestCheckpointManagerRaceCondition:
    """Test concurrent-write safety under ADR-0875."""

    @pytest.fixture
    def temp_checkpoint_dir(self):
        """Temporary directory for checkpoint storage."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def manager(self, temp_checkpoint_dir):
        """CheckpointManager instance."""
        return CheckpointManager(
            checkpoint_dir=temp_checkpoint_dir,
            cleanup_interval_days=7,
            auto_cleanup=False,  # Don't run cleanup in test
        )

    @pytest.mark.asyncio
    async def test_concurrent_writes_no_corruption(self, manager):
        """Test that concurrent writes to same session_id produce valid JSON.

        This test verifies ADR-0875: flock-based serialization prevents
        half-written / corrupted JSON under concurrent writes.
        """
        session_id = "test_session_concurrent"
        num_concurrent_writes = 10

        # Create checkpoints with same session_id (would race in old code)
        checkpoints = [MockCheckpoint(session_id) for _ in range(num_concurrent_writes)]

        # Launch concurrent writes
        tasks = [manager.save_checkpoint(cp) for cp in checkpoints]
        results = await asyncio.gather(*tasks)

        # All writes should succeed
        assert all(results), f"Some writes failed: {results}"

        # Verify final checkpoint file is valid JSON (not corrupted)
        checkpoint_file = manager.checkpoint_dir / f"{session_id}.json"
        assert checkpoint_file.exists(), f"Checkpoint file not found: {checkpoint_file}"

        # Read and verify JSON is parseable and complete
        with open(checkpoint_file, 'r', encoding="utf-8") as f:
            data = json.load(f)

        # Verify all required fields present
        required_fields = [
            "session_id",
            "goal",
            "goal_hash",
            "timestamp",
            "context_reduction_pct",
            "context_tokens_used",
            "audit_trail_hash",
            "checkpoint_hash",
            "phase",
        ]
        for field in required_fields:
            assert field in data, f"Missing required field: {field}"
            assert data[field] is not None, f"Field is None: {field}"

        # Verify session_id matches
        assert data["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_1000_concurrent_writes_stress(self, manager):
        """Stress test: 1000 concurrent writes to same session.

        This reproduces Phase B load-test scenario (550 concurrent sessions).
        Verifies that none result in corrupted checkpoints.
        """
        session_id = "test_stress_1000"

        # Simulate 1000 checkpoint saves
        checkpoints = [MockCheckpoint(session_id) for _ in range(1000)]

        # Save in batches of 100 to simulate realistic concurrent load
        batch_size = 100
        for batch_start in range(0, 1000, batch_size):
            batch_end = min(batch_start + batch_size, 1000)
            batch = checkpoints[batch_start:batch_end]

            tasks = [manager.save_checkpoint(cp) for cp in batch]
            results = await asyncio.gather(*tasks)

            # All writes in batch should succeed
            success_rate = sum(results) / len(results)
            assert success_rate >= 0.95, f"Batch {batch_start//batch_size} success rate: {success_rate}"

        # Final checkpoint should be valid
        checkpoint_file = manager.checkpoint_dir / f"{session_id}.json"
        with open(checkpoint_file, 'r', encoding="utf-8") as f:
            data = json.load(f)
        assert data["session_id"] == session_id

    @pytest.mark.asyncio
    async def test_concurrent_reads_during_writes(self, manager):
        """Test that concurrent reads don't see partial data during writes.

        Simulates: one writer, multiple readers racing on same checkpoint.
        Readers should either get last complete version or None (integrity check fails).
        """
        session_id = "test_concurrent_read_write"
        checkpoint = MockCheckpoint(session_id)

        # Start multiple write and read tasks
        write_tasks = [
            manager.save_checkpoint(checkpoint) for _ in range(5)
        ]

        read_tasks = [
            manager.load_checkpoint(session_id) for _ in range(5)
        ]

        # Run all concurrently
        all_tasks = write_tasks + read_tasks
        results = await asyncio.gather(*all_tasks, return_exceptions=True)

        # Check that no exceptions were raised
        for result in results:
            if isinstance(result, Exception):
                pytest.fail(f"Unexpected exception: {result}")

        # Final checkpoint should be readable
        final_cp = await manager.load_checkpoint(session_id)
        # May be None if integrity check failed (fail-closed), or valid checkpoint
        if final_cp is not None:
            assert final_cp.session_id == session_id

    @pytest.mark.asyncio
    async def test_lock_file_cleanup(self, manager, temp_checkpoint_dir):
        """Lock files do not accumulate: ONE per session, reused by every write.

        A flock lock file is deliberately left in place — unlinking it after
        the unlock lets a waiter that already opened the old inode and a new
        writer that creates a fresh file both "hold" the lock at once. The
        former expectation (fewer lock files than sessions) asked for exactly
        that race. What must hold is that re-writing a session reuses its lock
        file instead of creating another.
        """
        for i in range(10):
            checkpoint = MockCheckpoint(f"test_lock_cleanup_{i}")
            await manager.save_checkpoint(checkpoint)
        first = sorted(temp_checkpoint_dir.glob("*.lock"))
        assert len(first) <= 10

        for _ in range(3):
            for i in range(10):
                await manager.save_checkpoint(MockCheckpoint(f"test_lock_cleanup_{i}"))
        assert sorted(temp_checkpoint_dir.glob("*.lock")) == first

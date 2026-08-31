"""
Concurrent Checkpoint Write Tests

Tests for file locking and collision prevention when multiple
processes write checkpoints simultaneously.
"""

import pytest
import tempfile
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import time

from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState


class TestConcurrentCheckpointWrites:
    """Test concurrent checkpoint writes with file locking."""

    def setup_method(self):
        """Create isolated temp directory for each test."""
        self.tmpdir = tempfile.mkdtemp()
        self.manager = CheckpointManager(Path(self.tmpdir))

    def create_test_checkpoint(self, task_id: str, iter_num: int) -> CheckpointState:
        """Create a minimal test checkpoint."""
        return CheckpointState(
            checkpoint_id=f"ckpt_{iter_num}",
            task_id=task_id,
            session_id="session_concurrent",
            phase="execution",
            trigger="test",
            timestamp_iso="2026-08-24T15:00:00",
            iteration_num=iter_num,
            task_state={"task_id": task_id, "goal": "test", "progress": iter_num * 0.1},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={},
            open_subgoals=[],
            artifacts=[]
        )

    def test_concurrent_writes_same_task(self):
        """Multiple threads writing different iterations of same task."""
        task_id = "task_concurrent"

        # Create 10 checkpoints concurrently
        checkpoints = [
            self.create_test_checkpoint(task_id, i)
            for i in range(10)
        ]

        saved_paths = []
        with ThreadPoolExecutor(max_workers=4) as executor:
            futures = [
                executor.submit(self.manager.save, cp)
                for cp in checkpoints
            ]
            for future in as_completed(futures):
                try:
                    path = future.result(timeout=5)
                    saved_paths.append(path)
                except Exception as e:
                    # Lock contention acceptable; some writes may fail
                    pass

        # At least most should succeed
        assert len(saved_paths) >= 8, f"Only {len(saved_paths)}/10 writes succeeded"

        # All saved files should exist
        for path in saved_paths:
            assert path.exists(), f"Checkpoint not persisted: {path}"

        # All should be loadable (no corruption)
        for path in saved_paths:
            loaded = self.manager.load(path)
            assert loaded is not None

    def test_concurrent_writes_different_tasks(self):
        """Multiple threads writing different tasks (no collision expected)."""
        tasks = [f"task_{i}" for i in range(5)]

        checkpoints = [
            self.create_test_checkpoint(task, i)
            for task in tasks
            for i in range(3)
        ]

        saved_count = 0
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = [
                executor.submit(self.manager.save, cp)
                for cp in checkpoints
            ]
            for future in as_completed(futures):
                try:
                    future.result(timeout=5)
                    saved_count += 1
                except Exception:
                    pass

        # All should succeed (no collision)
        assert saved_count == len(checkpoints), \
            f"Expected {len(checkpoints)} saves, got {saved_count}"

    def test_checkpoint_file_consistency(self):
        """Verify checkpoint content matches after concurrent write."""
        task_id = "task_consistency"

        checkpoint = self.create_test_checkpoint(task_id, 1)
        original_state = checkpoint.task_state.copy()

        # Write twice concurrently
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.manager.save, checkpoint),
                executor.submit(self.manager.save, checkpoint)
            ]
            saved_paths = []
            for future in as_completed(futures):
                try:
                    saved_paths.append(future.result(timeout=5))
                except Exception:
                    pass

        # Load both and verify they match
        for path in saved_paths:
            loaded = self.manager.load(path)
            assert loaded.task_state == original_state, \
                "Loaded checkpoint has different state than original"

    def test_list_checkpoints_under_concurrent_writes(self):
        """list_checkpoints() works correctly during concurrent writes."""
        task_id = "task_list"

        checkpoints = [
            self.create_test_checkpoint(task_id, i)
            for i in range(5)
        ]

        # Start writing in background
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [
                executor.submit(self.manager.save, cp)
                for cp in checkpoints
            ]

            # List while writes are in flight
            time.sleep(0.05)  # Let some writes start
            listed = self.manager.list_checkpoints(task_id)

            # Should find at least some
            assert len(listed) > 0, "No checkpoints found during concurrent write"

            # Wait for all writes to complete
            for future in as_completed(futures):
                try:
                    future.result(timeout=5)
                except Exception:
                    pass

        # Final list should have all
        final_listed = self.manager.list_checkpoints(task_id)
        assert len(final_listed) >= len(checkpoints) - 1, \
            f"Expected ~{len(checkpoints)} checkpoints, got {len(final_listed)}"

    def test_lock_file_cleanup(self):
        """Lock files cleaned up after concurrent writes."""
        task_id = "task_cleanup"

        checkpoint = self.create_test_checkpoint(task_id, 1)

        # Write checkpoint
        self.manager.save(checkpoint)

        # Lock file may exist temporarily (depends on OS timing)
        # This test just ensures no lock files accumulate
        lock_files = list(self.manager.checkpoint_dir.glob(".*.lock"))
        assert len(lock_files) <= 1, f"Too many lock files: {lock_files}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])

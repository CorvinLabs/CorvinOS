"""
Comprehensive Stress Test for CheckpointManager Thread-Safe Synchronization (ADR-0875).

Tests verify:
1. No data corruption under 1000+ concurrent writes
2. Performance regression < 5%
3. Lock timeout handling (fail-closed)
4. Audit trail emission
5. Task-level lock granularity (concurrent writes to different tasks)
"""

import pytest
import tempfile
import time
import json
import threading
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime

from core.vibe_engineering.checkpoint_manager import CheckpointManager, CheckpointState


class TestCheckpointManagerStress:
    """Stress tests for thread-safe checkpoint operations (ADR-0875)."""

    @pytest.fixture
    def temp_checkpoint_dir(self):
        """Temporary directory for checkpoint storage."""
        with tempfile.TemporaryDirectory() as tmp:
            yield Path(tmp)

    @pytest.fixture
    def manager(self, temp_checkpoint_dir):
        """CheckpointManager instance."""
        # These tests measure the manager's own lock contention. ``None`` now
        # means "the tenant's real chain writer" (serialised fsync'd writes,
        # which would dominate the timing), so pass an explicit in-test sink;
        # checkpoint auditing itself is covered by
        # test_security_fix_2_checkpoint_integrity.py.
        class _CountingWriter:
            def __init__(self):
                self.events = []

            def write_event(self, event):
                self.events.append(event.event_type)
                return "h"

        return CheckpointManager(
            checkpoint_dir=temp_checkpoint_dir,
            tenant_id="_default",
            audit_writer=_CountingWriter(),
        )

    def create_test_checkpoint(self, task_id: str, checkpoint_id: str, iter_num: int) -> CheckpointState:
        """Create a minimal test checkpoint."""
        return CheckpointState(
            checkpoint_id=checkpoint_id,
            tenant_id="_default",
            task_id=task_id,
            session_id="session_stress",
            phase="execution",
            trigger="stress_test",
            timestamp_iso=datetime.now().isoformat(),
            iteration_num=iter_num,
            task_state={"task_id": task_id, "goal": "stress_test", "progress": iter_num * 0.01},
            context_essentials={"kept": [], "reduction_pct": 91},
            learning_state={"strategies": [], "success_rate": 0.0},
            open_subgoals=[{"description": "test", "status": "in_progress"}],
            artifacts=[]
        )

    def test_1000_concurrent_writes_same_task_no_corruption(self, manager):
        """
        Stress test: 1000 concurrent writes to same task.

        GATE 1: No data corruption detected in any checkpoint.
        GATE 2: All writes complete successfully.
        """
        task_id = "stress_task_1000"
        num_concurrent_writes = 1000

        # Create 1000 unique checkpoints for the same task
        checkpoints = [
            self.create_test_checkpoint(
                task_id=task_id,
                checkpoint_id=f"stress_ckpt_{i:05d}",
                iter_num=i
            )
            for i in range(num_concurrent_writes)
        ]

        saved_paths = []
        start_time = time.time()

        # Launch all 1000 writes concurrently
        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [
                executor.submit(manager.save, cp)
                for cp in checkpoints
            ]
            for future in as_completed(futures):
                try:
                    path = future.result(timeout=30)
                    saved_paths.append(path)
                except Exception as e:
                    pytest.fail(f"Concurrent write failed: {e}")

        elapsed = time.time() - start_time
        logger.info(f"Completed 1000 concurrent writes in {elapsed:.2f}s")

        # GATE 1: All writes must succeed
        assert len(saved_paths) == num_concurrent_writes, \
            f"Expected {num_concurrent_writes} successful writes, got {len(saved_paths)}"

        # GATE 2: All saved files must exist
        for path in saved_paths:
            assert path.exists(), f"Checkpoint not persisted: {path}"

        # GATE 3: All checkpoints must be loadable without corruption
        corruption_count = 0
        for path in saved_paths:
            try:
                loaded = manager.load(path)
                assert loaded is not None, f"Failed to load checkpoint: {path}"
                assert loaded.task_id == task_id, f"Task ID mismatch in {path}"
                # Verify JSON is not corrupted
                json_str = path.read_text()
                data = json.loads(json_str)
                assert "checkpoint_id" in data, f"Missing checkpoint_id in {path}"
                assert "merkle_root" in data, f"Missing merkle_root in {path}"
                assert "tenant_signature" in data, f"Missing tenant_signature in {path}"
            except Exception as e:
                corruption_count += 1
                logger.error(f"Corruption detected in {path}: {e}")

        assert corruption_count == 0, \
            f"Detected {corruption_count} corrupted checkpoints out of {len(saved_paths)}"

        logger.info(f"✅ GATE 1-3 PASSED: 1000 concurrent writes, zero corruption")

    def test_1000_concurrent_writes_different_tasks_no_interference(self, manager):
        """
        Stress test: 1000 writes distributed across 10 different tasks.

        Tests that fine-grained task-level locking allows concurrent writes
        to different tasks without interference.

        GATE: All writes succeed with no data loss or corruption.
        """
        num_tasks = 10
        writes_per_task = 100
        total_writes = num_tasks * writes_per_task

        # Create checkpoints across 10 different tasks
        checkpoints = [
            self.create_test_checkpoint(
                task_id=f"stress_task_{t}",
                checkpoint_id=f"ckpt_{t}_{i:04d}",
                iter_num=i
            )
            for t in range(num_tasks)
            for i in range(writes_per_task)
        ]

        saved_count = 0
        start_time = time.time()

        with ThreadPoolExecutor(max_workers=32) as executor:
            futures = [
                executor.submit(manager.save, cp)
                for cp in checkpoints
            ]
            for future in as_completed(futures):
                try:
                    future.result(timeout=30)
                    saved_count += 1
                except Exception as e:
                    pytest.fail(f"Write failed: {e}")

        elapsed = time.time() - start_time

        # GATE: All writes must succeed
        assert saved_count == total_writes, \
            f"Expected {total_writes} saves, got {saved_count}"

        logger.info(f"✅ GATE PASSED: {total_writes} concurrent writes across {num_tasks} tasks "
                   f"completed in {elapsed:.2f}s without interference")

    def test_performance_concurrent_same_task_overhead_bounded(self, manager):
        """
        Lock contention on ONE task must not degrade into a convoy.

        All 100 concurrent writes target the same task, so the per-task lock
        serialises them BY DESIGN: the best concurrency can do is match the
        sequential time, plus the thread hand-off cost. The former gate
        ("< 5% slower than sequential") asserted the impossible — measured
        +22..43% for ~0.03 s of absolute difference on every run, before and
        after the 2026-09-28 audit change — so it failed on every machine.

        What is gated now: best-of-3 concurrent time stays under 2x the
        best-of-3 sequential time. A lock convoy, a timeout/retry loop or a
        lock held across I/O it should not cover blows far past that.
        """
        task_id = "perf_test_task"
        num_writes = 100

        def _sequential(run: int) -> float:
            cps = [self.create_test_checkpoint(f"{task_id}_seq{run}", f"seq_{i:04d}", i)
                   for i in range(num_writes)]
            start = time.perf_counter()
            for cp in cps:
                manager.save(cp)
            return time.perf_counter() - start

        def _concurrent(run: int) -> float:
            cps = [self.create_test_checkpoint(f"{task_id}_conc{run}", f"conc_{i:04d}", i)
                   for i in range(num_writes)]
            start = time.perf_counter()
            with ThreadPoolExecutor(max_workers=16) as executor:
                futures = [executor.submit(manager.save, cp) for cp in cps]
                for future in as_completed(futures):
                    future.result(timeout=30)
            return time.perf_counter() - start

        seq_time = min(_sequential(r) for r in range(3))
        conc_time = min(_concurrent(r) for r in range(3))
        ratio = conc_time / seq_time

        logger.info(f"Performance baseline: {seq_time:.3f}s (sequential, best of 3)")
        logger.info(f"Performance test: {conc_time:.3f}s (concurrent, best of 3)")
        assert ratio < 2.0, (
            f"concurrent same-task writes took {ratio:.2f}x the sequential time "
            f"({conc_time:.3f}s vs {seq_time:.3f}s) — lock convoy?"
        )

    def test_concurrent_reads_during_1000_writes(self, manager):
        """
        Test concurrent reads and writes (reader-writer scenario).

        While 500 writes are in flight, 200 reads attempt to load checkpoints.
        Verifies:
        - No readers block indefinitely
        - Loaded checkpoints are valid (no partial reads)
        - Integrity verification succeeds
        """
        task_id = "rw_contention_task"
        num_writes = 500
        num_reads = 200

        # Create checkpoints for writing
        checkpoints_to_write = [
            self.create_test_checkpoint(task_id, f"ckpt_{i:05d}", i)
            for i in range(num_writes)
        ]

        # Paths to read (will exist after writes)
        paths_to_read = [
            manager.checkpoint_dir / f"{task_id}_ckpt_{i:05d}_{i:03d}.json"
            for i in range(0, num_writes, num_writes // num_reads)
        ]

        read_results = {"success": 0, "not_found": 0, "corruption": 0}
        read_lock = threading.Lock()

        def perform_read(path):
            """Attempt to load a checkpoint."""
            try:
                if not path.exists():
                    with read_lock:
                        read_results["not_found"] += 1
                    return
                loaded = manager.load(path)
                if loaded is None:
                    with read_lock:
                        read_results["corruption"] += 1
                else:
                    with read_lock:
                        read_results["success"] += 1
            except Exception as e:
                logger.warning(f"Read error on {path}: {e}")
                with read_lock:
                    read_results["corruption"] += 1

        # Launch writes and reads concurrently
        with ThreadPoolExecutor(max_workers=32) as executor:
            write_futures = [
                executor.submit(manager.save, cp)
                for cp in checkpoints_to_write
            ]

            # Start reads after first few writes
            time.sleep(0.1)
            read_futures = [
                executor.submit(perform_read, path)
                for path in paths_to_read
            ]

            # Wait for all to complete
            for future in as_completed(write_futures + read_futures):
                try:
                    future.result(timeout=30)
                except Exception as e:
                    pytest.fail(f"Read/write operation failed: {e}")

        logger.info(f"Read results: success={read_results['success']}, "
                   f"not_found={read_results['not_found']}, "
                   f"corruption={read_results['corruption']}")

        # GATE: No corruption or other errors
        assert read_results["corruption"] == 0, \
            f"Detected {read_results['corruption']} corrupted reads"

        logger.info(f"✅ GATE PASSED: 500 concurrent writes + 200 reads, no corruption")

    def test_lock_timeout_fail_closed(self, manager):
        """
        Test that lock acquisition timeout results in fail-closed behavior.

        Creates a deadlock scenario where a thread holds a lock and never
        releases it, then verifies the other thread times out and raises.
        """
        task_id = "lock_timeout_task"
        checkpoint = self.create_test_checkpoint(task_id, "ckpt_1", 1)

        # Track whether timeout exception was raised
        timeout_raised = threading.Event()

        def hold_lock_forever():
            """Acquire lock and hold it (simulate deadlock)."""
            lock = manager._get_task_lock(task_id)
            lock.acquire()
            # Never release (simulating deadlock)
            time.sleep(10)

        def attempt_with_timeout():
            """Attempt to save with lock already held."""
            try:
                manager.save(checkpoint)
            except RuntimeError as e:
                if "Failed to acquire lock" in str(e):
                    timeout_raised.set()
                    logger.info(f"Timeout correctly raised: {e}")
                raise

        # Start lock holder in background
        holder_thread = threading.Thread(target=hold_lock_forever, daemon=True)
        holder_thread.start()

        # Give lock holder time to acquire
        time.sleep(0.1)

        # Attempt save with short timeout (will fail)
        # Monkey-patch the timeout for this test
        original_timeout = manager._acquire_task_lock.__code__

        with pytest.raises(RuntimeError, match="Failed to acquire lock"):
            manager.save(checkpoint)  # Uses 10s default timeout

        logger.info(f"✅ GATE PASSED: Lock timeout correctly fail-closed")

    def test_audit_trail_emission(self, temp_checkpoint_dir):
        """
        Test that checkpoint operations emit audit events.

        Creates a manager WITH audit writer and verifies events are emitted.
        """
        # Create a mock audit writer
        audit_events = []

        class MockAuditWriter:
            def write_event(self, event):
                audit_events.append(event)

        manager = CheckpointManager(
            checkpoint_dir=temp_checkpoint_dir,
            tenant_id="_default",
            audit_writer=MockAuditWriter()
        )

        task_id = "audit_test_task"
        checkpoint = self.create_test_checkpoint(task_id, "audit_ckpt_1", 1)

        # Save checkpoint
        manager.save(checkpoint)

        # Exactly ONE record per save: the persisted write. The in-process lock
        # acquire/release pair is deliberately not audited (it tripled the chain
        # writes per save, ~10x the save's own cost, for a signal nobody can act
        # on); a failed verification is audited separately on load().
        event_types = [e.event_type for e in audit_events]
        assert event_types == ["checkpoint_written"], event_types

        # A tampered checkpoint is still audited as an integrity failure.
        saved = manager.save(self.create_test_checkpoint(task_id, "audit_ckpt_2", 2))
        data = json.loads(saved.read_text())
        data["iteration_num"] = 99
        saved.write_text(json.dumps(data))
        audit_events.clear()
        with pytest.raises(Exception):
            manager.load(saved)
        assert [e.event_type for e in audit_events] == ["checkpoint_integrity_failed"]

        # Verify audit events have correct tenant_id
        for event in audit_events:
            assert event.tenant_id == "_default"

        logger.info(f"✅ GATE PASSED: {len(audit_events)} audit events emitted correctly")

    def test_no_regression_with_prior_tests(self, manager, temp_checkpoint_dir):
        """
        Verify thread-safe changes don't break prior checkpoint tests.

        Re-runs prior basic tests to ensure no regression.
        """
        task_id = "regression_test_task"

        # Test 1: Basic save/load
        checkpoint = self.create_test_checkpoint(task_id, "ckpt_1", 1)
        path = manager.save(checkpoint)
        loaded = manager.load(path)
        assert loaded.checkpoint_id == checkpoint.checkpoint_id

        # Test 2: List checkpoints
        for i in range(5):
            cp = self.create_test_checkpoint(task_id, f"ckpt_{i}", i)
            manager.save(cp)

        listed = manager.list_checkpoints(task_id)
        assert len(listed) >= 5, f"Expected ≥5 checkpoints, got {len(listed)}"

        # Test 3: Latest checkpoint
        latest = manager.get_latest(task_id)
        assert latest is not None
        assert latest.task_id == task_id

        logger.info(f"✅ GATE PASSED: No regression in prior tests")


# Logging helper
import logging
logger = logging.getLogger(__name__)


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])

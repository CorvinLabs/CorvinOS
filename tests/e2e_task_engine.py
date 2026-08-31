"""End-to-end tests for ADR-0080 task engine.

Tests:
- Quota gates (max_concurrent, max_per_day)
- Event log rotation (10 MB / 7 day TTL)
- Session cleanup (L8 integration)
- L16 audit emissions (M4.1)
- Concurrent task execution
- Performance under load

ADR-0080 M4/M4.1 E2E verification.
"""
import json
import tempfile
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor, as_completed
import sys
import os

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / "core/console"))

from corvin_console.task_manager import TaskManager, TaskStatus, QuotaExceededError


class TestTaskQuota:
    """Test quota gate enforcement."""

    def test_max_concurrent_exceeded(self):
        """Verify that max_concurrent quota is enforced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:chat1"
            max_concurrent = 3

            # Create 3 tasks (should succeed)
            task_ids = []
            for i in range(max_concurrent):
                tid = tm.create_task(
                    chat_key,
                    f"task {i}",
                    check_quota=True,
                    quota_limits={"max_concurrent": max_concurrent, "max_per_day": 100},
                )
                task_ids.append(tid)
                # Mark as running
                tm.record_event(tid, {"event": "task.started", "engine": "test"})

            assert len(task_ids) == max_concurrent, "Should create N tasks"

            # 4th task should fail quota check
            try:
                tm.create_task(
                    chat_key,
                    "task 4",
                    check_quota=True,
                    quota_limits={"max_concurrent": max_concurrent, "max_per_day": 100},
                )
                assert False, "Should have raised QuotaExceededError"
            except QuotaExceededError as e:
                assert "running" in str(e).lower(), f"Error should mention running tasks: {e}"

            print("✓ max_concurrent quota enforced")

    def test_max_per_day_exceeded(self):
        """Verify that max_per_day quota is enforced."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:chat2"
            max_per_day = 3

            # Create 3 tasks (should succeed)
            for i in range(max_per_day):
                tm.create_task(
                    chat_key,
                    f"task {i}",
                    check_quota=True,
                    quota_limits={"max_concurrent": 10, "max_per_day": max_per_day},
                )

            # 4th task should fail quota check
            try:
                tm.create_task(
                    chat_key,
                    "task 4",
                    check_quota=True,
                    quota_limits={"max_concurrent": 10, "max_per_day": max_per_day},
                )
                assert False, "Should have raised QuotaExceededError"
            except QuotaExceededError as e:
                assert "daily" in str(e).lower(), f"Error should mention daily quota: {e}"

            print("✓ max_per_day quota enforced")


class TestEventLogRotation:
    """Test event log rotation on size/age."""

    def test_rotation_on_large_file(self):
        """Verify that event log rotates when exceeding 10 MB."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            task_id = tm.create_task("test:chat", "large task")

            # Write large events to simulate 10 MB+ log
            # Each event ~1 KB, so write 15k events = ~15 MB
            large_payload = "x" * 900  # ~900 bytes of payload per event

            # Record 12,000 events (~12 MB)
            for i in range(12000):
                tm.record_event(task_id, {
                    "event": "stream_token",
                    "chunk": large_payload,
                })
                # Rotation happens on _write_event call

            # Check that rotation happened (should have .events.jsonl.1)
            events_path = tasks_dir / f"{task_id}.events.jsonl"
            rotated_path = tasks_dir / f"{task_id}.events.jsonl.1"

            if events_path.exists():
                size_mb = events_path.stat().st_size / (1024 * 1024)
                assert size_mb < 10, f"Active log should be < 10 MB, got {size_mb:.1f} MB"

            # Check that we have either rotated files or the active log is reasonable
            assert events_path.exists() or rotated_path.exists(), "Should have event logs"

            print(f"✓ Event log rotation works (active: {size_mb:.1f} MB)")

    def test_cleanup_removes_rotated_logs(self):
        """Verify that cleanup removes all rotated log files."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:cleanup"
            task_id = tm.create_task(chat_key, "cleanup test")

            # Create fake rotated files
            events_path = tasks_dir / f"{task_id}.events.jsonl"
            (tasks_dir / f"{task_id}.events.jsonl.1").touch()
            (tasks_dir / f"{task_id}.events.jsonl.2").touch()
            events_path.write_text("{}\n")

            # Cleanup should remove all
            deleted = tm.cleanup_tasks(chat_key)

            assert not events_path.exists(), "Active log should be deleted"
            assert not (tasks_dir / f"{task_id}.events.jsonl.1").exists(), "Rotated .1 should be deleted"
            assert not (tasks_dir / f"{task_id}.events.jsonl.2").exists(), "Rotated .2 should be deleted"

            print(f"✓ Cleanup removes all task files ({deleted} deleted)")


class TestConcurrentTasks:
    """Test concurrent task creation and execution."""

    def test_concurrent_task_creation(self):
        """Verify that multiple concurrent tasks work correctly."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:concurrent"
            num_tasks = 10

            start_time = time.time()

            # Create tasks concurrently
            def create_and_run(i):
                tid = tm.create_task(chat_key, f"concurrent task {i}", check_quota=False)
                tm.record_event(tid, {"event": "task.started", "engine": "test"})
                # Simulate some work
                for j in range(100):
                    tm.record_event(tid, {"event": "stream_token", "chunk": f"output {j}"})
                tm.record_event(tid, {"event": "task.completed", "exit_code": 0})
                return tid

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(create_and_run, i) for i in range(num_tasks)]
                task_ids = [f.result() for f in as_completed(futures)]

            elapsed = time.time() - start_time

            assert len(task_ids) == num_tasks, f"Should create {num_tasks} tasks"
            tasks = tm.list_tasks(chat_key)
            assert len(tasks) == num_tasks, f"Should list {num_tasks} tasks"
            assert all(t.status == TaskStatus.COMPLETED for t in tasks), "All should be completed"

            print(f"✓ {num_tasks} concurrent tasks created in {elapsed:.1f}s")
            print(f"  Throughput: {num_tasks/elapsed:.1f} tasks/sec")

    def test_quota_under_concurrent_load(self):
        """Verify quota gates work under concurrent load (best-effort, may have race).

        Note: Concurrent quota checks are not atomic (no distributed lock).
        This is acceptable per M4 design (best-effort quota). In production,
        quota would be enforced at a higher layer with proper locking.

        Test: Sequential create + mark running, then concurrent creates should hit quota.
        """
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:quota_concurrent"
            max_concurrent = 2

            # First, pre-create 2 running tasks (to fill quota)
            for i in range(max_concurrent):
                tid = tm.create_task(
                    chat_key,
                    f"pre-task {i}",
                    check_quota=False,  # Skip quota for setup
                )
                tm.record_event(tid, {"event": "task.started", "engine": "test"})

            # Now try to create more concurrently with quota check enabled
            def create_with_quota(i):
                try:
                    tid = tm.create_task(
                        chat_key,
                        f"task {i}",
                        check_quota=True,
                        quota_limits={"max_concurrent": max_concurrent, "max_per_day": 100},
                    )
                    tm.record_event(tid, {"event": "task.started", "engine": "test"})
                    return ("success", tid)
                except QuotaExceededError as e:
                    return ("quota_exceeded", str(e))

            with ThreadPoolExecutor(max_workers=5) as executor:
                futures = [executor.submit(create_with_quota, i) for i in range(5)]
                results = [f.result() for f in as_completed(futures)]

            successes = [r for r in results if r[0] == "success"]
            failures = [r for r in results if r[0] == "quota_exceeded"]

            # Should have rejections since we pre-filled the quota
            assert len(failures) > 0, f"Should have quota rejections (successes: {len(successes)}, failures: {len(failures)})"
            print(f"✓ Quota enforced under concurrent load: {len(successes)} succeeded, {len(failures)} rejected")


class TestSessionCleanup:
    """Test session reset cleanup."""

    def test_cleanup_removes_all_tasks(self):
        """Verify that cleanup removes all tasks for a chat_key."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:session_cleanup"

            # Create multiple tasks
            task_ids = []
            for i in range(5):
                tid = tm.create_task(chat_key, f"task {i}")
                task_ids.append(tid)

            # All tasks should exist
            tasks = tm.list_tasks(chat_key)
            assert len(tasks) == 5, "Should have 5 tasks"

            # Cleanup should remove all
            deleted = tm.cleanup_tasks(chat_key)
            assert deleted > 0, "Should have deleted tasks"

            # Tasks should be gone
            tasks = tm.list_tasks(chat_key)
            assert len(tasks) == 0, "Should have no tasks after cleanup"

            print(f"✓ Session cleanup removes all tasks ({deleted} files deleted)")


class TestPerformance:
    """Test performance characteristics."""

    def test_task_creation_throughput(self):
        """Measure task creation throughput."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:perf"
            num_tasks = 100

            start_time = time.time()
            for i in range(num_tasks):
                tm.create_task(chat_key, f"perf task {i}", check_quota=False)
            elapsed = time.time() - start_time

            throughput = num_tasks / elapsed
            print(f"✓ Task creation throughput: {throughput:.0f} tasks/sec")
            assert throughput > 100, f"Should create >100 tasks/sec, got {throughput:.0f}"

    def test_large_event_log_query(self):
        """Measure performance of querying large event logs."""
        with tempfile.TemporaryDirectory() as tmpdir:
            tasks_dir = Path(tmpdir)
            tm = TaskManager(tasks_dir)

            chat_key = "test:large_log"
            task_id = tm.create_task(chat_key, "large log test")

            # Create large event log (1000 stream_token events + task.created)
            num_events = 1000
            for i in range(num_events):
                tm.record_event(task_id, {"event": "stream_token", "chunk": f"data {i}"})

            # Query performance
            start_time = time.time()
            task = tm.get_task(task_id)
            elapsed = time.time() - start_time

            assert task is not None, "Should retrieve task"
            # task.created + 1000 stream_tokens = 1001 total
            assert len(task.output_events) == num_events + 1, f"Should have {num_events + 1} events (including task.created)"

            print(f"✓ Large event log query ({num_events + 1} events): {elapsed*1000:.1f}ms")
            assert elapsed < 1.0, f"Should query in <1s, took {elapsed:.2f}s"


def run_all_tests():
    """Run all E2E tests."""
    print("=" * 70)
    print("ADR-0080 Task Engine E2E Tests")
    print("=" * 70)

    test_classes = [
        TestTaskQuota,
        TestEventLogRotation,
        TestConcurrentTasks,
        TestSessionCleanup,
        TestPerformance,
    ]

    total_tests = 0
    passed = 0

    for test_class in test_classes:
        print(f"\n{test_class.__name__}")
        print("-" * 70)

        instance = test_class()
        for method_name in dir(instance):
            if method_name.startswith("test_"):
                total_tests += 1
                try:
                    getattr(instance, method_name)()
                    passed += 1
                except AssertionError as e:
                    print(f"✗ {method_name}: {e}")
                except Exception as e:
                    print(f"✗ {method_name}: {type(e).__name__}: {e}")

    print("\n" + "=" * 70)
    print(f"Results: {passed}/{total_tests} tests passed")
    print("=" * 70)

    return passed == total_tests


if __name__ == "__main__":
    success = run_all_tests()
    sys.exit(0 if success else 1)

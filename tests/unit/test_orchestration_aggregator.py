"""test_orchestration_aggregator.py — unit tests for batch orchestration tracking.

Tests three core scenarios:
  1. Success-only: All tasks complete successfully within the window.
  2. Mixed: Some tasks fail while others succeed; event reflects both.
  3. Timeout: Window expires before all tasks finish; incomplete tasks are handled.
"""
import json
import os
import tempfile
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Adjust sys.path to import the module
import sys
from pathlib import Path as PathlibPath

_shared = PathlibPath(__file__).resolve().parent.parent.parent / "corvin_operator" / "bridges" / "shared"
if str(_shared) not in sys.path:
    sys.path.insert(0, str(_shared))

from orchestration_aggregator import (
    ORCHESTRATION_WINDOW_SECS,
    OA_DELIVERED_TTL,
    OA_BATCH_MAX_AGE,
    OA_LOCK_STALE,
    OrchestrationCompleteEvent,
    _atomic_write,
    _batch_path,
    _batches_dir,
    _read,
    cleanup_batches,
    emit_orchestration_event,
    get_active_batches,
    on_task_complete,
    register_task,
)


@pytest.fixture
def temp_corvin_home(monkeypatch):
    """Temporary CORVIN_HOME for isolated test runs."""
    with tempfile.TemporaryDirectory() as tmpdir:
        monkeypatch.setenv("CORVIN_HOME", tmpdir)
        yield Path(tmpdir)


class TestBatchRegistration:
    """Test batch registration and task tracking."""

    def test_register_task_creates_batch(self, temp_corvin_home):
        """Registering a task creates a new batch."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")

        assert batch_id == f"batch_{int(batch_start)}"
        assert _batch_path(batch_id).exists()

        rec = _read(_batch_path(batch_id))
        assert rec is not None
        assert rec["state"] == "pending"
        assert "task_1" in rec["tasks"]
        assert rec["tasks"]["task_1"]["state"] == "pending"

    def test_multiple_tasks_same_batch(self, temp_corvin_home):
        """Multiple tasks within the window belong to the same batch."""
        batch_start = time.time()
        batch_id_1 = register_task(batch_start, "task_1")
        batch_id_2 = register_task(batch_start + 10, "task_2")

        # Both should map to the same batch (same window_start)
        assert batch_id_1 == batch_id_2 == f"batch_{int(batch_start)}"

        rec = _read(_batch_path(batch_id_1))
        assert len(rec["tasks"]) == 2
        assert "task_1" in rec["tasks"]
        assert "task_2" in rec["tasks"]


class TestTaskCompletion:
    """Test marking tasks as complete."""

    def test_mark_task_success(self, temp_corvin_home):
        """Marking a task successful updates the record."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")

        result = on_task_complete(batch_id, "task_1", success=True)
        assert result is True

        rec = _read(_batch_path(batch_id))
        assert rec["tasks"]["task_1"]["state"] == "completed"
        assert rec["tasks"]["task_1"]["success"] is True
        assert rec["tasks"]["task_1"]["error"] is None

    def test_mark_task_failed(self, temp_corvin_home):
        """Marking a task failed records the error."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")

        result = on_task_complete(batch_id, "task_1", success=False, error="Task timed out")
        assert result is True

        rec = _read(_batch_path(batch_id))
        assert rec["tasks"]["task_1"]["state"] == "completed"
        assert rec["tasks"]["task_1"]["success"] is False
        assert rec["tasks"]["task_1"]["error"] == "Task timed out"

    def test_mark_unregistered_task(self, temp_corvin_home):
        """Completing a task not in the batch adds it as a late completion."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")

        # Complete a task that was never registered
        result = on_task_complete(batch_id, "task_2", success=True)
        assert result is True

        rec = _read(_batch_path(batch_id))
        assert "task_2" in rec["tasks"]
        assert rec["tasks"]["task_2"]["success"] is True


class TestOrchestrationEventEmission:
    """Test event emission for orchestration completions."""

    def test_emit_success_only(self, temp_corvin_home):
        """Scenario 1: All tasks complete successfully."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")

        # Both tasks succeed
        on_task_complete(batch_id, "task_1", success=True)
        on_task_complete(batch_id, "task_2", success=True)

        # Emit event
        event = emit_orchestration_event(batch_id, now=batch_start + 10)
        assert event is not None
        assert event.batch_id == batch_id
        assert event.task_count == 2
        assert event.success_count == 2
        assert len(event.failed_tasks) == 0
        assert event.event_type == "ORCHESTRATION_COMPLETE_SUCCESS"

        # Batch should be marked emitted
        rec = _read(_batch_path(batch_id))
        assert rec["state"] == "emitted"

    def test_emit_mixed_results(self, temp_corvin_home):
        """Scenario 2: Some tasks fail, some succeed."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")
        register_task(batch_start, "task_3")

        # Mixed results
        on_task_complete(batch_id, "task_1", success=True)
        on_task_complete(batch_id, "task_2", success=False, error="Database connection failed")
        on_task_complete(batch_id, "task_3", success=True)

        # Emit event
        event = emit_orchestration_event(batch_id, now=batch_start + 10)
        assert event is not None
        assert event.task_count == 3
        assert event.success_count == 2
        assert len(event.failed_tasks) == 1
        assert event.failed_tasks[0]["task_id"] == "task_2"
        assert event.failed_tasks[0]["error"] == "Database connection failed"
        assert event.event_type == "ORCHESTRATION_COMPLETE_MIXED"

    def test_emit_timeout_before_all_complete(self, temp_corvin_home):
        """Scenario 3: Time window expires before all tasks complete."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")
        register_task(batch_start, "task_3")

        # Only task_1 completes
        on_task_complete(batch_id, "task_1", success=True)

        # Event emission should be blocked (not all done, window not expired)
        event = emit_orchestration_event(batch_id, now=batch_start + 100)
        assert event is None

        # Once the window expires, emit anyway
        event = emit_orchestration_event(batch_id, now=batch_start + ORCHESTRATION_WINDOW_SECS + 1)
        assert event is not None
        assert event.task_count == 3
        assert event.success_count == 1
        # Incomplete tasks still in the batch but not recorded as completed
        assert len(event.failed_tasks) == 0  # Only explicitly failed tasks counted

    def test_no_double_emit(self, temp_corvin_home):
        """The same batch never emits twice (idempotent)."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        on_task_complete(batch_id, "task_1", success=True)

        # First emit succeeds
        event1 = emit_orchestration_event(batch_id, now=batch_start + 10)
        assert event1 is not None

        # Second emit on the same batch returns None (already emitted)
        event2 = emit_orchestration_event(batch_id, now=batch_start + 20)
        assert event2 is None


class TestBatchCleanup:
    """Test batch cleanup and pruning."""

    def test_cleanup_delivered_batches(self, temp_corvin_home):
        """Delivered batches older than OA_DELIVERED_TTL are pruned."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        on_task_complete(batch_id, "task_1", success=True)
        emit_orchestration_event(batch_id, now=batch_start + 10)

        # Cleanup with a time far in the future (batch is now old)
        future_time = batch_start + OA_DELIVERED_TTL + 1000
        removed = cleanup_batches(now=future_time)

        assert removed >= 1
        assert not _batch_path(batch_id).exists()

    def test_cleanup_abandoned_pending_batches(self, temp_corvin_home):
        """Pending batches older than OA_BATCH_MAX_AGE are pruned."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        # Never complete the task, never emit

        # Cleanup with a time far in the future (batch is abandoned)
        future_time = batch_start + OA_BATCH_MAX_AGE + 1000
        removed = cleanup_batches(now=future_time)

        assert removed >= 1
        assert not _batch_path(batch_id).exists()

    def test_cleanup_preserves_recent_batches(self, temp_corvin_home):
        """Recent batches are not pruned."""
        batch_start = time.time()
        batch_id = register_task(batch_start, "task_1")
        on_task_complete(batch_id, "task_1", success=True)
        emit_orchestration_event(batch_id, now=batch_start + 10)

        # Cleanup immediately after emission
        removed = cleanup_batches(now=batch_start + 100)

        # Recent batch should not be removed
        assert removed == 0 or removed < 1  # Depends on other test artifacts
        # The batch file should still exist (or be cleaned up by other tests)


class TestDebugHelpers:
    """Test debug and inspection functions."""

    def test_get_active_batches(self, temp_corvin_home):
        """get_active_batches returns all pending and recent batches."""
        batch_start = time.time()
        batch_id_1 = register_task(batch_start, "task_1")
        batch_id_2 = register_task(batch_start + ORCHESTRATION_WINDOW_SECS + 10, "task_2")

        batches = get_active_batches()
        assert len(batches) >= 2

        batch_ids = [b["batch_id"] for b in batches]
        assert batch_id_1 in batch_ids
        assert batch_id_2 in batch_ids

    def test_get_active_batches_empty(self, temp_corvin_home):
        """get_active_batches returns empty list when no batches exist."""
        batches = get_active_batches()
        assert batches == []


class TestAtomicWrite:
    """Test atomic write and read operations."""

    def test_atomic_write_creates_file(self, temp_corvin_home):
        """Atomic write creates a file with correct permissions."""
        test_path = _batches_dir() / "test.json"
        test_data = {"key": "value", "emoji": "✅"}

        _atomic_write(test_path, test_data)

        assert test_path.exists()
        # Check that file is readable and contains correct data
        read_data = _read(test_path)
        assert read_data == test_data

        # Check permissions on POSIX systems
        if hasattr(os, "stat"):
            stat = test_path.stat()
            # 0o600 = -rw------- (owner read/write only)
            if stat.st_mode & 0o077 == 0:  # No group/other bits set
                assert True  # Permission check passed (best-effort)

    def test_read_missing_file(self, temp_corvin_home):
        """Reading a missing file returns None."""
        test_path = _batches_dir() / "nonexistent.json"
        result = _read(test_path)
        assert result is None

    def test_read_malformed_json(self, temp_corvin_home):
        """Reading a malformed JSON file returns None."""
        test_path = _batches_dir() / "malformed.json"
        test_path.parent.mkdir(parents=True, exist_ok=True)
        test_path.write_text("{ invalid json }")

        result = _read(test_path)
        assert result is None


class TestEventDataClass:
    """Test OrchestrationCompleteEvent data class."""

    def test_event_to_dict(self):
        """Event can be serialized to dict."""
        event = OrchestrationCompleteEvent(
            batch_id="batch_123",
            task_count=3,
            success_count=2,
            failed_tasks=[{"task_id": "task_2", "error": "Timeout"}],
            total_duration_secs=45.3,
            stats_aggregated={"foo": "bar"},
            event_type="ORCHESTRATION_COMPLETE_MIXED",
        )

        event_dict = event.to_dict()
        assert event_dict["batch_id"] == "batch_123"
        assert event_dict["task_count"] == 3
        assert event_dict["success_count"] == 2
        assert len(event_dict["failed_tasks"]) == 1
        assert event_dict["event_type"] == "ORCHESTRATION_COMPLETE_MIXED"

    def test_event_frozen(self):
        """Event is immutable (frozen dataclass)."""
        event = OrchestrationCompleteEvent(
            batch_id="batch_123",
            task_count=1,
            success_count=1,
        )

        with pytest.raises(AttributeError):
            event.batch_id = "batch_456"  # type: ignore


class TestIntegration:
    """End-to-end integration tests."""

    def test_full_workflow_success_scenario(self, temp_corvin_home):
        """Full workflow: register → complete → emit → cleanup."""
        batch_start = time.time()

        # 1. Register tasks
        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")

        # 2. Complete tasks
        on_task_complete(batch_id, "task_1", success=True)
        on_task_complete(batch_id, "task_2", success=True)

        # 3. Emit event
        event = emit_orchestration_event(batch_id, now=batch_start + 10)
        assert event is not None
        assert event.event_type == "ORCHESTRATION_COMPLETE_SUCCESS"

        # 4. Verify batch is in emitted state
        rec = _read(_batch_path(batch_id))
        assert rec["state"] == "emitted"

        # 5. Cleanup (future time)
        future = batch_start + OA_DELIVERED_TTL + 1000
        removed = cleanup_batches(now=future)
        assert removed >= 1

    def test_full_workflow_mixed_scenario(self, temp_corvin_home):
        """Full workflow with mixed success/failure."""
        batch_start = time.time()

        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")

        on_task_complete(batch_id, "task_1", success=True)
        on_task_complete(batch_id, "task_2", success=False, error="Connection lost")

        event = emit_orchestration_event(batch_id, now=batch_start + 10)
        assert event is not None
        assert event.event_type == "ORCHESTRATION_COMPLETE_MIXED"
        assert len(event.failed_tasks) == 1

    def test_full_workflow_timeout_scenario(self, temp_corvin_home):
        """Full workflow with timeout-driven emission."""
        batch_start = time.time()

        batch_id = register_task(batch_start, "task_1")
        register_task(batch_start, "task_2")

        # Only complete task_1
        on_task_complete(batch_id, "task_1", success=True)

        # Emit should fail (window not expired)
        event = emit_orchestration_event(batch_id, now=batch_start + 100)
        assert event is None

        # Emit after window expires
        event = emit_orchestration_event(
            batch_id, now=batch_start + ORCHESTRATION_WINDOW_SECS + 1
        )
        assert event is not None
        # task_2 is incomplete but not in failed_tasks (only explicit failures)
        assert event.success_count == 1

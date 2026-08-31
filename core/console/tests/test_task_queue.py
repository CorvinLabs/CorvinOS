"""Tests for task_queue.py (M2 persistent queue, append-only log)."""
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(_REPO / "core" / "console"))


import json
import tempfile
import time
from pathlib import Path

import pytest

from corvin_console.task_queue import TaskQueue, TaskQueueEntry, TaskStatus


@pytest.fixture
def tmp_tenant_dir():
    """Temporary tenant directory with global subdir."""
    with tempfile.TemporaryDirectory() as td:
        tenant_dir = Path(td) / "tenants" / "_default" / "global"
        tenant_dir.mkdir(parents=True)
        yield tenant_dir


class TestTaskQueueBasics:
    """Basic enqueue/dequeue/status operations."""

    def test_enqueue_creates_entry(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        task_id = q.enqueue(
            tenant_id="_default",
            chat_key="web:session_1",
            instruction="test task",
            ttl_seconds=3600,
        )
        assert task_id
        assert len(task_id) == 36  # UUID4 format

    def test_get_task_after_enqueue(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        task_id = q.enqueue(
            tenant_id="_default",
            chat_key="web:session_1",
            instruction="test",
            ttl_seconds=3600,
        )
        task = q.get_task(task_id)
        assert task is not None
        assert task.task_id == task_id
        assert task.chat_key == "web:session_1"
        assert task.status == TaskStatus.PENDING

    def test_dequeue_returns_oldest_pending(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        id1 = q.enqueue("_default", "web:s1", "task 1", 3600)
        time.sleep(0.01)
        id2 = q.enqueue("_default", "web:s2", "task 2", 3600)

        task = q.dequeue("_default")
        assert task is not None
        assert task.task_id == id1  # FIFO

    def test_dequeue_skips_expired(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        q.enqueue("_default", "web:s1", "expired", ttl_seconds=1)
        time.sleep(1.1)
        q.enqueue("_default", "web:s2", "fresh", ttl_seconds=3600)

        task = q.dequeue("_default")
        assert task is not None
        assert task.chat_key == "web:s2"

    def test_update_status_changes_state(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        task_id = q.enqueue("_default", "web:s1", "test", 3600)

        q.update_status(task_id, TaskStatus.RUNNING)
        task = q.get_task(task_id)
        assert task.status == TaskStatus.RUNNING

        q.update_status(task_id, TaskStatus.COMPLETED, exit_code=0)
        task = q.get_task(task_id)
        assert task.status == TaskStatus.COMPLETED
        assert task.exit_code == 0

    def test_list_tenant_tasks_filters_by_status(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        id1 = q.enqueue("_default", "web:s1", "task 1", 3600)
        id2 = q.enqueue("_default", "web:s2", "task 2", 3600)
        id3 = q.enqueue("_default", "web:s3", "task 3", 3600)

        q.update_status(id1, TaskStatus.RUNNING)
        q.update_status(id2, TaskStatus.COMPLETED, exit_code=0)

        running = q.list_tenant_tasks("_default", status=TaskStatus.RUNNING)
        assert len(running) == 1
        assert running[0].task_id == id1

        completed = q.list_tenant_tasks("_default", status=TaskStatus.COMPLETED)
        assert len(completed) == 1

        all_tasks = q.list_tenant_tasks("_default")
        assert len(all_tasks) == 3

    def test_dequeue_marks_running(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        task_id = q.enqueue("_default", "web:s1", "test", 3600)

        task = q.dequeue("_default")
        assert task is not None
        assert task.status == TaskStatus.RUNNING

        # Verify persisted
        task = q.get_task(task_id)
        assert task.status == TaskStatus.RUNNING


class TestTaskQueueDurability:
    """Append-only log durability and replay."""

    def test_tasks_persist_across_instances(self, tmp_tenant_dir):
        q1 = TaskQueue(tmp_tenant_dir)
        task_id = q1.enqueue("_default", "web:s1", "test", 3600)
        q1.update_status(task_id, TaskStatus.RUNNING)

        # New instance reads from same log
        q2 = TaskQueue(tmp_tenant_dir)
        task = q2.get_task(task_id)
        assert task is not None
        assert task.status == TaskStatus.RUNNING

    def test_queue_log_is_append_only(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        q.enqueue("_default", "web:s1", "task 1", 3600)
        q.enqueue("_default", "web:s2", "task 2", 3600)

        # Read raw log file (named tasks_<tenant_id>.jsonl)
        log_path = tmp_tenant_dir / "tasks__default.jsonl"
        assert log_path.exists()

        lines = log_path.read_text().strip().split("\n")
        assert len(lines) >= 2  # At least 2 enqueue events

        # Verify each line is valid JSON
        for line in lines:
            data = json.loads(line)
            assert "event" in data
            assert data["event"] in ["task.enqueued", "task.status_changed"]


class TestTaskQueueTTL:
    """TTL expiry handling."""

    def test_task_marked_expired_on_dequeue(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        q.enqueue("_default", "web:s1", "short-ttl", ttl_seconds=1)
        q.enqueue("_default", "web:s2", "long-ttl", ttl_seconds=3600)

        time.sleep(1.1)

        task = q.dequeue("_default")
        assert task.chat_key == "web:s2"  # First task skipped (expired)

    def test_expired_tasks_marked_failed(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        task_id = q.enqueue("_default", "web:s1", "expired", ttl_seconds=1)

        time.sleep(1.1)

        q.cleanup_expired_tasks("_default")

        task = q.get_task(task_id)
        assert task.status == TaskStatus.FAILED
        assert task.exit_code == 124  # TIMEOUT


class TestTaskQueueMultiTenant:
    """Multi-tenant isolation."""

    def test_tasks_isolated_by_tenant(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        q.enqueue("acme", "discord:123", "acme task", 3600)
        q.enqueue("beta", "discord:456", "beta task", 3600)

        acme_tasks = q.list_tenant_tasks("acme")
        assert len(acme_tasks) == 1
        assert acme_tasks[0].chat_key == "discord:123"

        beta_tasks = q.list_tenant_tasks("beta")
        assert len(beta_tasks) == 1
        assert beta_tasks[0].chat_key == "discord:456"

    def test_dequeue_tenant_scoped(self, tmp_tenant_dir):
        q = TaskQueue(tmp_tenant_dir)
        q.enqueue("acme", "discord:123", "acme task", 3600)
        q.enqueue("beta", "discord:456", "beta task", 3600)

        acme_task = q.dequeue("acme")
        assert acme_task.chat_key == "discord:123"

        beta_task = q.dequeue("beta")
        assert beta_task.chat_key == "discord:456"

        assert q.dequeue("acme") is None
        assert q.dequeue("beta") is None

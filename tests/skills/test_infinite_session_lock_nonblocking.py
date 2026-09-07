"""R3-B5: the snapshot lock must never hang the task-completion path.

``core.infinite_session.event_store.EventStore._task_lock`` took a plain
``fcntl.flock(LOCK_EX)`` with NO timeout, twice per producer call
(``get_latest_snapshot`` then ``write_snapshot``). The producer is
``core.console.corvin_console.task_worker_pool._snapshot_task_turn``, which runs
AFTER ``update_status`` but BEFORE ``_notify_task_done`` — so a wedged lock
holder (a crashed writer whose fd the kernel had not reaped, an NFS mount, a
stopped process) stranded a finished task with no notification. The
``try/except`` around the producer cannot catch a hang.

ADR-0645 §6 ("a snapshot failure never fails the task") is therefore an
availability invariant, and it has to hold BY CONSTRUCTION: the lock is
``LOCK_EX | LOCK_NB`` with a bounded deadline, and every caller turns the
deadline into a returned reason.

The lock is held from a SECOND file description (a separate ``open()``), which
is exactly what a foreign process holding it looks like to ``flock``.
"""

from __future__ import annotations

import fcntl
import logging
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.infinite_session import EventStore, snapshot_task_state
from core.infinite_session import event_store as event_store_mod

TENANT = "_default"
TASK = "task_lock_probe"


def _audit_sink():
    sink: list = []

    def emit(event_type, *, tenant_id, details):
        sink.append((event_type, tenant_id, dict(details)))
        return f"ref-{len(sink)}"

    emit.sink = sink  # type: ignore[attr-defined]
    return emit


@pytest.fixture
def home(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("CORVIN_TENANT_ID", TENANT)
    return tmp_path


@pytest.fixture
def short_deadline(monkeypatch):
    monkeypatch.setattr(event_store_mod, "LOCK_TIMEOUT_SECONDS", 0.2)
    return 0.2


class _HeldLock:
    """Hold the per-task snapshot lock from an independent file description."""

    def __init__(self, task_dir: Path):
        task_dir.mkdir(parents=True, exist_ok=True)
        self.path = task_dir / ".lock"

    def __enter__(self):
        self.fh = open(self.path, "a+")
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        fcntl.flock(self.fh.fileno(), fcntl.LOCK_UN)
        self.fh.close()


class TestProducerNeverBlocks:
    def test_write_returns_a_reason_instead_of_hanging(self, home, short_deadline):
        store = EventStore(TENANT, audit=_audit_sink())
        task_dir = store.root_dir / TASK

        with _HeldLock(task_dir):
            started = time.monotonic()
            snapshot, error = snapshot_task_state(
                TENANT, TASK, {"status": "completed", "exit_code": 0}, store=store
            )
            elapsed = time.monotonic() - started

        assert snapshot is None
        assert "lock busy" in error, error
        assert elapsed < 5.0, f"producer waited {elapsed:.1f}s — still blocking"

    def test_reads_degrade_instead_of_hanging(self, home, short_deadline):
        store = EventStore(TENANT, audit=_audit_sink())
        task_dir = store.root_dir / TASK

        with _HeldLock(task_dir):
            started = time.monotonic()
            index, error = store.list_snapshots(TENANT, TASK)
            latest, latest_error = store.get_latest_snapshot(TENANT, TASK)
            elapsed = time.monotonic() - started

        assert index == [] and "lock busy" in error, error
        assert latest is None and "lock busy" in latest_error, latest_error
        assert elapsed < 5.0, f"reads waited {elapsed:.1f}s — still blocking"

    def test_archival_returns_a_reason_instead_of_hanging(self, home, short_deadline):
        store = EventStore(TENANT, audit=_audit_sink())
        task_dir = store.root_dir / TASK

        with _HeldLock(task_dir):
            started = time.monotonic()
            count, error = store.delete_snapshots_before(TENANT, TASK, "2099-01-01T00:00:00Z")
            elapsed = time.monotonic() - started

        assert count == 0 and "lock busy" in error, error
        assert elapsed < 5.0

    def test_the_lock_still_serialises_when_it_is_free(self, home):
        """The bounded lock must remain a real mutex, not a no-op."""
        store = EventStore(TENANT, audit=_audit_sink())
        first, err1 = snapshot_task_state(TENANT, TASK, {"n": 1}, store=store)
        second, err2 = snapshot_task_state(TENANT, TASK, {"n": 2}, store=store)
        assert first is not None and second is not None, (err1, err2)
        index, error = store.list_snapshots(TENANT, TASK)
        assert error == ""
        assert [m.seq for m in index] == [1, 2]
        assert second.prev_snapshot_hash == first.content_hash
        ok, chain_error = store.verify_snapshot_chain(TENANT, TASK)
        assert ok, chain_error


class TestCompletionPathProducer:
    def test_task_worker_pool_producer_returns_under_a_wedged_lock(
        self, home, short_deadline, caplog
    ):
        """The REAL call site (``_snapshot_task_turn``) must return, so the
        completion notification that follows it still happens."""
        from core.console.corvin_console.task_worker_pool import _snapshot_task_turn

        store = EventStore(TENANT, audit=_audit_sink())
        task = SimpleNamespace(tenant_id=TENANT, task_id=TASK, chat_key="chat_abc123")

        with _HeldLock(store.root_dir / TASK):
            with caplog.at_level(logging.ERROR):
                started = time.monotonic()
                _snapshot_task_turn(
                    task, status="completed", exit_code=0, duration_ms=12,
                    event_count=3, result_text="done",
                )
                elapsed = time.monotonic() - started

        assert elapsed < 5.0, f"completion path blocked for {elapsed:.1f}s"
        messages = [r.getMessage() for r in caplog.records]
        assert any("snapshot NOT written" in m and "lock busy" in m for m in messages), messages

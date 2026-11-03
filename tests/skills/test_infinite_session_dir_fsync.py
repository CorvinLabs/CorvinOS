"""Snapshot writes must work on Windows.

``_write_atomic`` fsynced the parent directory via ``os.open(dir, O_RDONLY)``,
which Windows refuses with ``[Errno 13] Permission denied: ...\snapshots\<task_id>``.
The snapshot file was already replaced into place but the index never written, so
every task turn logged "infinite-session snapshot NOT written" and the chain never
advanced. POSIX keeps the directory fsync.
"""
from __future__ import annotations

import os

from core.infinite_session import EventStore, snapshot_task_state
from core.infinite_session import event_store as event_store_mod

TENANT = "_default"


def _emit(event_type, *, tenant_id, details):
    return "ref-1"


def test_snapshot_chain_advances(tmp_path, monkeypatch):
    monkeypatch.setenv("CORVIN_HOME", str(tmp_path))
    monkeypatch.setenv("VOICE_AUDIT_PATH", str(tmp_path / "audit.jsonl"))
    store = EventStore(TENANT, corvin_home=tmp_path, audit=_emit)
    first, err = snapshot_task_state(TENANT, "task_win", {"status": "done"}, store=store)
    assert first is not None, err
    second, err = snapshot_task_state(TENANT, "task_win", {"status": "done"}, store=store)
    assert second is not None, err
    assert second.prev_snapshot_hash == first.content_hash


def test_dir_fsync_skipped_only_on_windows(tmp_path, monkeypatch):
    opened = []
    real_open = os.open
    monkeypatch.setattr(event_store_mod.os, "open",
                        lambda p, *a, **k: opened.append(p) or real_open(p, *a, **k))
    event_store_mod._write_atomic(tmp_path / "x.json", {"a": 1})
    assert (tmp_path / "x.json").exists()
    assert opened == ([] if os.name == "nt" else [str(tmp_path)])

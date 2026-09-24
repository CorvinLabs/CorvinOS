"""Per-tenant SQLite store for the Task-Tracking SSOT (ADR-2056 §1).

``<tenant_home>/global/task_tracking/tasks.db`` — WAL, file ``0o600``, directory
``0o700``. The path is resolved through ``tenant_home()`` only (which validates
the tenant id); nothing here composes a tenant path by hand. Columns follow
ADR-2051's ``tasks`` table where they overlap, so a Postgres backend stays a
drop-in behind this module.

Every statement also filters on ``tenant_id`` — the file is already per tenant;
the column is the second wall, not the first.
"""
from __future__ import annotations

import os
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

SCHEMA_VERSION = 1

_SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    id                  TEXT PRIMARY KEY,
    tenant_id           TEXT NOT NULL,
    kind                TEXT NOT NULL,
    parent_id           TEXT,
    title               TEXT NOT NULL,
    description         TEXT,
    status              TEXT NOT NULL DEFAULT 'open',
    status_reason       TEXT,
    status_changed_at   TEXT,
    priority            TEXT NOT NULL DEFAULT 'medium',
    owner               TEXT,
    assignee            TEXT,
    start_at            TEXT,
    deadline            TEXT,
    progress            INTEGER,
    work_estimate       REAL,
    work_actual         REAL,
    target_milestone    TEXT,
    category            TEXT,
    labels              TEXT NOT NULL DEFAULT '[]',
    approval_state      TEXT NOT NULL DEFAULT 'none',
    external_ref        TEXT,
    sort_key            INTEGER NOT NULL DEFAULT 0,
    created_at          TEXT NOT NULL,
    created_by          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    completed_at        TEXT,
    deleted_at          TEXT,
    deleted_by          TEXT,
    cascading_delete_id TEXT,
    restored_at         TEXT,
    version             INTEGER NOT NULL DEFAULT 1,
    UNIQUE (tenant_id, external_ref),
    CHECK (parent_id IS NULL OR parent_id != id)
);
CREATE INDEX IF NOT EXISTS idx_items_parent ON items(tenant_id, parent_id);
CREATE INDEX IF NOT EXISTS idx_items_status ON items(tenant_id, status);

CREATE TABLE IF NOT EXISTS dependencies (
    tenant_id     TEXT NOT NULL,
    item_id       TEXT NOT NULL,
    depends_on_id TEXT NOT NULL,
    dep_type      TEXT NOT NULL,
    created_at    TEXT NOT NULL,
    PRIMARY KEY (tenant_id, item_id, depends_on_id),
    CHECK (item_id != depends_on_id)
);

CREATE TABLE IF NOT EXISTS runs (
    tenant_id TEXT NOT NULL,
    item_id   TEXT NOT NULL,
    run_type  TEXT NOT NULL,
    run_ref   TEXT NOT NULL,
    linked_at TEXT NOT NULL,
    linked_by TEXT NOT NULL,
    PRIMARY KEY (tenant_id, item_id, run_type, run_ref)
);

-- Reference copy of the core-chain records (history view). NOT the source of
-- truth: the chain is. chain_hash ties each row to its chain record.
CREATE TABLE IF NOT EXISTS events (
    event_id   TEXT PRIMARY KEY,
    tenant_id  TEXT NOT NULL,
    item_id    TEXT NOT NULL,
    event_type TEXT NOT NULL,
    ts         TEXT NOT NULL,
    actor      TEXT NOT NULL,
    delta      TEXT NOT NULL,
    chain_hash TEXT
);
CREATE INDEX IF NOT EXISTS idx_events_item ON events(tenant_id, item_id, ts);

CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""

_LOCKS: dict[str, threading.RLock] = {}
_LOCKS_GUARD = threading.Lock()
_INITIALISED: set[str] = set()


def db_path(tenant_id: str) -> Path:
    from core.paths import tenant_home  # validates the tenant id  # noqa: PLC0415

    return Path(tenant_home(tenant_id)) / "global" / "task_tracking" / "tasks.db"


def tenant_lock(tenant_id: str) -> threading.RLock:
    """One writer at a time per tenant: chain order == commit order."""
    with _LOCKS_GUARD:
        lock = _LOCKS.get(tenant_id)
        if lock is None:
            lock = _LOCKS[tenant_id] = threading.RLock()
        return lock


def _init(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    if not path.exists():
        # Create at 0o600 up front — never let sqlite create it at umask.
        fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
        os.close(fd)
    conn = sqlite3.connect(path)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.executescript(_SCHEMA)
        conn.execute("INSERT OR IGNORE INTO meta(key, value) VALUES ('schema_version', ?)",
                     (str(SCHEMA_VERSION),))
        conn.commit()
    finally:
        conn.close()
    for suffix in ("-wal", "-shm"):
        side = path.with_name(path.name + suffix)
        if side.exists():
            try:
                os.chmod(side, 0o600)
            except OSError:
                pass


@contextmanager
def connect(tenant_id: str) -> Iterator[sqlite3.Connection]:
    path = db_path(tenant_id)
    key = str(path)
    if key not in _INITIALISED or not path.exists():
        with tenant_lock(tenant_id):
            _init(path)
            _INITIALISED.add(key)
    conn = sqlite3.connect(path, timeout=10, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=10000")
    try:
        yield conn
    finally:
        conn.close()


def exists(tenant_id: str) -> bool:
    try:
        return db_path(tenant_id).exists()
    except Exception:  # noqa: BLE001 — an invalid tenant has no store
        return False

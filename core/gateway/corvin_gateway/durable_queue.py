"""SQLite-backed durable run queue.

ADR-0007 Phase 7.1. The Phase 2.3 dispatcher is fire-and-forget on
``asyncio.create_task``; an accepted run that never reached a
terminal state because the gateway process crashed is structurally
unrecoverable in that model. The queue fixes it:

* ``enqueue(tenant_id, run_id)`` records every accepted run.
* ``next_pending(tenant_id)`` returns the oldest pending entry.
* ``mark_terminal(tenant_id, run_id)`` removes the entry once the
  dispatcher finishes (success / failure / budget-exceeded).
* ``recover_pending()`` lists every pending entry across tenants;
  the dispatcher calls this on startup and re-dispatches each.

Storage: SQLite with WAL journal mode at
``<corvin_home>/global/gateway/durable_queue.db`` — the
``_default`` tenant's tree is the natural home because the queue
spans every tenant. Mode ``0o600`` on the DB file.

Design constraints
------------------

* **Additive, not replacement.** The Phase 2.3 in-memory dispatch
  path stays unchanged. Every accepted run is BOTH enqueued AND
  immediately scheduled via ``asyncio.create_task``. The queue
  exists for the crash-recovery path; the in-memory dispatch is
  the fast path.
* **WAL mode** so concurrent reads (multiple workers in the same
  process) don't block writes. SQLite WAL is the right concurrency
  primitive for the Phase 2 traffic envelope; multi-process workers
  with row-level locking arrive when (and if) the envelope grows
  past what a single process can handle.
* **No cross-tenant leakage.** Every method takes a ``tenant_id``;
  pending entries for tenant A are invisible to a worker bound to
  tenant B. Recovery is the only cross-tenant view, and it returns
  ``(tenant_id, run_id)`` pairs.
"""
from __future__ import annotations

import os
import sqlite3
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
if str(_FORGE_PATH) not in sys.path:
    sys.path.insert(0, str(_FORGE_PATH))

from forge.tenants import validate_tenant_id  # noqa: E402
from forge import paths as _forge_paths  # noqa: E402


# ── Constants ───────────────────────────────────────────────────────


DB_FILENAME = "durable_queue.db"
_REQUIRED_MODE = 0o600


def _db_path() -> Path:
    """Single shared DB across tenants — Gateway-wide queue."""
    base = _forge_paths.tenant_global_dir("_default") / "gateway"
    base.mkdir(parents=True, exist_ok=True)
    return base / DB_FILENAME


# ── Schema bootstrap ────────────────────────────────────────────────


_SCHEMA = """
CREATE TABLE IF NOT EXISTS pending_runs (
    tenant_id   TEXT NOT NULL,
    run_id      TEXT NOT NULL,
    enqueued_at REAL NOT NULL,
    state       TEXT NOT NULL DEFAULT 'pending',
    PRIMARY KEY (tenant_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_pending_state_tenant
    ON pending_runs (state, tenant_id, enqueued_at);
"""


@contextmanager
def _connect() -> Iterator[sqlite3.Connection]:
    path = _db_path()
    fresh = not path.exists()
    conn = sqlite3.connect(str(path), isolation_level=None)
    try:
        if fresh:
            # Mode 0o600 — same pattern auth / runs / oidc use.
            os.chmod(path, _REQUIRED_MODE)
        # WAL for concurrent readers; busy_timeout so a parallel
        # writer never instantly fails.
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.executescript(_SCHEMA)
        yield conn
    finally:
        conn.close()


# ── Public API ──────────────────────────────────────────────────────


def enqueue(tenant_id: str, run_id: str, *, now: float | None = None) -> None:
    """Record an accepted run for crash-recovery.

    Idempotent on (tenant_id, run_id): inserting the same pair
    twice keeps the original enqueued_at.
    """
    validate_tenant_id(tenant_id)
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("run_id must be a non-empty string")
    ts = now if now is not None else time.time()
    with _connect() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO pending_runs "
            "(tenant_id, run_id, enqueued_at, state) "
            "VALUES (?, ?, ?, 'pending')",
            (tenant_id, run_id, ts),
        )


def mark_terminal(tenant_id: str, run_id: str) -> bool:
    """Remove the entry once the dispatcher reaches a terminal state.

    Returns True iff a row was actually removed; False on miss
    (e.g. the recovery sweep already drained it).
    """
    validate_tenant_id(tenant_id)
    with _connect() as conn:
        cur = conn.execute(
            "DELETE FROM pending_runs "
            "WHERE tenant_id = ? AND run_id = ?",
            (tenant_id, run_id),
        )
        return cur.rowcount > 0


def next_pending(tenant_id: str | None = None) -> tuple[str, str] | None:
    """Return the oldest pending ``(tenant_id, run_id)`` or None.

    When ``tenant_id`` is given, only that tenant's queue is read.
    """
    with _connect() as conn:
        if tenant_id is None:
            row = conn.execute(
                "SELECT tenant_id, run_id FROM pending_runs "
                "WHERE state = 'pending' "
                "ORDER BY enqueued_at ASC LIMIT 1"
            ).fetchone()
        else:
            validate_tenant_id(tenant_id)
            row = conn.execute(
                "SELECT tenant_id, run_id FROM pending_runs "
                "WHERE state = 'pending' AND tenant_id = ? "
                "ORDER BY enqueued_at ASC LIMIT 1",
                (tenant_id,),
            ).fetchone()
        if row is None:
            return None
        return (row[0], row[1])


def recover_pending() -> list[tuple[str, str]]:
    """Return every pending entry across tenants.

    Called by ``RunDispatcher.__init__`` after the gateway boots —
    every entry here is a run the previous gateway process
    accepted but never finished. The dispatcher re-creates the
    asyncio task for each.
    """
    with _connect() as conn:
        rows = conn.execute(
            "SELECT tenant_id, run_id FROM pending_runs "
            "WHERE state = 'pending' "
            "ORDER BY enqueued_at ASC"
        ).fetchall()
    out: list[tuple[str, str]] = []
    for tid, rid in rows:
        try:
            validate_tenant_id(tid)
        except Exception:
            # Defence: never trust on-disk strings; skip bogus rows.
            continue
        out.append((tid, rid))
    return out


def pending_count(tenant_id: str | None = None) -> int:
    """Operator-facing observable. Used by the metrics layer
    (`corvin_gateway_queue_depth`)."""
    with _connect() as conn:
        if tenant_id is None:
            cur = conn.execute(
                "SELECT COUNT(*) FROM pending_runs WHERE state = 'pending'"
            )
        else:
            validate_tenant_id(tenant_id)
            cur = conn.execute(
                "SELECT COUNT(*) FROM pending_runs "
                "WHERE state = 'pending' AND tenant_id = ?",
                (tenant_id,),
            )
        return cur.fetchone()[0]

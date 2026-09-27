"""Durable SQLite event queue for plugin lifecycle events — ADR-0682.

NOT WIRED: no production caller as of 2026-09-27 (adversarial review). The
only producers are ``core.plugins.corvin_plugins.lifecycle`` /
``core.audit.plugin_audit_integration`` and the only consumer is
``core.compliance.plugin_event_queue_tripwire`` — none of which is called from
a host, route, CLI or plugin registry path.

What this queue is (and is not):

* A per-TENANT staging buffer. Its default location is
  ``<corvin_home>/tenants/<tid>/global/plugin_events.db`` resolved at call time
  (``CORVIN_HOME`` honoured), and it refuses an event tagged with another
  tenant's id — one queue never mixes tenants (ADR-0007).
* NOT an audit trail. A queued event is not audited until :meth:`drain` has
  written it to THE tenant chain (``forge.paths.tenant_audit_chain``) through
  ``forge.security_events.write_event``. A row is marked emitted only after
  its chain record committed; a failed chain write leaves it pending and makes
  the drain raise (fail-closed), so nothing is ever reported as audited that
  is not in the chain.

Backpressure: ``max_pending`` bounds the pending rows. LOW rows are shed
(``enqueue`` returns ``False``) from 80 % of that bound so HIGH rows keep
headroom; a HIGH row is refused with :class:`QueueFullError` only when the
queue is actually full.
"""

from __future__ import annotations

import json
import logging
import re
import sqlite3
import threading
import time
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, List, Optional

logger = logging.getLogger(__name__)

#: Queue event type → canonical chain event type (registered in forge
#: ``EVENT_SEVERITY`` + ``_EVENT_ALLOWLIST``).
CHAIN_EVENT_TYPES: dict[str, str] = {
    "plugin_loaded": "plugin.lifecycle_loaded",
    "plugin_loaded_summary": "plugin.lifecycle_loaded",
    "plugin_executed": "plugin.lifecycle_executed",
    "plugin_error": "plugin.lifecycle_error",
    "plugin_disabled": "plugin.lifecycle_disabled",
}

#: Content-free fields copied from a queued event into its chain record.
CHAIN_FIELDS: frozenset[str] = frozenset({
    "plugin_id", "tenant_id", "version", "method_name", "input_hash",
    "output_hash", "error_type", "error_message_hash", "latency_ms", "reason",
    "load_count", "queued_at", "lom",
})

_PRIORITIES = ("HIGH", "LOW")


_TS_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})?")


def _parse_ts(value: str) -> Optional[datetime]:
    """Parse ``YYYY-MM-DDTHH:MM:SS[.ffffff][Z|±HH:MM]``; ``None`` if malformed.

    Strict on purpose: ``datetime.fromisoformat`` accepts shapes such as
    ``2026-09-27T00:000`` depending on suffix handling, and the dedup window
    compares these values."""
    if not isinstance(value, str) or not _TS_RE.fullmatch(value):
        return None
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


@dataclass(frozen=True)
class EventQueueStats:
    """Immutable queue statistics."""
    total_events: int
    high_priority_count: int
    low_priority_count: int
    pending_count: int
    emitted_count: int


class EventQueue:
    """Durable, tenant-scoped SQLite queue for plugin lifecycle events.

    ``UNIQUE(event_type, plugin_id, tenant_id, timestamp)`` makes a replayed
    identical event a no-op (``enqueue`` returns ``False``).
    """

    DEFAULT_MAX_PENDING = 10_000
    LOW_SHED_RATIO = 0.8
    LOAD_DEDUP_WINDOW_S = 60  # plugin_loaded rows within 60 s → one summary

    def __init__(
        self,
        db_path: Optional[Path] = None,
        *,
        tenant_id: str = "_default",
        max_pending: int = DEFAULT_MAX_PENDING,
    ):
        from forge import paths as forge_paths  # type: ignore[import-not-found]

        # validates the id (raises on a traversal / malformed tenant)
        self.tenant_id = forge_paths.tenant_home(tenant_id).name
        self.db_path = Path(db_path) if db_path is not None else self.default_path(self.tenant_id)
        if int(max_pending) < 1:
            raise ValueError("max_pending must be >= 1")
        self.max_pending = int(max_pending)
        self._lock = threading.Lock()
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @staticmethod
    def default_path(tenant_id: str = "_default") -> Path:
        """``<corvin_home>/tenants/<tid>/global/plugin_events.db`` (resolved now)."""
        from forge import paths as forge_paths  # type: ignore[import-not-found]

        return forge_paths.tenant_global_dir(tenant_id) / "plugin_events.db"

    def _connect(self, timeout: float = 5.0) -> sqlite3.Connection:
        return sqlite3.connect(self.db_path, timeout=timeout, isolation_level=None)

    def _init_schema(self) -> None:
        conn = self._connect()
        try:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("""
                CREATE TABLE IF NOT EXISTS plugin_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_type TEXT NOT NULL,
                    plugin_id TEXT NOT NULL,
                    tenant_id TEXT NOT NULL,
                    payload_json TEXT NOT NULL,
                    priority TEXT NOT NULL CHECK(priority IN ('HIGH', 'LOW')),
                    timestamp TEXT NOT NULL,
                    emitted_at_chain TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    UNIQUE(event_type, plugin_id, tenant_id, timestamp)
                )
            """)
            conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_priority_id
                ON plugin_events(priority, id)
                WHERE emitted_at_chain IS NULL
            """)
        finally:
            conn.close()

    # ── enqueue ────────────────────────────────────────────────────────────

    def enqueue(self, event: Any) -> bool:
        """Store one event. Returns ``True`` if stored, ``False`` if it was an
        identical duplicate or a LOW event shed under backpressure.

        Raises:
            ValueError: malformed event (missing field, bad priority/timestamp).
            EventQueueTenantMismatch: event tagged with another tenant.
            QueueFullError: HIGH event while the queue holds ``max_pending`` rows.
            EventQueueError: the database write failed (the event is NOT stored).
        """
        payload = asdict(event) if hasattr(event, "__dataclass_fields__") else dict(event)
        for key in ("event_type", "plugin_id", "tenant_id", "timestamp"):
            if not payload.get(key):
                raise ValueError(f"plugin event missing {key!r}")
        if payload["tenant_id"] != self.tenant_id:
            raise EventQueueTenantMismatch(
                f"event for tenant {payload['tenant_id']!r} refused by the queue of "
                f"tenant {self.tenant_id!r}"
            )
        if payload["event_type"] not in CHAIN_EVENT_TYPES:
            raise ValueError(f"unknown plugin event type {payload['event_type']!r}")
        priority = payload.get("priority") or "LOW"
        if priority not in _PRIORITIES:
            raise ValueError(f"invalid priority {priority!r}")
        if _parse_ts(payload["timestamp"]) is None:
            raise ValueError(f"invalid ISO-8601 timestamp {payload['timestamp']!r}")
        payload_json = json.dumps(payload, default=str, sort_keys=True)

        with self._lock:
            conn = self._connect()
            try:
                conn.execute("BEGIN IMMEDIATE")
                pending = conn.execute(
                    "SELECT COUNT(*) FROM plugin_events WHERE emitted_at_chain IS NULL"
                ).fetchone()[0]
                if priority == "HIGH" and pending >= self.max_pending:
                    conn.execute("ROLLBACK")
                    raise QueueFullError(
                        f"queue full ({pending}/{self.max_pending} pending); HIGH event refused"
                    )
                if priority == "LOW" and pending >= self.max_pending * self.LOW_SHED_RATIO:
                    conn.execute("ROLLBACK")
                    logger.warning(
                        "plugin event queue at %d/%d pending: LOW %s shed",
                        pending, self.max_pending, payload["event_type"],
                    )
                    return False
                try:
                    conn.execute(
                        """INSERT INTO plugin_events
                           (event_type, plugin_id, tenant_id, payload_json, priority, timestamp)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (payload["event_type"], payload["plugin_id"], payload["tenant_id"],
                         payload_json, priority, payload["timestamp"]),
                    )
                except sqlite3.IntegrityError:
                    conn.execute("ROLLBACK")
                    return False
                conn.execute("COMMIT")
                return True
            except (QueueFullError, ValueError):
                raise
            except sqlite3.Error as exc:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.Error:
                    pass
                raise EventQueueError(f"enqueue failed: {type(exc).__name__}") from exc
            finally:
                conn.close()

    # ── drain ──────────────────────────────────────────────────────────────

    def drain(self, batch_size: int = 50, timeout_sec: float = 5.0) -> List[dict]:
        """Write pending events to the tenant audit chain; return what was written.

        HIGH rows first, then LOW, each in insertion order. ``plugin_loaded``
        rows of one plugin within ``LOAD_DEDUP_WINDOW_S`` are written as ONE
        ``plugin_loaded_summary`` record carrying ``load_count``; all rows it
        covers are marked emitted together.

        A row is marked emitted only after its chain record committed. On a
        chain-write failure or when ``timeout_sec`` elapses, the rows written
        so far stay marked and :class:`DrainingError` is raised — the rest stay
        pending for the next drain.
        """
        from forge import paths as forge_paths  # type: ignore[import-not-found]
        from forge import security_events  # type: ignore[import-not-found]

        chain = forge_paths.tenant_audit_chain(self.tenant_id)
        deadline = time.monotonic() + float(timeout_sec)
        written: List[dict] = []

        with self._lock:
            try:
                conn = self._connect(timeout=max(float(timeout_sec), 0.1))
            except sqlite3.Error as exc:
                raise DrainingError(f"drain failed: {type(exc).__name__}") from exc
            try:
                conn.execute("BEGIN IMMEDIATE")
                rows = conn.execute(
                    """SELECT id, payload_json, event_type, plugin_id, timestamp
                       FROM plugin_events
                       WHERE emitted_at_chain IS NULL
                       ORDER BY CASE priority WHEN 'HIGH' THEN 0 ELSE 1 END, id
                       LIMIT ?""",
                    (int(batch_size),),
                ).fetchall()
                units = self._group(rows)
                try:
                    for row_ids, event in units:
                        if time.monotonic() > deadline:
                            raise DrainingError(
                                f"drain timeout after {len(written)} event(s); "
                                f"{len(units) - len(written)} left pending"
                            )
                        self._write_chain(security_events, chain, event)
                        now = datetime.utcnow().isoformat() + "Z"
                        conn.executemany(
                            "UPDATE plugin_events SET emitted_at_chain = ? WHERE id = ?",
                            [(now, rid) for rid in row_ids],
                        )
                        written.append(event)
                finally:
                    conn.execute("COMMIT")
                return written
            except DrainingError:
                raise
            except Exception as exc:  # noqa: BLE001 - every failure is a failed drain
                raise DrainingError(f"drain failed: {type(exc).__name__}: {exc}") from exc
            finally:
                conn.close()

    def _group(self, rows: list) -> List[tuple[list[int], dict]]:
        """Decode rows; fold plugin_loaded rows per plugin into summaries."""
        units: List[tuple[list[int], dict]] = []
        loads: dict[str, list[tuple[int, dict, datetime]]] = {}
        for row_id, payload_json, event_type, plugin_id, ts in rows:
            try:
                payload = json.loads(payload_json)
            except json.JSONDecodeError:
                # enqueue only stores json.dumps output; a corrupt row is a
                # tampered/damaged queue — never skip it silently.
                raise DrainingError(f"queue row {row_id} is not valid JSON")
            if event_type == "plugin_loaded":
                parsed = _parse_ts(ts)
                if parsed is None:
                    raise DrainingError(f"queue row {row_id} has an invalid timestamp")
                loads.setdefault(plugin_id, []).append((row_id, payload, parsed))
            else:
                units.append(([row_id], payload))
        for plugin_rows in loads.values():
            plugin_rows.sort(key=lambda r: r[2])
            batch = [plugin_rows[0]]
            for item in plugin_rows[1:]:
                if (item[2] - batch[0][2]).total_seconds() < self.LOAD_DEDUP_WINDOW_S:
                    batch.append(item)
                else:
                    units.append(self._summary(batch))
                    batch = [item]
            units.append(self._summary(batch))
        return units

    @staticmethod
    def _summary(batch: list) -> tuple[list[int], dict]:
        ids = [r[0] for r in batch]
        if len(batch) == 1:
            return ids, batch[0][1]
        summary = dict(batch[0][1])
        summary["event_type"] = "plugin_loaded_summary"
        summary["load_count"] = len(batch)
        return ids, summary

    def _write_chain(self, security_events: Any, chain: Path, event: dict) -> None:
        chain_type = CHAIN_EVENT_TYPES[event["event_type"]]
        details = {k: event[k] for k in CHAIN_FIELDS if event.get(k) is not None}
        details["queued_at"] = event.get("timestamp")
        details["tenant_id"] = self.tenant_id
        if event["event_type"] == "plugin_loaded":
            details.setdefault("load_count", 1)
        security_events.write_event(chain, chain_type, details=details)

    # ── inspection ─────────────────────────────────────────────────────────

    def mark_emitted(self, event_ids: List[int]) -> None:  # pragma: no cover - guard
        """Refused: rows are marked emitted only by :meth:`drain`, after their
        chain record committed. Marking them by hand would report as audited an
        event that is not in the chain."""
        raise NotImplementedError(
            "EventQueue.mark_emitted is not a public operation — use drain(), which "
            "marks a row only after its audit-chain record committed"
        )

    def batch_read(self, batch_size: int = 50, offset: int = 0) -> List[dict]:
        """Read stored events (inspection only; does not drain)."""
        conn = self._connect()
        try:
            cur = conn.execute(
                "SELECT payload_json FROM plugin_events ORDER BY id LIMIT ? OFFSET ?",
                (batch_size, offset),
            )
            return [json.loads(r[0]) for r in cur.fetchall()]
        finally:
            conn.close()

    def count_by_priority(self) -> dict[str, int]:
        """Pending events by priority: ``{'HIGH': n, 'LOW': m}``."""
        conn = self._connect()
        try:
            cur = conn.execute(
                """SELECT priority, COUNT(*) FROM plugin_events
                   WHERE emitted_at_chain IS NULL GROUP BY priority"""
            )
            return {p: c for p, c in cur.fetchall()}
        finally:
            conn.close()

    def stats(self) -> EventQueueStats:
        """Queue statistics. Raises :class:`EventQueueError` when unreadable —
        an unreadable queue must never read as an empty one."""
        try:
            conn = self._connect()
        except sqlite3.Error as exc:
            raise EventQueueError(f"stats failed: {type(exc).__name__}") from exc
        try:
            return self._get_stats_internal(conn)
        except sqlite3.Error as exc:
            raise EventQueueError(f"stats failed: {type(exc).__name__}") from exc
        finally:
            conn.close()

    def _get_stats_internal(self, conn: sqlite3.Connection) -> EventQueueStats:
        total = conn.execute("SELECT COUNT(*) FROM plugin_events").fetchone()[0]
        high = conn.execute(
            "SELECT COUNT(*) FROM plugin_events WHERE priority='HIGH'"
        ).fetchone()[0]
        pending = conn.execute(
            "SELECT COUNT(*) FROM plugin_events WHERE emitted_at_chain IS NULL"
        ).fetchone()[0]
        return EventQueueStats(total, high, total - high, pending, total - pending)


class EventQueueError(Exception):
    """The queue database could not be read or written."""


class EventQueueTenantMismatch(EventQueueError):
    """An event tagged with another tenant was offered to this tenant's queue."""


class QueueFullError(Exception):
    """HIGH event refused: the queue holds ``max_pending`` pending rows."""


class DrainingError(Exception):
    """A drain did not write every selected event to the audit chain."""

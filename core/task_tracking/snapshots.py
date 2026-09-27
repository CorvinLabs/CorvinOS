"""Task Rollback via Snapshots — Version History Tracking + Temporal Queries.

Implements hybrid snapshot strategy:
1. Immutable audit events (source of truth for all mutations)
2. Snapshot-index table tracks key versions (every N-th version for performance)
3. Rollback queries events WHERE ts <= snapshot_ts, reconstructs state

Compliance: ADR-0232 (Audit-First), GDPR Art. 30 (complete record), ADR-2051 (schema)

NOT WIRED: no production caller as of 2026-09-27 (adversarial review).
"""
from __future__ import annotations

import json
import logging
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

from . import store

log = logging.getLogger(__name__)

# Take snapshots every N versions (configurable)
SNAPSHOT_INTERVAL = 10


@dataclass
class RollbackResult:
    """Result of a rollback operation."""
    success: bool
    item_id: str
    from_version: int
    to_version: int
    reason: str
    snapshot_ts: Optional[str] = None
    events_replayed: int = 0


async def take_snapshot(
    tenant_id: str,
    item_id: str,
    version: int,
) -> Optional[str]:
    """
    Record a snapshot at this version (checkpoint for fast rollback).

    Args:
        tenant_id: tenant ID
        item_id: task ID
        version: current version number

    Returns:
        Timestamp of snapshot, or None if not taken (e.g., outside snapshot interval)
    """
    # Only snapshot every SNAPSHOT_INTERVAL versions
    if version % SNAPSHOT_INTERVAL != 0:
        return None

    with store.connect(tenant_id) as conn:
        item = conn.execute(
            "SELECT * FROM items WHERE tenant_id=? AND id=?",
            (tenant_id, item_id),
        ).fetchone()

        if item is None:
            log.warning(f"Cannot snapshot {item_id}: not found")
            return None

        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

        # Record in snapshot-index
        conn.execute(
            "INSERT INTO snapshot_index(tenant_id, item_id, version, snapshot_ts) "
            "VALUES (?, ?, ?, ?)",
            (tenant_id, item_id, version, ts),
        )
        conn.commit()
        log.info(f"Snapshot recorded: {item_id} v{version} at {ts}")
        return ts


async def get_snapshot_for_version(
    tenant_id: str,
    item_id: str,
    target_version: int,
) -> Optional[str]:
    """
    Get timestamp of nearest snapshot for this version.

    Returns timestamp for temporal query, or None if no snapshot exists.
    """
    with store.connect(tenant_id) as conn:
        # Find nearest snapshot at or before target version
        row = conn.execute(
            "SELECT snapshot_ts FROM snapshot_index "
            "WHERE tenant_id=? AND item_id=? AND version <= ? "
            "ORDER BY version DESC LIMIT 1",
            (tenant_id, item_id, target_version),
        ).fetchone()

        return row["snapshot_ts"] if row else None


#: Chain event for an executed rollback. Registered at import (below) so the
#: default-deny floor does not scrub it down to nothing.
ROLLBACK_EVENT_TYPE = "task_item.rollback_executed"
_ROLLBACK_ALLOWLIST = frozenset({
    "item_id", "from_version", "to_version", "version", "fields",
    "undone_events", "actor_kind", "sid_fingerprint", "tenant_id",
})

#: Events whose ``delta`` is ``{field: [old, new]}`` and that bumped the item's
#: version by exactly one — the only ones a rollback can invert.
_INVERTIBLE_EVENTS = ("task_item.updated", "task_item.rollback_executed")
#: Every event type that bumps the item's own version.
_VERSION_BUMPING_EVENTS = _INVERTIBLE_EVENTS + (
    "task_item.decision_recorded", "task_item.deleted", "task_item.restored",
)
#: Fields a rollback may write back. Deliberately NOT approval_state (only
#: decide() may grant/revoke an approval — a rollback across a decision would
#: be an approval without a reviewer), kind / parent_id (hierarchy invariants
#: are checked by update(), not here) — a range touching them is refused.
_RESTORABLE_FIELDS = (
    "title", "description", "status", "status_reason", "priority",
    "owner", "assignee", "start_at", "deadline", "progress", "work_estimate",
    "work_actual", "target_milestone", "category",
)


def _register_rollback_allowlist() -> None:
    try:
        from forge import security_events  # noqa: PLC0415
    except ImportError:  # forge not on sys.path: the writer is unavailable anyway
        return
    security_events.register_event_allowlist(ROLLBACK_EVENT_TYPE, _ROLLBACK_ALLOWLIST)


_register_rollback_allowlist()


async def rollback_to_version(
    tenant_id: str,
    item_id: str,
    target_version: int,
    actor: str,
    *,
    expected_version: Optional[int] = None,
    sid_fingerprint: Optional[str] = None,
) -> RollbackResult:
    """Restore the field values an item had at ``target_version``.

    NOT WIRED: no production caller as of 2026-09-27 (adversarial review).

    A rollback is a NEW change, never a rewind: the item moves FORWARD to
    ``current_version + 1`` carrying the values it had at ``target_version``.
    (Writing ``version = target_version`` re-issued a version number clients
    had already seen, so an optimistic-concurrency client holding that number
    could overwrite the rollback without a 409.)

    The state is reconstructed EXACTLY or not at all: the ``[old, new]``
    deltas of the version-bumping events after ``target_version`` are inverted
    newest-first. If any of them is not invertible (a delete/restore) or the
    event history does not account for every version bump (a cascade from a
    deleted parent bumps the version without an event on this item), the
    rollback is refused — never approximated.

    Audit-first, one transaction (``service._txn``): the chain record is
    written before the row changes; if it cannot be written, nothing changes.
    """
    from . import service  # noqa: PLC0415

    if isinstance(target_version, bool) or not isinstance(target_version, int) or target_version < 1:
        return RollbackResult(False, item_id, 0, target_version if isinstance(target_version, int) else 0,
                              "target_version must be a positive integer")

    def refuse(cur_v: int, reason: str) -> RollbackResult:
        return RollbackResult(success=False, item_id=item_id, from_version=cur_v,
                              to_version=target_version, reason=reason)

    def fn(conn: sqlite3.Connection) -> RollbackResult:
        row = conn.execute("SELECT * FROM items WHERE tenant_id=? AND id=?",
                           (tenant_id, item_id)).fetchone()
        if row is None:
            return refuse(0, f"Task {item_id} not found")
        cur = {k: row[k] for k in row.keys()}
        cur_v = int(cur["version"])
        if cur["deleted_at"]:
            return refuse(cur_v, "item is deleted — restore it first")
        if expected_version is not None and expected_version != cur_v:
            return refuse(cur_v, "item changed since it was read")
        if target_version >= cur_v:
            return refuse(cur_v, f"Cannot rollback to version {target_version} (current: {cur_v})")

        bumps = [dict(e) for e in conn.execute(
            "SELECT event_type, delta FROM events WHERE tenant_id=? AND item_id=? "
            "AND event_type IN (%s) ORDER BY ts, rowid" % ",".join("?" * len(_VERSION_BUMPING_EVENTS)),
            (tenant_id, item_id, *_VERSION_BUMPING_EVENTS),
        )]
        # version 1 = created; every later version must be one recorded bump.
        if len(bumps) + 1 != cur_v:
            return refuse(cur_v, "event history does not account for every version "
                                 "(cascade or import) — cannot reconstruct exactly")
        to_undo = bumps[target_version - 1:]
        state = dict(cur)
        for ev in reversed(to_undo):
            if ev["event_type"] not in _INVERTIBLE_EVENTS:
                return refuse(cur_v, f"{ev['event_type']} in range is not invertible")
            try:
                delta = json.loads(ev["delta"]) if isinstance(ev["delta"], str) else ev["delta"]
            except (json.JSONDecodeError, TypeError):
                return refuse(cur_v, "unreadable event delta in range")
            if not isinstance(delta, dict):
                return refuse(cur_v, "unreadable event delta in range")
            for field, change in delta.items():
                if field == "rollback":  # marker pair of an earlier rollback
                    continue
                if not (isinstance(change, list) and len(change) == 2):
                    return refuse(cur_v, "event delta in range is not an [old, new] pair")
                if field not in _RESTORABLE_FIELDS:
                    return refuse(cur_v, f"field {field!r} in range is not restorable")
                state[field] = change[0]

        changed = {f: state[f] for f in _RESTORABLE_FIELDS if f in state and state[f] != cur.get(f)}
        new_v = cur_v + 1
        # Audit FIRST (inside the transaction): if this raises, _txn rolls back
        # and the row is untouched.
        service._record(
            conn, tenant_id, item_id=item_id, event_type=ROLLBACK_EVENT_TYPE, actor=actor,
            chain_details={"from_version": cur_v, "to_version": target_version, "version": new_v,
                           "fields": ",".join(sorted(changed))[:512], "undone_events": len(to_undo),
                           **service._actor_details(actor, sid_fingerprint)},
            delta={**{f: [cur.get(f), v] for f, v in changed.items()},
                   "rollback": [cur_v, target_version]},
        )
        now = service.now_iso()
        cols = [*changed, "updated_at", "version"]
        vals = [*changed.values(), now, new_v]
        res = conn.execute(
            f"UPDATE items SET {', '.join(c + '=?' for c in cols)} "
            "WHERE tenant_id=? AND id=? AND version=?",
            (*vals, tenant_id, item_id, cur_v),
        )
        if res.rowcount != 1:
            raise service.Conflict("item changed since it was read",
                                   service._fetch(conn, tenant_id, item_id))
        return RollbackResult(
            success=True, item_id=item_id, from_version=cur_v, to_version=target_version,
            reason=f"Restored v{target_version} as v{new_v}", snapshot_ts=None,
            events_replayed=len(to_undo),
        )

    return service._txn(tenant_id, fn)


async def list_versions(
    tenant_id: str,
    item_id: str,
    limit: int = 50,
) -> list[dict[str, Any]]:
    """
    List all versions of a task (from snapshot-index).

    Returns:
        List of {version, snapshot_ts, ...}
    """
    with store.connect(tenant_id) as conn:
        rows = conn.execute(
            "SELECT * FROM snapshot_index "
            "WHERE tenant_id=? AND item_id=? "
            "ORDER BY version DESC "
            "LIMIT ?",
            (tenant_id, item_id, limit),
        ).fetchall()

        return [dict(row) for row in rows] if rows else []

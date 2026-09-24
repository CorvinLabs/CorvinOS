"""Task-Tracking SSOT service — every read and every audited mutation (ADR-2051, ADR-2056).

Audit-first: a mutation runs inside ONE SQLite transaction —

    apply the change  →  write the ``task_item.*`` record to the core chain
                      →  insert the local reference row  →  COMMIT

If the chain write fails the transaction is rolled back and
:class:`AuditUnavailable` is raised: no chain record, no row. The chain record
carries ids, enums and field NAMES only — never a title, description, assignee
or run reference. The local ``events`` table holds the full delta for the
history view and the chain hash that ties it to the chain record.

Rollups (progress, status counts, overdue) are derived on every read and never
stored, so they cannot drift from the items they summarise.
"""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Optional

from . import store
from .models import (
    DEPENDENCY_TYPES,
    PARENT_RULES,
    DependencyBody,
    ItemCreate,
    ItemPatch,
    RunLinkBody,
)

log = logging.getLogger(__name__)
_TXN = threading.local()  # chain records appended by the transaction running on this thread

WORK_KINDS = ("task", "subtask", "issue", "proposal")
CONTAINER_KINDS = ("initiative", "epic", "story")
CLOSED = ("complete", "archived")

_COLUMNS = (
    "id", "tenant_id", "kind", "parent_id", "title", "description", "status", "status_reason",
    "status_changed_at", "priority", "owner", "assignee", "start_at", "deadline", "progress",
    "work_estimate", "work_actual", "target_milestone", "category", "labels", "approval_state",
    "external_ref", "sort_key", "created_at", "created_by", "updated_at", "completed_at",
    "deleted_at", "deleted_by", "cascading_delete_id", "restored_at", "version",
)
#: Fields a PATCH may change (``version`` is the concurrency token, not a field).
_PATCHABLE = (
    "title", "kind", "parent_id", "description", "status", "status_reason", "priority", "owner",
    "assignee", "start_at", "deadline", "progress", "work_estimate", "work_actual",
    "target_milestone", "category", "labels", "approval_state",
)


#: approval states a create/PATCH may set; approved / rejected only via decide().
REQUESTABLE_APPROVAL = ("none", "suggested", "pending")
GATE_STATUS = {"approved": "complete", "rejected": "blocked", "pending": "open"}


class TaskTrackingError(ValueError):
    """Caller error → HTTP 400."""


class NotFound(TaskTrackingError):
    """→ HTTP 404."""


class Conflict(TaskTrackingError):
    """Stale ``version`` → HTTP 409; ``current`` is the record as it is now."""

    def __init__(self, msg: str, current: dict[str, Any]):
        super().__init__(msg)
        self.current = current


class AuditUnavailable(RuntimeError):
    """The core chain did not commit — nothing was written (→ HTTP 503)."""


# ── Chain writer ─────────────────────────────────────────────────────────────

def _default_chain_writer(tenant_id: str, event_type: str, details: dict[str, Any]) -> Optional[str]:
    from forge import security_events  # noqa: PLC0415

    from core.paths import tenant_audit_chain  # noqa: PLC0415

    rec = security_events.write_event(
        Path(tenant_audit_chain(tenant_id)), event_type, details={**details, "tenant_id": tenant_id},
    )
    return rec.get("hash") if isinstance(rec, dict) else None


#: Replaceable in tests (e.g. to prove a failing chain write rolls back).
chain_writer: Callable[[str, str, dict[str, Any]], Optional[str]] = _default_chain_writer


def _chain(tenant_id: str, event_type: str, details: dict[str, Any]) -> Optional[str]:
    try:
        return chain_writer(tenant_id, event_type, details)
    except Exception as exc:  # noqa: BLE001 — any chain failure is a refusal
        raise AuditUnavailable(f"audit chain write failed: {type(exc).__name__}") from exc


# ── Helpers ──────────────────────────────────────────────────────────────────

def now_iso(now: Optional[datetime] = None) -> str:
    d = now or datetime.now(timezone.utc)
    return d.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def parse_ts(value: Optional[str], *, end_of_day: bool = False) -> Optional[float]:
    """ISO-8601 → epoch seconds. A bare date means that day (end of day for deadlines)."""
    if not value:
        return None
    try:
        if len(value) == 10:
            d = datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
            if end_of_day:
                d = d + timedelta(days=1) - timedelta(seconds=1)
            return d.timestamp()
        d = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if d.tzinfo is None:
            d = d.replace(tzinfo=timezone.utc)
        return d.timestamp()
    except ValueError:
        return None


def _row(r: sqlite3.Row) -> dict[str, Any]:
    d = {k: r[k] for k in r.keys()}
    try:
        d["labels"] = json.loads(d.get("labels") or "[]")
    except (TypeError, ValueError):
        d["labels"] = []
    return d


def _new_id() -> str:
    return "t_" + uuid.uuid4().hex[:16]


def _fetch(conn: sqlite3.Connection, tenant_id: str, item_id: str, *, deleted_ok: bool = True) -> dict[str, Any]:
    r = conn.execute("SELECT * FROM items WHERE tenant_id=? AND id=?", (tenant_id, item_id)).fetchone()
    if r is None or (not deleted_ok and r["deleted_at"]):
        raise NotFound(f"item {item_id!r} not found")
    return _row(r)


def _check_parent(conn: sqlite3.Connection, tenant_id: str, kind: str,
                  parent_id: Optional[str], item_id: Optional[str]) -> None:
    allowed = PARENT_RULES[kind]
    if parent_id is None:
        if None not in allowed:
            raise TaskTrackingError(f"a {kind} needs a parent ({', '.join(a for a in allowed if a)})")
        return
    parent = conn.execute("SELECT id, kind, parent_id, deleted_at FROM items WHERE tenant_id=? AND id=?",
                          (tenant_id, parent_id)).fetchone()
    if parent is None or parent["deleted_at"]:
        raise TaskTrackingError(f"parent {parent_id!r} not found")
    if parent["kind"] not in allowed:
        raise TaskTrackingError(f"a {kind} cannot sit under a {parent['kind']}")
    # Cycle: walk up from the new parent; meeting the item itself closes a loop.
    seen = set()
    cur = parent_id
    while cur is not None:
        if item_id is not None and cur == item_id:
            raise TaskTrackingError("that parent would create a cycle")
        if cur in seen:
            raise TaskTrackingError("existing hierarchy contains a cycle")
        seen.add(cur)
        r = conn.execute("SELECT parent_id FROM items WHERE tenant_id=? AND id=?", (tenant_id, cur)).fetchone()
        cur = r["parent_id"] if r else None


def _check_children_fit(conn: sqlite3.Connection, tenant_id: str, item_id: str, new_kind: str) -> None:
    for r in conn.execute("SELECT kind FROM items WHERE tenant_id=? AND parent_id=? AND deleted_at IS NULL",
                          (tenant_id, item_id)):
        if new_kind not in PARENT_RULES[r["kind"]]:
            raise TaskTrackingError(f"a {new_kind} cannot hold its existing {r['kind']} children")


def _record(conn: sqlite3.Connection, tenant_id: str, *, item_id: str, event_type: str,
            actor: str, chain_details: dict[str, Any], delta: dict[str, Any]) -> None:
    """Chain FIRST, then the reference row — inside the caller's transaction."""
    h = _chain(tenant_id, event_type, {"item_id": item_id, **chain_details})
    written = getattr(_TXN, "records", None)
    if written is not None:
        written.append(item_id)
    conn.execute(
        "INSERT INTO events(event_id, tenant_id, item_id, event_type, ts, actor, delta, chain_hash)"
        " VALUES (?,?,?,?,?,?,?,?)",
        ("e_" + uuid.uuid4().hex, tenant_id, item_id, event_type, now_iso(), actor,
         json.dumps(delta, default=str, ensure_ascii=False), h),
    )


def _txn(tenant_id: str, fn: Callable[[sqlite3.Connection], Any]) -> Any:
    """One transaction. A chain record is permanent, a row is not: if the
    transaction rolls back after >=1 ``task_item.*`` record was appended (a later
    chain write failed, the commit failed, the process was told to stop), a
    ``task_item.rolled_back`` record names how many preceding records did NOT
    take effect — the chain never claims a change the store does not hold."""
    with store.tenant_lock(tenant_id), store.connect(tenant_id) as conn:
        _TXN.records = []
        conn.execute("BEGIN IMMEDIATE")
        try:
            out = fn(conn)
            conn.execute("COMMIT")
            return out
        except BaseException as exc:
            try:
                conn.execute("ROLLBACK")
            finally:
                if _TXN.records:
                    try:
                        _chain(tenant_id, "task_item.rolled_back", {
                            "item_id": _TXN.records[0], "count": len(_TXN.records),
                            "error_class": type(exc).__name__,
                        })
                    except AuditUnavailable:
                        log.error("task_tracking: %d chain record(s) rolled back and the compensating "
                                  "record could not be written", len(_TXN.records))
            raise
        finally:
            _TXN.records = None


def _actor_details(actor: str, sid_fingerprint: Optional[str]) -> dict[str, Any]:
    d: dict[str, Any] = {"actor_kind": actor.split(":", 1)[0]}
    if sid_fingerprint:
        d["sid_fingerprint"] = sid_fingerprint
    return d


# ── Reads ────────────────────────────────────────────────────────────────────

def _all_rows(tenant_id: str, include_deleted: bool) -> list[dict[str, Any]]:
    if not store.exists(tenant_id):
        return []
    with store.connect(tenant_id) as conn:
        q = "SELECT * FROM items WHERE tenant_id=?"
        if not include_deleted:
            q += " AND deleted_at IS NULL"
        q += " ORDER BY sort_key, created_at"
        rows = [_row(r) for r in conn.execute(q, (tenant_id,))]
        deps = conn.execute("SELECT item_id, depends_on_id, dep_type FROM dependencies WHERE tenant_id=?",
                            (tenant_id,)).fetchall()
        runs = conn.execute("SELECT item_id, COUNT(*) AS n FROM runs WHERE tenant_id=? GROUP BY item_id",
                            (tenant_id,)).fetchall()
    by_id = {r["id"]: r for r in rows}
    run_n = {r["item_id"]: r["n"] for r in runs}
    for r in rows:
        r["depends_on"] = []
        r["run_count"] = run_n.get(r["id"], 0)
    for d in deps:
        item = by_id.get(d["item_id"])
        if item is not None and d["dep_type"] in ("depends_on", "blocks"):
            item["depends_on"].append(d["depends_on_id"])
    return rows


def has_any_rows(tenant_id: str) -> bool:
    """True when the store holds any item, deleted ones included."""
    if not store.exists(tenant_id):
        return False
    with store.connect(tenant_id) as conn:
        return conn.execute("SELECT 1 FROM items WHERE tenant_id=? LIMIT 1", (tenant_id,)).fetchone() is not None


#: Categories that mark a date rather than a piece of work: never overdue,
#: never in the KPI counts.
MARKER_CATEGORIES = ("checkpoint",)


def _is_overdue(item: dict[str, Any], now_ts: float) -> bool:
    if item.get("category") in MARKER_CATEGORIES:
        return False
    dl = parse_ts(item.get("deadline"), end_of_day=True)
    return dl is not None and item["status"] not in CLOSED and now_ts > dl


def _effective_progress(item: dict[str, Any]) -> int:
    if item["status"] == "complete":
        return 100
    return int(item.get("progress") or 0)


def with_rollups(rows: list[dict[str, Any]], now_ts: float) -> list[dict[str, Any]]:
    """Add ``overdue``, ``waiting_on`` and a ``rollup`` per item (derived, never stored)."""
    by_id = {r["id"]: r for r in rows}
    kids: dict[Optional[str], list[str]] = {}
    for r in rows:
        if r.get("deleted_at"):
            continue
        parent = r["parent_id"] if r["parent_id"] in by_id else None
        kids.setdefault(parent, []).append(r["id"])

    memo: dict[str, dict[str, Any]] = {}

    def roll(iid: str, depth: int = 0) -> dict[str, Any]:
        if iid in memo:
            return memo[iid]
        if depth > 64:  # hierarchy depth guard — a cycle is refused on write
            return {"leaves": 0, "progress_sum": 0, "counts": {}, "overdue": 0, "descendants": 0}
        item = by_id[iid]
        children = kids.get(iid, [])
        counts: dict[str, int] = {}
        leaves = progress_sum = overdue = descendants = 0
        for c in children:
            cr = roll(c, depth + 1)
            child = by_id[c]
            descendants += 1 + cr["descendants"]
            counts[child["status"]] = counts.get(child["status"], 0) + 1
            for k, v in cr["counts"].items():
                counts[k] = counts.get(k, 0) + v
            overdue += cr["overdue"] + (1 if _is_overdue(child, now_ts) else 0)
            leaves += cr["leaves"]
            progress_sum += cr["progress_sum"]
        if not children and item["status"] != "archived":
            leaves, progress_sum = 1, _effective_progress(item)
        out = {"leaves": leaves, "progress_sum": progress_sum, "counts": counts,
               "overdue": overdue, "descendants": descendants}
        memo[iid] = out
        return out

    status_of = {r["id"]: r["status"] for r in rows}
    for r in rows:
        r["overdue"] = _is_overdue(r, now_ts)
        r["waiting_on"] = [d for d in r.get("depends_on", []) if status_of.get(d) not in CLOSED and d in status_of]
        r["child_ids"] = kids.get(r["id"], [])
        if r.get("deleted_at"):
            r["rollup"] = None
            continue
        ro = roll(r["id"])
        r["rollup"] = {
            "progress": round(ro["progress_sum"] / ro["leaves"]) if ro["leaves"] else None,
            "descendants": ro["descendants"],
            "counts": ro["counts"],
            "overdue": ro["overdue"],
        }
    return rows


def summary(rows: Iterable[dict[str, Any]], now_ts: float) -> dict[str, Any]:
    counts = {s: 0 for s in ("open", "in_progress", "blocked", "complete", "archived")}
    overdue = approvals = done_7d = initiatives_active = total = 0
    week_ago = now_ts - 7 * 86400
    for r in rows:
        if r.get("deleted_at"):
            continue
        if r["kind"] == "initiative" and r["status"] not in CLOSED:
            initiatives_active += 1
        if r["approval_state"] == "pending":
            approvals += 1
        if r["kind"] not in WORK_KINDS or r.get("category") in MARKER_CATEGORIES:
            continue
        total += 1
        counts[r["status"]] = counts.get(r["status"], 0) + 1
        if _is_overdue(r, now_ts):
            overdue += 1
        ct = parse_ts(r.get("completed_at"))
        if r["status"] == "complete" and ct is not None and ct >= week_ago:
            done_7d += 1
    return {**counts, "total": total, "overdue": overdue, "approvals_pending": approvals,
            "done_7d": done_7d, "initiatives_active": initiatives_active}


def list_items(tenant_id: str, *, include_deleted: bool = False,
               now: Optional[datetime] = None) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    rows = with_rollups(_all_rows(tenant_id, include_deleted), now_dt.timestamp())
    return {"server_time": now_iso(now_dt), "items": rows, "summary": summary(rows, now_dt.timestamp())}


def detail(tenant_id: str, item_id: str, *, history_limit: int = 200,
           now: Optional[datetime] = None) -> dict[str, Any]:
    now_dt = now or datetime.now(timezone.utc)
    rows = with_rollups(_all_rows(tenant_id, include_deleted=True), now_dt.timestamp())
    by_id = {r["id"]: r for r in rows}
    item = by_id.get(item_id)
    if item is None:
        raise NotFound(f"item {item_id!r} not found")

    def brief(r: dict[str, Any]) -> dict[str, Any]:
        return {k: r.get(k) for k in ("id", "kind", "title", "status", "priority", "deadline",
                                      "overdue", "deleted_at", "category", "approval_state")}

    ancestors = []
    cur, guard = item.get("parent_id"), 0
    while cur and cur in by_id and guard < 64:
        ancestors.insert(0, brief(by_id[cur]))
        cur, guard = by_id[cur].get("parent_id"), guard + 1
    with store.connect(tenant_id) as conn:
        deps_out = conn.execute(
            "SELECT depends_on_id, dep_type, created_at FROM dependencies WHERE tenant_id=? AND item_id=?",
            (tenant_id, item_id)).fetchall()
        deps_in = conn.execute(
            "SELECT item_id, dep_type, created_at FROM dependencies WHERE tenant_id=? AND depends_on_id=?",
            (tenant_id, item_id)).fetchall()
        runs = conn.execute(
            "SELECT run_type, run_ref, linked_at, linked_by FROM runs WHERE tenant_id=? AND item_id=?"
            " ORDER BY linked_at DESC", (tenant_id, item_id)).fetchall()
        hist = conn.execute(
            "SELECT event_id, event_type, ts, actor, delta, chain_hash FROM events"
            " WHERE tenant_id=? AND item_id=? ORDER BY ts DESC, rowid DESC LIMIT ?",
            (tenant_id, item_id, history_limit)).fetchall()
    return {
        "server_time": now_iso(now_dt),
        "item": item,
        "ancestors": ancestors,
        "children": [brief(by_id[c]) | {"rollup": by_id[c]["rollup"]} for c in item["child_ids"]],
        "depends_on": [brief(by_id[d["depends_on_id"]]) | {"dep_type": d["dep_type"]}
                       for d in deps_out if d["depends_on_id"] in by_id],
        "required_by": [brief(by_id[d["item_id"]]) | {"dep_type": d["dep_type"]}
                        for d in deps_in if d["item_id"] in by_id],
        "runs": [dict(r) for r in runs],
        "history": [{**dict(h), "delta": json.loads(h["delta"] or "{}")} for h in hist],
    }


# ── Mutations ────────────────────────────────────────────────────────────────

def _insert(conn: sqlite3.Connection, tenant_id: str, fields: dict[str, Any], *, actor: str) -> dict[str, Any]:
    now = now_iso()
    row = {c: None for c in _COLUMNS}
    row.update({
        "id": _new_id(), "tenant_id": tenant_id, "status": "open", "priority": "medium",
        "approval_state": "none", "labels": [], "sort_key": 0, "created_at": now,
        "created_by": actor, "updated_at": now, "version": 1,
    })
    row.update({k: v for k, v in fields.items() if k in _COLUMNS and v is not None})
    _check_parent(conn, tenant_id, row["kind"], row["parent_id"], None)
    # Stamp "now" only when the caller said nothing about completion; an import
    # that does not know when an item was done passes completed_at=None
    # explicitly and it stays unknown — a guessed date would inflate "done in
    # the last 7 days".
    if row["status"] == "complete" and not row["completed_at"] and "completed_at" not in fields:
        row["completed_at"] = now
    if row["status"] != "complete":
        row["completed_at"] = None
    row["status_changed_at"] = row["status_changed_at"] or now
    if not row["sort_key"]:
        r = conn.execute("SELECT COALESCE(MAX(sort_key), 0) + 1 AS n FROM items WHERE tenant_id=?",
                         (tenant_id,)).fetchone()
        row["sort_key"] = r["n"]
    vals = dict(row, labels=json.dumps(row["labels"] or []))
    conn.execute(f"INSERT INTO items({', '.join(_COLUMNS)}) VALUES ({', '.join('?' * len(_COLUMNS))})",
                 tuple(vals[c] for c in _COLUMNS))
    return row


def create(tenant_id: str, body: ItemCreate, *, actor: str, sid_fingerprint: Optional[str] = None,
           extra: Optional[dict[str, Any]] = None, source: str = "console") -> dict[str, Any]:
    if body.approval_state not in REQUESTABLE_APPROVAL:
        raise TaskTrackingError("a new item cannot start approved or rejected — record a decision")
    fields = body.model_dump(exclude_none=True)
    if extra:
        fields.update(extra)

    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        row = _insert(conn, tenant_id, fields, actor=actor)
        _record(conn, tenant_id, item_id=row["id"], event_type="task_item.created", actor=actor,
                chain_details={"kind": row["kind"], "parent_id": row["parent_id"], "status": row["status"],
                               "priority": row["priority"], "category": row["category"], "source": source,
                               **_actor_details(actor, sid_fingerprint)},
                delta={"created": {k: row[k] for k in ("kind", "title", "status", "priority", "parent_id")}})
        return _fetch(conn, tenant_id, row["id"])

    return _txn(tenant_id, fn)


def update(tenant_id: str, item_id: str, patch: ItemPatch, *, actor: str,
           sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    sent = patch.model_fields_set - {"version"}

    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = _fetch(conn, tenant_id, item_id)
        if cur["deleted_at"]:
            raise TaskTrackingError("item is deleted — restore it first")
        if cur["version"] != patch.version:
            raise Conflict("item changed since it was read", cur)
        new = {k: getattr(patch, k) for k in sent if k in _PATCHABLE}
        if "labels" in new and new["labels"] is None:
            new["labels"] = []
        if new.get("approval_state") not in (None, *REQUESTABLE_APPROVAL):
            raise TaskTrackingError("approve or reject through the decision endpoint, not a field update")
        for required in ("title", "kind", "status", "priority", "approval_state"):
            if required in new and new[required] is None:
                raise TaskTrackingError(f"{required} cannot be cleared")
        changed = {k: v for k, v in new.items() if cur.get(k) != v}
        if not changed:
            return cur
        kind = changed.get("kind", cur["kind"])
        if "kind" in changed or "parent_id" in changed:
            _check_parent(conn, tenant_id, kind, changed.get("parent_id", cur["parent_id"]), item_id)
        if "kind" in changed:
            _check_children_fit(conn, tenant_id, item_id, kind)
        now = now_iso()
        if "status" in changed:
            changed["status_changed_at"] = now
            changed["completed_at"] = now if changed["status"] == "complete" else None
            if "status_reason" not in new:
                changed["status_reason"] = None
        changed["updated_at"] = now
        changed["version"] = cur["version"] + 1
        cols = list(changed)
        vals = [json.dumps(changed[c]) if c == "labels" else changed[c] for c in cols]
        res = conn.execute(
            f"UPDATE items SET {', '.join(c + '=?' for c in cols)} WHERE tenant_id=? AND id=? AND version=?",
            (*vals, tenant_id, item_id, cur["version"]))
        if res.rowcount != 1:
            raise Conflict("item changed since it was read", _fetch(conn, tenant_id, item_id))
        user_fields = sorted(k for k in changed if k in new)  # what the caller changed, not side effects
        details: dict[str, Any] = {"kind": kind, "fields": ",".join(user_fields), "version": changed["version"],
                                   **_actor_details(actor, sid_fingerprint)}
        if "status" in changed:
            details.update(old_status=cur["status"], new_status=changed["status"])
        if "priority" in changed:
            details.update(old_priority=cur["priority"], new_priority=changed["priority"])
        if "parent_id" in changed:
            details["parent_id"] = changed["parent_id"]
        if "approval_state" in changed:
            details.update(old_approval=cur["approval_state"], new_approval=changed["approval_state"])
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.updated", actor=actor,
                chain_details=details,
                delta={k: [cur.get(k), changed[k]] for k in user_fields})
        return _fetch(conn, tenant_id, item_id)

    return _txn(tenant_id, fn)


def decide(tenant_id: str, item_id: str, decision: str, version: int, *, actor: str,
           sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    """Record a gate/approval decision (pending → approved | rejected, or reset)."""
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = _fetch(conn, tenant_id, item_id, deleted_ok=False)
        if cur["version"] != version:
            raise Conflict("item changed since it was read", cur)
        if cur["approval_state"] == "none":
            raise TaskTrackingError("this item has no approval to decide")
        if cur["approval_state"] == decision:
            return cur
        now = now_iso()
        # A gate's status follows its decision (go → complete, no-go → blocked,
        # reset → open) — the same mapping the initiatives.json import used.
        status = cur["status"]
        if cur["category"] == "gate":
            status = GATE_STATUS[decision]
        completed_at = (cur["completed_at"] or now) if status == "complete" else None
        conn.execute("UPDATE items SET approval_state=?, status=?, completed_at=?, status_changed_at=?,"
                     " updated_at=?, version=version+1 WHERE tenant_id=? AND id=?",
                     (decision, status, completed_at,
                      now if status != cur["status"] else cur["status_changed_at"], now, tenant_id, item_id))
        details = {"decision": decision, "previous": cur["approval_state"], "version": cur["version"] + 1,
                   **_actor_details(actor, sid_fingerprint)}
        delta: dict[str, Any] = {"approval_state": [cur["approval_state"], decision]}
        if status != cur["status"]:
            details.update(old_status=cur["status"], new_status=status)
            delta["status"] = [cur["status"], status]
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.decision_recorded", actor=actor,
                chain_details=details, delta=delta)
        return _fetch(conn, tenant_id, item_id)

    return _txn(tenant_id, fn)


def _descendants(conn: sqlite3.Connection, tenant_id: str, item_id: str) -> list[str]:
    out, stack, seen = [], [item_id], {item_id}
    while stack:
        cur = stack.pop()
        for r in conn.execute("SELECT id FROM items WHERE tenant_id=? AND parent_id=?", (tenant_id, cur)):
            if r["id"] not in seen:
                seen.add(r["id"])
                out.append(r["id"])
                stack.append(r["id"])
    return out


def delete(tenant_id: str, item_id: str, *, actor: str, sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    """Soft delete with cascade — rows stay, ``cascading_delete_id`` groups them for restore."""
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = _fetch(conn, tenant_id, item_id)
        if cur["deleted_at"]:
            return cur
        cascade_id = "d_" + uuid.uuid4().hex[:16]
        now = now_iso()
        ids = [item_id] + [d for d in _descendants(conn, tenant_id, item_id)
                           if not _fetch(conn, tenant_id, d)["deleted_at"]]
        for i in ids:
            conn.execute("UPDATE items SET deleted_at=?, deleted_by=?, cascading_delete_id=?, updated_at=?,"
                         " version=version+1 WHERE tenant_id=? AND id=?",
                         (now, actor, cascade_id, now, tenant_id, i))
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.deleted", actor=actor,
                chain_details={"kind": cur["kind"], "cascade_count": len(ids) - 1,
                               **_actor_details(actor, sid_fingerprint)},
                delta={"deleted": True, "cascade_count": len(ids) - 1})
        return _fetch(conn, tenant_id, item_id)

    return _txn(tenant_id, fn)


def restore(tenant_id: str, item_id: str, *, actor: str, sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        cur = _fetch(conn, tenant_id, item_id)
        if not cur["deleted_at"]:
            return cur
        if cur["parent_id"]:
            parent = conn.execute("SELECT deleted_at FROM items WHERE tenant_id=? AND id=?",
                                  (tenant_id, cur["parent_id"])).fetchone()
            if parent is not None and parent["deleted_at"]:
                raise TaskTrackingError("its parent is deleted — restore the parent first")
        # The parent may have changed kind while this item was deleted.
        _check_parent(conn, tenant_id, cur["kind"], cur["parent_id"], item_id)
        now = now_iso()
        res = conn.execute(
            "UPDATE items SET deleted_at=NULL, deleted_by=NULL, restored_at=?, updated_at=?, version=version+1,"
            " cascading_delete_id=NULL WHERE tenant_id=? AND cascading_delete_id=?",
            (now, now, tenant_id, cur["cascading_delete_id"]))
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.restored", actor=actor,
                chain_details={"kind": cur["kind"], "cascade_count": max(0, res.rowcount - 1),
                               **_actor_details(actor, sid_fingerprint)},
                delta={"restored": True, "cascade_count": max(0, res.rowcount - 1)})
        return _fetch(conn, tenant_id, item_id)

    return _txn(tenant_id, fn)


def _reaches(conn: sqlite3.Connection, tenant_id: str, start: str, target: str) -> bool:
    stack, seen = [start], set()
    while stack:
        cur = stack.pop()
        if cur == target:
            return True
        if cur in seen:
            continue
        seen.add(cur)
        stack.extend(r["depends_on_id"] for r in conn.execute(
            "SELECT depends_on_id FROM dependencies WHERE tenant_id=? AND item_id=?"
            " AND dep_type IN ('depends_on','blocks')", (tenant_id, cur)))
    return False


def add_dependency(tenant_id: str, item_id: str, body: DependencyBody, *, actor: str,
                   sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    if body.dep_type not in DEPENDENCY_TYPES:
        raise TaskTrackingError("unknown dependency type")

    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        _fetch(conn, tenant_id, item_id, deleted_ok=False)
        _fetch(conn, tenant_id, body.depends_on_id, deleted_ok=False)
        if body.depends_on_id == item_id:
            raise TaskTrackingError("an item cannot depend on itself")
        if body.dep_type in ("depends_on", "blocks") and _reaches(conn, tenant_id, body.depends_on_id, item_id):
            raise TaskTrackingError("that dependency would create a cycle")
        exists = conn.execute("SELECT 1 FROM dependencies WHERE tenant_id=? AND item_id=? AND depends_on_id=?",
                              (tenant_id, item_id, body.depends_on_id)).fetchone()
        if exists:
            raise TaskTrackingError("dependency already exists")
        conn.execute("INSERT INTO dependencies VALUES (?,?,?,?,?)",
                     (tenant_id, item_id, body.depends_on_id, body.dep_type, now_iso()))
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.dependency_added", actor=actor,
                chain_details={"depends_on_id": body.depends_on_id, "dep_type": body.dep_type,
                               **_actor_details(actor, sid_fingerprint)},
                delta={"depends_on": body.depends_on_id, "dep_type": body.dep_type})
        return {"ok": True}

    return _txn(tenant_id, fn)


def remove_dependency(tenant_id: str, item_id: str, depends_on_id: str, *, actor: str,
                      sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        res = conn.execute("DELETE FROM dependencies WHERE tenant_id=? AND item_id=? AND depends_on_id=?",
                           (tenant_id, item_id, depends_on_id))
        if res.rowcount == 0:
            raise NotFound("no such dependency")
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.dependency_removed", actor=actor,
                chain_details={"depends_on_id": depends_on_id, **_actor_details(actor, sid_fingerprint)},
                delta={"depends_on_removed": depends_on_id})
        return {"ok": True}

    return _txn(tenant_id, fn)


def link_run(tenant_id: str, item_id: str, body: RunLinkBody, *, actor: str,
             sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        _fetch(conn, tenant_id, item_id, deleted_ok=False)
        try:
            conn.execute("INSERT INTO runs VALUES (?,?,?,?,?,?)",
                         (tenant_id, item_id, body.run_type, body.run_ref, now_iso(), actor))
        except sqlite3.IntegrityError as exc:
            raise TaskTrackingError("run already linked to this item") from exc
        # run_ref stays local: a chat run's ref names a channel/conversation.
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.run_linked", actor=actor,
                chain_details={"run_type": body.run_type, **_actor_details(actor, sid_fingerprint)},
                delta={"run_linked": f"{body.run_type}:{body.run_ref}"})
        return {"ok": True}

    return _txn(tenant_id, fn)


def unlink_run(tenant_id: str, item_id: str, run_type: str, run_ref: str, *, actor: str,
               sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        res = conn.execute("DELETE FROM runs WHERE tenant_id=? AND item_id=? AND run_type=? AND run_ref=?",
                           (tenant_id, item_id, run_type, run_ref))
        if res.rowcount == 0:
            raise NotFound("no such run link")
        _record(conn, tenant_id, item_id=item_id, event_type="task_item.run_unlinked", actor=actor,
                chain_details={"run_type": run_type, **_actor_details(actor, sid_fingerprint)},
                delta={"run_unlinked": f"{run_type}:{run_ref}"})
        return {"ok": True}

    return _txn(tenant_id, fn)


def import_items(tenant_id: str, items: list[dict[str, Any]], *, source: str, actor: str,
                 sid_fingerprint: Optional[str] = None) -> dict[str, Any]:
    """Insert-only bulk import, idempotent on ``external_ref``.

    ``items`` are ordered parent-first; ``parent_ref`` names a parent by its
    ``external_ref``. An item whose ref already exists is skipped (never
    updated — after import the SSOT is authoritative). One ``task_item.created``
    record per inserted item plus one ``task_item.imported`` summary record.
    """
    def fn(conn: sqlite3.Connection) -> dict[str, Any]:
        existing = {r["external_ref"]: r for r in conn.execute(
            "SELECT id, kind, deleted_at, external_ref FROM items WHERE tenant_id=? AND external_ref IS NOT NULL",
            (tenant_id,))}
        ref_to_id = {ref: r["id"] for ref, r in existing.items()}
        # Plan and validate the whole batch BEFORE the first chain write: a chain
        # record is permanent, so a batch that would fail halfway fails up front.
        # A new item whose parent was deleted in the store (or is itself skipped)
        # is skipped with it — the operator deleted that branch.
        known = {ref: r["kind"] for ref, r in existing.items() if not r["deleted_at"]}
        dropped = {ref for ref, r in existing.items() if r["deleted_at"]}
        planned: list[dict[str, Any]] = []
        skipped = skipped_deleted = 0
        for spec in items:
            ref = spec["external_ref"]
            if ref in existing:
                skipped += 1
                continue
            pref = spec.get("parent_ref")
            if pref is not None and pref in dropped:
                dropped.add(ref)
                skipped_deleted += 1
                continue
            if pref is not None and pref not in known:
                raise TaskTrackingError(f"import: parent {pref!r} of {ref!r} missing")
            if (known.get(pref) if pref is not None else None) not in PARENT_RULES[spec["kind"]]:
                raise TaskTrackingError(f"import: a {spec['kind']} cannot sit under {known.get(pref)!r}")
            ItemCreate.model_validate({k: v for k, v in spec.items() if k in ItemCreate.model_fields})
            known[ref] = spec["kind"]
            planned.append(spec)
        inserted = 0
        first_id = None
        for spec in planned:
            ref = spec["external_ref"]
            fields = {k: v for k, v in spec.items() if k not in ("parent_ref", "depends_on_refs")}
            pref = spec.get("parent_ref")
            if pref is not None:
                fields["parent_id"] = ref_to_id[pref]
            row = _insert(conn, tenant_id, fields, actor=actor)
            ref_to_id[ref] = row["id"]
            first_id = first_id or row["id"]
            inserted += 1
            _record(conn, tenant_id, item_id=row["id"], event_type="task_item.created", actor=actor,
                    chain_details={"kind": row["kind"], "parent_id": row["parent_id"], "status": row["status"],
                                   "priority": row["priority"], "category": row["category"], "source": source,
                                   **_actor_details(actor, sid_fingerprint)},
                    delta={"imported_from": ref})
        for spec in planned:  # only new items — never re-add a dependency the operator removed
            for dref in spec.get("depends_on_refs") or []:
                a, b = ref_to_id.get(spec["external_ref"]), ref_to_id.get(dref)
                if not a or not b or a == b:
                    continue
                if conn.execute("SELECT 1 FROM dependencies WHERE tenant_id=? AND item_id=? AND depends_on_id=?",
                                (tenant_id, a, b)).fetchone() or _reaches(conn, tenant_id, b, a):
                    continue
                conn.execute("INSERT INTO dependencies VALUES (?,?,?,?,?)",
                             (tenant_id, a, b, "depends_on", now_iso()))
                _record(conn, tenant_id, item_id=a, event_type="task_item.dependency_added", actor=actor,
                        chain_details={"depends_on_id": b, "dep_type": "depends_on", "source": source,
                                       **_actor_details(actor, sid_fingerprint)},
                        delta={"depends_on": b, "dep_type": "depends_on"})
        if inserted:
            _record(conn, tenant_id, item_id=first_id, event_type="task_item.imported", actor=actor,
                    chain_details={"count": inserted, "source": source, **_actor_details(actor, sid_fingerprint)},
                    delta={"imported": inserted, "skipped": skipped, "source": source})
        return {"inserted": inserted, "skipped": skipped, "skipped_deleted_parent": skipped_deleted}

    return _txn(tenant_id, fn)

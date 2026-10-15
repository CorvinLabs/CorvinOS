"""Per-task timeline: a READ MODEL folded from the task's append-only event log (PLAN-0958 P2).

Never a second writer of state. One row per transition or notice, oldest first:
``{seq, at, state, kind, text}``. ``text`` is only ever a rendered notice (fixed template, no ids,
see ``task_notices``); rows for plain transitions carry none. Correcting a row is a new row.
"""
from __future__ import annotations

from typing import Any, Iterable

_STATE_BY_EVENT = {
    "task.created": "pending",
    "task.started": "running",
    "task.completed": "completed",
    "task.failed": "failed",
    "task.cancelled": "cancelled",
    "task.interrupted": "interrupted",
}


def fold(events: Iterable[tuple[int, dict[str, Any]]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for seq, ev in events:
        name = ev.get("event")
        at = ev.get("timestamp") if ev.get("timestamp") is not None else ev.get("at")
        if name in _STATE_BY_EVENT:
            rows.append({"seq": seq, "at": at, "state": _STATE_BY_EVENT[name], "kind": "transition", "text": None})
        elif name == "task.notice":
            rows.append({"seq": seq, "at": at, "state": None, "kind": str(ev.get("notice_kind") or "notice"),
                         "text": str(ev.get("text") or "") or None})
    return rows


def timeline(tm: Any, task_id: str) -> list[dict[str, Any]]:
    """Rows for one task, from a ``TaskManager``."""
    return fold(tm.events_since(task_id, start_seq=None))

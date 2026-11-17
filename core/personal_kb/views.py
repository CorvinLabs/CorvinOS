"""Read models for the console: the personal graph and the personal task list (ADR-2325 item 7).

Same wire shapes as the Knowledge Graph panel (``plugins_corvin_knowledge_api.load_graph_data``)
and the task board (``task_tracking`` items), so the existing panels render them unchanged. These
payloads exist only in a response to the unlocked owner; nothing here is written to ``tasks.db``.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from .store import PersonalStore

STATUS_TO_BOARD = {"open": "open", "in_progress": "in_progress", "blocked": "blocked", "done": "complete",
                   "cancelled": "archived"}
BOARD_TO_STATUS = {v: k for k, v in STATUS_TO_BOARD.items()}
ID_PREFIX = "pkb:"


def _iso(ts: Any) -> str:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()
    except (TypeError, ValueError, OSError):
        return datetime.now(timezone.utc).isoformat()


def graph_payload(store: PersonalStore) -> dict[str, Any]:
    entities = [{"id": e["uid"], "label": e.get("display_id") or e["uid"][-6:], "file": "",
                 "type": e["type"], "title": e["title"], "status": e["status"],
                 "tags": list(e.get("labels", [])) + ([e["key"]] if e.get("key") else []),
                 "review": e.get("review"), "origin": e.get("origin")}
                for e in store.list()]
    ids = {e["id"] for e in entities}
    seen, relations = set(), []
    for e in store.list():
        for ln in e.get("links", []):
            k = (e["uid"], ln["to"], ln["rel"])
            if ln["to"] in ids and k not in seen:
                seen.add(k)
                relations.append({"from_id": e["uid"], "to_id": ln["to"], "relation": ln["rel"]})
    return {"entities": entities, "relations": relations, "duplicate_rows": 0, "project": None,
            "scope": "personal", "repo_path": "", "graph_state": "ok" if entities else "empty", "graph_error": None,
            "problems": len(store.problems)}


def task_items(store: PersonalStore) -> list[dict[str, Any]]:
    out = []
    for e in store.list(type="task"):
        t = e.get("task") or {}
        reason = t.get("blocked_reason") if e["status"] == "blocked" else t.get("cancel_reason") \
            if e["status"] == "cancelled" else None
        out.append({
            "id": ID_PREFIX + e["uid"], "tenant_id": store.tenant_id, "kind": "task", "parent_id": None,
            "title": e["title"], "description": e.get("body") or "", "status": STATUS_TO_BOARD[e["status"]],
            "status_reason": reason, "status_changed_at": _iso(e.get("updated")), "priority": "medium",
            "owner": None, "assignee": None, "start_at": None, "deadline": None, "progress": None,
            "work_estimate": None, "work_actual": None, "target_milestone": None, "category": "personal",
            "labels": list(e.get("labels", [])), "approval_state": "none", "external_ref": None, "sort_key": 0,
            "created_at": _iso(e.get("created")), "created_by": "owner", "updated_at": _iso(e.get("updated")),
            "completed_at": _iso(e.get("updated")) if e["status"] == "done" else None, "deleted_at": None,
            "deleted_by": None, "cascading_delete_id": None, "project": None, "scope": "personal",
            "display_id": e.get("display_id"), "dod": t.get("dod"),
            # the idea a decoder task came from: a blocked or cancelled one can send the idea back a step
            "idea_uid": next((ln["to"] for ln in e.get("links", []) if ln.get("rel") == "derived_from"
                              and not str(ln.get("to", "")).startswith("legacy:")), None),
        })
    return out


def entity_public(e: dict[str, Any]) -> dict[str, Any]:
    """The owner's view of one entry (everything except internal decoder context)."""
    out = {k: v for k, v in e.items() if k != "decoder"}
    if e.get("decoder"):
        st = e["decoder"]
        out["decoder"] = {"stage": st.get("stage"), "loss": st.get("loss"), "iterations": st.get("iterations"),
                          "gates": st.get("gates"), "objections": st.get("objections"),
                          "children": st.get("children"), "last_stage": st.get("last_stage"),
                          "dismissed": st.get("dismissed") or []}
    return out

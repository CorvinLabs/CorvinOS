"""Universal Activity Hub (UAH) — /v1/console/activity

Surfaces actions performed from the Chat (Chat as Kommandozentrale) in the
console's activity feed. Backed by the append-only JSONL file written by
MCP servers whenever CORVIN_CHAT_KEY is set.

Storage: <tenant_global>/chat_activity.jsonl
Schema:  {ts, action, panel, entity_id, chat_key, summary[, extra]}

Endpoints:
  GET /v1/console/activity/feed   — paginated activity list (read-only)
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query
from forge import paths as _forge_paths

import corvin_console.auth as _auth
from ..deps import require_session

router = APIRouter()

_PANEL_LABELS = {
    "compute":     "Agentic Compute",
    "datasources": "Data Sources",
    "forge":       "Forge Tools",
    "skills":      "Skills",
    "a2a":         "Remote Triggers",
    "orgs":        "Organisations",
    "workflows":   "Workflows",
}

_ACTION_LABELS = {
    "compute.run_submit":   "Compute run started",
    "datasource.register":  "Data source registered",
    "forge.tool_create":    "Forge tool created",
    "skill.create":         "Skill created",
    "a2a.envelope_sent":    "A2A task sent",
    "org.create":           "Organisation created",
    "org.join":             "Joined organisation",
    "workflow.run":         "Workflow started",
}


_MAX_READ_BYTES = 10 * 1024 * 1024  # 10 MB tail cap — bounded read, no full-file OOM


def _activity_path(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "chat_activity.jsonl"


def _read_tail_lines(path: Path) -> list[str]:
    """Read only the tail of a potentially large JSONL file (bounded at 10 MB)."""
    size = path.stat().st_size
    with open(path, "rb") as fh:
        if size > _MAX_READ_BYTES:
            fh.seek(-_MAX_READ_BYTES, 2)
            fh.readline()  # skip partial first line from mid-entry seek
        raw = fh.read().decode("utf-8", errors="replace")
    return raw.splitlines()


def _enrich(entry: dict[str, Any]) -> dict[str, Any]:
    out = dict(entry)
    out["panel_label"] = _PANEL_LABELS.get(entry.get("panel", ""), entry.get("panel", ""))
    out["action_label"] = _ACTION_LABELS.get(entry.get("action", ""), entry.get("action", ""))
    return out


@router.get("/activity/feed")
def get_activity_feed(
    rec: Annotated[_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(100, ge=1, le=500),
    panel: str | None = Query(None),
    chat_key: str | None = Query(None),
) -> dict[str, Any]:
    """Return recent chat-initiated activity entries, newest first."""
    path = _activity_path(rec.tenant_id)
    if not path.exists():
        return {"items": [], "total": 0}

    try:
        raw = _read_tail_lines(path)
    except OSError:
        return {"items": [], "returned": 0}

    items: list[dict[str, Any]] = []
    for line in reversed(raw):
        line = line.strip()
        if not line:
            continue
        try:
            entry = json.loads(line)
        except json.JSONDecodeError:
            continue
        if panel and entry.get("panel") != panel:
            continue
        if chat_key and entry.get("chat_key") != chat_key:
            continue
        items.append(_enrich(entry))
        if len(items) >= limit:
            break

    return {"items": items, "returned": len(items)}

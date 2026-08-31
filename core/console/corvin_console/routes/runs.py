"""Runs — Gateway-Run-Liste (ADR-0007 Phase 2.2).

Reads ``<tenant_home>/global/gateway/runs/<run_id>.json`` files and
returns a sorted projection. Pure read-only. The records carry
status (accepted/running/completed/failed/budget_exceeded), persona,
created_at, terminal_at, and the result/error if terminal.
"""
from __future__ import annotations

import json
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


@router.get("/runs")
def list_runs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = Query(default=50, ge=1, le=500),
    status: str | None = Query(default=None,
                                description="accepted|running|completed|failed|budget_exceeded"),
) -> dict[str, Any]:
    tid = rec.tenant_id
    runs_dir = _forge_paths.tenant_global_dir(tid) / "gateway" / "runs"
    items: list[dict[str, Any]] = []
    if runs_dir.exists():
        for f in runs_dir.iterdir():
            if not f.is_file() or f.suffix != ".json":
                continue
            try:
                rec_obj = json.loads(f.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            if status and rec_obj.get("status") != status:
                continue
            spec = rec_obj.get("request", {}).get("spec", {}) if isinstance(rec_obj.get("request"), dict) else {}
            items.append({
                "run_id":         rec_obj.get("run_id"),
                "status":         rec_obj.get("status"),
                "persona":        spec.get("persona"),
                "created_at":     rec_obj.get("created_at"),
                "terminal_at":    rec_obj.get("terminal_at"),
                "error":          rec_obj.get("error"),
                "duration_s":     (
                    (rec_obj.get("terminal_at") or 0) - (rec_obj.get("created_at") or 0)
                    if rec_obj.get("terminal_at") and rec_obj.get("created_at")
                    else None
                ),
            })
    items.sort(key=lambda r: r.get("created_at") or 0.0, reverse=True)
    items = items[:limit]
    return {
        "tenant_id":  tid,
        "ts":         time.time(),
        "count":      len(items),
        "runs":       items,
    }

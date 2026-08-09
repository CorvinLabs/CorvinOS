"""Vibe Engineering — read-only Context-Engineering pipeline view (ADR-0275).

Exposes the per-turn CEL traces that ``operator/context_engineering/trace.py``
persists under each session workdir (``.corvin-cel-traces.jsonl``). Read-only:
GET only, no CSRF (Cookie same-origin). Tenant isolation is structural — every
lookup is rooted at ``tenant_sessions_dir(rec.tenant_id)``, so one tenant can
never read another's traces even though the on-disk file is per session.

The reader is replicated inline (not imported from the CEL package) so this
route has no dependency on the importlib-loaded ``context_engineering`` module
being present in ``sys.modules`` at request time — the trace file is plain JSONL.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException

from .. import auth as session_auth
from ..deps import require_session

try:  # canonical path helpers (same import other routes use)
    from forge import paths as _forge_paths
except Exception:  # noqa: BLE001 — degrade to an empty view, never 500 the page
    _forge_paths = None  # type: ignore[assignment]

router = APIRouter(prefix="/vibe-engineering", tags=["console-vibe-engineering"])

_TRACE_FILE = ".corvin-cel-traces.jsonl"


def _read_recent(workdir: Path, n: int) -> list[dict]:
    """Last ``n`` traces from one session workdir, most recent first. Inline copy
    of trace.read_recent_traces — see module docstring for why it is not imported."""
    try:
        p = workdir / _TRACE_FILE
        if not p.exists():
            return []
        lines = [ln for ln in p.read_text(encoding="utf-8").splitlines() if ln.strip()]
        out: list[dict] = []
        for ln in reversed(lines[-max(0, n):]):
            try:
                out.append(json.loads(ln))
            except (json.JSONDecodeError, ValueError):
                continue
        return out
    except Exception:  # noqa: BLE001
        return []


@router.get("/traces")
async def get_vibe_traces(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    limit: int = 20,
) -> dict[str, Any]:
    """CEL pipeline traces for the authenticated tenant, grouped by session.

    Empty ``sessions`` is the legitimate P1 empty-state (flag never on, or no
    turn context-engineered yet) — the UI renders an onboarding card, not an error.
    """
    if not 1 <= limit <= 200:
        raise HTTPException(status_code=400, detail="limit must be between 1 and 200")
    if _forge_paths is None:
        return {"tenant_id": rec.tenant_id, "sessions": [], "available": False}

    try:
        sessions_root = _forge_paths.tenant_sessions_dir(rec.tenant_id)
    except Exception:  # noqa: BLE001
        return {"tenant_id": rec.tenant_id, "sessions": [], "available": False}

    root = Path(sessions_root)
    sessions: list[dict[str, Any]] = []
    if root.is_dir():
        root_resolved = root.resolve()
        for tf in sorted(root.rglob(_TRACE_FILE)):
            workdir = tf.parent
            try:  # traversal guard: never escape the tenant's sessions root
                rel = workdir.resolve().relative_to(root_resolved)
            except ValueError:
                continue
            traces = _read_recent(workdir, limit)
            if traces:
                sessions.append({
                    "session": workdir.name,
                    "path": str(rel),
                    "traces": traces,
                })
    # newest-active session first (by the most recent trace ts it holds)
    sessions.sort(key=lambda s: s["traces"][0].get("ts", 0), reverse=True)
    return {"tenant_id": rec.tenant_id, "sessions": sessions, "available": True}

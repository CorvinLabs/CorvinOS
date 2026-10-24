"""Memory panel — tenant-scoped, honest empty state.

Until 2026-10 this module served ``~/.claude/projects/<slug>/memory`` — the memory of
whoever runs Claude Code on the host — to EVERY console tenant, and let any session write
and delete in it (S-1a). The auto-memory of the host's developer is not tenant data, so no
route here touches ``~/.claude`` any more, and the write/delete routes are gone.

A per-tenant memory view does not exist yet (ADR A-10: owner-only, read-only, indexed from
the tenant's own conversation recall store). Until it does, ``GET /memory`` answers with an
explicit empty state that names the caller's tenant and carries no path. Never put sample
data here (ADR-0763).
"""
from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session

router = APIRouter()

_UNAVAILABLE = "A per-tenant memory view is not available on this build yet."


@router.get("/memory")
def memory_index(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Empty state for the caller's own tenant. No filesystem access."""
    return {
        "tenant_id": rec.tenant_id,
        "present":   False,
        "available": False,
        "reason":    _UNAVAILABLE,
        "count":     0,
        "files":     [],
    }


@router.get("/memory/{name}")
def memory_file(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """There is no per-tenant memory file store yet, so every name is absent."""
    raise HTTPException(
        status_code=http_status.HTTP_404_NOT_FOUND,
        detail=_UNAVAILABLE,
    )

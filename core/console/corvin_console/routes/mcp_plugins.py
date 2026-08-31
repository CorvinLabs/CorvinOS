"""MCP Plugin Manager — console REST endpoints (ADR-0096 M3).

Endpoints
---------
  GET    /v1/console/mcp-plugins              → list installed + active status
  POST   /v1/console/mcp-plugins/install      → install a tool (body: {source})
  POST   /v1/console/mcp-plugins/{id}/activate   → activate (body: {scope})
  POST   /v1/console/mcp-plugins/{id}/deactivate → deactivate (body: {scope})
  DELETE /v1/console/mcp-plugins/{id}         → uninstall a tool

Security invariants:
  - Owner-only access (require_session, tier check is implicit via session)
  - CSRF required on all POST / DELETE mutations
  - Audit events for every mutation (mcp_plugin.*)
  - Metadata-only audit: no source paths, no secrets, no env values
  - MCP manager unavailable → 503 (never 500 from import side-effects)
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException
from fastapi import status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from ..deps import require_csrf, require_session

import logging
_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_MCP_ROOT = _REPO / "operator" / "mcp_manager"
if str(_MCP_ROOT) not in sys.path:
    sys.path.insert(0, str(_MCP_ROOT))

try:
    from mcp_manager import catalog as _cat  # type: ignore[import-not-found]
    from mcp_manager import activate as _act  # type: ignore[import-not-found]
    from mcp_manager import installer as _ins  # type: ignore[import-not-found]
    from mcp_manager.compliance import ComplianceError  # type: ignore[import-not-found]
    _MCP_OK = True
except Exception:
    _MCP_OK = False
    _cat = None  # type: ignore[assignment]
    _act = None  # type: ignore[assignment]
    _ins = None  # type: ignore[assignment]
    ComplianceError = Exception  # type: ignore[assignment,misc]


router = APIRouter(prefix="/mcp-plugins", tags=["console-mcp-plugins"])


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _require_mcp() -> None:
    if not _MCP_OK:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="MCP Plugin Manager not available on this installation.",
        )


def _tool_view(entry: dict, active: dict) -> dict:
    tool_id = entry.get("id", "")
    active_scopes = [s for s in _act.VALID_SCOPES if tool_id in active.get(s, [])]
    return {
        "id": tool_id,
        "source": entry.get("source", ""),
        "installed_at": entry.get("installed_at"),
        "runtime": entry.get("runtime"),
        "compliance": entry.get("compliance", {}),
        "secrets": [
            {"name": s.get("name"), "required": s.get("required", False)}
            for s in (entry.get("secrets") or [])
        ],
        "active": len(active_scopes) > 0,
        "active_scopes": active_scopes,
        "sha256": entry.get("sha256"),
    }


# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------

class InstallRequest(BaseModel, extra="forbid"):
    source: str = Field(..., description="Install source: npm:<pkg>, github:<o>/<r>[@tag], pip:<pkg>, local:<path>")
    allow_unpin: bool = Field(False, description="Allow GitHub branch-head installs (not recommended)")


class ScopeRequest(BaseModel, extra="forbid"):
    scope: str = Field("user", description="Activation scope: session|project|user|tenant")


class ToolListResponse(BaseModel):
    tenant_id: str
    count: int
    tools: list[dict]
    active: dict  # raw active.json for scope-level detail


class ToolResponse(BaseModel):
    ok: bool
    tool: dict


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("", response_model=ToolListResponse)
def list_tools(
    rec: Annotated[Any, Depends(require_session)],
) -> dict:
    _require_mcp()
    tid = rec.tenant_id
    tools = _cat.list_tools(tid)
    active = _act.load_active(tid)
    return {
        "tenant_id": tid,
        "count": len(tools),
        "tools": [_tool_view(t, active) for t in tools],
        "active": active,
    }



# Scopes the console can manage — session/project require an adapter session_key
# not available in the web context; those scopes are managed via CLI or Discord.
_CONSOLE_SCOPES = frozenset({"user", "tenant"})


def _require_console_scope(scope: str) -> None:
    if scope not in _CONSOLE_SCOPES:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail=(
                f"Scope {scope!r} is not supported from the console. "
                "Use 'user' or 'tenant'. For session/project scope use the CLI or Discord commands."
            ),
        )


@router.post("/install", response_model=ToolResponse)
def install_tool(
    body: InstallRequest,
    rec: Annotated[Any, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    _require_mcp()
    tid = rec.tenant_id
    try:
        entry = _ins.install(body.source, tid, allow_unpin=body.allow_unpin)
    except (ValueError, RuntimeError, ComplianceError, OSError) as exc:
        # OSError/FileNotFoundError: `_ins.install` may shell out to npm/git/pip,
        # which can be absent under a fresh-install stripped systemd PATH — turn
        # that into a clean 400 instead of an unhandled 500.
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="internal error",
        ) from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="mcp_plugin.install",
        target_kind="mcp_tool",
        target_id=entry.get("id", ""),
    )
    active = _act.load_active(tid)
    return {"ok": True, "tool": _tool_view(entry, active)}


@router.post("/{tool_id}/activate", response_model=ToolResponse)
def activate_tool(
    tool_id: str,
    body: ScopeRequest,
    rec: Annotated[Any, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    _require_mcp()
    _require_console_scope(body.scope)
    tid = rec.tenant_id
    try:
        _act.activate(tid, tool_id, body.scope)
    except (ValueError, ComplianceError) as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="internal error",
        ) from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="mcp_plugin.activate",
        target_kind="mcp_tool",
        target_id=tool_id,
    )
    entry = _cat.get_tool(tid, tool_id)
    active = _act.load_active(tid)
    return {"ok": True, "tool": _tool_view(entry or {"id": tool_id}, active)}


@router.post("/{tool_id}/deactivate", response_model=ToolResponse)
def deactivate_tool(
    tool_id: str,
    body: ScopeRequest,
    rec: Annotated[Any, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    _require_mcp()
    _require_console_scope(body.scope)
    tid = rec.tenant_id
    try:
        _act.deactivate(tid, tool_id, body.scope)
    except ValueError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="internal error",
        ) from exc
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="mcp_plugin.deactivate",
        target_kind="mcp_tool",
        target_id=tool_id,
    )
    entry = _cat.get_tool(tid, tool_id)
    active = _act.load_active(tid)
    return {"ok": True, "tool": _tool_view(entry or {"id": tool_id}, active)}


@router.delete("/{tool_id}")
def remove_tool(
    tool_id: str,
    rec: Annotated[Any, Depends(require_session)],
    _csrf: Annotated[None, Depends(require_csrf)],
) -> dict:
    _require_mcp()
    tid = rec.tenant_id
    # Only deactivate console-manageable scopes (user/tenant).
    # Session/project-scope deactivation requires adapter context; those files
    # are cleaned up by session_reset.py and project teardown respectively.
    for scope in _CONSOLE_SCOPES:
        try:
            _act.deactivate(tid, tool_id, scope)
        except Exception:  # noqa: BLE001
            pass
    removed = _ins.uninstall(tool_id, tid)
    if not removed:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"Tool {tool_id!r} not found.",
        )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="mcp_plugin.remove",
        target_kind="mcp_tool",
        target_id=tool_id,
    )
    return {"ok": True, "tool_id": tool_id}

"""Custom Connector Registry (ADR-0124 M2).

Operators register custom MCP connectors (stdio / SSE / HTTP) without
modifying source code. Connectors are picked up by the persona MCP-config
builder just like built-in ones.

Routes:
  GET    /connectors/custom                    list all custom connectors
  PUT    /connectors/custom/{connector_id}     register or update
  DELETE /connectors/custom/{connector_id}     remove
"""
from __future__ import annotations

import json
import os
import re
import tempfile
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

_VALID_TRANSPORTS = frozenset({"stdio", "sse", "http"})
_VALID_LOCALITIES = frozenset({"local", "eu_cloud", "us_cloud"})
_VALID_EGRESS = frozenset({"none", "restricted", "full"})
_CONNECTOR_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


# ── Storage ───────────────────────────────────────────────────────────────────

def _connectors_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "connectors" / "custom"


def _connector_path(tid: str, connector_id: str) -> Path:
    return _connectors_dir(tid) / f"{connector_id}.json"


def _load_connector(tid: str, connector_id: str) -> dict[str, Any] | None:
    p = _connector_path(tid, connector_id)
    if not p.exists():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _list_connectors(tid: str) -> list[dict[str, Any]]:
    d = _connectors_dir(tid)
    if not d.is_dir():
        return []
    results = []
    for p in sorted(d.glob("*.json")):
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                results.append(data)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _write_connector(tid: str, connector_id: str, data: dict[str, Any]) -> None:
    p = _connector_path(tid, connector_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(data, indent=2, ensure_ascii=False))
        os.replace(tmp, str(p))
        os.chmod(str(p), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Models ────────────────────────────────────────────────────────────────────

class CustomConnectorRequest(BaseModel):
    display_name: str = Field(..., min_length=1, max_length=100)
    transport: str = Field(..., description="stdio | sse | http")
    command: list[str] | None = Field(None, description="Command + args for stdio transport")
    url: str | None = Field(None, description="URL for sse/http transport")
    env_secrets: list[str] = Field(
        default_factory=list,
        description="Vault env-var names injected at spawn time",
    )
    capabilities: list[str] = Field(default_factory=list)
    locality: str = Field("local")
    network_egress: str = Field("none")
    description: str = Field("", max_length=500)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/connectors/custom")
def list_custom_connectors(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    connectors = _list_connectors(rec.tenant_id)
    return {
        "tenant_id": rec.tenant_id,
        "count": len(connectors),
        "connectors": connectors,
    }


@router.put("/connectors/custom/{connector_id}")
def register_custom_connector(
    connector_id: str,
    body: CustomConnectorRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _CONNECTOR_ID_RE.match(connector_id):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "connector_id must be lowercase alphanumeric with _ or -",
        )
    if body.transport not in _VALID_TRANSPORTS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"transport must be one of {sorted(_VALID_TRANSPORTS)}",
        )
    if body.transport == "stdio" and not body.command:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "command is required for stdio transport",
        )
    if body.transport in ("sse", "http") and not body.url:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"url is required for {body.transport} transport",
        )
    if body.locality not in _VALID_LOCALITIES:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"locality must be one of {sorted(_VALID_LOCALITIES)}",
        )
    if body.network_egress not in _VALID_EGRESS:
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            f"network_egress must be one of {sorted(_VALID_EGRESS)}",
        )

    existing = _load_connector(rec.tenant_id, connector_id)
    is_update = existing is not None

    manifest: dict[str, Any] = {
        "connector_id": connector_id,
        "display_name": body.display_name,
        "transport": body.transport,
        "description": body.description,
        "env_secrets": body.env_secrets,
        "capabilities": body.capabilities,
        "locality": body.locality,
        "network_egress": body.network_egress,
        "created_at": existing.get("created_at", time.time()) if existing else time.time(),
        "updated_at": time.time(),
        "kind": "custom_connector",
    }
    if body.command is not None:
        manifest["command"] = body.command
    if body.url is not None:
        manifest["url"] = body.url

    try:
        _write_connector(rec.tenant_id, connector_id, manifest)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="connector.custom_updated" if is_update else "connector.custom_registered",
        target_kind="custom_connector",
        target_id=connector_id,
    )
    return {"ok": True, "connector_id": connector_id, "updated": is_update}


@router.delete("/connectors/custom/{connector_id}")
def remove_custom_connector(
    connector_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    p = _connector_path(rec.tenant_id, connector_id)
    if not p.exists():
        raise HTTPException(
            http_status.HTTP_404_NOT_FOUND,
            f"connector {connector_id!r} not found",
        )
    try:
        p.unlink()
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="connector.custom_removed",
        target_kind="custom_connector",
        target_id=connector_id,
    )
    return {"ok": True, "connector_id": connector_id}

"""Manual Tool Creation (ADR-0124 M5b).

Operators author forge tools directly in the console. Tools are stored
in the user-scope forge directory and picked up by the existing tool
listing endpoint. A preview endpoint runs the tool with sample input
inside a subprocess (bwrap sandbox in production; plain subprocess in dev).

Routes:
  GET    /tools/manual                  list manually created tools
  POST   /tools/manual                  create a new tool
  PUT    /tools/manual/{name}           update tool
  DELETE /tools/manual/{name}           remove tool
  POST   /tools/preview                 dry-run with sample input
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import sys
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

_TOOL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{0,63}$")
_PREVIEW_TIMEOUT = 10  # seconds


# ── Storage ───────────────────────────────────────────────────────────────────

def _manual_tools_dir(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "forge" / "manual"


def _tool_dir(tid: str, name: str) -> Path:
    return _manual_tools_dir(tid) / name


def _list_manual_tools(tid: str) -> list[dict[str, Any]]:
    root = _manual_tools_dir(tid)
    if not root.is_dir():
        return []
    results = []
    for entry in sorted(root.iterdir()):
        if not entry.is_dir():
            continue
        meta_path = entry / "meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            if isinstance(meta, dict):
                results.append(meta)
        except (OSError, json.JSONDecodeError):
            pass
    return results


def _load_tool_meta(tid: str, name: str) -> dict[str, Any] | None:
    meta_path = _tool_dir(tid, name) / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


def _write_tool(
    tid: str,
    name: str,
    description: str,
    impl: str,
    input_schema: dict[str, Any],
    existing_meta: dict[str, Any] | None,
) -> None:
    tool_dir = _tool_dir(tid, name)
    tool_dir.mkdir(parents=True, exist_ok=True)

    sha = hashlib.sha256(impl.encode()).hexdigest()
    now = time.time()
    meta: dict[str, Any] = {
        "name": name,
        "description": description,
        "input_schema": input_schema,
        "origin": "manual",
        "sha256": sha,
        "runtime": "python",
        "scope": "user",
        "created_at": existing_meta.get("created_at", now) if existing_meta else now,
        "updated_at": now,
    }

    # Write impl.py (mode 0o600 — contains operator code, not world-readable)
    impl_path = tool_dir / "impl.py"
    fd, tmp = tempfile.mkstemp(dir=str(tool_dir), suffix=".py")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(impl)
        os.replace(tmp, str(impl_path))
        os.chmod(str(impl_path), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise

    # Write meta.json
    meta_path = tool_dir / "meta.json"
    fd, tmp = tempfile.mkstemp(dir=str(tool_dir))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(json.dumps(meta, indent=2, ensure_ascii=False))
        os.replace(tmp, str(meta_path))
        os.chmod(str(meta_path), 0o600)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


# ── Models ────────────────────────────────────────────────────────────────────

class ToolCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    description: str = Field(..., min_length=1, max_length=500)
    impl: str = Field(..., min_length=1, max_length=65_536, description="Python implementation")
    input_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema for inputs")
    model_config = {"extra": "forbid"}


class ToolUpdateRequest(BaseModel):
    description: str | None = Field(None, max_length=500)
    impl: str | None = Field(None, min_length=1, max_length=65_536)
    input_schema: dict[str, Any] | None = None
    model_config = {"extra": "forbid"}


class ToolPreviewRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=64)
    inputs: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/tools/manual")
def list_manual_tools(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    tools = _list_manual_tools(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(tools), "tools": tools}


@router.post("/tools/manual")
def create_manual_tool(
    body: ToolCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _TOOL_NAME_RE.match(body.name):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "name must be lowercase alphanumeric with _ or . (max 64 chars)",
        )
    if _load_tool_meta(rec.tenant_id, body.name) is not None:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"tool {body.name!r} already exists — use PUT to update",
        )

    try:
        _write_tool(rec.tenant_id, body.name, body.description, body.impl, body.input_schema, None)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="tool.manual_created",
        target_kind="manual_tool",
        target_id=body.name,
    )
    return {"ok": True, "name": body.name}


@router.put("/tools/manual/{name}")
def update_manual_tool(
    name: str,
    body: ToolUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _TOOL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid tool name")
    existing = _load_tool_meta(rec.tenant_id, name)
    if existing is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"tool {name!r} not found")

    description = body.description if body.description is not None else existing.get("description", "")
    impl_path = _tool_dir(rec.tenant_id, name) / "impl.py"
    impl = body.impl if body.impl is not None else (
        impl_path.read_text(encoding="utf-8") if impl_path.exists() else ""
    )
    input_schema = body.input_schema if body.input_schema is not None else existing.get("input_schema", {})

    try:
        _write_tool(rec.tenant_id, name, description, impl, input_schema, existing)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="tool.manual_updated",
        target_kind="manual_tool",
        target_id=name,
    )
    return {"ok": True, "name": name}


@router.delete("/tools/manual/{name}")
def delete_manual_tool(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    import shutil

    if not _TOOL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid tool name")
    tool_dir = _tool_dir(rec.tenant_id, name)
    if not tool_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"tool {name!r} not found")

    try:
        shutil.rmtree(tool_dir)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="tool.manual_deleted",
        target_kind="manual_tool",
        target_id=name,
    )
    return {"ok": True, "name": name}


@router.post("/tools/preview")
def preview_tool(
    body: ToolPreviewRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Run the tool impl with sample inputs and return stdout/stderr.

    SECURITY: Executes operator-supplied Python in a plain subprocess (no bwrap).
    Access is gated by CSRF + session — only authenticated console operators can call
    this endpoint. Do NOT expose it to untrusted users.
    """
    meta = _load_tool_meta(rec.tenant_id, body.name)
    if meta is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"tool {body.name!r} not found")

    impl_path = _tool_dir(rec.tenant_id, body.name) / "impl.py"
    if not impl_path.exists():
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "impl.py not found")

    # Wrap the impl so inputs arrive as the variable `inputs`.
    # json.dumps(body.inputs) produces a JSON string; repr() embeds it safely
    # as a Python string literal in the generated code.
    _inputs_json = json.dumps(body.inputs)
    runner_code = f"""
import json as _json

import logging
_log = logging.getLogger(__name__)
inputs = _json.loads({_inputs_json!r})
""" + impl_path.read_text(encoding="utf-8")

    try:
        result = subprocess.run(
            [sys.executable, "-c", runner_code],
            capture_output=True,
            text=True,
            timeout=_PREVIEW_TIMEOUT,
        )
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="tool.preview_run",
            target_kind="manual_tool",
            target_id=body.name,
        )
        return {
            "ok": True,
            "name": body.name,
            "exit_code": result.returncode,
            "stdout": result.stdout[:8_192],
            "stderr": result.stderr[:2_048],
        }
    except subprocess.TimeoutExpired:
        raise HTTPException(
            http_status.HTTP_408_REQUEST_TIMEOUT,
            f"preview timed out after {_PREVIEW_TIMEOUT}s",
        )
    except OSError as exc:
        _log.error("tool operation failed", exc_info=True)
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "internal error") from exc

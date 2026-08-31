"""Forge tools — multi-scope aggregation.

Forge keeps a per-scope ``registry.json`` that maps tool-name → entry
(name, description, input_schema, runtime, impl_path, scope, sha256,
call_count, promoted, meta). We walk every scope under the tenant tree
plus the user-scope, project-scope (if accessible) and aggregate.

Scopes (read order — later ones override earlier on name collision,
so the user-scope wins for displayed entries):

  1. session/<key>/forge/registry.json   (per-session)
  2. forge/registry.json                  (tenant-root, "session-default")
  3. global/forge/registry.json           (user scope)

Phase C ships LIST + DETAIL. The DETAIL endpoint additionally reads
the impl_path file (if present) so the operator can inspect the body.
Run-history will land in Phase E next to mutation hooks.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


def _read_registry(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _registry_paths(tid: str) -> list[tuple[str, Path]]:
    """Return ``(scope_label, registry_path)`` pairs in read-order."""
    pairs: list[tuple[str, Path]] = []
    sessions_dir = _forge_paths.tenant_sessions_dir(tid)
    if sessions_dir.exists():
        for entry in sorted(sessions_dir.iterdir()):
            reg = entry / "forge" / "registry.json"
            if reg.exists():
                pairs.append((f"session:{entry.name}", reg))
    tenant_root = _forge_paths.tenant_home(tid) / "forge" / "registry.json"
    if tenant_root.exists():
        pairs.append(("session-default", tenant_root))
    user_global = _forge_paths.tenant_global_dir(tid) / "forge" / "registry.json"
    if user_global.exists():
        pairs.append(("user", user_global))
    return pairs


def _project(entry: dict[str, Any], scope_label: str) -> dict[str, Any]:
    schema = entry.get("input_schema") or {}
    props = schema.get("properties", {}) if isinstance(schema, dict) else {}
    required = schema.get("required", []) if isinstance(schema, dict) else []
    return {
        "name":         entry.get("name"),
        "description":  entry.get("description") or "",
        "scope":        entry.get("scope") or "",
        "scope_source": scope_label,
        "runtime":      entry.get("runtime") or "python",
        "promoted":     bool(entry.get("promoted")),
        "call_count":   entry.get("call_count", 0),
        "created_at":   entry.get("created_at"),
        "sha256":       entry.get("sha256") or "",
        "param_count":  len(props),
        "param_names":  list(props.keys())[:8],
        "required":     required,
        "impl_path":    entry.get("impl_path"),
    }


@router.get("/tools")
def list_tools(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Aggregate every forge tool reachable from the owner's tenant."""
    tid = rec.tenant_id
    by_name: dict[str, dict[str, Any]] = {}
    for scope_label, reg in _registry_paths(tid):
        for name, entry in _read_registry(reg).items():
            if not isinstance(entry, dict):
                continue
            # Last scope wins: user > tenant-root > session
            by_name[name] = _project(entry, scope_label)
    items = sorted(by_name.values(), key=lambda r: r.get("name") or "")
    return {
        "tenant_id":  tid,
        "ts":         time.time(),
        "count":      len(items),
        "tools":      items,
    }


@router.get("/tools/{name}")
def tool_detail(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    tid = rec.tenant_id
    found_entry: dict[str, Any] | None = None
    found_scope: str | None = None
    found_registry: Path | None = None
    # Walk in read-order — last write wins so we get the user-most copy.
    for scope_label, reg in _registry_paths(tid):
        data = _read_registry(reg)
        if name in data and isinstance(data[name], dict):
            found_entry = data[name]
            found_scope = scope_label
            found_registry = reg

    if found_entry is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"tool {name!r} not found in any registry",
        )

    impl_text: str | None = None
    impl_path = found_entry.get("impl_path")
    if impl_path and isinstance(impl_path, str):
        try:
            p = Path(impl_path)
            # Cap impl preview at 64 KiB to keep the response cheap.
            if p.exists() and p.is_file() and p.stat().st_size <= 64 * 1024:
                impl_text = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            impl_text = None

    return {
        "name":          name,
        "scope_source":  found_scope,
        "registry_path": str(found_registry) if found_registry else None,
        "entry":         found_entry,
        "impl_preview":  impl_text,
    }

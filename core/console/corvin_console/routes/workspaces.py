"""Workspaces — read-only filesystem browser over the tenant tree.

Walks the directory structure under ``<corvin_home>/tenants/<tid>/``,
returning per-entry size + mtime + child-count. Bounded depth to keep
the response cheap (depth=3 by default, max 5).

Phase F ships the LIST endpoint. A future "preview file" endpoint
(Phase G) would let the operator inspect individual files — for now
the existing /memory/* and /tools/* endpoints already cover the
file-content reads inside their respective subtrees.

Path-Gate semantics are honoured implicitly: this endpoint is
READ-ONLY and never invokes Write/Edit. The path-gate hook only
fires on tool-calls; this is a Python file-stat which the path-gate
doesn't see.
"""
from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()

_MAX_DEPTH = 5
_MAX_CHILDREN_PER_DIR = 200

# Pre-marked subtrees the path-gate hook protects. Surfaced in the
# UI so the operator sees at a glance which entries are write-locked.
_PROTECTED_HINTS = {"forge", "skill-forge", "audit.jsonl", "policy.json", "compute"}


def _is_protected(rel_path: str) -> bool:
    parts = rel_path.split("/")
    return any(p in _PROTECTED_HINTS for p in parts)


def _entry_size_and_mtime(path: Path) -> tuple[int, float, int]:
    """Return (size_bytes, newest_mtime, file_count) for a directory.

    Bounded — does NOT recurse infinitely; uses os.walk which is
    bounded by the OS-side dirent enumeration. Per-call cost is
    linear in tree size, but typical session/forge subtrees stay
    well under 1000 entries.
    """
    total = 0
    newest = 0.0
    files = 0
    if path.is_file():
        try:
            st = path.stat()
            return st.st_size, st.st_mtime, 1
        except OSError:
            return 0, 0.0, 0
    try:
        for root, _dirs, fs in os.walk(path):
            for f in fs:
                fp = Path(root) / f
                try:
                    st = fp.stat()
                    total += st.st_size
                    files += 1
                    if st.st_mtime > newest:
                        newest = st.st_mtime
                except OSError:
                    continue
    except OSError:
        pass
    return total, newest, files


def _list_dir(path: Path, *, rel: str, depth: int, max_depth: int) -> list[dict[str, Any]]:
    """List immediate children of *path*, with one-level recursion
    if depth < max_depth."""
    if not path.exists() or not path.is_dir():
        return []
    items: list[dict[str, Any]] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name))
    except OSError:
        return []
    for entry in children[:_MAX_CHILDREN_PER_DIR]:
        is_dir = entry.is_dir()
        size_b, mtime, file_count = _entry_size_and_mtime(entry)
        rel_path = f"{rel}/{entry.name}" if rel else entry.name
        item: dict[str, Any] = {
            "name":       entry.name,
            "rel_path":   rel_path,
            "is_dir":     is_dir,
            "size_bytes": size_b,
            "mtime":      mtime if mtime > 0 else None,
            "file_count": file_count if is_dir else None,
            "protected":  _is_protected(rel_path),
        }
        if is_dir and depth + 1 < max_depth:
            item["children"] = _list_dir(
                entry, rel=rel_path, depth=depth + 1, max_depth=max_depth,
            )
        items.append(item)
    return items


@router.get("/workspaces")
def workspaces(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    depth: int = Query(default=2, ge=1, le=_MAX_DEPTH,
                        description="recursion depth from tenant root"),
    sub: str = Query(default="",
                      description="optional sub-path inside the tenant tree"),
) -> dict[str, Any]:
    """Return the directory tree under the tenant root."""
    tid = rec.tenant_id
    root = _forge_paths.tenant_home(tid)
    if not root.exists():
        return {
            "tenant_id":  tid,
            "root":       str(root),
            "present":    False,
            "ts":         time.time(),
            "children":   [],
        }
    target = root
    if sub:
        # Path-traversal guard: resolve and ensure it's still within root.
        candidate = (root / sub).resolve()
        try:
            candidate.relative_to(root.resolve())
        except ValueError:
            raise HTTPException(
                status_code=http_status.HTTP_400_BAD_REQUEST,
                detail="sub-path escapes tenant root",
            )
        target = candidate
        if not target.exists():
            raise HTTPException(
                status_code=http_status.HTTP_404_NOT_FOUND,
                detail=f"sub-path {sub!r} not found",
            )
    children = _list_dir(target, rel=sub or "", depth=0, max_depth=depth)
    total_size, total_newest, total_files = _entry_size_and_mtime(target)
    return {
        "tenant_id":   tid,
        "root":        str(root),
        "sub":         sub,
        "present":     True,
        "ts":          time.time(),
        "depth":       depth,
        "total_size":  total_size,
        "total_files": total_files,
        "children":    children,
    }

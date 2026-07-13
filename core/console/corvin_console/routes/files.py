"""File Hub — browse, download, upload, delete files in the tenant tree.

Extends the read-only workspaces endpoint with mutation endpoints:

  GET  /files/tree      — directory listing with access levels
  GET  /files/content   — file content preview (text / image)
  GET  /files/download  — stream file as attachment
  POST /files/upload    — multipart upload to a directory
  DELETE /files         — delete file or empty directory
  POST /files/mkdir     — create directory

Access levels:
  none  — audit.jsonl, policy.json, secrets.json, recall.db, etc.: blocked
  read  — forge/, skill-forge/: list + download only
  full  — everything else (tenants/<tid>/files/ is the cloud zone)
"""
from __future__ import annotations

import mimetypes
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter(prefix="/files")

_MAX_DEPTH = 5
_MAX_CHILDREN = 200
_MAX_UPLOAD_BYTES = 50 * 1024 * 1024   # 50 MB per file
_MAX_TEXT_PREVIEW = 512 * 1024          # 512 KB
_MAX_IMAGE_INLINE = 2 * 1024 * 1024    # 2 MB

# Per-tenant disk quota — applies to the writable files/ subtree only
_TENANT_QUOTA_BYTES = 1 * 1024 * 1024 * 1024   # 1 GB


def _tenant_used_bytes(root: Path) -> int:
    """Return total bytes used under <tenant>/files/ (quota zone only)."""
    files_dir = root / "files"
    if not files_dir.exists():
        return 0
    return sum(p.stat().st_size for p in files_dir.rglob("*") if p.is_file())

_NO_ACCESS: frozenset[str] = frozenset({
    "audit.jsonl", "policy.json", "secrets.json", "recall.db",
    ".env", "instance_id.json", "vault",
})

_READ_ONLY: frozenset[str] = frozenset({"forge", "skill-forge"})

_TEXT_EXTS: frozenset[str] = frozenset({
    ".txt", ".md", ".json", ".yaml", ".yml", ".toml", ".py", ".js", ".ts",
    ".tsx", ".jsx", ".sh", ".cfg", ".ini", ".log", ".csv", ".html",
    ".css", ".xml", ".rst", ".env",
})

_IMAGE_EXTS: frozenset[str] = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp", ".ico",
})


def _access(rel_path: str) -> Literal["full", "read", "none"]:
    parts = Path(rel_path).parts if rel_path else ()
    # NO_ACCESS must WIN over READ_ONLY regardless of component order. The
    # hash-chained audit log lives at global/forge/audit.jsonl, so the old
    # first-match loop hit "forge" (→ read) and never reached "audit.jsonl"
    # (→ none), making the GDPR L16 audit chain + any secrets.json/.env/vault
    # under a forge/ or skill-forge/ subtree downloadable via /files/download by
    # any authenticated session (round-2 HIGH). Scan ALL parts for a NO_ACCESS
    # component first, THEN decide read/full.
    if any(part in _NO_ACCESS for part in parts):
        return "none"
    if any(part in _READ_ONLY for part in parts):
        return "read"
    return "full"


def _resolve_safe(root: Path, rel: str) -> Path:
    """Resolve rel within root, raising 400 on traversal attempt."""
    if not rel:
        return root
    try:
        candidate = (root / rel).resolve()
        candidate.relative_to(root.resolve())
    except ValueError:
        # Also catches Path.resolve() itself raising ValueError for an
        # embedded null byte in rel — previously that ValueError escaped
        # this handler (resolve() ran before the try) as an unhandled 500.
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="path escapes tenant root",
        )
    return candidate


def _entry_meta(path: Path) -> tuple[int, float]:
    try:
        st = path.stat()
        return st.st_size, st.st_mtime
    except OSError:
        return 0, 0.0


def _list_dir(path: Path, rel: str, depth: int, max_depth: int) -> list[dict[str, Any]]:
    if not path.is_dir():
        return []
    try:
        entries = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    result: list[dict[str, Any]] = []
    for entry in entries[:_MAX_CHILDREN]:
        entry_rel = f"{rel}/{entry.name}".lstrip("/")
        acc = _access(entry_rel)
        if acc == "none":
            continue
        is_dir = entry.is_dir()
        size, mtime = _entry_meta(entry)
        item: dict[str, Any] = {
            "name":       entry.name,
            "rel_path":   entry_rel,
            "is_dir":     is_dir,
            "size_bytes": size,
            "mtime":      mtime if mtime > 0 else None,
            "access":     acc,
        }
        if is_dir and depth + 1 < max_depth:
            item["children"] = _list_dir(entry, entry_rel, depth + 1, max_depth)
        result.append(item)
    return result


# ── Tree ────────────────────────────────────────────────────────────────

@router.get("/tree")
def files_tree(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    path: str = Query(default="", description="relative path inside tenant root"),
    depth: int = Query(default=2, ge=1, le=_MAX_DEPTH),
) -> dict[str, Any]:
    """Return directory tree with per-entry access levels."""
    root = _forge_paths.tenant_home(rec.tenant_id)
    # Ensure the cloud zone exists on first access.
    (root / "files").mkdir(parents=True, exist_ok=True)

    target = _resolve_safe(root, path)
    if not target.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="path not found")

    acc = _access(path)
    if acc == "none":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="path is protected")

    children = _list_dir(target, path, 0, depth)
    size, mtime = _entry_meta(target)
    used = _tenant_used_bytes(root)
    return {
        "tenant_id":    rec.tenant_id,
        "root":         str(root),
        "path":         path,
        "is_dir":       target.is_dir(),
        "size_bytes":   size,
        "mtime":        mtime if mtime > 0 else None,
        "access":       acc,
        "ts":           time.time(),
        "children":     children,
        "quota": {
            "used_bytes":  used,
            "limit_bytes": _TENANT_QUOTA_BYTES,
            "used_pct":    round(used / _TENANT_QUOTA_BYTES * 100, 1),
        },
    }


# ── Content preview ─────────────────────────────────────────────────────

@router.get("/content")
def files_content(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    path: str = Query(..., description="relative path to file"),
) -> dict[str, Any]:
    """Return file content for inline preview."""
    import base64

    root = _forge_paths.tenant_home(rec.tenant_id)
    target = _resolve_safe(root, path)

    acc = _access(path)
    if acc == "none":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="path is protected")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="file not found")

    size, mtime = _entry_meta(target)
    ext = target.suffix.lower()
    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"

    if ext in _IMAGE_EXTS and size <= _MAX_IMAGE_INLINE:
        data = target.read_bytes()
        return {
            "path": path, "name": target.name, "size_bytes": size,
            "mtime": mtime, "mime": mime, "kind": "image",
            "content_b64": base64.b64encode(data).decode(),
        }

    if ext in _TEXT_EXTS or mime.startswith("text/"):
        try:
            text = target.read_text(encoding="utf-8", errors="replace")
            raw = text.encode("utf-8")
            truncated = len(raw) > _MAX_TEXT_PREVIEW
            if truncated:
                text = raw[:_MAX_TEXT_PREVIEW].decode("utf-8", errors="replace").rsplit("\n", 1)[0]
                text += "\n…(truncated)"
            return {
                "path": path, "name": target.name, "size_bytes": size,
                "mtime": mtime, "mime": mime, "kind": "text",
                "content": text, "truncated": truncated,
            }
        except OSError:
            pass

    return {
        "path": path, "name": target.name, "size_bytes": size,
        "mtime": mtime, "mime": mime, "kind": "binary", "content": None,
    }


# ── Download ────────────────────────────────────────────────────────────

@router.get("/download")
def files_download(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    path: str = Query(..., description="relative path to file"),
) -> FileResponse:
    """Stream file as a download attachment."""
    root = _forge_paths.tenant_home(rec.tenant_id)
    target = _resolve_safe(root, path)

    acc = _access(path)
    if acc == "none":
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="path is protected")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="file not found")

    mime = mimetypes.guess_type(str(target))[0] or "application/octet-stream"
    return FileResponse(
        path=str(target),
        filename=target.name,
        media_type=mime,
    )


# ── Upload ──────────────────────────────────────────────────────────────

@router.post("/upload")
async def files_upload(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    dir: str = Query(default="files", description="destination directory (relative path)"),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    """Upload a file to the specified directory. Max 50 MB."""
    root = _forge_paths.tenant_home(rec.tenant_id)
    dest_dir = _resolve_safe(root, dir)

    acc = _access(dir)
    if acc in ("none", "read"):
        raise HTTPException(
            status_code=http_status.HTTP_403_FORBIDDEN,
            detail="destination directory is read-only or protected",
        )

    dest_dir.mkdir(parents=True, exist_ok=True)

    safe_name = Path(file.filename or "upload").name
    if not safe_name or safe_name.startswith("."):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid filename")

    # Quota pre-check: reject early if already at limit
    used = _tenant_used_bytes(root)
    if used >= _TENANT_QUOTA_BYTES:
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"storage quota exceeded ({_TENANT_QUOTA_BYTES // 1024 // 1024 // 1024} GB limit)",
        )

    dest = dest_dir / safe_name
    written = 0
    try:
        with dest.open("wb") as out:
            while True:
                chunk = await file.read(65536)
                if not chunk:
                    break
                written += len(chunk)
                if written > _MAX_UPLOAD_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"file exceeds {_MAX_UPLOAD_BYTES // 1024 // 1024} MB limit",
                    )
                # Live quota check: abort mid-stream if total would exceed limit
                if used + written > _TENANT_QUOTA_BYTES:
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
                        detail=f"storage quota exceeded ({_TENANT_QUOTA_BYTES // 1024 // 1024 // 1024} GB limit)",
                    )
                out.write(chunk)
    finally:
        await file.close()

    rel = str(dest.relative_to(root))
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="files.upload",
        target_kind="file",
        target_id=rel,
    )
    return {"ok": True, "path": rel, "name": safe_name, "size_bytes": written}


# ── Delete ──────────────────────────────────────────────────────────────

@router.delete("")
def files_delete(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    path: str = Query(..., description="relative path to delete"),
) -> dict[str, Any]:
    """Delete a file or an empty directory."""
    root = _forge_paths.tenant_home(rec.tenant_id)
    target = _resolve_safe(root, path)

    acc = _access(path)
    if acc in ("none", "read"):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="path is protected")
    if target == root:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="cannot delete tenant root")
    if not target.exists():
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="path not found")

    if target.is_file():
        target.unlink()
    elif target.is_dir():
        try:
            target.rmdir()
        except OSError:
            raise HTTPException(
                status_code=http_status.HTTP_409_CONFLICT,
                detail="directory is not empty — delete contents first",
            )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="files.delete",
        target_kind="file",
        target_id=path,
    )
    return {"ok": True, "path": path, "deleted": True}


# ── Mkdir ───────────────────────────────────────────────────────────────

class _MkdirBody(BaseModel):
    path: str = Field(..., max_length=512)


@router.post("/mkdir")
def files_mkdir(
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    body: _MkdirBody,
) -> dict[str, Any]:
    """Create a directory (parents created automatically)."""
    root = _forge_paths.tenant_home(rec.tenant_id)
    target = _resolve_safe(root, body.path)

    acc = _access(body.path)
    if acc in ("none", "read"):
        raise HTTPException(status_code=http_status.HTTP_403_FORBIDDEN, detail="path is protected")

    target.mkdir(parents=True, exist_ok=True)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="files.mkdir",
        target_kind="directory",
        target_id=body.path,
    )
    return {"ok": True, "path": body.path}

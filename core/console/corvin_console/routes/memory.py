"""Auto-Memory browser — read-only view of the persistent memory store.

Memory lives at ``~/.claude/projects/<project-slug>/memory/``. The
slug is derived from the project's absolute path with ``/`` → ``-``.
For the active workdir we resolve the slug deterministically; if the
auto-memory dir doesn't exist yet (no persisted state), every endpoint
returns a clean empty shape rather than 404'ing.

Phase C ships LIST + GET. Phase E will add WRITE/DELETE behind the
re-auth gate.
"""
from __future__ import annotations

import re
import time
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Body, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session, verify_reauth

import logging
_log = logging.getLogger(__name__)


router = APIRouter()


def _memory_dir() -> Path:
    """Derive the Claude auto-memory directory for the current CorvinOS installation.

    Walks up from the console package's directory to find the repo root (marked by
    the presence of a CLAUDE.md or .git directory), then converts the absolute repo
    path to the slug format that Claude Code uses for memory directories.
    """
    # Find the repo root by walking up from this file's location.
    candidate = Path(__file__).resolve()
    for parent in candidate.parents:
        if (parent / "CLAUDE.md").exists() or (parent / ".git").is_dir():
            repo_root = parent
            break
    else:
        # Fallback: use cwd as the project path
        repo_root = Path.cwd()

    # Claude Code uses str(path).replace("/", "-") as the project slug, with a
    # leading "-" because the absolute path starts with "/".
    slug = str(repo_root).replace("/", "-")
    return Path.home() / ".claude" / "projects" / slug / "memory"


_MEMORY_DIR = _memory_dir()

# Memory file naming convention: <type>_<slug>.md where type is one of
# user / feedback / project / reference. MEMORY.md is the index.
_TYPE_RE = re.compile(r"^(user|feedback|project|reference)_")
_FILENAME_OK = re.compile(r"^[A-Za-z0-9._-]+\.md$")


def _classify(filename: str) -> str:
    if filename == "MEMORY.md":
        return "index"
    m = _TYPE_RE.match(filename)
    return m.group(1) if m else "other"


def _safe_path(name: str) -> Path:
    """Reject path-traversal; only allow plain *.md filenames."""
    if not _FILENAME_OK.match(name):
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="invalid memory filename",
        )
    return _MEMORY_DIR / name


def _read_summary(path: Path) -> dict[str, Any]:
    try:
        st = path.stat()
    except OSError:
        return {"present": False}
    summary: dict[str, Any] = {
        "present": True,
        "size_bytes": st.st_size,
        "modified": st.st_mtime,
    }
    # Read first 4 KiB and parse minimal frontmatter for description.
    try:
        head = path.read_text(encoding="utf-8", errors="replace")[:4096]
    except OSError:
        head = ""
    desc: str | None = None
    if head.startswith("---"):
        end = head.find("\n---", 3)
        if end > 0:
            for line in head[3:end].splitlines():
                if line.startswith("description:"):
                    desc = line.split(":", 1)[1].strip()
                    break
    summary["description"] = desc
    return summary


@router.get("/memory")
def memory_index(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List all memory files with type, size, mtime, description."""
    if not _MEMORY_DIR.exists():
        return {
            "tenant_id":  rec.tenant_id,
            "memory_dir": str(_MEMORY_DIR),
            "present":    False,
            "count":      0,
            "files":      [],
        }
    items: list[dict[str, Any]] = []
    for entry in sorted(_MEMORY_DIR.iterdir()):
        if entry.suffix != ".md" or not entry.is_file():
            continue
        meta = _read_summary(entry)
        items.append({
            "name":        entry.name,
            "type":        _classify(entry.name),
            "size_bytes":  meta.get("size_bytes"),
            "modified":    meta.get("modified"),
            "description": meta.get("description"),
        })
    # Index first, then by type, then alpha.
    type_rank = {"index": 0, "user": 1, "feedback": 2, "project": 3,
                 "reference": 4, "other": 5}
    items.sort(key=lambda r: (type_rank.get(r["type"], 9), r["name"]))
    return {
        "tenant_id":  rec.tenant_id,
        "memory_dir": str(_MEMORY_DIR),
        "present":    True,
        "ts":         time.time(),
        "count":      len(items),
        "files":      items,
    }


@router.get("/memory/{name}")
def memory_file(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the full content of a single memory file."""
    path = _safe_path(name)
    if not path.exists():
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"memory file {name!r} not found",
        )
    try:
        body = path.read_text(encoding="utf-8", errors="replace")
        st = path.stat()
    except OSError as e:
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="read failed",
        )
    return {
        "name":        name,
        "type":        _classify(name),
        "path":        str(path),
        "size_bytes":  st.st_size,
        "modified":    st.st_mtime,
        "body":        body,
    }


# ── Mutations (Phase E) ────────────────────────────────────────────────


class MemoryWriteRequest(BaseModel):
    body:           str  = Field(..., description="full markdown body")
    re_auth_token:  str | None = None
    model_config = {"extra": "forbid"}


_MAX_BODY_BYTES = 256 * 1024   # 256 KiB cap; same order as memory file convention


@router.put("/memory/{name}")
def memory_write(
    name: str,
    body: MemoryWriteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Create-or-replace a memory file under the auto-memory dir.

    Filename must satisfy ``[A-Za-z0-9._-]+\\.md`` (path-traversal
    rejected). Body capped at 256 KiB. Re-auth token verified
    against the session's bearer-token fingerprint.
    """
    path = _safe_path(name)

    if not verify_reauth(rec, body.re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.write",
            target_kind="memory_file",
            target_id=name,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    if len(body.body.encode("utf-8")) > _MAX_BODY_BYTES:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.write",
            target_kind="memory_file",
            target_id=name,
            reason="body-too-large",
        )
        raise HTTPException(
            status_code=http_status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail=f"body exceeds {_MAX_BODY_BYTES} bytes",
        )

    try:
        # Ensure parent dir exists; mode 0o600 to mirror auth-store hygiene
        # for personal memory.
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(body.body, encoding="utf-8")
        try:
            tmp.chmod(0o600)
        except OSError:
            pass
        tmp.replace(path)
    except OSError as e:
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.write",
            target_kind="memory_file",
            target_id=name,
            reason=f"io-error",
        )
        raise HTTPException(
            status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="write failed",
        )

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="memory.write",
        target_kind="memory_file",
        target_id=name,
    )

    st = path.stat()
    return {
        "name":       name,
        "size_bytes": st.st_size,
        "modified":   st.st_mtime,
        "ok":         True,
    }


@router.delete("/memory/{name}")
def memory_delete(
    name: str,
    body: Annotated[dict[str, Any], Body(...)],
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Delete a memory file. Re-auth required.

    DELETE-with-body is the cleanest way to attach the re-auth
    token without a GET-arg leak. Returns ``{ok, found}``.
    """
    path = _safe_path(name)
    re_auth_token = body.get("re_auth_token") if isinstance(body, dict) else None

    if not verify_reauth(rec, re_auth_token):
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.delete",
            target_kind="memory_file",
            target_id=name,
            reason="reauth-failed",
        )
        raise HTTPException(
            status_code=http_status.HTTP_401_UNAUTHORIZED,
            detail="re-auth failed",
        )

    if name == "MEMORY.md":
        # The index file is structurally distinct — we don't let the
        # console wipe it (would orphan every typed entry's pointer).
        console_audit.action_failed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.delete",
            target_kind="memory_file",
            target_id=name,
            reason="protected-file",
        )
        raise HTTPException(
            status_code=http_status.HTTP_400_BAD_REQUEST,
            detail="MEMORY.md is the index file — edit it instead of deleting",
        )

    found = path.exists()
    if found:
        try:
            path.unlink()
        except OSError as e:
            console_audit.action_failed(
                tenant_id=rec.tenant_id,
                sid_fingerprint=rec.sid_fingerprint,
                action="memory.delete",
                target_kind="memory_file",
                target_id=name,
                reason="io-error",
            )
            raise HTTPException(
                status_code=http_status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="delete failed",
            )
        console_audit.action_performed(
            tenant_id=rec.tenant_id,
            sid_fingerprint=rec.sid_fingerprint,
            action="memory.delete",
            target_kind="memory_file",
            target_id=name,
        )

    return {"name": name, "found": found, "ok": True}

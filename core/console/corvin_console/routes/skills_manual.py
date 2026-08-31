"""Manual Skill Creation (ADR-0124 M5a).

Operators author skills directly in the console with a Markdown editor.
Created skills land in the user scope where the existing skill listing
endpoint already picks them up.

Routes:
  GET    /skills/manual              list manually created skills
  POST   /skills/manual              create a new manual skill
  PUT    /skills/manual/{name}       update skill body
  DELETE /skills/manual/{name}       remove skill
"""
from __future__ import annotations

import hashlib
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
from ..utils import atomic_write_json
from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

router = APIRouter()

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,127}$")


# ── Storage ───────────────────────────────────────────────────────────────────

def _skills_root(tid: str) -> Path:
    return _forge_paths.tenant_global_dir(tid) / "skill-forge" / "skills"


def _skill_dir(tid: str, name: str) -> Path:
    return _skills_root(tid) / f"manual__{name}"


def _list_manual_skills(tid: str) -> list[dict[str, Any]]:
    root = _skills_root(tid)
    if not root.is_dir():
        return []
    results = []
    for entry in sorted(root.iterdir()):
        if not entry.name.startswith("manual__"):
            continue
        skill_md = entry / "SKILL.md"
        meta_path = entry / "meta.json"
        if not skill_md.exists():
            continue
        meta: dict[str, Any] = {}
        if meta_path.exists():
            try:
                meta = json.loads(meta_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                pass
        name = entry.name[len("manual__"):]
        results.append({
            "name": name,
            "scope": "user",
            "origin": "manual",
            "created_at": meta.get("created_at"),
            "updated_at": meta.get("updated_at"),
            "sha256": meta.get("sha256", ""),
            "grade_count": 0,
            "mean_score": None,
        })
    return results


def _write_skill(tid: str, name: str, body: str, existing_meta: dict[str, Any] | None) -> None:
    skill_dir = _skill_dir(tid, name)
    skill_dir.mkdir(parents=True, exist_ok=True)

    sha = hashlib.sha256(body.encode()).hexdigest()
    now = time.time()
    meta = {
        "name": name,
        "scope": "user",
        "origin": "manual",
        "sha256": sha,
        "created_at": existing_meta.get("created_at", now) if existing_meta else now,
        "updated_at": now,
        "grades": existing_meta.get("grades", []) if existing_meta else [],
    }

    # Write SKILL.md atomically
    skill_md = skill_dir / "SKILL.md"
    atomic_write_json(skill_md, body)

    # Write meta.json atomically
    meta_path = skill_dir / "meta.json"
    atomic_write_json(meta_path, meta)


def _load_meta(tid: str, name: str) -> dict[str, Any] | None:
    meta_path = _skill_dir(tid, name) / "meta.json"
    if not meta_path.exists():
        return None
    try:
        return json.loads(meta_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None


# ── Models ────────────────────────────────────────────────────────────────────

class SkillCreateRequest(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    body: str = Field(..., min_length=1, max_length=65_536, description="Markdown skill body")
    model_config = {"extra": "forbid"}


class SkillUpdateRequest(BaseModel):
    body: str = Field(..., min_length=1, max_length=65_536)
    model_config = {"extra": "forbid"}


# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/skills/manual")
def list_manual_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    skills = _list_manual_skills(rec.tenant_id)
    return {"tenant_id": rec.tenant_id, "count": len(skills), "skills": skills}


@router.post("/skills/manual")
def create_manual_skill(
    body: SkillCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _SKILL_NAME_RE.match(body.name):
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "name must be lowercase alphanumeric with _ or - (max 128 chars)",
        )
    existing = _load_meta(rec.tenant_id, body.name)
    if existing is not None:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"skill {body.name!r} already exists — use PUT to update",
        )

    try:
        _write_skill(rec.tenant_id, body.name, body.body, None)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_created",
        target_kind="manual_skill",
        target_id=body.name,
    )
    return {"ok": True, "name": body.name}


@router.put("/skills/manual/{name}")
def update_manual_skill(
    name: str,
    body: SkillUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid skill name")
    existing = _load_meta(rec.tenant_id, name)
    if existing is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"skill {name!r} not found")

    try:
        _write_skill(rec.tenant_id, name, body.body, existing)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_updated",
        target_kind="manual_skill",
        target_id=name,
    )
    return {"ok": True, "name": name}


@router.delete("/skills/manual/{name}")
def delete_manual_skill(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    import shutil

    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid skill name")
    skill_dir = _skill_dir(rec.tenant_id, name)
    if not skill_dir.exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"skill {name!r} not found")

    try:
        shutil.rmtree(skill_dir)
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_deleted",
        target_kind="manual_skill",
        target_id=name,
    )
    return {"ok": True, "name": name}

"""Skill-Forge — multi-scope aggregation.

Skills live as ``<scope>/skill-forge/skills/<name>/{SKILL.md,meta.json}``.
The meta.json carries scope, grades (list of {run_id, score, ts, notes}),
created_at, sha256. We compute mean_score + grade_count from grades.

Scopes walked:
  * sessions/<key>/skill-forge/skills/  (per-session)
  * skill-forge/skills/                  (tenant-root)
  * global/skill-forge/skills/           (user scope)

LIST returns the projection; DETAIL adds the SKILL.md body.
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from statistics import mean
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status

from .. import auth as session_auth
from ..deps import require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths


router = APIRouter()


def _scope_skill_dirs(tid: str) -> list[tuple[str, Path]]:
    """Return ``(scope_label, skills_dir)`` pairs across every scope."""
    pairs: list[tuple[str, Path]] = []
    sessions_dir = _forge_paths.tenant_sessions_dir(tid)
    if sessions_dir.exists():
        for entry in sorted(sessions_dir.iterdir()):
            sk = entry / "skill-forge" / "skills"
            if sk.is_dir():
                pairs.append((f"session:{entry.name}", sk))
    tenant_root = _forge_paths.tenant_home(tid) / "skill-forge" / "skills"
    if tenant_root.is_dir():
        pairs.append(("session-default", tenant_root))
    user = _forge_paths.tenant_global_dir(tid) / "skill-forge" / "skills"
    if user.is_dir():
        pairs.append(("user", user))
    return pairs


def _load_meta(meta_path: Path) -> dict[str, Any]:
    if not meta_path.exists():
        return {}
    try:
        data = json.loads(meta_path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _project(meta: dict[str, Any], scope_label: str, skill_dir: Path) -> dict[str, Any]:
    grades = meta.get("grades") or []
    scores = [g.get("score") for g in grades if isinstance(g, dict) and isinstance(g.get("score"), (int, float))]
    return {
        "name":         meta.get("name") or skill_dir.name,
        "scope":        meta.get("scope") or "",
        "scope_source": scope_label,
        "type":         meta.get("type") or "",
        "description":  meta.get("description") or "",
        "created_at":   meta.get("created_at"),
        "grade_count":  len(scores),
        "mean_score":   round(mean(scores), 3) if scores else None,
        "sha256":       meta.get("sha256") or "",
        "skill_dir":    str(skill_dir),
    }


@router.get("/skills")
def list_skills(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    tid = rec.tenant_id
    items: list[dict[str, Any]] = []
    by_name: dict[tuple[str, str], dict[str, Any]] = {}
    for scope_label, sk_dir in _scope_skill_dirs(tid):
        try:
            for entry in sk_dir.iterdir():
                if not entry.is_dir():
                    continue
                meta = _load_meta(entry / "meta.json")
                if not meta and not (entry / "SKILL.md").exists():
                    continue
                proj = _project(meta, scope_label, entry)
                # Key by (name, scope_source) so per-session and user-scope
                # skills with the same name don't collapse.
                by_name[(proj["name"], scope_label)] = proj
        except OSError:
            continue
    items = sorted(by_name.values(),
                   key=lambda r: (r["scope_source"] != "user", r["name"] or ""))
    return {
        "tenant_id":  tid,
        "ts":         time.time(),
        "count":      len(items),
        "skills":     items,
    }


@router.get("/skills/{name}")
def skill_detail(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return body + meta for the highest-priority skill with this name.

    Search order: user > session-default > session:<key> (oldest first).
    """
    tid = rec.tenant_id
    matches: list[tuple[str, Path]] = []
    for scope_label, sk_dir in _scope_skill_dirs(tid):
        candidate = sk_dir / name
        if candidate.is_dir():
            matches.append((scope_label, candidate))

    if not matches:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail=f"skill {name!r} not found in any scope",
        )

    # Prefer user scope, then session-default, then sessions.
    def _priority(p: tuple[str, Path]) -> int:
        label = p[0]
        if label == "user": return 0
        if label == "session-default": return 1
        return 2
    matches.sort(key=_priority)
    scope_label, skill_dir = matches[0]

    meta = _load_meta(skill_dir / "meta.json")
    body_path = skill_dir / "SKILL.md"
    body_text: str | None = None
    if body_path.exists():
        try:
            # Cap at 64 KiB — same limit as forge impl_preview.
            if body_path.stat().st_size <= 64 * 1024:
                body_text = body_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            body_text = None

    other_scopes = [m[0] for m in matches[1:]]

    return {
        "name":          name,
        "scope_source":  scope_label,
        "skill_dir":     str(skill_dir),
        "meta":          meta,
        "body_preview":  body_text,
        "other_scopes":  other_scopes,
    }

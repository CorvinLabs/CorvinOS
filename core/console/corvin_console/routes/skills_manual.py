"""Manual Skill Creation (ADR-0124 M5a).

Operators author skills directly in the console with a Markdown editor.

Storage (adversarial review D-14): manual skills are written THROUGH the
canonical SkillForge registry — ``MultiSkillRegistry.create(scope="user")``
— which is the only registry ``skill_inject`` reads. The previous version
wrote ``<tenant>/global/skill-forge/skills/manual__<name>`` by hand: a
directory ``MultiSkillRegistry._root_for("user")`` never looks at, so the
console listed skills the engine never injected. Going through the registry
also gives manual skills the linter (fail-closed), the content-hash binding,
the hash-chained ``skill.create``/``skill.delete`` audit and the plugin-slot
mirror for free.

Name contract: registry names are ``[a-z0-9][a-z0-9_.]*`` — no ``-`` (the
registry rejects it), so the console validates the same shape.

Routes:
  GET    /skills/manual              list manually created skills
  POST   /skills/manual              create a new manual skill
  PUT    /skills/manual/{name}       update skill body (grades preserved)
  DELETE /skills/manual/{name}       remove skill
"""
from __future__ import annotations

import logging
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import _bootstrap  # noqa: F401 — puts operator/skill-forge + forge on sys.path
from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session

logger = logging.getLogger(__name__)
router = APIRouter()

_SKILL_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.]{0,127}$")
#: ``SkillSpec.created_by`` marker that identifies a console-authored skill.
MANUAL_CREATED_BY = "console-manual"
MANUAL_SCOPE = "user"
MANUAL_TYPE = "domain"


# ── Registry access ───────────────────────────────────────────────────────────

def _registry(tid: str):
    """The tenant's MultiSkillRegistry (503 when SkillForge is not installed)."""
    try:
        from skill_forge.multi_registry import MultiSkillRegistry  # type: ignore[import-not-found]
    except ImportError as exc:
        raise HTTPException(
            http_status.HTTP_503_SERVICE_UNAVAILABLE,
            "SkillForge registry unavailable",
        ) from exc
    return MultiSkillRegistry(tenant_id=tid)


def _linter_error(reg):
    """``LinterError`` as seen by THIS registry instance.

    Resolved through the instance's own module chain (MultiSkillRegistry →
    its SkillRegistry → that module's LinterError) rather than a fresh
    ``from skill_forge.registry import LinterError``: a process that
    re-imports ``skill_forge.registry`` (test isolation does) ends up with two
    class identities, and an ``except`` on the wrong one turned a linter
    rejection into a 500.
    """
    try:
        # Function globals are the namespace the class was DEFINED in — not
        # whatever sys.modules currently maps the name to.
        registry_cls = type(reg).create.__globals__["SkillRegistry"]
        return registry_cls.create.__globals__["LinterError"]
    except (KeyError, AttributeError):  # pragma: no cover — defensive
        return ()


def _description_from(body: str) -> str:
    for line in body.splitlines():
        text = line.strip().lstrip("#").strip()
        if text:
            return text[:200]
    return "manual skill"


def _manual_spec(tid: str, name: str):
    """The user-scope, console-authored SkillSpec for ``name`` or None."""
    reg = _registry(tid)
    spec = reg.get_in_scope(name, MANUAL_SCOPE)
    if spec is None or spec.created_by != MANUAL_CREATED_BY:
        return None
    return spec


def _project(spec) -> dict[str, Any]:
    return {
        "name": spec.name,
        "scope": MANUAL_SCOPE,
        "origin": "manual",
        "created_at": spec.created_at,
        "updated_at": (spec.meta or {}).get("updated_at", spec.created_at),
        "sha256": spec.sha256,
        "grade_count": spec.n_grades,
        "mean_score": spec.mean_score if spec.n_grades else None,
        "injectable": spec.n_grades >= 1 and spec.mean_score > 0,
    }


def _list_manual_skills(tid: str) -> list[dict[str, Any]]:
    reg = _registry(tid)
    out = []
    for scope, spec in reg.list_with_scope():
        if scope == MANUAL_SCOPE and spec.created_by == MANUAL_CREATED_BY:
            out.append(_project(spec))
    out.sort(key=lambda d: d["name"])
    return out


def _write_skill(tid: str, name: str, body: str, *, overwrite: bool):
    reg = _registry(tid)
    try:
        if overwrite:
            return reg.update_body(
                name, scope=MANUAL_SCOPE, body_md=body,
                description=_description_from(body),
            )
        return reg.create(
            scope=MANUAL_SCOPE, name=name, type=MANUAL_TYPE, body_md=body,
            description=_description_from(body), claim={},
            created_by=MANUAL_CREATED_BY,
        )
    except _linter_error(reg) as exc:  # type: ignore[misc]
        raise HTTPException(
            http_status.HTTP_400_BAD_REQUEST,
            "linter rejected: " + "; ".join(getattr(exc, "violations", []) or [str(exc)]),
        ) from exc
    except (ValueError, KeyError) as exc:
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, str(exc)) from exc
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "storage error") from exc


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
            "name must be lowercase alphanumeric with _ or . (max 128 chars)",
        )
    if _registry(rec.tenant_id).get_in_scope(body.name, MANUAL_SCOPE) is not None:
        raise HTTPException(
            http_status.HTTP_409_CONFLICT,
            f"skill {body.name!r} already exists — use PUT to update",
        )

    spec = _write_skill(rec.tenant_id, body.name, body.body, overwrite=False)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_created",
        target_kind="manual_skill",
        target_id=body.name,
    )
    return {"ok": True, "name": spec.name, "scope": MANUAL_SCOPE, "sha256": spec.sha256}


@router.put("/skills/manual/{name}")
def update_manual_skill(
    name: str,
    body: SkillUpdateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid skill name")
    if _manual_spec(rec.tenant_id, name) is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"skill {name!r} not found")

    spec = _write_skill(rec.tenant_id, name, body.body, overwrite=True)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_updated",
        target_kind="manual_skill",
        target_id=name,
    )
    return {"ok": True, "name": name, "scope": MANUAL_SCOPE, "sha256": spec.sha256}


@router.delete("/skills/manual/{name}")
def delete_manual_skill(
    name: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not _SKILL_NAME_RE.match(name):
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid skill name")
    if _manual_spec(rec.tenant_id, name) is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"skill {name!r} not found")

    try:
        removed = _registry(rec.tenant_id).delete(
            name, scope=MANUAL_SCOPE, reason="deleted from console",
        )
    except OSError as exc:
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "delete failed") from exc
    if not removed:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"skill {name!r} not found")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.manual_deleted",
        target_kind="manual_skill",
        target_id=name,
    )
    return {"ok": True, "name": name}

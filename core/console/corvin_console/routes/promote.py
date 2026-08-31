"""Promote a forge tool / skill-forge skill to a higher scope.

Wraps the existing ``MultiRegistry.promote`` (forge) and
``MultiSkillRegistry.promote`` (skill-forge). Re-auth required;
audit lands on every promotion success / failure.

Skill-Forge gates promotion structurally:
  * task → session: ≥1 positive grade
  * session → project: ≥3 grades, mean ≥ 0.5
  * project → user: explicit ``force=True``

Forge has no grade gate but the layer-11 dialectic-decide site
``forge_promotion`` (heat-scored) may surface a recommendation in
the audit chain.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, verify_reauth

import logging
_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_FORGE_PATH = _REPO / "operator" / "forge"
_SKILL_FORGE_PATH = _REPO / "operator" / "skill-forge"
for _p in (_FORGE_PATH, _SKILL_FORGE_PATH):
    if str(_p) not in sys.path:
        sys.path.insert(0, str(_p))


router = APIRouter()

_VALID_SCOPES = ("session", "project", "user")


class PromoteRequest(BaseModel):
    to:             str  = Field(..., pattern="^(session|project|user)$")
    re_auth_token:  str | None = None
    force:          bool = False
    model_config = {"extra": "forbid"}


def _audit_fail(rec: session_auth.SessionRecord, *, action: str,
                kind: str, name: str, reason: str) -> None:
    console_audit.action_failed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action=action,
        target_kind=kind,
        target_id=name,
        reason=reason,
    )


@router.post("/tools/{name}/promote")
def tool_promote(
    name: str,
    body: PromoteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not verify_reauth(rec, body.re_auth_token):
        _audit_fail(rec, action="tool.promote", kind="forge_tool",
                    name=name, reason="reauth-failed")
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    try:
        from forge.multi_registry import MultiRegistry
    except Exception as e:
        _audit_fail(rec, action="tool.promote", kind="forge_tool",
                    name=name, reason="forge-import-failed")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"forge import failed: {e}",
        )
    try:
        reg = MultiRegistry()
        spec = reg.promote(name, to=body.to)
    except KeyError:
        _audit_fail(rec, action="tool.promote", kind="forge_tool",
                    name=name, reason="not-found")
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            f"tool {name!r} not found in any scope")
    except ValueError as e:
        _audit_fail(rec, action="tool.promote", kind="forge_tool",
                    name=name, reason="bad-scope")
        raise HTTPException(http_status.HTTP_400_BAD_REQUEST, "invalid request")
    except Exception as e:
        _audit_fail(rec, action="tool.promote", kind="forge_tool",
                    name=name, reason="promote-failed")
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "internal error")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="tool.promote",
        target_kind="forge_tool",
        target_id=name,
    )
    return {"name": name, "to": body.to, "ok": True,
            "promoted": True}


@router.post("/skills/{name}/promote")
def skill_promote(
    name: str,
    body: PromoteRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if not verify_reauth(rec, body.re_auth_token):
        _audit_fail(rec, action="skill.promote", kind="skill",
                    name=name, reason="reauth-failed")
        raise HTTPException(http_status.HTTP_401_UNAUTHORIZED, "re-auth failed")

    try:
        from skill_forge.multi_registry import MultiSkillRegistry
    except Exception as e:
        _audit_fail(rec, action="skill.promote", kind="skill",
                    name=name, reason="skill-forge-import-failed")
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            f"skill-forge import failed: {e}",
        )
    try:
        reg = MultiSkillRegistry()
        reg.promote(name, to=body.to, force=body.force)
    except KeyError:
        _audit_fail(rec, action="skill.promote", kind="skill",
                    name=name, reason="not-found")
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            f"skill {name!r} not found in any scope")
    except ValueError as e:
        # Promotion-gate denial OR bad scope. Either way, surface the
        # message verbatim so the operator sees "needs ≥3 grades" etc.
        _audit_fail(rec, action="skill.promote", kind="skill",
                    name=name, reason="gate-denied")
        raise HTTPException(http_status.HTTP_409_CONFLICT, str(e))
    except Exception as e:
        _audit_fail(rec, action="skill.promote", kind="skill",
                    name=name, reason="promote-failed")
        raise HTTPException(http_status.HTTP_500_INTERNAL_SERVER_ERROR, "internal error")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="skill.promote",
        target_kind="skill",
        target_id=name,
    )
    return {"name": name, "to": body.to, "ok": True,
            "promoted": True, "force": body.force}

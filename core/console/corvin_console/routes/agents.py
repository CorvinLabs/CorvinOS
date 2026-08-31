"""Agent Lifecycle Governance — ADR-0131 console REST endpoints.

Endpoints
---------
  GET  /agents                       → list all charters with status
  GET  /agents/{agent_id}            → single charter + status detail
  POST /agents                       → create a new charter
  PUT  /agents/{agent_id}            → update an existing charter (full replace)
  PUT  /agents/{agent_id}/sign       → add a sign-off for a scope target
  DELETE /agents/{agent_id}/sign/{role} → revoke a sign-off
  POST /agents/{agent_id}/disable    → hard-disable an agent

All mutations require CSRF. Sign-off also requires re-auth token.

MUST NOT import anthropic.
"""
from __future__ import annotations

import sys
from datetime import date
from pathlib import Path
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import auth as session_auth
from .. import audit as console_audit
from ..deps import require_csrf, require_session

_THIS_DIR = Path(__file__).resolve().parent
_REPO = _THIS_DIR.parents[3]
_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

import agent_charter as _ac  # noqa: E402

router = APIRouter()


# ── Helpers ───────────────────────────────────────────────────────────────────

def _charter_to_api(charter: _ac.AgentCharter, now: date | None = None) -> dict[str, Any]:
    if now is None:
        now = date.today()
    status = _ac.compute_status(charter, now_date=now)
    try:
        days_to_review = _ac.days_until(charter.review_date, now_date=now)
        days_to_sunset = _ac.days_until(charter.sunset_date, now_date=now)
    except ValueError:
        days_to_review = 0
        days_to_sunset = 0
    signed_scope = _ac.current_signed_scope(charter)
    required_for_scope = _ac.get_required_roles(charter.scope)
    return {
        "agent_id":     charter.agent_id,
        "name":         charter.name,
        "kind":         charter.kind,
        "scope":        charter.scope,
        "status":       status,
        "it_owner":     charter.it_owner,
        "business_owner": charter.business_owner,
        "compliance_owner": charter.compliance_owner,
        "problem":      charter.problem,
        "success_metric": charter.success_metric,
        "baseline":     charter.baseline,
        "target":       charter.target,
        "unit":         charter.unit,
        "created_at":   charter.created_at,
        "review_date":  charter.review_date,
        "sunset_date":  charter.sunset_date,
        "data_class":   charter.data_class,
        "egress_zone":  charter.egress_zone,
        "engine_allowlist": charter.engine_allowlist,
        "sign_offs":    [{"role": s.role, "signer": s.signer, "signed_at": s.signed_at}
                         for s in charter.sign_offs],
        "signed_scope": signed_scope,
        "required_roles": list(required_for_scope),
        "days_to_review": days_to_review,
        "days_to_sunset": days_to_sunset,
        "disabled":     charter.disabled,
        "version":      charter.version,
    }


# ── GET /agents ───────────────────────────────────────────────────────────────

@router.get("/agents")
def list_agents(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> list[dict[str, Any]]:
    charters = _ac.list_charters(rec.tenant_id)
    now = date.today()
    return [_charter_to_api(c, now=now) for c in charters]


# ── GET /agents/{agent_id} ────────────────────────────────────────────────────

@router.get("/agents/{agent_id:path}")
def get_agent(
    agent_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    charter = _ac.load_charter(rec.tenant_id, agent_id)
    if charter is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"charter not found: {agent_id!r}")
    return _charter_to_api(charter)


# ── POST /agents ──────────────────────────────────────────────────────────────

class CreateCharterRequest(BaseModel):
    agent_id: str
    name: str
    kind: str
    scope: str
    problem: str
    success_metric: str
    baseline: float
    target: float
    unit: str
    it_owner: str
    business_owner: str
    compliance_owner: str
    review_date: str
    sunset_date: str
    data_class: str
    egress_zone: str
    engine_allowlist: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


@router.post("/agents", status_code=http_status.HTTP_201_CREATED)
def create_charter(
    body: CreateCharterRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    data = body.model_dump()
    data["created_at"] = date.today().isoformat()
    data["version"] = 1

    errors = _ac.validate_charter(data)
    if errors:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"errors": errors})

    # Compliance pre-check
    charter = _ac.AgentCharter(
        version=1,
        agent_id=body.agent_id,
        name=body.name,
        kind=body.kind,
        scope=body.scope,
        problem=body.problem,
        success_metric=body.success_metric,
        baseline=body.baseline,
        target=body.target,
        unit=body.unit,
        it_owner=body.it_owner,
        business_owner=body.business_owner,
        compliance_owner=body.compliance_owner,
        created_at=data["created_at"],
        review_date=body.review_date,
        sunset_date=body.sunset_date,
        data_class=body.data_class,
        egress_zone=body.egress_zone,
        engine_allowlist=body.engine_allowlist,
    )
    check_failures = _ac.compliance_pre_check(charter, rec.tenant_id)
    for check_id in check_failures:
        _ac.emit_compliance_check_failed(rec.tenant_id, body.agent_id, check_id)
    if check_failures:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"compliance_failures": check_failures})

    # Single-owner exception
    owners = {charter.it_owner, charter.business_owner, charter.compliance_owner}
    if len(owners) == 1:
        _ac.emit_single_owner_exception(rec.tenant_id, charter.agent_id)

    # Audit-first (L16): emit before write
    _ac.emit_charter_created(rec.tenant_id, charter, sid_fingerprint=rec.sid_fingerprint)
    try:
        _ac.save_charter(rec.tenant_id, charter, exclusive=True)
    except FileExistsError:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "charter already exists for this agent_id")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="agent.charter.create",
        target_kind="agent",
        target_id=charter.agent_id,
    )
    return _charter_to_api(charter)


# ── PUT /agents/{agent_id}/sign ───────────────────────────────────────────────

class RenewCharterRequest(BaseModel):
    problem: str
    success_metric: str
    baseline: float
    target: float
    unit: str
    it_owner: str
    business_owner: str
    compliance_owner: str
    review_date: str
    sunset_date: str
    data_class: str
    egress_zone: str
    engine_allowlist: list[str] = Field(default_factory=list)
    model_config = {"extra": "forbid"}


@router.put("/agents/{agent_id:path}", response_model=None)
def renew_charter(
    agent_id: str,
    body: RenewCharterRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Charter renewal: increments version, clears sign-offs, emits charter_renewed."""
    charter = _ac.load_charter(rec.tenant_id, agent_id)
    if charter is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"charter not found: {agent_id!r}")
    if charter.disabled:
        raise HTTPException(http_status.HTTP_409_CONFLICT,
                            "disabled agent requires a new charter cycle (different agent_id)")

    prior_version = charter.version
    updated = _ac.AgentCharter(
        version=prior_version + 1,
        agent_id=charter.agent_id,
        name=charter.name,
        kind=charter.kind,
        scope=charter.scope,
        problem=body.problem,
        success_metric=body.success_metric,
        baseline=body.baseline,
        target=body.target,
        unit=body.unit,
        it_owner=body.it_owner,
        business_owner=body.business_owner,
        compliance_owner=body.compliance_owner,
        created_at=charter.created_at,
        review_date=body.review_date,
        sunset_date=body.sunset_date,
        data_class=body.data_class,
        egress_zone=body.egress_zone,
        engine_allowlist=body.engine_allowlist,
        sign_offs=[],  # renewal clears sign-offs; fresh approval cycle required
        disabled=False,
    )
    validate_data = updated.__dict__.copy()
    validate_data["sign_offs"] = []
    validate_data["version"] = updated.version
    errors = _ac.validate_charter(validate_data)
    if errors:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, {"errors": errors})

    check_failures = _ac.compliance_pre_check(updated, rec.tenant_id)
    for check_id in check_failures:
        _ac.emit_compliance_check_failed(rec.tenant_id, agent_id, check_id)
    if check_failures:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"compliance_failures": check_failures})

    # Audit-first before overwrite
    _ac.emit_charter_renewed(rec.tenant_id, updated, prior_version,
                              sid_fingerprint=rec.sid_fingerprint)
    _ac.save_charter(rec.tenant_id, updated)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="agent.charter.renew",
        target_kind="agent",
        target_id=agent_id,
    )
    return _charter_to_api(updated)


class SignOffRequest(BaseModel):
    scope_target: str
    role: str
    model_config = {"extra": "forbid"}


@router.put("/agents/{agent_id:path}/sign")
def sign_charter(
    agent_id: str,
    body: SignOffRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    charter = _ac.load_charter(rec.tenant_id, agent_id)
    if charter is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"charter not found: {agent_id!r}")
    if charter.disabled:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "agent is disabled")

    if body.scope_target not in _ac.AGENT_SCOPES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid scope_target: {body.scope_target!r}")
    if body.scope_target != charter.scope:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"scope_target must match charter scope '{charter.scope}'")
    if body.role not in _ac.SIGN_ROLES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid role: {body.role!r}")

    allowed, reason = _ac.can_sign_for_scope(charter, body.role, body.scope_target)
    if not allowed:
        raise HTTPException(http_status.HTTP_409_CONFLICT, reason)

    # Compliance pre-check before accepting any sign-off
    check_failures = _ac.compliance_pre_check(charter, rec.tenant_id)
    for check_id in check_failures:
        _ac.emit_compliance_check_failed(rec.tenant_id, agent_id, check_id)
    if check_failures:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            {"compliance_failures": check_failures})

    # Audit-first: emit before mutation (L16 invariant).
    # signer is bound to the session fingerprint — not user-supplied — to prevent impersonation.
    _ac.emit_sign_off(rec.tenant_id, agent_id, body.role, body.scope_target,
                      sid_fingerprint=rec.sid_fingerprint)
    charter.sign_offs.append(_ac.SignOff(
        role=body.role,
        signer=rec.sid_fingerprint,
        signed_at=date.today().isoformat(),
    ))
    _ac.save_charter(rec.tenant_id, charter)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="agent.sign_off.add",
        target_kind="agent",
        target_id=agent_id,
    )
    return _charter_to_api(charter)


# ── DELETE /agents/{agent_id}/sign/{role} ─────────────────────────────────────

@router.delete("/agents/{agent_id:path}/sign/{role}")
def revoke_sign_off(
    agent_id: str,
    role: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    if role not in _ac.SIGN_ROLES:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY,
                            f"invalid role: {role!r}")
    charter = _ac.load_charter(rec.tenant_id, agent_id)
    if charter is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"charter not found: {agent_id!r}")

    prior_scope = _ac.current_signed_scope(charter)
    before_len = len(charter.sign_offs)
    charter.sign_offs = [s for s in charter.sign_offs if s.role != role]
    if len(charter.sign_offs) == before_len:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND,
                            f"no sign-off found for role: {role!r}")

    # Audit-first: emit before write (L16 invariant)
    _ac.emit_sign_off_revoked(rec.tenant_id, agent_id, role, prior_scope,
                              sid_fingerprint=rec.sid_fingerprint)
    _ac.save_charter(rec.tenant_id, charter)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="agent.sign_off.revoke",
        target_kind="agent",
        target_id=agent_id,
    )
    return _charter_to_api(charter)


# ── POST /agents/{agent_id}/disable ──────────────────────────────────────────

@router.post("/agents/{agent_id:path}/disable")
def disable_agent(
    agent_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    charter = _ac.load_charter(rec.tenant_id, agent_id)
    if charter is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"charter not found: {agent_id!r}")
    if charter.disabled:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "agent already disabled")

    prior_scope = _ac.current_signed_scope(charter) or "none"
    # Audit-first: emit CRITICAL event before mutation (L16 invariant)
    _ac.emit_sunset(rec.tenant_id, agent_id, charter.kind, prior_scope)
    charter.disabled = True
    _ac.save_charter(rec.tenant_id, charter)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="agent.disable",
        target_kind="agent",
        target_id=agent_id,
    )
    return _charter_to_api(charter)

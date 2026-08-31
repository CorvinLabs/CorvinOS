"""Layer 42 CorvinOrg — console REST routes.

Endpoints (mounted at /v1/console/orgs by app.py):

  GET    /orgs                           → list all orgs
  POST   /orgs                           → create org
  GET    /orgs/{handle}                  → org detail
  DELETE /orgs/{handle}                  → dissolve org
  POST   /orgs/{handle}/members          → add member
  DELETE /orgs/{handle}/members          → remove member (?actor_id=...)
  POST   /orgs/{handle}/agents           → affiliate agent
  DELETE /orgs/{handle}/agents/{eid}     → deaffiliate agent
  GET    /orgs/{handle}/grants           → list org outbound grants
  POST   /orgs/{handle}/grants           → issue org grant
  DELETE /orgs/{handle}/grants/{gid}     → revoke org grant

Must NOT import anthropic (CI AST lint).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from ..utils import sanitize_grant_doc as _sanitize_grant

import logging
_log = logging.getLogger(__name__)

_THIS_DIR = Path(__file__).resolve().parent
_SHARED = _THIS_DIR.parents[3] / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

try:
    from org_store import OrgStore, OrgError, list_org_handles
    from org_actor import (
        create_org,
        affiliate_agent,
        deaffiliate_agent,
    )
    from grant_store import GrantStore
    from grant_issuer import build_grant, validate_capabilities, validate_conditions, GrantError
    from audit import audit_event
except ImportError as _e:
    raise ImportError(f"L42 org modules unavailable: {_e}") from _e

router = APIRouter()

# ── helpers ───────────────────────────────────────────────────────────────────

def _get_store(handle: str, tenant_id: str) -> OrgStore:
    try:
        store = OrgStore(handle, tenant_id)
    except OrgError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid request")
    if not store.actor_exists():
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, f"org {handle!r} not found")
    return store

def _sanitize_endorsement(doc: dict) -> dict:
    return {
        "endorsement_id": doc.get("endorsement_id"),
        "agent_actor_id": doc.get("agent_actor_id"),
        "org_actor_id": doc.get("org_actor_id"),
        "scope": doc.get("scope", []),
        "issued_at": doc.get("issued_at"),
        "expires_at": doc.get("expires_at"),
        "revoked_at": doc.get("revoked_at"),
    }

def _org_summary(handle: str, tenant_id: str) -> dict:
    try:
        store = OrgStore(handle, tenant_id)
        actor = store.get_actor() if store.actor_exists() else {}
        cfg = store.get_config()
        members = store.get_members()
        agents = store.list_endorsements(include_revoked=False)
        return {
            "handle": handle,
            "actor_id": actor.get("id", f"@{handle}"),
            "display_name": cfg.get("display_name", handle),
            "summary": actor.get("summary", ""),
            "verified_domain": actor.get("verified_domain"),
            "member_count": len(members),
            "agent_count": len(agents),
        }
    except Exception:
        return {"handle": handle, "actor_id": f"@{handle}", "display_name": handle,
                "summary": "", "verified_domain": None, "member_count": 0, "agent_count": 0}

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class OrgCreateRequest(BaseModel):
    handle: str = Field(..., max_length=63, pattern=r"^[a-z0-9][a-z0-9_-]{0,62}$")
    display_name: str = Field(..., max_length=64)
    summary: str = Field("", max_length=300)
    host: str | None = Field(None, max_length=200)
    model_config = {"extra": "forbid"}

class MemberAddRequest(BaseModel):
    actor_id: str = Field(..., max_length=256)
    role: Literal["owner", "admin", "editor", "agent"]
    model_config = {"extra": "forbid"}

class AgentAffiliateRequest(BaseModel):
    agent_actor_id: str = Field(..., max_length=256)
    scope: list[str] = Field(default_factory=list, max_length=20)
    ttl_days: int | None = Field(None, ge=1, le=3650)
    model_config = {"extra": "forbid"}

class OrgGrantCreateRequest(BaseModel):
    grantee_actor: str = Field(..., max_length=256)
    capabilities: list[str] = Field(..., min_length=1, max_length=20)
    conditions: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

# ── Org CRUD ──────────────────────────────────────────────────────────────────

@router.get("")
def list_orgs(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    handles = list_org_handles(rec.tenant_id)
    return {
        "orgs": [_org_summary(h, rec.tenant_id) for h in handles],
        "ts": time.time(),
    }

@router.post("")
def create_org_route(
    body: OrgCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    # owner_actor_id = the personal actor or just the tenant_id as a placeholder
    from social_actor import actor_doc_path
    import json as _json
    owner_actor_id = rec.tenant_id
    try:
        ap = actor_doc_path(rec.tenant_id)
        if ap.exists():
            doc = _json.loads(ap.read_text())
            owner_actor_id = doc.get("id") or doc.get("actor_id") or rec.tenant_id
    except Exception:
        pass

    try:
        store = create_org(
            org_handle=body.handle,
            display_name=body.display_name,
            owner_actor_id=owner_actor_id,
            summary=body.summary,
            host=body.host,
            tenant_id=rec.tenant_id,
        )
    except OrgError as exc:
        raise HTTPException(http_status.HTTP_409_CONFLICT, "conflict")

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.created",
        target_kind="org",
        target_id=body.handle,
    )
    return {"ok": True, "org": _org_summary(body.handle, rec.tenant_id), "ts": time.time()}

@router.get("/{handle}")
def get_org(
    handle: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    actor = store.get_actor()
    cfg = store.get_config()
    members = store.get_members()
    agents = [_sanitize_endorsement(e) for e in store.list_endorsements(include_revoked=False)]
    grants_db = store.grant_db_path()
    grants = []
    if grants_db.exists():
        gs = GrantStore(grants_db)
        grants = [_sanitize_grant(g) for g in gs.list_grants()]
    return {
        "handle": handle,
        "actor": {
            "id": actor.get("id"),
            "display_name": actor.get("display_name"),
            "summary": actor.get("summary"),
            "public_key_hex": actor.get("public_key_hex", "")[:16] + "…",
            "verified_domain": actor.get("verified_domain"),
            "affiliated_actors": actor.get("affiliated_actors", []),
            "created_at": actor.get("created_at"),
        },
        "config": {
            "responsible_party": cfg.get("responsible_party"),
            "policy": cfg.get("policy", {}),
        },
        "members": members,
        "agents": agents,
        "grants": grants,
        "ts": time.time(),
    }

@router.delete("/{handle}")
def dissolve_org(
    handle: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    audit_event(
        "org.dissolved",
        details={"org_handle": handle, "reason": "console_delete"},
    )
    store.dissolve()
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.dissolved",
        target_kind="org",
        target_id=handle,
    )
    return {"ok": True, "ts": time.time()}

# ── Members ───────────────────────────────────────────────────────────────────

@router.post("/{handle}/members")
def add_member(
    handle: str,
    body: MemberAddRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    try:
        audit_event(
            "org.member_added",
            details={"org_handle": handle, "member_prefix": body.actor_id[:16], "role": body.role},
        )
        store.add_member(body.actor_id, body.role)
    except OrgError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid request")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.member_added",
        target_kind="org_member",
        target_id=f"{handle}/{body.actor_id[:32]}",
    )
    return {"ok": True, "members": store.get_members(), "ts": time.time()}

@router.delete("/{handle}/members")
def remove_member(
    handle: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
    actor_id: str = Query(..., max_length=256),
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    found = store.remove_member(actor_id)
    if not found:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "member not found")
    audit_event(
        "org.member_removed",
        details={"org_handle": handle, "member_prefix": actor_id[:16]},
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.member_removed",
        target_kind="org_member",
        target_id=f"{handle}/{actor_id[:32]}",
    )
    return {"ok": True, "members": store.get_members(), "ts": time.time()}

# ── Agents ────────────────────────────────────────────────────────────────────

@router.post("/{handle}/agents")
def affiliate_agent_route(
    handle: str,
    body: AgentAffiliateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    if body.scope:
        try:
            validate_capabilities(body.scope)
        except GrantError as exc:
            raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid request")
    ttl_s = body.ttl_days * 86400 if body.ttl_days else None
    end = affiliate_agent(store, body.agent_actor_id, scope=body.scope, ttl_seconds=ttl_s)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.agent_affiliated",
        target_kind="org_endorsement",
        target_id=end["endorsement_id"],
    )
    return {"ok": True, "endorsement": _sanitize_endorsement(end), "ts": time.time()}

@router.delete("/{handle}/agents/{endorsement_id}")
def deaffiliate_agent_route(
    handle: str,
    endorsement_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    ok = deaffiliate_agent(store, endorsement_id)
    if not ok:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "endorsement not found or already revoked")
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.agent_deaffiliated",
        target_kind="org_endorsement",
        target_id=endorsement_id,
    )
    return {"ok": True, "ts": time.time()}

# ── Org grants ────────────────────────────────────────────────────────────────

@router.get("/{handle}/grants")
def list_org_grants(
    handle: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    include_revoked: bool = False,
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    grants_db = store.grant_db_path()
    if not grants_db.exists():
        return {"grants": [], "ts": time.time()}
    gs = GrantStore(grants_db)
    return {
        "grants": [_sanitize_grant(g) for g in gs.list_grants(include_revoked=include_revoked)],
        "ts": time.time(),
    }

@router.post("/{handle}/grants")
def create_org_grant(
    handle: str,
    body: OrgGrantCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    try:
        validate_capabilities(body.capabilities)
        validate_conditions(body.conditions)
    except GrantError as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid request")

    try:
        priv_hex, _ = store.load_keypair()
    except OrgError as exc:
        raise HTTPException(http_status.HTTP_412_PRECONDITION_FAILED, "precondition failed")

    actor_doc = store.get_actor()
    grantor_actor = actor_doc.get("id", f"@{handle}")

    audit_event(
        "org.grant_proposed",
        details={
            "org_handle": handle,
            "grantee_prefix": body.grantee_actor[:16],
            "capability_count": len(body.capabilities),
            "grantee_type": "org" if body.grantee_actor.startswith("@") else "individual",
        },
    )
    doc = build_grant(
        grantor_actor=grantor_actor,
        grantee_actor=body.grantee_actor,
        capabilities=body.capabilities,
        conditions=body.conditions,
        private_key_hex=priv_hex,
    )
    gs = GrantStore(store.grant_db_path())
    gs.save_grant(doc)

    audit_event(
        "org.grant_approved",
        details={"org_handle": handle, "grant_id": doc["grant_id"], "approver_count": 1},
    )
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.grant_issued",
        target_kind="org_grant",
        target_id=doc["grant_id"],
    )
    return {"ok": True, "grant": _sanitize_grant(doc), "ts": time.time()}

@router.delete("/{handle}/grants/{grant_id}")
def revoke_org_grant(
    handle: str,
    grant_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = _get_store(handle, rec.tenant_id)
    gs = GrantStore(store.grant_db_path())
    doc = gs.get_grant(grant_id)
    if doc is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "grant not found")
    audit_event(
        "grant.revoked",
        details={"grant_id": grant_id, "org_handle": handle},
    )
    gs.set_revoked(grant_id)
    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="org.grant_revoked",
        target_kind="org_grant",
        target_id=grant_id,
    )
    return {"ok": True, "ts": time.time()}

# ── Network graph ─────────────────────────────────────────────────────────────

@router.get("/{handle}/network")
def get_org_network(
    handle: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Return the A2A social graph for an org — nodes (members, agents, orgs) + edges.

    Metadata-only: actor_id labels, roles, capability names. No PII fields.
    """
    store = _get_store(handle, rec.tenant_id)

    nodes: list[dict[str, Any]] = []
    edges: list[dict[str, Any]] = []
    seen_node_ids: set[str] = set()

    def _add_node(node_id: str, label: str, node_type: str, role: str | None = None) -> None:
        if node_id not in seen_node_ids:
            seen_node_ids.add(node_id)
            nodes.append({"id": node_id, "label": label, "type": node_type, "role": role})

    # Org itself
    actor = store.get_actor()
    org_id = actor.get("id") or f"@{handle}"
    org_label = actor.get("display_name") or handle
    _add_node(org_id, org_label, "org")

    # Members
    for m in store.get_members():
        mid = m.get("actor_id", "")
        if not mid:
            continue
        short = mid.split("/")[-1] if "/" in mid else mid
        _add_node(mid, short, "human", m.get("role"))
        edges.append({"from": mid, "to": org_id, "type": "member", "caps": []})

    # Affiliated agents
    for end in store.list_endorsements(include_revoked=False):
        aid = end.get("agent_actor_id", "")
        if not aid:
            continue
        short = aid.split("/")[-1] if "/" in aid else aid
        _add_node(aid, short, "agent")
        edges.append({"from": aid, "to": org_id, "type": "agent", "caps": end.get("scope", [])})

    # Outbound grants (org → grantee)
    try:
        gs = GrantStore(store.grant_db_path())
        for grant in gs.list_grants(include_revoked=False):
            grantee = grant.get("grantee_actor", "")
            if not grantee:
                continue
            short = grantee.split("/")[-1] if "/" in grantee else grantee
            _add_node(grantee, short, "org")
            edges.append({
                "from": org_id,
                "to": grantee,
                "type": "grant",
                "caps": grant.get("capabilities", []),
            })
    except Exception:
        pass  # grant DB may not exist yet

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        action="orgs.get_network",
        target_kind="org",
        target_id=handle,
        sid_fingerprint=rec.sid_fingerprint,
    )
    return {"org_handle": handle, "nodes": nodes, "edges": edges}

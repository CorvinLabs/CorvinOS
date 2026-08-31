"""Layer 41 Social Capability Grants — console REST routes.

Endpoints (mounted at /v1/console/grants by app.py):

  GET  /grants            → list personal-actor grants
  POST /grants            → issue a new personal-actor grant
  DELETE /grants/{id}     → revoke a grant
  GET  /grants/templates  → built-in grant templates

Must NOT import anthropic (CI AST lint).
"""
from __future__ import annotations

import sys
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from .. import audit as console_audit
from .. import auth as session_auth
from ..deps import require_csrf, require_session
from ..utils import sanitize_grant_doc as _sanitize_grant

_THIS_DIR = Path(__file__).resolve().parent
_SHARED = _THIS_DIR.parents[3] / "operator" / "bridges" / "shared"
if str(_SHARED) not in sys.path:
    sys.path.insert(0, str(_SHARED))

try:
    from grant_store import GrantStore, grant_db_path
    from grant_issuer import build_grant
    from social_actor import load_keypair, actor_doc_path
except ImportError as _e:
    raise ImportError(f"L41 grant modules unavailable: {_e}") from _e

import json

import logging
_log = logging.getLogger(__name__)

router = APIRouter()

# ── Helpers ───────────────────────────────────────────────────────────────────

GRANT_TEMPLATES = [
    {
        "id": "reader",
        "label": "Reader",
        "description": "Read all your public and followers-only domains.",
        "capabilities": ["domain.*.read"],
        "conditions": {},
    },
    {
        "id": "collaborator",
        "label": "Collaborator",
        "description": "Read and publish into all your domains.",
        "capabilities": ["domain.*.read", "domain.*.publish"],
        "conditions": {},
    },
    {
        "id": "agent-delegate",
        "label": "Agent Delegate",
        "description": "Send tasks to your agents and deliver A2A envelopes.",
        "capabilities": ["agent.invoke.*", "a2a.send"],
        "conditions": {},
    },
    {
        "id": "full-trust",
        "label": "Full Trust",
        "description": "All capabilities. Use with care.",
        "capabilities": [
            "domain.*.read",
            "domain.*.publish",
            "agent.invoke.*",
            "forge.exec.*",
            "a2a.send",
            "social.graph.read",
        ],
        "conditions": {},
        "requires_confirmation": True,
    },
]

def _local_actor_id(tenant_id: str) -> str:
    path = actor_doc_path(tenant_id)
    if not path.exists():
        raise HTTPException(
            http_status.HTTP_412_PRECONDITION_FAILED,
            "CorvinFed not configured — join the federation first.",
        )
    try:
        doc = json.loads(path.read_text(encoding="utf-8"))
        actor_id = doc.get("id") or doc.get("actor_id") or ""
        if not actor_id:
            raise ValueError("no id field")
        return actor_id
    except Exception as exc:
        raise HTTPException(
            http_status.HTTP_500_INTERNAL_SERVER_ERROR,
            "actor document unavailable",
        ) from exc

def _load_private_key(tenant_id: str) -> str:
    try:
        priv, _ = load_keypair(tenant_id=tenant_id)
        return priv
    except Exception as exc:
        raise HTTPException(
            http_status.HTTP_412_PRECONDITION_FAILED,
            "actor keypair not found",
        ) from exc

def _public_key(tenant_id: str) -> str:
    try:
        _, pub = load_keypair(tenant_id=tenant_id)
        return pub
    except Exception:
        return ""

# ── Pydantic schemas ──────────────────────────────────────────────────────────

class GrantCreateRequest(BaseModel):
    grantee_actor: str = Field(..., max_length=256)
    capabilities: list[str] = Field(..., min_length=1, max_length=20)
    conditions: dict[str, Any] = Field(default_factory=dict)
    model_config = {"extra": "forbid"}

# ── Routes ────────────────────────────────────────────────────────────────────

@router.get("/templates")
def list_templates() -> dict:
    return {"templates": GRANT_TEMPLATES}

@router.get("")
def list_grants(
    rec: Annotated[session_auth.SessionRecord, Depends(require_session)],
    grantee_actor: str | None = None,
    include_revoked: bool = False,
) -> dict:
    store = GrantStore(grant_db_path(tenant_id=rec.tenant_id))
    grants = store.list_grants(
        grantee_actor=grantee_actor,
        include_revoked=include_revoked,
    )
    actor_id = ""
    try:
        actor_id = _local_actor_id(rec.tenant_id)
    except HTTPException:
        pass
    return {
        "local_actor_id": actor_id,
        "grants": [_sanitize_grant(g) for g in grants],
        "ts": time.time(),
    }

@router.post("")
def create_grant(
    body: GrantCreateRequest,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    actor_id = _local_actor_id(rec.tenant_id)
    priv_hex = _load_private_key(rec.tenant_id)

    try:
        from grant_issuer import validate_capabilities, validate_conditions, GrantError
        validate_capabilities(body.capabilities)
        validate_conditions(body.conditions)
    except Exception as exc:
        raise HTTPException(http_status.HTTP_422_UNPROCESSABLE_ENTITY, "invalid request") from exc

    try:
        doc = build_grant(
            grantor_actor=actor_id,
            grantee_actor=body.grantee_actor,
            capabilities=body.capabilities,
            conditions=body.conditions,
            private_key_hex=priv_hex,
        )
    except Exception as exc:
        raise HTTPException(
            http_status.HTTP_422_UNPROCESSABLE_ENTITY, "grant build failed"
        ) from exc

    store = GrantStore(grant_db_path(tenant_id=rec.tenant_id))
    store.save_grant(doc)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="grant.issued",
        target_kind="grant",
        target_id=doc["grant_id"],
    )
    return {"ok": True, "grant": _sanitize_grant(doc), "ts": time.time()}

@router.delete("/{grant_id}")
def revoke_grant(
    grant_id: str,
    rec: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict:
    store = GrantStore(grant_db_path(tenant_id=rec.tenant_id))
    doc = store.get_grant(grant_id)
    if doc is None:
        raise HTTPException(http_status.HTTP_404_NOT_FOUND, "grant not found")

    store.set_revoked(grant_id)

    console_audit.action_performed(
        tenant_id=rec.tenant_id,
        sid_fingerprint=rec.sid_fingerprint,
        action="grant.revoked",
        target_kind="grant",
        target_id=grant_id,
    )
    return {"ok": True, "ts": time.time()}

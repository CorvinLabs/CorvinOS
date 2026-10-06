"""Federation Routes — Local Agent Registry (CONCEPT-0097 Phase 1).

Phase 1 scope only: which worker-engine deployments exist on THIS
installation. Cross-peer discovery/delegation over A2A is explicitly
deferred (see the Phase 1 ADR) pending a dedicated review of the A2A wire
protocol it would extend.
"""
from __future__ import annotations

from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from core.federation.audit import FederationAuditError
from core.federation.local_agent import LocalAgentError, LocalAgentRegistry

from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(prefix="/federation", tags=["federation"])


class RegisterLocalAgentRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    engine_type: str = Field(..., min_length=1)
    capabilities: list[str] = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    max_concurrent: int = Field(default=1, ge=1)
    cost_per_task_usd: float = Field(default=0.0, ge=0.0)


def _agent_to_response(agent: Any) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "engine_type": agent.engine_type,
        "capabilities": sorted(agent.capabilities),
        "model": agent.model,
        "max_concurrent": agent.max_concurrent,
        "cost_per_task_usd": agent.cost_per_task_usd,
        "registered_at": agent.registered_at,
        "scope": "local",
    }


@router.post("/agents", status_code=http_status.HTTP_201_CREATED)
async def register_local_agent(
    body: RegisterLocalAgentRequest,
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Register a local worker-engine deployment as a federation agent."""
    tenant_id = session.get("tenant_id") if isinstance(session, dict) else getattr(session, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="tenant_id missing")

    registry = LocalAgentRegistry(tenant_id)
    try:
        agent = registry.register(
            agent_id=body.agent_id,
            engine_type=body.engine_type,
            capabilities=body.capabilities,
            model=body.model,
            max_concurrent=body.max_concurrent,
            cost_per_task_usd=body.cost_per_task_usd,
        )
    except LocalAgentError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FederationAuditError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"registration could not be recorded: {exc}",
        ) from exc
    return _agent_to_response(agent)


@router.get("/agents")
async def list_local_agents(
    capability: Optional[str] = None,
    session: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    """List all local federation agents for the caller's tenant."""
    tenant_id = session.get("tenant_id") if isinstance(session, dict) else getattr(session, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="tenant_id missing")

    registry = LocalAgentRegistry(tenant_id)
    if capability:
        agents = registry.list_agents_by_capability(capability)
    else:
        agents = registry.list_agents()
    return {"agents": [_agent_to_response(a) for a in agents]}


@router.get("/agents/{agent_id}")
async def get_local_agent(
    agent_id: str,
    session: dict[str, Any] = Depends(require_session),
) -> dict[str, Any]:
    tenant_id = session.get("tenant_id") if isinstance(session, dict) else getattr(session, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="tenant_id missing")

    registry = LocalAgentRegistry(tenant_id)
    try:
        agent = registry.get(agent_id)
    except LocalAgentError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    if agent is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="agent not found")
    return _agent_to_response(agent)


@router.delete("/agents/{agent_id}")
async def deregister_local_agent(
    agent_id: str,
    session: dict[str, Any] = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    tenant_id = session.get("tenant_id") if isinstance(session, dict) else getattr(session, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="tenant_id missing")

    registry = LocalAgentRegistry(tenant_id)
    try:
        removed = registry.deregister(agent_id)
    except LocalAgentError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FederationAuditError as exc:
        raise HTTPException(
            status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"deregistration could not be recorded: {exc}",
        ) from exc
    if not removed:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="agent not found")
    return {"agent_id": agent_id, "deregistered": True}

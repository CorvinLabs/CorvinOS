"""Federation Routes — Local Agent Registry (CONCEPT-0097 Phase 1).

Phase 1 (ADR-2230): which worker-engine deployments exist on THIS
installation. Phases 2-4 (ADR-2231 identity, ADR-2232 wire extension):
peer catalogs over signed A2A, agent selection across installations,
delegation of one task to one peer agent, and the cross-peer hop trace.
"""
from __future__ import annotations

import asyncio
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, status as http_status
from pydantic import BaseModel, Field

from core.federation.audit import FederationAuditError
from core.federation.delegation import DelegationError, delegate, rank_candidates, trace
from core.federation.local_agent import LocalAgentError, LocalAgentRegistry
from core.federation.peer_catalog import PeerCatalog, PeerCatalogError

from ..deps import require_session, require_session_csrf_on_mutation

router = APIRouter(prefix="/federation", tags=["federation"])


class RegisterLocalAgentRequest(BaseModel):
    agent_id: str = Field(..., min_length=1, max_length=128)
    engine_type: str = Field(..., min_length=1)
    capabilities: list[str] = Field(..., min_length=1)
    model: str = Field(..., min_length=1)
    max_concurrent: int = Field(default=1, ge=1)
    cost_per_task_usd: float = Field(default=0.0, ge=0.0)
    # Offer this agent to paired A2A peers (ADR-2232). Off unless asked for;
    # re-POST the same agent_id to change it.
    federable: bool = False


def _agent_to_response(agent: Any) -> dict[str, Any]:
    return {
        "agent_id": agent.agent_id,
        "engine_type": agent.engine_type,
        "capabilities": sorted(agent.capabilities),
        "model": agent.model,
        "max_concurrent": agent.max_concurrent,
        "cost_per_task_usd": agent.cost_per_task_usd,
        "registered_at": agent.registered_at,
        "federable": agent.federable,
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
            federable=body.federable,
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


# ── Cross-peer federation (ADR-2232, CONCEPT-0097 Phases 2-4) ──────────────

_ENDPOINT_ID_CHARS = frozenset("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_.")


def _tenant_of(session: Any) -> str:
    tenant_id = session.get("tenant_id") if isinstance(session, dict) else getattr(session, "tenant_id", None)
    if not tenant_id:
        raise HTTPException(status_code=http_status.HTTP_401_UNAUTHORIZED, detail="tenant_id missing")
    return tenant_id


def _check_endpoint_id(endpoint_id: str) -> str:
    if not endpoint_id or len(endpoint_id) > 128 or not all(c in _ENDPOINT_ID_CHARS for c in endpoint_id):
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid endpoint_id")
    return endpoint_id


def _peer_agent_to_response(p: Any) -> dict[str, Any]:
    return {
        "agent_id": p.agent_id, "address": p.address, "endpoint_id": p.endpoint_id,
        "peer_instance_id": p.peer_instance_id, "capabilities": sorted(p.capabilities),
        "model": p.model, "max_concurrent": p.max_concurrent,
        "cost_per_task_usd": p.cost_per_task_usd, "fetched_at": p.fetched_at, "scope": "peer",
    }


@router.get("/peers")
async def list_federation_peers(session: Any = Depends(require_session)) -> dict[str, Any]:
    """Peers whose agent catalog this installation has fetched."""
    tenant_id = _tenant_of(session)
    return {"peers": await asyncio.to_thread(lambda: PeerCatalog(tenant_id).peers())}


@router.post("/peers/{endpoint_id}/refresh")
async def refresh_peer_catalog(
    endpoint_id: str, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Fetch a paired peer's federable agents over signed A2A."""
    tenant_id = _tenant_of(session)
    endpoint_id = _check_endpoint_id(endpoint_id)
    try:
        agents = await asyncio.to_thread(PeerCatalog(tenant_id).refresh, endpoint_id)
    except PeerCatalogError as exc:
        raise HTTPException(status_code=http_status.HTTP_502_BAD_GATEWAY,
                            detail=f"catalog fetch failed: {exc.reason}") from exc
    except FederationAuditError as exc:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"catalog fetch could not be recorded: {exc}") from exc
    return {"endpoint_id": endpoint_id, "agents": [_peer_agent_to_response(a) for a in agents]}


@router.get("/peer-agents")
async def list_peer_agents(
    capability: Optional[str] = None, session: Any = Depends(require_session),
) -> dict[str, Any]:
    """Agents advertised by peers (fresh catalogs only)."""
    tenant_id = _tenant_of(session)
    agents = await asyncio.to_thread(lambda: PeerCatalog(tenant_id).agents())
    if capability:
        agents = [a for a in agents if capability in a.capabilities]
    return {"agents": [_peer_agent_to_response(a) for a in agents]}


class SelectAgentRequest(BaseModel):
    capability: str = Field(..., min_length=1)
    model: Optional[str] = None
    include_local: bool = True
    include_peers: bool = True
    prefer_local: bool = True


@router.post("/select")
async def select_federation_agent(
    body: SelectAgentRequest, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Rank local and peer agents for one capability (advisory, runs nothing)."""
    tenant_id = _tenant_of(session)
    try:
        ranked = await asyncio.to_thread(lambda: rank_candidates(
            tenant_id, body.capability, model=body.model,
            include_local=body.include_local, include_peers=body.include_peers,
            prefer_local=body.prefer_local))
    except DelegationError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return {"capability": body.capability, "candidates": [c.to_dict() for c in ranked]}


class DelegateRequest(BaseModel):
    instruction: str = Field(..., min_length=1, max_length=16384)
    capability: Optional[str] = None
    endpoint_id: Optional[str] = None
    agent_id: Optional[str] = Field(default=None, max_length=128)
    parent_task_id: Optional[str] = Field(default=None, max_length=256)
    ttl_s: Optional[int] = Field(default=None, ge=1, le=3600)


@router.post("/delegate")
async def delegate_federated_task(
    body: DelegateRequest, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Run one task on a peer agent over signed A2A."""
    tenant_id = _tenant_of(session)
    endpoint_id = _check_endpoint_id(body.endpoint_id) if body.endpoint_id else None
    try:
        result = await asyncio.to_thread(
            lambda: delegate(
                tenant_id, instruction=body.instruction, capability=body.capability,
                endpoint_id=endpoint_id, agent_id=body.agent_id,
                parent_task_id=body.parent_task_id, ttl_s=body.ttl_s))
    except DelegationError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except FederationAuditError as exc:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"delegation could not be recorded, nothing was sent: {exc}") from exc
    return result.to_dict()


@router.get("/tasks/{task_id}/trace")
async def trace_federated_task(task_id: str, session: Any = Depends(require_session)) -> dict[str, Any]:
    """Hop tree of a delegated task, with both chains' anchor hashes per hop."""
    if not task_id or len(task_id) > 256:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail="invalid task_id")
    tenant_id = _tenant_of(session)
    try:
        return await asyncio.to_thread(trace, tenant_id, task_id)
    except DelegationError as exc:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc


# ── Agent-to-agent conversations (ADR-2234) ──────────────────────────────

class StartConversationRequest(BaseModel):
    local_agent_id: str = Field(..., min_length=1, max_length=128)
    endpoint_id: str = Field(..., min_length=1, max_length=128)
    peer_agent_id: str = Field(..., min_length=1, max_length=128)
    opener: str = Field(..., min_length=1, max_length=2000)
    max_turns: int = Field(default=6, ge=1, le=12)
    first_speaker: str = Field(default="local", pattern="^(local|peer)$")


def _conversation_or_404(fn: Any, *args: Any, **kw: Any) -> Any:
    from core.federation import conversation as conv
    try:
        return fn(*args, **kw)
    except conv.ConversationError as exc:
        raise HTTPException(status_code=http_status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    except KeyError as exc:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND,
                            detail="conversation not found") from exc


@router.post("/conversations", status_code=http_status.HTTP_202_ACCEPTED)
async def start_agent_conversation(
    body: StartConversationRequest, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    """Start a moderated conversation between a local and a peer agent."""
    from core.federation import conversation as conv
    tenant_id = _tenant_of(session)
    endpoint_id = _check_endpoint_id(body.endpoint_id)
    try:
        return await asyncio.to_thread(
            _conversation_or_404, conv.start, tenant_id, local_agent_id=body.local_agent_id,
            endpoint_id=endpoint_id, peer_agent_id=body.peer_agent_id, opener=body.opener,
            max_turns=body.max_turns, first_speaker=body.first_speaker)
    except FederationAuditError as exc:
        raise HTTPException(status_code=http_status.HTTP_503_SERVICE_UNAVAILABLE,
                            detail=f"conversation could not be recorded, nothing was sent: {exc}") from exc


@router.get("/conversations")
async def list_agent_conversations(session: Any = Depends(require_session)) -> dict[str, Any]:
    from core.federation import conversation as conv
    tenant_id = _tenant_of(session)
    return {"conversations": await asyncio.to_thread(conv.list_conversations, tenant_id)}


@router.get("/conversations/{conversation_id}")
async def get_agent_conversation(
    conversation_id: str, after_seq: int = -1, session: Any = Depends(require_session),
) -> dict[str, Any]:
    """Transcript (``messages`` with ``seq > after_seq``) plus live status — poll this."""
    from core.federation import conversation as conv
    tenant_id = _tenant_of(session)
    return await asyncio.to_thread(_conversation_or_404, conv.get, tenant_id,
                                   conversation_id, after_seq=after_seq)


@router.post("/conversations/{conversation_id}/stop")
async def stop_agent_conversation(
    conversation_id: str, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    from core.federation import conversation as conv
    tenant_id = _tenant_of(session)
    stopping = await asyncio.to_thread(_conversation_or_404, conv.stop, tenant_id, conversation_id)
    return {"conversation_id": conversation_id, "stopping": stopping}


@router.delete("/conversations/{conversation_id}")
async def delete_agent_conversation(
    conversation_id: str, session: Any = Depends(require_session_csrf_on_mutation),
) -> dict[str, Any]:
    from core.federation import conversation as conv
    tenant_id = _tenant_of(session)
    if not await asyncio.to_thread(_conversation_or_404, conv.delete, tenant_id, conversation_id):
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return {"deleted": conversation_id}

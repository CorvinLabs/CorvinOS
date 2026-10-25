"""RAG Integration routes — Retrieval-Augmented Generation endpoints.

Wires the Phase 3 orchestrator (rag_orchestrator.py) per tenant. When no
orchestrator / no providers are registered, the endpoints return an honest
EMPTY state — fabricated mock providers / results are NEVER served (a fresh
install genuinely has no backends and the UI must reflect that).

Endpoints:
- GET /rag/providers — list all RAG providers with health status
- GET /rag/providers/{id}/health — health check for a specific provider
- POST /rag/query — execute a RAG query across providers
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Annotated, Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field


from .. import auth as session_auth
from ..deps import require_csrf, require_session

from .. import _bootstrap
_forge_paths = _bootstrap.forge_paths

logger = logging.getLogger(__name__)

_STATISTICS_AGGS = {}  # Statistics aggregators (per tenant)

# ── Phase 3 Orchestrator Integration ──────────────────────────
# Try to import the Phase 3 RAG orchestrator; when unavailable or when no
# providers are registered, endpoints return an honest EMPTY state (never
# fabricated mock providers/results).

_ORCHESTRATORS: dict[str, tuple[Any, Any]] = {}  # tenant_id -> (signature, RAGOrchestrator)
_ORCHESTRATORS_LOCK = asyncio.Lock()


def _registry_signature(registry_dir: Any) -> tuple:
    """Provider ids + manifest mtimes: a change anywhere rebuilds the orchestrator."""
    from shared import rag_registry_store as store  # noqa: PLC0415

    sig = []
    for pid in store.provider_ids(registry_dir):
        path = store.find_manifest(registry_dir, pid)
        try:
            sig.append((pid, path.stat().st_mtime_ns if path else 0))
        except OSError:
            sig.append((pid, 0))
    return tuple(sig)


def invalidate_orchestrator(tenant_id: str) -> None:
    """Drop the cached orchestrator (call after a provider is created/imported/removed)."""
    _ORCHESTRATORS.pop(tenant_id, None)


async def _get_orchestrator(tenant_id: str) -> Any | None:
    """Return the RAG orchestrator for the tenant, or None when no provider exists.

    Keyed by tenant_id from the authenticated session (never env). The cached
    orchestrator is reused while the registry is unchanged (so circuit-breaker
    state survives) and rebuilt as soon as a provider is added, replaced or
    removed - including by something other than this process. A missing registry
    is NOT cached: the first provider created after a failed lookup is seen on
    the next request (a cached ``None`` used to hide it until restart).
    """
    async with _ORCHESTRATORS_LOCK:
        try:
            from shared.rag_orchestrator import RAGOrchestrator  # noqa: PLC0415

            registry_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag"
            if not registry_dir.exists():
                _ORCHESTRATORS.pop(tenant_id, None)
                return None
            signature = _registry_signature(registry_dir)
            cached = _ORCHESTRATORS.get(tenant_id)
            if cached is not None and cached[0] == signature:
                return cached[1]
            if not signature:
                _ORCHESTRATORS.pop(tenant_id, None)
                return None
            orch = RAGOrchestrator(registry_dir=registry_dir, auth_tokens=_VaultTokens())
            await orch.initialize()
            logger.info("RAG Orchestrator initialized for tenant=%s (%s providers)",
                        tenant_id, len(orch.engines))
            _ORCHESTRATORS[tenant_id] = (signature, orch)
            return orch
        except ImportError as e:
            logger.warning(f"RAG orchestrator not available: {e}")
            return None
        except Exception as e:
            logger.error(f"Failed to initialize orchestrator for tenant={tenant_id}: {e}")
            return None


from shared.rag_service import (  # noqa: E402
    VaultTokens as _VaultTokens,
    health_word as _health_word,
    manifest_summary as _manifest_summary,
)


router = APIRouter(prefix="/rag", tags=["console-rag"])


def _safe_source_url(value: Any) -> str | None:
    """http(s)-only allowlist for provider-supplied result links (R2-C1).

    Delegates to the query engine's shared implementation when importable so
    the two layers cannot drift; falls back to an equivalent local check.
    """
    try:
        from shared.rag_query_engine import safe_http_url  # noqa: PLC0415

        return safe_http_url(value)
    except ImportError:
        pass
    if not isinstance(value, str):
        return None
    text = value.strip()
    if not text or len(text) > 2048 or any(ch in text for ch in "\r\n\t\x00"):
        return None
    from urllib.parse import urlsplit  # noqa: PLC0415

    try:
        parts = urlsplit(text)
        host = parts.hostname
    except ValueError:
        return None
    if parts.scheme.lower() not in ("http", "https") or not host:
        return None
    return text



# ── Data Models ──────────────────────────────────────────────

class RAGProvider:
    """Provider info with health status and query stats."""

    def __init__(
        self,
        id: str,
        name: str,
        status: str = "active",
        health_status: str = "healthy",
        latency_ms: int = 50,
        total_queries: int = 0,
        queries_today: int = 0,
        avg_latency_ms: int = 50,
    ):
        self.id = id
        self.name = name
        self.status = status
        self.health_status = health_status
        self.latency_ms = latency_ms
        self.query_stats = {
            "total_queries": total_queries,
            "queries_today": queries_today,
            "average_latency_ms": avg_latency_ms,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "health_status": self.health_status,
            "latency_ms": self.latency_ms,
            "query_stats": self.query_stats,
        }


# ── Endpoints ────────────────────────────────────────────────

@router.get("/providers")
async def list_providers(
    _session: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """List registered RAG providers with health and query counts (empty when none).

    ``registered_count`` comes from the same counter the licence gate uses, so
    the badge and the 402 can never disagree.
    """
    from shared import rag_registry_store as store  # noqa: PLC0415

    tid = _session.tenant_id
    registry_dir = _forge_paths.tenant_global_dir(tid) / "rag"
    registered_count = store.count(registry_dir)

    orch = await _get_orchestrator(tid)
    if orch is not None:
        try:
            health = await orch.health_check_all()
            stats = store.query_stats(registry_dir)
            providers = []
            for provider_id, status in health.items():
                info = _manifest_summary(registry_dir, provider_id)
                qs = stats.get(provider_id, {})
                latency = status.get("latency_ms", 0)
                provider = RAGProvider(
                    id=provider_id,
                    name=info.get("name") or provider_id,
                    status="active",
                    health_status=_health_word(status),
                    latency_ms=latency,
                    total_queries=qs.get("total_queries", 0),
                    queries_today=qs.get("queries_today", 0),
                    avg_latency_ms=qs.get("average_latency_ms", 0),
                ).to_dict()
                provider["circuit_state"] = status.get("circuit_state", "unknown")
                provider["error"] = status.get("error")
                provider["data_classification"] = info.get("data_classification", "PUBLIC")
                provider["compliance_zone"] = info.get("compliance_zone", "")
                provider["capabilities"] = info.get("capabilities", [])
                providers.append(provider)
            if providers:
                return {"providers": providers, "registered_count": registered_count}
        except Exception as e:
            logger.warning(f"Failed to load providers from orchestrator: {e}")

    # No orchestrator / no providers: an EMPTY list, never fabricated providers.
    return {"providers": [], "registered_count": registered_count}


@router.get("/providers/{provider_id}/health")
async def get_provider_health(
    provider_id: str,
    _session: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Health of ONE registered provider (probes only that provider)."""
    from shared import rag_registry_store as store  # noqa: PLC0415

    tid = _session.tenant_id
    orch = await _get_orchestrator(tid)
    if orch is not None:
        try:
            status = await orch.health_check_one(provider_id)
            if status is not None:
                registry_dir = _forge_paths.tenant_global_dir(tid) / "rag"
                qs = store.query_stats(registry_dir).get(provider_id, {})
                latency_ms = status.get("latency_ms", 0)
                return {
                    "id": provider_id,
                    "name": _manifest_summary(registry_dir, provider_id).get("name") or provider_id,
                    "status": "active",
                    "health_status": _health_word(status),
                    "circuit_state": status.get("circuit_state", "unknown"),
                    "error": status.get("error"),
                    "latency_ms": latency_ms,
                    "query_stats": {
                        "total_queries": qs.get("total_queries", 0),
                        "queries_today": qs.get("queries_today", 0),
                        "average_latency_ms": qs.get("average_latency_ms", latency_ms),
                    },
                }
        except Exception as e:
            logger.warning(f"Provider health check failed: {e}")

    return {
        "id": provider_id,
        "status": "inactive",
        "health_status": "unknown",
        "latency_ms": 0,
        "query_stats": {"total_queries": 0, "queries_today": 0, "average_latency_ms": 0},
        "error": "Provider not found",
    }


class RAGQueryRequest(BaseModel):
    query: str = Field(..., max_length=8192)
    limit: int = Field(default=5, ge=1, le=100)
    preferred_providers: list[str] = Field(default_factory=list, max_length=20)
    timeout_ms: int = Field(default=5000, ge=100, le=30000)
    classification: str = Field(default="PUBLIC", max_length=32)
    model_config = {"extra": "forbid"}


@router.post("/query")
async def execute_rag_query(
    req: RAGQueryRequest,
    _session: Annotated[session_auth.SessionRecord, Depends(require_csrf)],
) -> dict[str, Any]:
    """Execute a RAG query across providers (empty result when none are registered).

    Gate, run, count and audit are shared with the chat tool ``rag_query``
    (``shared.rag_service.run_query``).
    """
    from shared import rag_service  # noqa: PLC0415

    tenant_id = _session.tenant_id
    return await rag_service.run_query(
        tenant_id,
        _forge_paths.tenant_global_dir(tenant_id) / "rag",
        await _get_orchestrator(tenant_id),
        req.query,
        limit=req.limit,
        preferred_providers=req.preferred_providers,
        timeout_ms=req.timeout_ms,
        classification=req.classification,
        safe_url=_safe_source_url,
    )


@router.get("/statistics")
async def get_statistics(
    _session: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Get aggregated RAG statistics (query metrics, performance)."""
    tenant_id = _session.tenant_id

    try:
        from shared.rag_statistics import (
            RAGStatisticsAggregator,
        )

        # Honour CORVIN_HOME via the shared tenant-path helper (NOT a hardcoded
        # Path.home()/".corvin" — that ignored the configured runtime root and
        # pointed the aggregator at the wrong tree).
        registry_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag"
        if tenant_id not in _STATISTICS_AGGS:
            _STATISTICS_AGGS[tenant_id] = RAGStatisticsAggregator(registry_dir)

        stats = _STATISTICS_AGGS[tenant_id].aggregate()
        return {"statistics": stats.to_dict()}
    except Exception as e:
        logger.warning(f"Statistics aggregation failed: {e}")
        return {
            "statistics": {
                "total_queries": 0,
                "queries_today": 0,
                "average_latency_ms": 0.0,
                "cache_hit_rate": 0.0,
                "error": str(e),
            }
        }

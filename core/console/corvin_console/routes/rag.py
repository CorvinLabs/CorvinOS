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

# ── L34 Data Classification Gate ───────────────────────────────
# Import at module level to initialize gate per tenant

_L34_GATES = {}  # Cached per tenant_id
_AUDIT_EMITTERS = {}  # L16 audit events (per tenant)
_STATISTICS_AGGS = {}  # Statistics aggregators (per tenant)


def _get_l34_gate(tenant_id: str):
    """Get or create L34 gate for tenant."""
    if tenant_id not in _L34_GATES:
        try:
            from shared.rag_data_classification import (
                L34DataClassificationGate,
            )
            _L34_GATES[tenant_id] = L34DataClassificationGate(tenant_id)
            logger.info(f"✅ L34 gate initialized for tenant {tenant_id}")
        except ImportError:
            logger.warning("L34 data classification not available, skipping gate")
            return None

    return _L34_GATES.get(tenant_id)

# ── Phase 3 Orchestrator Integration ──────────────────────────
# Try to import the Phase 3 RAG orchestrator; when unavailable or when no
# providers are registered, endpoints return an honest EMPTY state (never
# fabricated mock providers/results).

_ORCHESTRATORS: dict[str, Any] = {}  # tenant_id → RAGOrchestrator | None
_ORCHESTRATORS_LOCK = asyncio.Lock()


async def _get_orchestrator(tenant_id: str) -> Any | None:
    """Return the Phase 3 RAG orchestrator for the given tenant, or None.

    Keyed by tenant_id from the authenticated session — never from env-var —
    so each tenant gets an isolated orchestrator pointing to its own registry.
    _ORCHESTRATORS_LOCK serialises the entire init path so concurrent first
    requests for the same tenant cannot double-init the orchestrator.

    async, not sync-wrapping-asyncio.run(): every caller is itself an async
    route handler already running on the event loop, and asyncio.run() raises
    "cannot be called from a running event loop" in that context (the /query
    route works around exactly this by hopping to a worker thread first via
    asyncio.to_thread() — this function instead just awaits directly, since
    it has no need for a separate thread).
    """
    async with _ORCHESTRATORS_LOCK:
        if tenant_id in _ORCHESTRATORS:
            return _ORCHESTRATORS[tenant_id]

        try:
            from shared.rag_orchestrator import RAGOrchestrator  # noqa: PLC0415

            registry_dir = _forge_paths.tenant_global_dir(tenant_id) / "rag"
            if registry_dir.exists():
                auth_tokens: dict = {}  # Load from vault in production
                orch = RAGOrchestrator(registry_dir=registry_dir, auth_tokens=auth_tokens)
                # RAGOrchestrator.__init__ only sets up the (empty) engines dict —
                # providers are loaded from registry.json/manifests/*.yaml by the
                # async initialize() step, which nothing here was calling. Every
                # registered provider therefore silently vanished from
                # health_check_all() regardless of what was on disk.
                await orch.initialize()
                logger.info(f"RAG Orchestrator initialized for tenant={tenant_id} ({registry_dir})")
                _ORCHESTRATORS[tenant_id] = orch
                return orch
            logger.warning(f"RAG registry not found for tenant={tenant_id}: {registry_dir}")
            _ORCHESTRATORS[tenant_id] = None
            return None
        except ImportError as e:
            logger.warning(f"Phase 3 orchestrator not available: {e}")
            _ORCHESTRATORS[tenant_id] = None
            return None
        except Exception as e:
            logger.error(f"Failed to initialize orchestrator for tenant={tenant_id}: {e}")
            _ORCHESTRATORS[tenant_id] = None
            return None

router = APIRouter(prefix="/rag", tags=["console-rag"])


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
    """List all registered RAG providers with health status (empty when none).

    Always includes `registered_count` — the number of actual YAML manifests in
    the tenant's RAG registry dir.  This is the authoritative count used by the
    licence-gate UI; the empty-state path returns no providers, so it can never
    inflate this count.
    """
    # Count actual registered YAML manifests (the licence-gate source of truth).
    # RAGOrchestrator.initialize() loads manifests from registry_dir/manifests/
    # (see rag_orchestrator.py), not registry_dir itself -- glob the same place
    # the loader reads, or this count silently stays 0 forever even with
    # providers registered.
    tid = _session.tenant_id
    registry_dir = _forge_paths.tenant_global_dir(tid) / "rag"
    manifests_dir = registry_dir / "manifests"
    registered_count = len(list(manifests_dir.glob("*.yaml"))) if manifests_dir.exists() else 0

    orch = await _get_orchestrator(tid)
    if orch is not None:
        try:
            # Get health status of all providers via orchestrator
            health = await orch.health_check_all()
            providers = []

            # Transform orchestrator output to API format
            for provider_id, status in health.items():
                providers.append(
                    RAGProvider(
                        id=provider_id,
                        name=provider_id.replace("-", " ").title(),
                        status="active",
                        health_status=status.get("circuit_state", "unknown"),
                        latency_ms=status.get("latency_ms", 0),
                        total_queries=0,  # Populated from registry in Phase 5.1
                        queries_today=0,
                        avg_latency_ms=status.get("latency_ms", 0),
                    )
                )

            if providers:
                logger.info(f"Loaded {len(providers)} providers from orchestrator")
                return {"providers": [p.to_dict() for p in providers], "registered_count": registered_count}
        except Exception as e:
            logger.warning(f"Failed to load providers from orchestrator: {e}")
            # Fall through to the honest empty-state response below.

    # Fallback: no orchestrator (or it returned no providers). Serve an EMPTY
    # provider list so the real "No providers registered" empty state shows.
    # Fabricated demo/mock providers must NEVER be served outside an explicit
    # demo flag — they misrepresent a fresh install as having live backends.
    return {
        "providers": [],
        "registered_count": registered_count,
    }


@router.get("/providers/{provider_id}/health")
async def get_provider_health(
    provider_id: str,
    _session: Annotated[session_auth.SessionRecord, Depends(require_session)],
) -> dict[str, Any]:
    """Health check for a specific registered provider (via orchestrator).

    Returns a "Provider not found" response when the provider is not registered
    — fabricated mock providers are never served.
    """
    orch = await _get_orchestrator(_session.tenant_id)
    if orch is not None:
        try:
            health = await orch.health_check_all()
            status = health.get(provider_id)
            if status is not None:
                latency_ms = status.get("latency_ms", 0)
                return {
                    "id": provider_id,
                    "name": provider_id.replace("-", " ").title(),
                    "status": "active",
                    "health_status": status.get("circuit_state", "unknown"),
                    "latency_ms": latency_ms,
                    # ProviderCard reads query_stats.{total_queries,queries_today}
                    # unconditionally (current = health || provider) -- omitting
                    # this crashed the whole page with "Cannot read properties of
                    # undefined (reading 'total_queries')" the moment a real
                    # provider replaced the list-endpoint's RAGProvider default.
                    # Per-provider query counting isn't implemented yet (see
                    # RAGProvider.to_dict() above), so this is honestly zeroed,
                    # not fabricated.
                    "query_stats": {
                        "total_queries": 0,
                        "queries_today": 0,
                        "average_latency_ms": latency_ms,
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
    """Execute a RAG query across providers (Phase 3 orchestrator; empty when none)."""
    query_text = req.query
    limit = req.limit
    preferred_providers = req.preferred_providers
    timeout_ms = req.timeout_ms
    classification_str = req.classification

    # ── L34: Data Classification Gate ──────────────────────────
    tenant_id = _session.tenant_id
    l34_gate = _get_l34_gate(tenant_id)

    if l34_gate:
        try:
            from shared.rag_data_classification import (
                DataClassification,
            )

            # Parse classification level
            try:
                classification = DataClassification[classification_str.upper()]
            except KeyError:
                classification = DataClassification.PUBLIC

            # Check gate (fail-closed)
            allowed, denial_reason = l34_gate.check(query_text, classification)
            if not allowed:
                # L16: Emit audit event for blocked query
                try:
                    from shared.rag_audit_events import (
                        RAGAuditEmitter,
                    )
                    if tenant_id not in _AUDIT_EMITTERS:
                        _AUDIT_EMITTERS[tenant_id] = RAGAuditEmitter()
                    _AUDIT_EMITTERS[tenant_id].query_blocked(
                        tenant_id,
                        reason=denial_reason,
                        classification=classification.name,
                    )
                except Exception as e:
                    logger.warning(f"Audit emit failed: {e}")

                return {
                    "items": [],
                    "total_time_ms": 0,
                    "providers_queried": 0,
                    "cache_hit": False,
                    "error": denial_reason,
                    "blocked_by": "L34_DATA_CLASSIFICATION",
                }
        except Exception as e:
            # L34 fail-closed per ADR-0042: gate errors must block the query,
            # not silently allow it. Log and return a blocked response.
            logger.error(f"L34 gate check error (fail-closed): {e}")
            return {
                "items": [],
                "total_time_ms": 0,
                "providers_queried": 0,
                "cache_hit": False,
                "error": "data_classification_gate_unavailable",
                "blocked_by": "L34_DATA_CLASSIFICATION",
            }

    if not query_text.strip():
        return {
            "items": [],
            "total_time_ms": 0,
            "providers_queried": 0,
            "cache_hit": False,
            "error": "Empty query",
        }

    start = time.time()

    # Execute via the Phase 3 orchestrator if one exists for this tenant.
    _query_tid = _session.tenant_id
    orch = await _get_orchestrator(_query_tid)
    if orch is not None:
        try:
            from shared.rag_query_engine import RAGQuery  # noqa: PLC0415

            # Create query and execute via orchestrator
            rag_query = RAGQuery(
                query=query_text,
                limit=limit,
                preferred_providers=preferred_providers or None,
                timeout_ms=timeout_ms,
            )

            # Execute asynchronously
            results = await asyncio.to_thread(
                lambda: asyncio.run(orch.query(rag_query))
            )

            elapsed_ms = int((time.time() - start) * 1000)

            # L16: Emit hash-chained audit event for the executed query.
            try:
                from shared.rag_audit_events import RAGAuditEmitter
                if tenant_id not in _AUDIT_EMITTERS:
                    _AUDIT_EMITTERS[tenant_id] = RAGAuditEmitter()
                _AUDIT_EMITTERS[tenant_id].query_executed(
                    tenant_id,
                    provider_count=len(preferred_providers),
                    result_count=len(results),
                    latency_ms=elapsed_ms,
                    cache_hit=False,
                    classification=classification_str,
                )
            except Exception as e:
                logger.warning(f"Audit emit failed: {e}")

            return {
                "items": [
                    {
                        "content": item.content,
                        "score": item.score,
                        "metadata": item.metadata,
                        "source_url": item.source_url,
                    }
                    for item in results
                ],
                "total_time_ms": elapsed_ms,
                "providers_queried": len(preferred_providers),
                "cache_hit": False,
            }
        except Exception as e:
            logger.warning(f"Orchestrator query failed: {e}")
            elapsed_ms = int((time.time() - start) * 1000)
            return {
                "items": [],
                "total_time_ms": elapsed_ms,
                "providers_queried": 0,
                "cache_hit": False,
                "status": "query_failed",
                "error": "rag_query_failed",
            }

    # No orchestrator / no providers configured: return an explicit empty
    # result. Fabricated demo results must NEVER be served — a fresh install
    # has no providers and the UI must show that honestly.
    elapsed_ms = int((time.time() - start) * 1000)
    return {
        "items": [],
        "total_time_ms": elapsed_ms,
        "providers_queried": 0,
        "cache_hit": False,
        "status": "no_providers_configured",
    }


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

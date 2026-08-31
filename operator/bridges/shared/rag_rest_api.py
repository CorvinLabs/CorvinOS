"""REST API wrapper for RAG Integration (Phase 4).

Exposes Phase 3 RAG Orchestrator via FastAPI endpoints:
  - GET /rag/providers → list registered providers
  - GET /rag/providers/{id}/health → check provider health
  - POST /rag/query → execute multi-provider query
"""
from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

from fastapi import APIRouter, HTTPException, Query

logger = logging.getLogger(__name__)


# ── Data Models ────────────────────────────────────────────────────

class RAGResultItem:
    """Single result from a provider."""
    def __init__(self, content: str, score: float, metadata: dict, source_url: Optional[str] = None):
        self.content = content
        self.score = score
        self.metadata = metadata
        self.source_url = source_url

    def dict(self) -> dict:
        return {
            "content": self.content,
            "score": self.score,
            "metadata": self.metadata,
            "source_url": self.source_url,
        }


class RAGProvider:
    """Provider entry from registry."""
    def __init__(self, id: str, name: str, status: str, health_status: str, latency_ms: int, query_stats: dict):
        self.id = id
        self.name = name
        self.status = status
        self.health_status = health_status
        self.latency_ms = latency_ms
        self.query_stats = query_stats

    def dict(self) -> dict:
        return {
            "id": self.id,
            "name": self.name,
            "status": self.status,
            "health_status": self.health_status,
            "latency_ms": self.latency_ms,
            "query_stats": self.query_stats,
        }


# ── Mock Registry (Phase 4.1 — Real registry in Phase 2) ────────────

def _get_mock_providers() -> list[RAGProvider]:
    """Mock provider data for initial testing."""
    return [
        RAGProvider(
            id="elasticsearch-docs",
            name="Elasticsearch Knowledge Base",
            status="active",
            health_status="healthy",
            latency_ms=145,
            query_stats={
                "total_queries": 1250,
                "queries_today": 42,
                "average_latency_ms": 138,
            },
        ),
        RAGProvider(
            id="vector-db",
            name="Vector Semantic Search",
            status="active",
            health_status="healthy",
            latency_ms=98,
            query_stats={
                "total_queries": 3100,
                "queries_today": 87,
                "average_latency_ms": 102,
            },
        ),
        RAGProvider(
            id="documentation",
            name="Public API Documentation",
            status="inactive",
            health_status="unknown",
            latency_ms=0,
            query_stats={
                "total_queries": 450,
                "queries_today": 0,
                "average_latency_ms": 156,
            },
        ),
    ]


# ── Mock Orchestrator (Phase 4.1 — Real orchestrator in Phase 3) ────

def _execute_query_mock(query: str, limit: int = 5) -> dict:
    """Mock query execution — returns synthetic results."""
    import time
    import hashlib

    # Simulate processing time
    time.sleep(0.1)

    # Generate consistent results based on query hash
    query_hash = hashlib.md5(query.encode()).hexdigest()
    seed = int(query_hash[:8], 16)

    results = [
        {
            "content": f"Result {i+1}: Relevant information about '{query}' with semantic match.",
            "score": max(0.5, min(1.0, 0.95 - (i * 0.15) + (seed % 10) / 100)),
            "metadata": {"source": ["elasticsearch-docs", "vector-db"][i % 2], "rank": i+1},
            "source_url": f"https://docs.example.com/article-{seed % 100}/{i+1}",
        }
        for i in range(min(limit, 5))
    ]

    return {
        "items": results,
        "total_time_ms": int(150 + (seed % 100)),
        "providers_queried": 2,
        "cache_hit": (seed % 3) == 0,  # ~33% cache hit rate
    }


# ── FastAPI Router ────────────────────────────────────────────────

def create_rag_router() -> APIRouter:
    """Create FastAPI router for RAG endpoints."""
    router = APIRouter(prefix="/rag", tags=["rag"])

    @router.get("/providers")
    async def list_providers():
        """List all registered RAG providers."""
        try:
            providers = _get_mock_providers()
            return {
                "providers": [p.dict() for p in providers],
            }
        except Exception as e:
            logger.error(f"Failed to list providers: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.get("/providers/{provider_id}/health")
    async def get_provider_health(provider_id: str):
        """Check health status of a specific provider."""
        try:
            providers = _get_mock_providers()
            for p in providers:
                if p.id == provider_id:
                    return p.dict()
            raise HTTPException(status_code=404, detail=f"Provider {provider_id} not found")
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Health check failed for {provider_id}: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    @router.post("/query")
    async def execute_query(
        query: str = Query(..., description="Search query"),
        limit: int = Query(5, ge=1, le=50, description="Result limit"),
        providers: Optional[str] = Query(None, description="Comma-separated provider IDs"),
    ):
        """Execute query across RAG providers."""
        try:
            if not query or not query.strip():
                raise HTTPException(status_code=400, detail="Query cannot be empty")

            # Parse preferred providers if specified
            preferred = None
            if providers:
                preferred = [p.strip() for p in providers.split(",")]

            # Execute query (mock for Phase 4.1, real orchestrator in Phase 3)
            result = _execute_query_mock(query, limit)

            return result

        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Query execution failed: {e}")
            raise HTTPException(status_code=500, detail=str(e))

    return router

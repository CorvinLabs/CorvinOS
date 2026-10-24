"""RAG Orchestrator — Multi-provider query execution with ranking, caching, and fallback.

Handles:
- Parallel query execution across multiple providers
- Result ranking and deduplication
- TTL-based result caching
- Provider fallback chain
- Circuit breaker integration
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .rag_query_engine import (
    RAGQuery,
    RAGQueryEngine,
    RAGResultItem,
    RAGProviderResult,
    QueryStatus,
)

logger = logging.getLogger(__name__)


# ── Caching ──────────────────────────────────────────────────

@dataclass
class CachedResult:
    """Cached query result with TTL."""
    items: list[RAGResultItem]
    timestamp: float
    ttl_seconds: int = 300

    def is_expired(self) -> bool:
        """Check if cache entry has expired."""
        age = time.time() - self.timestamp
        return age > self.ttl_seconds


class RAGCache:
    """Simple TTL-based query result cache."""

    def __init__(self, cache_dir: Optional[Path] = None):
        """Initialize cache."""
        self.cache_dir = cache_dir
        self.memory_cache: dict[str, CachedResult] = {}

    def _query_hash(self, query: RAGQuery) -> str:
        """Generate cache key for query."""
        query_str = json.dumps(
            {
                "query": query.query,
                "filters": query.metadata_filters,
                # A limit-1 answer must not be served to a limit-5 question,
                # nor one provider's answer to a query that targets another.
                "limit": query.limit,
                "providers": sorted(query.preferred_providers or []),
            },
            sort_keys=True,
        )
        return hashlib.sha256(query_str.encode()).hexdigest()

    def get(self, query: RAGQuery) -> Optional[list[RAGResultItem]]:
        """Get cached result if exists and not expired."""
        key = self._query_hash(query)

        # Check memory cache
        if key in self.memory_cache:
            cached = self.memory_cache[key]
            if not cached.is_expired():
                logger.debug(f"Cache HIT: {query.query[:50]}...")
                return cached.items
            else:
                del self.memory_cache[key]

        return None

    def set(
        self,
        query: RAGQuery,
        items: list[RAGResultItem],
        ttl_seconds: int = 300,
    ) -> None:
        """Cache query results."""
        key = self._query_hash(query)
        self.memory_cache[key] = CachedResult(
            items=items,
            timestamp=time.time(),
            ttl_seconds=ttl_seconds,
        )
        logger.debug(f"Cache SET: {query.query[:50]}... ({len(items)} items)")

    def clear(self) -> None:
        """Clear all cache entries."""
        self.memory_cache.clear()


# ── Orchestrator ─────────────────────────────────────────────

@dataclass
class QueryOutcome:
    """Result of one orchestrated query plus who answered."""
    items: list[RAGResultItem]
    provider_ids: list[str] = field(default_factory=list)  # providers that answered OK
    cache_hit: bool = False
    queried: list[str] = field(default_factory=list)  # providers that were asked


@dataclass
class RankedResult:
    """Result with relevance score for ranking."""
    item: RAGResultItem
    provider_id: str
    provider_score_weight: float = 1.0  # Provider-specific ranking weight

    @property
    def final_score(self) -> float:
        """Calculate final ranking score."""
        return self.item.score * self.provider_score_weight


class RAGOrchestrator:
    """Orchestrate multi-provider RAG queries with advanced ranking and fallback."""

    def __init__(
        self,
        registry_dir: Path,
        auth_tokens: dict,
        cache_ttl_seconds: int = 300,
    ):
        """Initialize orchestrator."""
        self.registry_dir = registry_dir
        self.auth_tokens = auth_tokens
        self.cache = RAGCache(registry_dir / "cache")
        self.cache_ttl_seconds = cache_ttl_seconds
        self.engines: dict[str, RAGQueryEngine] = {}

    async def initialize(self) -> None:
        """Load providers from the registry layout and initialize engines.

        The provider set is :func:`rag_registry_store.provider_ids` — every
        manifest under ``manifests/`` (plus legacy flat files, migrated first),
        not only the ids listed in ``registry.json``.
        """
        import yaml

        from . import rag_registry_store as store

        try:
            store.migrate_legacy(self.registry_dir)
        except Exception as e:  # noqa: BLE001
            logger.error(f"Legacy RAG layout migration failed: {e}")

        for provider_id in store.provider_ids(self.registry_dir):
            try:
                manifest_file = store.find_manifest(self.registry_dir, provider_id)
                if manifest_file is None:
                    continue
                with open(manifest_file) as f:
                    manifest = yaml.safe_load(f)
                if not isinstance(manifest, dict):
                    continue
                self.engines[provider_id] = RAGQueryEngine(
                    provider_id, manifest, self.auth_tokens
                )
                logger.info(f"Initialized engine for provider: {provider_id}")
            except Exception as e:  # noqa: BLE001 - one bad manifest must not hide the rest
                logger.error(f"Failed to initialize provider {provider_id}: {e}")

    async def query(
        self,
        user_query: RAGQuery,
    ) -> list[RAGResultItem]:
        """Execute query across configured providers."""
        return (await self.query_with_meta(user_query)).items

    async def query_with_meta(self, user_query: RAGQuery) -> "QueryOutcome":
        """Like :meth:`query` but also reports which providers answered."""
        cached = self.cache.get(user_query)
        if cached:
            return QueryOutcome(items=cached, provider_ids=[], cache_hit=True)

        provider_ids = self._select_providers(user_query)
        if not provider_ids:
            logger.warning("No providers available for query")
            return QueryOutcome(items=[], provider_ids=[], cache_hit=False)

        results = await self._execute_parallel(user_query, provider_ids)
        answered = [r.provider_id for r in results if r.status == QueryStatus.SUCCESS]

        ranked = self._rank_results(results)
        final_items = [r.item for r in ranked[: user_query.limit]]

        # Never cache a failure: an empty list from providers that errored (or
        # an empty answer at all) would be served for the whole TTL.
        if final_items and answered:
            self.cache.set(user_query, final_items, self.cache_ttl_seconds)

        return QueryOutcome(
            items=final_items,
            provider_ids=answered,
            cache_hit=False,
            queried=list(provider_ids),
        )

    def _select_providers(self, query: RAGQuery) -> list[str]:
        """Select which providers to query."""
        # If preferred providers specified, use those
        if query.preferred_providers:
            available = [
                p for p in query.preferred_providers if p in self.engines
            ]
            if available:
                return available

        # Otherwise use all active providers (circuit breaker not OPEN)
        active = []
        for provider_id, engine in self.engines.items():
            if engine.circuit_breaker.can_execute():
                active.append(provider_id)

        return active

    async def _execute_parallel(
        self,
        query: RAGQuery,
        provider_ids: list[str],
    ) -> list[RAGProviderResult]:
        """Execute query across providers in parallel."""
        tasks = [
            self.engines[pid].execute(query)
            for pid in provider_ids
            if pid in self.engines
        ]

        results = await asyncio.gather(*tasks, return_exceptions=False)
        return results

    def _rank_results(self, results: list[RAGProviderResult]) -> list[RankedResult]:
        """Rank and deduplicate results from multiple providers."""
        ranked: list[RankedResult] = []
        seen_content_hashes: set[str] = set()

        for result in results:
            if result.status != QueryStatus.SUCCESS:
                logger.warning(
                    f"Provider {result.provider_id} failed: {result.status.value}"
                )
                continue

            # Provider-specific weight (future: based on manifest SLA, history)
            provider_weight = 1.0

            for item in result.items:
                # Deduplication by content hash
                content_hash = hashlib.md5(item.content.encode()).hexdigest()
                if content_hash in seen_content_hashes:
                    logger.debug(f"Deduplicating result from {result.provider_id}")
                    continue

                seen_content_hashes.add(content_hash)
                ranked_item = RankedResult(
                    item=item,
                    provider_id=result.provider_id,
                    provider_score_weight=provider_weight,
                )
                ranked.append(ranked_item)

        # Sort by final score (highest first)
        ranked.sort(key=lambda r: r.final_score, reverse=True)
        return ranked

    async def health_check_all(self) -> dict[str, dict]:
        """Check health of all providers."""
        results = {}
        for provider_id, engine in self.engines.items():
            test_query = RAGQuery(query="test", limit=1)
            result = await engine.execute(test_query)
            results[provider_id] = {
                "status": result.status.value,
                "circuit_state": engine.circuit_breaker.state.value,
                "latency_ms": result.latency_ms,
                "error": result.error_message,
            }
        return results

    async def health_check_one(self, provider_id: str) -> Optional[dict]:
        """Probe ONE provider (the per-card poll must not hit every provider)."""
        engine = self.engines.get(provider_id)
        if engine is None:
            return None
        result = await engine.execute(RAGQuery(query="test", limit=1))
        return {
            "status": result.status.value,
            "circuit_state": engine.circuit_breaker.state.value,
            "latency_ms": result.latency_ms,
            "error": result.error_message,
        }

    async def close(self) -> None:
        """Close all engine connections."""
        for engine in self.engines.values():
            await engine.close()

    def clear_cache(self) -> None:
        """Clear all cached results."""
        self.cache.clear()
        logger.info("RAG cache cleared")

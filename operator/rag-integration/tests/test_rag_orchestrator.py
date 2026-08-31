"""Unit tests for RAG Orchestrator."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.rag_orchestrator import (
    RAGCache,
    RAGOrchestrator,
    RankedResult,
)
from shared.rag_query_engine import (
    QueryStatus,
    RAGProviderResult,
    RAGQuery,
    RAGResultItem,
)


class TestRAGCache:
    """Tests for query result cache."""

    def test_cache_initialization(self):
        """Cache initializes correctly."""
        cache = RAGCache()
        assert len(cache.memory_cache) == 0

    def test_cache_set_and_get(self):
        """Set and retrieve cached result."""
        cache = RAGCache()
        query = RAGQuery(query="test search", limit=5)
        items = [
            RAGResultItem(content="result 1", score=0.9),
            RAGResultItem(content="result 2", score=0.8),
        ]

        cache.set(query, items)
        cached = cache.get(query)
        assert cached is not None
        assert len(cached) == 2
        assert cached[0].content == "result 1"

    def test_cache_miss_on_different_query(self):
        """Different query doesn't hit cache."""
        cache = RAGCache()
        query1 = RAGQuery(query="test search", limit=5)
        query2 = RAGQuery(query="different search", limit=5)
        items = [RAGResultItem(content="result", score=0.9)]

        cache.set(query1, items)
        cached = cache.get(query2)
        assert cached is None

    def test_cache_expiration(self):
        """Expired cache entry not returned."""
        cache = RAGCache()
        query = RAGQuery(query="test search", limit=5)
        items = [RAGResultItem(content="result", score=0.9)]

        cache.set(query, items, ttl_seconds=1)

        # Immediately should hit
        assert cache.get(query) is not None

        # Expire the entry by manipulating timestamp
        key = cache._query_hash(query)
        cache.memory_cache[key].timestamp = 0  # Way in the past

        # Should miss
        assert cache.get(query) is None

    def test_cache_clear(self):
        """Clear removes all entries."""
        cache = RAGCache()
        query = RAGQuery(query="test search", limit=5)
        items = [RAGResultItem(content="result", score=0.9)]

        cache.set(query, items)
        assert cache.get(query) is not None

        cache.clear()
        assert cache.get(query) is None


class TestRankedResult:
    """Tests for ranked result."""

    def test_final_score_calculation(self):
        """Final score calculated correctly."""
        item = RAGResultItem(content="test", score=0.8)
        ranked = RankedResult(
            item=item,
            provider_id="test-provider",
            provider_score_weight=1.25,
        )
        assert ranked.final_score == 1.0  # 0.8 * 1.25 = 1.0


class TestRAGOrchestrator:
    """Tests for orchestrator."""

    @pytest.fixture
    def temp_registry(self, tmp_path):
        """Create temporary registry directory."""
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        (registry_dir / "manifests").mkdir()
        return registry_dir

    @pytest.fixture
    def orchestrator(self, temp_registry):
        """Create orchestrator instance."""
        return RAGOrchestrator(
            registry_dir=temp_registry,
            auth_tokens={"TEST_TOKEN": "token123"},
            cache_ttl_seconds=300,
        )

    def test_orchestrator_initialization(self, orchestrator):
        """Orchestrator initializes correctly."""
        assert len(orchestrator.engines) == 0
        assert orchestrator.cache is not None

    @pytest.mark.asyncio
    async def test_initialize_no_registry(self, temp_registry):
        """Initialize gracefully when registry missing."""
        # Remove registry file if it exists
        registry_file = temp_registry / "registry.json"
        if registry_file.exists():
            registry_file.unlink()

        orch = RAGOrchestrator(
            registry_dir=temp_registry,
            auth_tokens={},
        )
        await orch.initialize()
        assert len(orch.engines) == 0

    def test_select_no_preferred_providers(self, orchestrator):
        """Select returns all active providers when none preferred."""
        # Add mock engines
        mock_engine1 = MagicMock()
        mock_engine1.circuit_breaker.can_execute.return_value = True
        orchestrator.engines["provider1"] = mock_engine1

        mock_engine2 = MagicMock()
        mock_engine2.circuit_breaker.can_execute.return_value = True
        orchestrator.engines["provider2"] = mock_engine2

        query = RAGQuery(query="test")
        selected = orchestrator._select_providers(query)
        assert len(selected) == 2
        assert "provider1" in selected
        assert "provider2" in selected

    def test_select_with_preferred_providers(self, orchestrator):
        """Select respects preferred providers."""
        mock_engine1 = MagicMock()
        mock_engine1.circuit_breaker.can_execute.return_value = True
        orchestrator.engines["provider1"] = mock_engine1

        mock_engine2 = MagicMock()
        mock_engine2.circuit_breaker.can_execute.return_value = True
        orchestrator.engines["provider2"] = mock_engine2

        query = RAGQuery(
            query="test",
            preferred_providers=["provider1"],
        )
        selected = orchestrator._select_providers(query)
        assert selected == ["provider1"]

    def test_select_circuit_breaker_filtering(self, orchestrator):
        """Select excludes providers with open circuit."""
        mock_engine1 = MagicMock()
        mock_engine1.circuit_breaker.can_execute.return_value = True
        orchestrator.engines["provider1"] = mock_engine1

        mock_engine2 = MagicMock()
        mock_engine2.circuit_breaker.can_execute.return_value = False  # Open
        orchestrator.engines["provider2"] = mock_engine2

        query = RAGQuery(query="test")
        selected = orchestrator._select_providers(query)
        assert selected == ["provider1"]

    def test_rank_results_empty(self, orchestrator):
        """Rank handles empty results."""
        results = []
        ranked = orchestrator._rank_results(results)
        assert ranked == []

    def test_rank_results_single_provider(self, orchestrator):
        """Rank single provider results."""
        items = [
            RAGResultItem(content="Item 1", score=0.9),
            RAGResultItem(content="Item 2", score=0.7),
        ]
        result = RAGProviderResult(
            provider_id="provider1",
            status=QueryStatus.SUCCESS,
            items=items,
        )
        ranked = orchestrator._rank_results([result])
        assert len(ranked) == 2
        assert ranked[0].item.score == 0.9  # Highest first
        assert ranked[1].item.score == 0.7

    def test_rank_results_multiple_providers(self, orchestrator):
        """Rank and merge results from multiple providers."""
        result1 = RAGProviderResult(
            provider_id="provider1",
            status=QueryStatus.SUCCESS,
            items=[RAGResultItem(content="Item 1", score=0.9)],
        )
        result2 = RAGProviderResult(
            provider_id="provider2",
            status=QueryStatus.SUCCESS,
            items=[RAGResultItem(content="Item 2", score=0.85)],
        )
        ranked = orchestrator._rank_results([result1, result2])
        assert len(ranked) == 2
        assert ranked[0].item.score == 0.9
        assert ranked[1].item.score == 0.85

    def test_rank_results_deduplication(self, orchestrator):
        """Rank deduplicates identical content."""
        # Same content from two providers
        result1 = RAGProviderResult(
            provider_id="provider1",
            status=QueryStatus.SUCCESS,
            items=[RAGResultItem(content="Identical content", score=0.9)],
        )
        result2 = RAGProviderResult(
            provider_id="provider2",
            status=QueryStatus.SUCCESS,
            items=[RAGResultItem(content="Identical content", score=0.85)],
        )
        ranked = orchestrator._rank_results([result1, result2])
        # Should only keep first occurrence
        assert len(ranked) == 1
        assert ranked[0].provider_id == "provider1"

    def test_rank_results_failed_provider(self, orchestrator):
        """Rank skips failed provider results."""
        result1 = RAGProviderResult(
            provider_id="provider1",
            status=QueryStatus.SUCCESS,
            items=[RAGResultItem(content="Item 1", score=0.9)],
        )
        result2 = RAGProviderResult(
            provider_id="provider2",
            status=QueryStatus.TIMEOUT,
            error_message="Timed out",
        )
        ranked = orchestrator._rank_results([result1, result2])
        assert len(ranked) == 1
        assert ranked[0].provider_id == "provider1"

    def test_clear_cache(self, orchestrator):
        """Clear cache works."""
        query = RAGQuery(query="test")
        items = [RAGResultItem(content="result", score=0.9)]
        orchestrator.cache.set(query, items)

        assert orchestrator.cache.get(query) is not None
        orchestrator.clear_cache()
        assert orchestrator.cache.get(query) is None

    @pytest.mark.asyncio
    async def test_execute_parallel(self, orchestrator):
        """Execute parallel queries."""
        mock_engine1 = AsyncMock()
        mock_engine1.execute = AsyncMock(
            return_value=RAGProviderResult(
                provider_id="provider1",
                status=QueryStatus.SUCCESS,
                items=[RAGResultItem(content="Item 1", score=0.9)],
            )
        )
        orchestrator.engines["provider1"] = mock_engine1

        mock_engine2 = AsyncMock()
        mock_engine2.execute = AsyncMock(
            return_value=RAGProviderResult(
                provider_id="provider2",
                status=QueryStatus.SUCCESS,
                items=[RAGResultItem(content="Item 2", score=0.8)],
            )
        )
        orchestrator.engines["provider2"] = mock_engine2

        query = RAGQuery(query="test")
        results = await orchestrator._execute_parallel(query, ["provider1", "provider2"])

        assert len(results) == 2
        assert results[0].provider_id == "provider1"
        assert results[1].provider_id == "provider2"

    @pytest.mark.asyncio
    async def test_query_uses_cache(self, orchestrator):
        """Query returns cached result when available."""
        query = RAGQuery(query="cached query")
        cached_items = [RAGResultItem(content="cached", score=0.95)]
        orchestrator.cache.set(query, cached_items)

        # Execute query — should return from cache
        result = await orchestrator.query(query)
        assert len(result) == 1
        assert result[0].content == "cached"

    @pytest.mark.asyncio
    async def test_query_no_providers(self, orchestrator):
        """Query with no available providers returns empty."""
        query = RAGQuery(query="test")
        result = await orchestrator.query(query)
        assert result == []

    @pytest.mark.asyncio
    async def test_health_check_all(self, orchestrator):
        """Health check aggregates provider status."""
        mock_engine1 = AsyncMock()
        mock_engine1.circuit_breaker.state.value = "closed"
        mock_engine1.execute = AsyncMock(
            return_value=RAGProviderResult(
                provider_id="provider1",
                status=QueryStatus.SUCCESS,
                latency_ms=100,
            )
        )
        orchestrator.engines["provider1"] = mock_engine1

        health = await orchestrator.health_check_all()
        assert "provider1" in health
        assert health["provider1"]["status"] == "success"
        assert health["provider1"]["latency_ms"] == 100

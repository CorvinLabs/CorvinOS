"""End-to-end tests for Phase 3 RAG Query Engine + Orchestrator."""
import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from shared.rag_orchestrator import RAGOrchestrator
from shared.rag_query_engine import (
    QueryStatus,
    RAGProviderResult,
    RAGQuery,
    RAGQueryEngine,
    RAGResultItem,
)


class TestPhase3E2E:
    """End-to-end tests for Phase 3."""

    @pytest.fixture
    def elasticsearch_manifest(self):
        """Elasticsearch manifest for testing."""
        return {
            "apiVersion": "rag/v1alpha1",
            "kind": "RAGProvider",
            "metadata": {
                "name": "elasticsearch-docs",
                "namespace": "default",
            },
            "spec": {
                "retrieval": {
                    "endpoint": "https://es.example.com/search",
                    "method": "POST",
                    "timeout_ms": 5000,
                    "auth": {
                        "type": "bearer-token",
                        "token_env_var": "ES_TOKEN",
                    },
                },
                "resilience": {
                    "circuit_breaker": {
                        "failure_threshold": 3,
                        "timeout_seconds": 30,
                    },
                    "retry_strategy": "exponential",
                    "max_retries": 2,
                },
                "resultFormat": {
                    "contentPath": "results[].content",
                    "scorePath": "results[].score",
                },
                "dataClassification": "INTERNAL",
                "complianceZone": "EU",
                "capabilities": ["keyword", "semantic"],
                "quotas": {
                    "requestsPerSecond": 10,
                    "dailyLimit": 100000,
                },
                "erasureHandler": {
                    "type": "http-delete",
                    "endpoint": "https://es.example.com/delete",
                },
            },
        }

    @pytest.fixture
    def vector_manifest(self):
        """Vector database manifest."""
        return {
            "apiVersion": "rag/v1alpha1",
            "kind": "RAGProvider",
            "metadata": {
                "name": "vector-db",
                "namespace": "default",
            },
            "spec": {
                "retrieval": {
                    "endpoint": "https://vectors.example.com/query",
                    "method": "POST",
                    "timeout_ms": 3000,
                    "auth": {
                        "type": "api-key",
                        "token_env_var": "VECTOR_API_KEY",
                    },
                },
                "resilience": {
                    "circuit_breaker": {
                        "failure_threshold": 5,
                        "timeout_seconds": 30,
                    },
                },
                "dataClassification": "INTERNAL",
                "complianceZone": "EU",
                "capabilities": ["semantic", "hybrid"],
            },
        }

    @pytest.mark.asyncio
    async def test_e2e_single_provider_query(self, elasticsearch_manifest):
        """E2E: Query single provider successfully."""
        auth_tokens = {"ES_TOKEN": "test-token-123"}

        engine = RAGQueryEngine(
            provider_id="elasticsearch-docs",
            manifest=elasticsearch_manifest,
            auth_tokens=auth_tokens,
        )

        # Mock successful response
        mock_response = {
            "results": [
                {
                    "content": "Elasticsearch is a search and analytics engine.",
                    "score": 0.95,
                    "metadata": {"source": "docs"},
                    "source_url": "https://example.com/es-guide",
                },
                {
                    "content": "It powers full-text search capabilities.",
                    "score": 0.87,
                    "metadata": {"source": "docs"},
                },
            ]
        }

        with patch(
            "shared.rag_query_engine.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_response_obj = AsyncMock()
            mock_response_obj.json.return_value = mock_response
            mock_response_obj.status_code = 200
            mock_client.post.return_value = mock_response_obj

            query = RAGQuery(query="What is Elasticsearch?", limit=5)
            result = await engine.execute(query)

            assert result.status == QueryStatus.SUCCESS
            assert len(result.items) == 2
            assert result.items[0].score == 0.95
            assert "Elasticsearch" in result.items[0].content

    @pytest.mark.asyncio
    async def test_e2e_multi_provider_orchestration(
        self, elasticsearch_manifest, vector_manifest, tmp_path
    ):
        """E2E: Orchestrate queries across multiple providers."""
        # Setup
        auth_tokens = {
            "ES_TOKEN": "es-token-123",
            "VECTOR_API_KEY": "vector-key-456",
        }
        registry_dir = tmp_path / "registry"
        registry_dir.mkdir()
        (registry_dir / "manifests").mkdir()

        # Create registry file
        registry_data = {
            "version": 1,
            "providers": [
                {
                    "id": "elasticsearch-docs",
                    "name": "Elasticsearch",
                    "status": "active",
                    "health_status": "healthy",
                    "query_stats": {
                        "total_queries": 0,
                        "queries_today": 0,
                        "average_latency_ms": 0,
                    },
                },
                {
                    "id": "vector-db",
                    "name": "Vector DB",
                    "status": "active",
                    "health_status": "healthy",
                    "query_stats": {
                        "total_queries": 0,
                        "queries_today": 0,
                        "average_latency_ms": 0,
                    },
                },
            ]
        }
        with open(registry_dir / "registry.json", "w") as f:
            json.dump(registry_data, f)

        # Save manifests
        import yaml

        with open(registry_dir / "manifests" / "elasticsearch-docs.yaml", "w") as f:
            yaml.dump(elasticsearch_manifest, f)

        with open(registry_dir / "manifests" / "vector-db.yaml", "w") as f:
            yaml.dump(vector_manifest, f)

        # Create orchestrator
        orch = RAGOrchestrator(
            registry_dir=registry_dir,
            auth_tokens=auth_tokens,
            cache_ttl_seconds=300,
        )

        # Mock engines for orchestrator
        mock_engine_es = AsyncMock()
        mock_engine_es.circuit_breaker.can_execute.return_value = True
        mock_engine_es.circuit_breaker.state.value = "closed"
        mock_engine_es.execute = AsyncMock(
            return_value=RAGProviderResult(
                provider_id="elasticsearch-docs",
                status=QueryStatus.SUCCESS,
                items=[
                    RAGResultItem(
                        content="ES result 1",
                        score=0.9,
                        metadata={"source": "es"},
                    ),
                    RAGResultItem(
                        content="ES result 2",
                        score=0.75,
                        metadata={"source": "es"},
                    ),
                ],
                latency_ms=150,
            )
        )

        mock_engine_vector = AsyncMock()
        mock_engine_vector.circuit_breaker.can_execute.return_value = True
        mock_engine_vector.circuit_breaker.state.value = "closed"
        mock_engine_vector.execute = AsyncMock(
            return_value=RAGProviderResult(
                provider_id="vector-db",
                status=QueryStatus.SUCCESS,
                items=[
                    RAGResultItem(
                        content="Vector result 1",
                        score=0.88,
                        metadata={"source": "vector"},
                    ),
                    RAGResultItem(
                        content="Vector result 2",
                        score=0.72,
                        metadata={"source": "vector"},
                    ),
                ],
                latency_ms=100,
            )
        )

        orch.engines = {
            "elasticsearch-docs": mock_engine_es,
            "vector-db": mock_engine_vector,
        }

        # Execute query
        query = RAGQuery(query="Tell me about RAG systems", limit=4)
        results = await orch.query(query)

        # Verify results
        assert len(results) == 4
        # Should be ranked by score: ES 0.9, Vector 0.88, ES 0.75, Vector 0.72
        assert results[0].score == 0.9
        assert results[1].score == 0.88
        assert results[2].score == 0.75
        assert results[3].score == 0.72

        # Verify caching
        cached = orch.cache.get(query)
        assert cached is not None
        assert len(cached) == 4

    @pytest.mark.asyncio
    async def test_e2e_provider_failure_with_fallback(self, elasticsearch_manifest):
        """E2E: Fallback when provider fails."""
        auth_tokens = {"ES_TOKEN": "test-token"}

        engine = RAGQueryEngine(
            provider_id="elasticsearch-docs",
            manifest=elasticsearch_manifest,
            auth_tokens=auth_tokens,
        )

        # Mock timeout
        with patch(
            "shared.rag_query_engine.httpx.AsyncClient"
        ) as mock_client_class:
            mock_client = AsyncMock()
            mock_client_class.return_value = mock_client
            mock_client.post.side_effect = asyncio.TimeoutError()

            query = RAGQuery(query="test")
            result = await engine.execute(query)

            assert result.status == QueryStatus.TIMEOUT
            assert result.error_message is not None
            assert engine.circuit_breaker.failures > 0

    @pytest.mark.asyncio
    async def test_e2e_deduplication_across_providers(self):
        """E2E: Deduplication removes duplicate content from multiple providers."""
        # Create orchestrator
        orch = RAGOrchestrator(
            registry_dir=Path("/tmp"),
            auth_tokens={},
            cache_ttl_seconds=300,
        )

        # Simulate same content from two providers
        result1 = RAGProviderResult(
            provider_id="provider1",
            status=QueryStatus.SUCCESS,
            items=[
                RAGResultItem(
                    content="Unique content from provider 1",
                    score=0.95,
                ),
                RAGResultItem(
                    content="Shared content across providers",
                    score=0.8,
                ),
            ],
        )

        result2 = RAGProviderResult(
            provider_id="provider2",
            status=QueryStatus.SUCCESS,
            items=[
                RAGResultItem(
                    content="Shared content across providers",
                    score=0.85,
                ),
                RAGResultItem(
                    content="Unique content from provider 2",
                    score=0.75,
                ),
            ],
        )

        ranked = orch._rank_results([result1, result2])

        # Should deduplicate
        assert len(ranked) == 3
        content_set = {r.item.content for r in ranked}
        assert len(content_set) == 3  # All unique

    @pytest.mark.asyncio
    async def test_e2e_circuit_breaker_recovery(self):
        """E2E: Circuit breaker opens and recovers."""
        manifest = {
            "spec": {
                "retrieval": {
                    "endpoint": "https://api.example.com/search",
                    "auth": {"type": "none"},
                },
                "resilience": {
                    "circuit_breaker": {
                        "failure_threshold": 2,
                        "timeout_seconds": 1,
                    }
                },
            }
        }

        engine = RAGQueryEngine(
            provider_id="test-provider",
            manifest=manifest,
            auth_tokens={},
        )

        # Record failures to open circuit
        engine.circuit_breaker.record_failure()
        engine.circuit_breaker.record_failure()
        assert engine.circuit_breaker.state.value == "open"

        # Query should be rejected
        query = RAGQuery(query="test")
        result = await engine.execute(query)
        assert result.status.value == "provider_error"

        # Simulate timeout passage and recovery
        from shared.rag_query_engine import CircuitState

        engine.circuit_breaker.state = CircuitState.OPEN
        engine.circuit_breaker.last_failure_time = 0  # Way in the past
        assert engine.circuit_breaker.can_execute() is True
        assert engine.circuit_breaker.state == CircuitState.HALF_OPEN

"""Shared bridge utilities and components."""

# RAG Integration exports
try:
    from .rag_query_engine import (
        CircuitBreaker,
        CircuitState,
        QueryStatus,
        RAGProviderResult,
        RAGQuery,
        RAGQueryEngine,
        RAGResultItem,
    )
    from .rag_orchestrator import RAGCache, RAGOrchestrator, RankedResult

    __all__ = [
        "RAGQuery",
        "RAGResultItem",
        "RAGProviderResult",
        "RAGQueryEngine",
        "QueryStatus",
        "CircuitBreaker",
        "CircuitState",
        "RAGCache",
        "RAGOrchestrator",
        "RankedResult",
    ]
except ImportError:
    # RAG components not yet available in this environment
    pass

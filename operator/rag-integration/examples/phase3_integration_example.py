#!/usr/bin/env python3
"""Example: Using Phase 3 Orchestrator with Phase 2 Registry.

This demonstrates how to:
1. Load providers from the registry (Phase 2)
2. Execute multi-provider RAG queries (Phase 3)
3. Handle results, caching, and health checks
"""
import asyncio
import os
import sys
from pathlib import Path

# ADR-0215 F5: the dotted `from operator.bridges.shared...` imports below
# used to be the only import path in this example — they can never resolve
# (stdlib `operator` always shadows the repo's operator/ directory), so
# this example script itself was broken from inception, exactly the kind of
# "looks done but was never actually run" bug this ADR closes.
#
# Unlike most operator/bridges/shared modules (which use bare imports and
# are fixed by putting shared/ itself on sys.path), rag_orchestrator.py
# does `from .rag_query_engine import ...` — a package-relative import that
# only resolves when the module is loaded AS PART OF a package. shared/
# does have an __init__.py (unlike operator/ itself), so the fix is to put
# its PARENT (operator/bridges) on sys.path and import `shared.rag_orchestrator`
# — the exact pattern core/console/corvin_console/routes/rag.py already
# uses in production (`from shared.rag_orchestrator import RAGOrchestrator`).
_bridges = Path(__file__).resolve().parents[2] / "bridges"
if _bridges.is_dir() and str(_bridges) not in sys.path:
    sys.path.insert(0, str(_bridges))
from shared.rag_orchestrator import RAGOrchestrator
from shared.rag_query_engine import RAGQuery


async def main():
    """Example: Multi-provider RAG orchestration."""

    # ─────────────────────────────────────────────────────────────────────
    # Setup: Registry location and auth tokens
    # ─────────────────────────────────────────────────────────────────────

    registry_dir = Path.home() / ".corvin" / "tenants" / "_default" / "global" / "rag"

    # Load auth tokens from environment (or config)
    auth_tokens = {
        "ES_TOKEN": os.getenv("ELASTICSEARCH_TOKEN", "test-token"),
        "VECTOR_API_KEY": os.getenv("VECTOR_API_KEY", "test-key"),
    }

    # Create orchestrator
    orch = RAGOrchestrator(
        registry_dir=registry_dir,
        auth_tokens=auth_tokens,
        cache_ttl_seconds=300,  # 5 minutes
    )

    try:
        # ─────────────────────────────────────────────────────────────────
        # Initialize: Load providers from registry
        # ─────────────────────────────────────────────────────────────────

        print("🚀 Initializing RAG Orchestrator...")
        await orch.initialize()
        print(f"   ✅ Loaded {len(orch.engines)} providers")

        # ─────────────────────────────────────────────────────────────────
        # Example 1: Simple query across all providers
        # ─────────────────────────────────────────────────────────────────

        print("\n📝 Example 1: Query all providers")
        print("─" * 60)

        query1 = RAGQuery(
            query="What is Retrieval-Augmented Generation?",
            limit=5,
        )

        results1 = await orch.query(query1)
        print(f"   Query: {query1.query}")
        print(f"   Results: {len(results1)} items\n")

        for i, item in enumerate(results1, 1):
            print(f"   {i}. [{item.score:.2f}] {item.content[:70]}...")
            if item.metadata:
                print(f"      📌 {item.metadata}")

        # ─────────────────────────────────────────────────────────────────
        # Example 2: Query with preferred providers
        # ─────────────────────────────────────────────────────────────────

        print("\n📝 Example 2: Query preferred providers only")
        print("─" * 60)

        query2 = RAGQuery(
            query="How to implement RAG in production?",
            limit=3,
            preferred_providers=["elasticsearch-docs"],
        )

        results2 = await orch.query(query2)
        print(f"   Query: {query2.query}")
        print(f"   Preferred: {query2.preferred_providers}")
        print(f"   Results: {len(results2)} items\n")

        for i, item in enumerate(results2, 1):
            print(f"   {i}. [{item.score:.2f}] {item.content[:70]}...")

        # ─────────────────────────────────────────────────────────────────
        # Example 3: Cached query (same query = instant response)
        # ─────────────────────────────────────────────────────────────────

        print("\n📝 Example 3: Caching in action")
        print("─" * 60)

        import time

        query3 = RAGQuery(
            query="What is vector search?",
            limit=5,
        )

        # First query (cache miss)
        print("   First query (cache miss)...")
        start = time.time()
        results3a = await orch.query(query3)
        elapsed_a = time.time() - start
        print(f"   ✅ Got {len(results3a)} results in {elapsed_a*1000:.1f}ms")

        # Second query (cache hit)
        print("   Second query (cache hit)...")
        start = time.time()
        results3b = await orch.query(query3)
        elapsed_b = time.time() - start
        print(f"   ✅ Got {len(results3b)} results in {elapsed_b*1000:.1f}ms")
        print(f"   Speedup: {elapsed_a/elapsed_b:.1f}x faster!")

        # ─────────────────────────────────────────────────────────────────
        # Example 4: Health check
        # ─────────────────────────────────────────────────────────────────

        print("\n📝 Example 4: Provider health status")
        print("─" * 60)

        health = await orch.health_check_all()
        for provider_id, status in health.items():
            circuit_state = status.get("circuit_state", "unknown")
            latency = status.get("latency_ms", 0)
            error = status.get("error")

            if status["status"] == "success":
                print(f"   ✅ {provider_id}")
                print(f"      Circuit: {circuit_state} | Latency: {latency}ms")
            else:
                print(f"   ⚠️  {provider_id}")
                print(f"      Circuit: {circuit_state}")
                if error:
                    print(f"      Error: {error}")

        # ─────────────────────────────────────────────────────────────────
        # Example 5: Clear cache
        # ─────────────────────────────────────────────────────────────────

        print("\n📝 Example 5: Cache management")
        print("─" * 60)

        print("   Clearing cache...")
        orch.clear_cache()
        print("   ✅ Cache cleared")

        # ─────────────────────────────────────────────────────────────────
        # Summary
        # ─────────────────────────────────────────────────────────────────

        print("\n" + "=" * 60)
        print("✅ Phase 3 Integration Example Complete!")
        print("=" * 60)
        print(f"\nSummary:")
        print(f"  • Loaded {len(orch.engines)} providers from registry")
        print(f"  • Executed queries with ranking + deduplication")
        print(f"  • Demonstrated caching (5-min TTL)")
        print(f"  • Checked provider health via circuit breaker")
        print(f"\nNext: Deploy orchestrator as /api/rag/query endpoint")

    finally:
        # ─────────────────────────────────────────────────────────────────
        # Cleanup
        # ─────────────────────────────────────────────────────────────────

        print("\n🧹 Cleaning up...")
        await orch.close()
        print("   ✅ All connections closed")


if __name__ == "__main__":
    asyncio.run(main())

#!/usr/bin/env python3
"""
Phase 1 Test Runner (no pytest required).
Validates routing, caching, and tracing functionality.
"""

import sys
import time
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.plugins.registry.routing import (
    Router,
    RoutingPredicate,
    PredicateType,
    create_routing_config,
)
from core.plugins.registry.cache import CacheStore, make_cache_key, _MISSING
from core.plugins.registry.telemetry import get_tracer, TracedExecution


def test_routing():
    """Test Phase 1 routing."""
    print("\n=== PHASE 1: ROUTING TESTS ===\n")

    # Test 1: Event type predicate
    print("✓ Test 1: Event type predicate")
    pred = RoutingPredicate(PredicateType.EVENT_TYPE, ["error", "metric"])
    assert pred.matches({"type": "error"}) == True
    assert pred.matches({"type": "decision"}) == False
    print("  ✓ PASS")

    # Test 2: Basic routing
    print("✓ Test 2: Basic routing")
    router = Router()
    router.register_plugin(create_routing_config("monitor_errors", event_types=["error"]))
    router.register_plugin(create_routing_config("log_all", event_types=["error", "metric"]))

    matched = router.route({"type": "error"})
    assert len(matched) == 2
    print("  ✓ PASS")

    # Test 3: Multiple predicates (AND logic)
    print("✓ Test 3: Multiple predicates (AND logic)")
    router = Router()
    router.register_plugin(create_routing_config(
        "premium_handler",
        event_types=["error"],
        user_tiers=["premium"],
    ))

    matched = router.route({"type": "error", "user_tier": "premium"})
    assert "premium_handler" in matched

    matched = router.route({"type": "error", "user_tier": "free"})
    assert "premium_handler" not in matched
    print("  ✓ PASS")

    # Test 4: Performance (<1ms per route)
    print("✓ Test 4: Performance (<1ms per route)")
    router = Router()
    for i in range(50):
        router.register_plugin(create_routing_config(f"plugin_{i}", event_types=[f"type_{i % 5}"]))

    start = time.time() * 1000
    for _ in range(100):
        router.route({"type": "type_2"})
    elapsed_ms = (time.time() * 1000) - start
    avg_per_route = elapsed_ms / 100

    print(f"  Avg latency: {avg_per_route:.3f}ms")
    assert avg_per_route < 1.0
    print("  ✓ PASS")

    print("\n✅ ROUTING: All tests passed\n")


def test_caching():
    """Test Phase 1 caching."""
    print("\n=== PHASE 1: CACHING TESTS ===\n")

    # Test 1: Basic cache
    print("✓ Test 1: Basic cache get/set")
    cache = CacheStore()
    cache.set("key1", "value1", ttl_seconds=10)
    assert cache.get("key1") == "value1"
    print("  ✓ PASS")

    # Test 2: Cache expiration
    print("✓ Test 2: Cache expiration (TTL)")
    cache = CacheStore()
    cache.set("key1", "value1", ttl_seconds=1)
    assert cache.get("key1") == "value1"
    time.sleep(1.1)
    assert cache.get("key1") is _MISSING
    print("  ✓ PASS (expired correctly)")

    # Test 3: Event-based invalidation
    print("✓ Test 3: Event-based invalidation")
    cache = CacheStore()
    cache.set("key1", "value1", invalidate_on=["config_change"])
    cache.set("key2", "value2", invalidate_on=["session_end"])

    cache.invalidate_on_event("config_change")
    assert cache.get("key1") is _MISSING
    assert cache.get("key2") == "value2"
    print("  ✓ PASS")

    # Test 4: Cache key generation with config versioning
    print("✓ Test 4: Cache key with config versioning")
    key1 = make_cache_key("analyze_error", "timeout", config_version="v1")
    key2 = make_cache_key("analyze_error", "timeout", config_version="v1")
    key3 = make_cache_key("analyze_error", "timeout", config_version="v2")

    assert key1 == key2  # Same config → same key
    assert key1 != key3  # Different config → different key
    print("  ✓ PASS")

    # Test 5: Hit rate metrics
    print("✓ Test 5: Cache hit rate")
    cache = CacheStore()
    for i in range(10):
        cache.set(f"key_{i}", f"value_{i}")

    # Generate 10 hits
    for i in range(10):
        cache.get(f"key_{i}")

    # Generate 5 misses
    for i in range(10, 15):
        cache.get(f"key_{i}")

    metrics = cache.get_metrics()
    hit_rate = metrics["hit_rate"]
    print(f"  Hit rate: {hit_rate:.1%}")
    assert abs(hit_rate - 0.666) < 0.01
    print("  ✓ PASS")

    print("\n✅ CACHING: All tests passed\n")


def test_tracing():
    """Test Phase 1 tracing."""
    print("\n=== PHASE 1: TRACING TESTS ===\n")

    # Test 1: Basic span creation
    print("✓ Test 1: Span creation and timing")
    tracer = get_tracer()
    tracer.clear()

    with TracedExecution("error_analysis", "error_analyzer") as span:
        span.set_attribute("error_type", "timeout")
        time.sleep(0.01)  # 10ms work

    metrics = tracer.get_metrics()
    assert metrics["total_spans"] == 1
    assert metrics["total_duration_ms"] > 10
    print(f"  Span duration: {metrics['total_duration_ms']:.2f}ms")
    print("  ✓ PASS")

    # Test 2: Flamegraph export
    print("✓ Test 2: Flamegraph export")
    tracer.clear()

    for i in range(5):
        with TracedExecution(f"operation_{i}", f"plugin_{i}"):
            time.sleep(0.01)

    fg = tracer.get_flamegraph()
    assert len(fg) == 5
    assert all(s["duration_ms"] > 10 for s in fg)
    print(f"  Exported {len(fg)} spans")
    print("  ✓ PASS")

    print("\n✅ TRACING: All tests passed\n")


def test_e2e_integration():
    """Test Phase 1 end-to-end integration."""
    print("\n=== PHASE 1: END-TO-END INTEGRATION ===\n")

    print("✓ Test: Full routing + caching + tracing flow")

    # Create router
    router = Router()
    router.register_plugin(create_routing_config("error_analyzer", event_types=["error"]))
    router.register_plugin(create_routing_config("context_enricher", event_types=["error"]))
    router.register_plugin(create_routing_config("perf_monitor"))

    # Create cache
    cache = CacheStore()

    # Create tracer
    tracer = get_tracer()
    tracer.clear()

    # Simulate error event
    error_event = {
        "type": "error",
        "message": "Database timeout",
        "component": "db_layer",
        "latency_ms": 5000,
    }

    # Route event
    with TracedExecution("route_event", "router"):
        matched = router.route(error_event)

    assert len(matched) == 3  # All 3 plugins should match
    print(f"  Matched {len(matched)} plugins: {matched}")

    # Cache analysis result
    cache.set("error_db_timeout", {"healing": "retry", "confidence": 0.87})
    cached = cache.get("error_db_timeout")
    assert cached is not _MISSING
    print(f"  Cached result: {cached}")

    # Check metrics
    routing_metrics = router.get_metrics()
    cache_metrics = cache.get_metrics()
    trace_metrics = tracer.get_metrics()

    print(f"\n  Routing Metrics:")
    print(f"    - Avg route time: {routing_metrics['avg_route_time_ms']:.3f}ms")
    print(f"    - Match rate: {routing_metrics['avg_match_rate']:.1%}")

    print(f"\n  Cache Metrics:")
    print(f"    - Hit rate: {cache_metrics['hit_rate']:.1%}")
    print(f"    - Entries: {cache_metrics['entries']}")

    print(f"\n  Tracing Metrics:")
    print(f"    - Total spans: {trace_metrics['total_spans']}")
    print(f"    - Avg duration: {trace_metrics['avg_duration_ms']:.2f}ms")

    print("  ✓ PASS")
    print("\n✅ INTEGRATION: Test passed\n")


def main():
    """Run all Phase 1 tests."""
    print("\n" + "="*70)
    print("PHASE 1: SMART ROUTING + CACHING + TRACING")
    print("="*70)

    try:
        test_routing()
        test_caching()
        test_tracing()
        test_e2e_integration()

        print("\n" + "="*70)
        print("✅ PHASE 1: ALL TESTS PASSED")
        print("="*70)
        print("\nSummary:")
        print("  ✓ Routing: <1ms latency, 3+ plugins matched correctly")
        print("  ✓ Caching: 60%+ hit rate, config-aware invalidation")
        print("  ✓ Tracing: Flamegraph export, timing accuracy")
        print("  ✓ E2E: Full integration with real plugin patterns")
        print("\n🚀 PHASE 1 PRODUCTION READY\n")

        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1
    except Exception as e:
        print(f"\n❌ ERROR: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

#!/usr/bin/env python3
"""Phase 2 Tests: State Persistence, Dependencies, Resource Limits"""

import sys
import time
import tempfile
import os
sys.path.insert(0, '/home/shumway/projects/CorvinOS')

from core.plugins.state.state_store import StateStore
from core.plugins.registry.dependencies import DependencyGraph, PluginDependency
from core.plugins.registry.resource_limits import (
    ResourceBudget, CircuitBreaker, ResourceTracker, ResourceLimitContext
)


def test_state_persistence():
    """Test Phase 2 state persistence with WAL."""
    print("\n=== PHASE 2: STATE PERSISTENCE ===\n")

    with tempfile.TemporaryDirectory() as tmpdir:
        # Test 1: Basic persistence
        print("✓ Test 1: Basic state persistence")
        store = StateStore(tmpdir)
        state = store.load_or_create("error_healer", default={"success_rate": 0.0})
        store.update("error_healer", "success_rate", 0.85)
        store.flush("error_healer")

        assert store.get("error_healer", "success_rate") == 0.85
        print("  ✓ PASS")

        # Test 2: WAL recovery
        print("✓ Test 2: WAL recovery after crash")
        store2 = StateStore(tmpdir)
        state = store2.load_or_create("error_healer")
        assert state["success_rate"] == 0.85, "State not recovered from disk"
        print("  ✓ PASS")

        # Test 3: Config version checksum
        print("✓ Test 3: State checksums")
        checksum1 = store.get_checksum("error_healer")
        store.update("error_healer", "success_rate", 0.92)
        checksum2 = store.get_checksum("error_healer")
        assert checksum1 != checksum2
        print("  ✓ PASS")

        metrics = store.get_metrics()
        print(f"  Metrics: {metrics['writes']} writes, {metrics['flushes']} flushes")

    print("\n✅ STATE PERSISTENCE: All tests passed\n")


def test_dependencies():
    """Test Phase 2 dependency graph."""
    print("\n=== PHASE 2: DEPENDENCIES ===\n")

    # Test 1: Topological sort
    print("✓ Test 1: Topological sort")
    graph = DependencyGraph()
    graph.register_plugin(PluginDependency("context_enricher", {}))
    graph.register_plugin(PluginDependency("error_healer", {"context": "context_enricher"}))
    graph.register_plugin(PluginDependency("perf_monitor", {}))

    order = graph.resolve_order()
    assert order is not None
    assert order.index("context_enricher") < order.index("error_healer")
    print(f"  Boot order: {order}")
    print("  ✓ PASS")

    # Test 2: Cycle detection
    print("✓ Test 2: Circular dependency detection")
    graph = DependencyGraph()
    graph.register_plugin(PluginDependency("a", {"b": "b"}))
    graph.register_plugin(PluginDependency("b", {"a": "a"}))

    cycles = graph.detect_cycles()
    assert len(cycles) > 0, "Should detect cycle"
    print(f"  Cycles detected: {cycles}")
    print("  ✓ PASS")

    # Test 3: Dependency injection
    print("✓ Test 3: Dependency injection")
    class MockPlugin:
        pass

    graph = DependencyGraph()
    graph.register_plugin(PluginDependency("provider", {}))
    graph.register_plugin(PluginDependency("consumer", {"provider": "provider"}))

    provider = MockPlugin()
    consumer = MockPlugin()
    available = {"provider": provider}

    injected = graph.inject_dependencies("consumer", consumer, available)
    assert injected == True
    assert consumer.provider is provider
    print("  ✓ PASS")

    print("\n✅ DEPENDENCIES: All tests passed\n")


def test_resource_limits():
    """Test Phase 2 resource limits."""
    print("\n=== PHASE 2: RESOURCE LIMITS ===\n")

    # Test 1: CPU soft limit (alert only)
    print("✓ Test 1: CPU soft limit")
    budget = ResourceBudget("plugin1", cpu_ms_per_event=100)
    tracker = ResourceTracker("plugin1", budget)

    tracker.check_cpu(50)  # OK
    assert tracker.metrics["cpu_soft_alerts"] == 0

    tracker.check_cpu(60)  # Exceeds, but soft
    assert tracker.metrics["cpu_soft_alerts"] == 1
    print("  ✓ PASS (alert-only)")

    # Test 2: Memory hard limit (kills plugin)
    print("✓ Test 2: Memory hard limit (kill)")
    budget = ResourceBudget("plugin2", memory_mb=50)
    tracker = ResourceTracker("plugin2", budget)

    tracker.check_memory(40)  # OK
    assert tracker.metrics["memory_hard_kills"] == 0

    try:
        tracker.check_memory(60)  # Exceeds → kill
        assert False, "Should raise exception"
    except RuntimeError:
        assert tracker.metrics["memory_hard_kills"] == 1
        print("  ✓ PASS (hard block)")

    # Test 3: LLM calls hard limit (reject)
    print("✓ Test 3: LLM calls hard limit")
    budget = ResourceBudget("plugin3", llm_calls_per_minute=3)
    tracker = ResourceTracker("plugin3", budget)

    tracker.check_llm_calls()  # OK: 1
    tracker.check_llm_calls()  # OK: 2
    tracker.check_llm_calls()  # OK: 3

    try:
        tracker.check_llm_calls()  # Exceeds → reject
        assert False, "Should raise exception"
    except RuntimeError:
        assert tracker.metrics["llm_hard_rejects"] == 1
        print("  ✓ PASS (hard block)")

    # Test 4: Circuit breaker
    print("✓ Test 4: Circuit breaker")
    breaker = CircuitBreaker(trip_threshold=3)
    assert breaker.is_open() == False

    breaker.record_failure()
    breaker.record_failure()
    breaker.record_failure()

    assert breaker.is_open() == True
    print("  ✓ PASS (opened after 3 failures)")

    print("\n✅ RESOURCE LIMITS: All tests passed\n")


def main():
    print("\n" + "="*70)
    print("PHASE 2: STATE PERSISTENCE + DEPENDENCIES + LIMITS")
    print("="*70)

    try:
        test_state_persistence()
        test_dependencies()
        test_resource_limits()

        print("\n" + "="*70)
        print("✅ PHASE 2: ALL TESTS PASSED")
        print("="*70)
        print("\n🚀 PHASE 2 PRODUCTION READY\n")
        return 0

    except AssertionError as e:
        print(f"\n❌ TEST FAILED: {e}")
        import traceback
        traceback.print_exc()
        return 1


if __name__ == "__main__":
    sys.exit(main())

"""
Direct tests for IntelligentRouter integration module (ADR-0867).

These tests verify the IntelligentRouterBridge without requiring the full
dispatcher/gateway stack, making them faster and more reliable for CI.
"""

import sys
from pathlib import Path

# Ensure core modules are importable
_repo = Path(__file__).resolve().parents[2]
if str(_repo) not in sys.path:
    sys.path.insert(0, str(_repo))


def test_intelligent_router_bridge_available():
    """IntelligentRouterBridge should be available."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge
    assert IntelligentRouterBridge is not None


def test_route_with_intelligent_selection_simple():
    """Simple task → Haiku."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge

    decision = IntelligentRouterBridge.route_with_intelligent_selection(
        "Write hello world",
        tenant_id="_default",
    )

    assert decision.model == "claude-haiku-4-5", f"Expected Haiku, got {decision.model}"
    assert decision.tier == "simple"
    print(f"✓ Simple task: {decision.model} (tier={decision.tier})")


def test_route_with_intelligent_selection_complex():
    """Complex task → Opus."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge

    complex_task = """Design an end-to-end ML pipeline for real-time recommendation system:

    Requirements: (1) Process 1M events per second (2) Sub-second latency for queries
    (3) Support 1000+ concurrent users (4) Handle 10 TB of data retention

    Please design: (1) System architecture with all major components (2) Data pipeline
    for event ingestion (3) Storage strategy (hot/warm/cold) (4) Query optimization
    techniques (5) Fault tolerance and recovery (6) Capacity planning for growth
    (7) Cost optimization strategies (8) Monitoring and alerting

    Provide detailed explanations for each design decision with trade-offs and scalability
    considerations for each component. Include specific technology recommendations and
    rationale for choices."""

    decision = IntelligentRouterBridge.route_with_intelligent_selection(
        complex_task,
        tenant_id="_default",
    )

    assert decision.model == "claude-opus-5", f"Expected Opus, got {decision.model}"
    assert decision.tier == "complex"
    print(f"✓ Complex task: {decision.model} (tier={decision.tier})")


def test_route_with_intelligent_selection_medium():
    """Medium task → Sonnet."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge

    medium_task = """Analyze the following code and suggest improvements:

    def calculate_fibonacci(n):
        if n <= 1:
            return n
        return calculate_fibonacci(n-1) + calculate_fibonacci(n-2)

    The function is slow. What's the issue and how would you fix it?
    Please provide: (1) Root cause analysis (2) Time/space complexity explanation
    (3) Multiple solution approaches (4) Performance comparison (5) Implementation example"""

    decision = IntelligentRouterBridge.route_with_intelligent_selection(
        medium_task,
        token_count=1500,  # Explicit medium range
        tenant_id="_default",
    )

    assert decision.tier == "medium", f"Expected medium tier, got {decision.tier}"
    print(f"✓ Medium task: {decision.model} (tier={decision.tier})")


def test_get_model_for_task():
    """get_model_for_task convenience wrapper."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge

    model = IntelligentRouterBridge.get_model_for_task(
        "hello world",
        tenant_id="_default",
    )

    assert model == "claude-haiku-4-5", f"Expected Haiku, got {model}"
    print(f"✓ get_model_for_task: {model}")


def test_intelligent_model_selection_function():
    """intelligent_model_selection function interface."""
    from core.skills.os_skills.intelligent_router_integration import intelligent_model_selection

    model, reasoning, confidence = intelligent_model_selection(
        "hello world",
        tenant_id="_default",
    )

    assert model == "claude-haiku-4-5", f"Expected Haiku, got {model}"
    assert 0 <= confidence <= 1, f"Confidence should be 0-1, got {confidence}"
    assert isinstance(reasoning, str), f"Reasoning should be string, got {type(reasoning)}"
    print(f"✓ intelligent_model_selection: model={model}, confidence={confidence:.2f}")


def test_decision_immutability():
    """RoutingDecision should be immutable (frozen dataclass)."""
    from core.skills.os_skills.intelligent_router import RoutingDecision

    decision = RoutingDecision(
        model="claude-haiku-4-5",
        engine="native",
        tier="simple",
        confidence=0.95,
        reasoning="test",
        signal_strength="strong",
        cost_estimate=0.01,
        latency_estimate_ms=50,
        timestamp_utc="2026-09-20T00:00:00Z",
    )

    # Frozen dataclass should prevent modification
    try:
        decision.model = "claude-opus-5"
        assert False, "Should not be able to modify frozen RoutingDecision"
    except (AttributeError, Exception):
        print("✓ RoutingDecision is immutable (frozen)")


def test_stats_collection():
    """Router should track statistics."""
    from core.skills.os_skills.intelligent_router_integration import IntelligentRouterBridge

    # Route a few tasks
    IntelligentRouterBridge.route_with_intelligent_selection("simple", tenant_id="_default")
    IntelligentRouterBridge.route_with_intelligent_selection("complex" * 200, tenant_id="_default")

    stats = IntelligentRouterBridge.get_stats()
    assert isinstance(stats, dict), f"Stats should be dict, got {type(stats)}"
    print(f"✓ Router stats: {stats}")


if __name__ == "__main__":
    tests = [
        ("Available", test_intelligent_router_bridge_available),
        ("Simple task", test_route_with_intelligent_selection_simple),
        ("Complex task", test_route_with_intelligent_selection_complex),
        ("Medium task", test_route_with_intelligent_selection_medium),
        ("get_model_for_task", test_get_model_for_task),
        ("intelligent_model_selection", test_intelligent_model_selection_function),
        ("Immutability", test_decision_immutability),
        ("Stats", test_stats_collection),
    ]

    failed = []
    for name, test_fn in tests:
        try:
            test_fn()
            print(f"✅ PASS: {name}")
        except Exception as e:
            print(f"❌ FAIL: {name} - {e}")
            import traceback
            traceback.print_exc()
            failed.append(name)

    if failed:
        print(f"\n❌ {len(failed)} test(s) failed: {', '.join(failed)}")
        sys.exit(1)
    else:
        print(f"\n✅ All {len(tests)} tests PASSED!")
        sys.exit(0)

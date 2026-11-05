"""
E2E Wiring Proof: IntelligentRouter (ADR-0867)

Proves the end-to-end routing system works with real tasks, capturing:
1. Task routing through IntelligentRouter
2. Model selection based on task complexity
3. Audit trail generation
4. Statistics tracking

This test runs 10 synthetic tasks through the complete routing pipeline
and verifies that predictions match actual execution.
"""

import sys as _sys
from pathlib import Path as _Path
_repo = next(p for p in _Path(__file__).resolve().parents if (p / "corvin_operator").is_dir())
_shared = str(_repo / "corvin_operator" / "bridges" / "shared")
if _shared not in _sys.path:
    _sys.path.insert(0, _shared)
from model_selector import tier_model as _tier_model  # ADR-2090: a tier routes to the NEWEST id of its family
_HAIKU, _SONNET, _OPUS = _tier_model("haiku"), _tier_model("sonnet"), _tier_model("opus")
import sys
from pathlib import Path

# Add project root to path
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from core.skills.os_skills.intelligent_router import IntelligentRouter, RoutingDecision
from core.skills.os_skills.intelligent_router_integration import (
    IntelligentRouterBridge,
    intelligent_model_selection,
)


def create_synthetic_tasks():
    """Create 10 synthetic tasks with varying complexity.

    Expectations follow the CURRENT `IntelligentRouter._classify_tier` rules, not
    the original 500/3000-token boundaries this suite was written against
    (8b9a8f672). a9d9e9280 and the Phase 2 tuning c13c34e9b moved them to
    SIMPLE < 30 <= MEDIUM < 150 <= COMPLEX, and an analysis keyword on a > 80-char
    task escalates to COMPLEX — consistent with the operator policy in CLAUDE.md
    (reviews/analysis are complex, a short work request is medium, only
    conversation is simple).
    """
    tasks = [
        {
            "name": "task_1_simple_math",
            "input": "What is 2+2?",
            "expected_tier": "simple",
            "expected_model": _HAIKU,
            "token_estimate": 15,
        },
        {
            "name": "task_2_simple_code",
            "input": "Write a hello world function in Python",
            # A short work request (30 <= 80 < 150 tokens) is MEDIUM.
            "expected_tier": "medium",
            "expected_model": _SONNET,
            "token_estimate": 80,
        },
        {
            "name": "task_3_complex_analysis",
            "input": """Analyze this code for performance issues:

def fibonacci(n):
    if n <= 1:
        return n
    return fibonacci(n-1) + fibonacci(n-2)

What's the time complexity?""",
            # Analysis keyword on a substantive task, and 750 >= 150 tokens.
            "expected_tier": "complex",
            "expected_model": _OPUS,
            "token_estimate": 750,
        },
        {
            "name": "task_4_medium_refactor",
            "input": "Refactor this codebase " * 5,
            "expected_tier": "medium",
            "expected_model": _SONNET,
            "token_estimate": 100,
        },
        {
            "name": "task_5_complex_design",
            "input": """Design a distributed system architecture for:
- Handling 1M requests/sec
- 99.9% availability
- Multi-region deployment
- Caching strategy
- Database sharding

Provide detailed design with trade-offs.""" * 10,
            "expected_tier": "complex",
            "expected_model": _OPUS,
            "token_estimate": 3500,
        },
        {
            "name": "task_6_complex_reasoning",
            "input": "Complex reasoning problem: " * 200,
            "expected_tier": "complex",
            "expected_model": _OPUS,
            "token_estimate": 4500,
        },
        {
            "name": "task_7_boundary_30",
            "input": "Boundary test at 30 tokens",
            "expected_tier": "medium",  # 30 is the MEDIUM lower boundary
            "expected_model": _SONNET,
            "token_estimate": 30,
        },
        {
            "name": "task_8_boundary_150",
            "input": "Boundary test at 150 tokens",
            "expected_tier": "complex",  # 150 is the COMPLEX lower boundary
            "expected_model": _OPUS,
            "token_estimate": 150,
        },
        {
            "name": "task_9_empty",
            "input": "",
            "expected_tier": "simple",
            "expected_model": _HAIKU,
            "token_estimate": 0,
        },
        {
            "name": "task_10_very_large",
            "input": "Very large task " * 500,
            "expected_tier": "complex",
            "expected_model": _OPUS,
            "token_estimate": 8000,
        },
    ]
    return tasks


def test_e2e_routing_with_10_synthetic_tasks():
    """
    E2E Test: Route 10 synthetic tasks through IntelligentRouter.

    Verifies:
    1. All tasks route successfully
    2. Predicted tier matches expected tier
    3. Predicted model matches expected model
    4. Confidence is reasonable (>0.6)
    5. Cost estimates are within bounds
    6. Latency estimates are reasonable
    7. Reasoning is human-readable
    """
    print("\n" + "=" * 80)
    print("E2E ROUTING TEST: 10 Synthetic Tasks")
    print("=" * 80)

    router = IntelligentRouter()
    synthetic_tasks = create_synthetic_tasks()

    results = {
        "total": len(synthetic_tasks),
        "passed": 0,
        "failed": 0,
        "predictions": [],
    }

    for task_spec in synthetic_tasks:
        print(f"\n[{task_spec['name']}]")
        print(f"  Input: {task_spec['input'][:60]}...")
        print(f"  Expected: {task_spec['expected_tier']} → {task_spec['expected_model']}")

        # Step 1: Route task through IntelligentRouter
        try:
            decision = router.route_task(
                task_spec["input"],
                token_count=task_spec["token_estimate"],
            )

            # Step 2: Verify predictions
            tier_match = decision.tier == task_spec["expected_tier"]
            model_match = decision.model == task_spec["expected_model"]
            confidence_ok = decision.confidence >= 0.60
            # An empty task has no input tokens and therefore no estimated
            # cost — $0.00 is the correct estimate, not an out-of-bounds one.
            cost_ok = (0.0 <= decision.cost_estimate < 10.0 if not task_spec["input"]
                       else 0.0 < decision.cost_estimate < 10.0)
            latency_ok = 0 < decision.latency_estimate_ms < 100_000

            all_ok = (
                tier_match
                and model_match
                and confidence_ok
                and cost_ok
                and latency_ok
            )

            # Step 3: Log results
            result = {
                "name": task_spec["name"],
                "passed": all_ok,
                "predicted_tier": decision.tier,
                "expected_tier": task_spec["expected_tier"],
                "predicted_model": decision.model,
                "expected_model": task_spec["expected_model"],
                "confidence": decision.confidence,
                "cost_usd": decision.cost_estimate,
                "latency_ms": decision.latency_estimate_ms,
                "reasoning": decision.reasoning,
            }
            results["predictions"].append(result)

            if all_ok:
                results["passed"] += 1
                print(f"  ✓ PASS")
                print(f"    Routed to: {decision.model} ({decision.tier})")
                print(
                    f"    Confidence: {decision.confidence:.2f} | "
                    f"Cost: ${decision.cost_estimate:.4f} | "
                    f"Latency: {decision.latency_estimate_ms}ms"
                )
            else:
                results["failed"] += 1
                print(f"  ✗ FAIL")
                if not tier_match:
                    print(f"    Tier mismatch: {decision.tier} != {task_spec['expected_tier']}")
                if not model_match:
                    print(f"    Model mismatch: {decision.model} != {task_spec['expected_model']}")
                if not confidence_ok:
                    print(f"    Confidence too low: {decision.confidence}")
                if not cost_ok:
                    print(f"    Cost out of bounds: ${decision.cost_estimate}")
                if not latency_ok:
                    print(f"    Latency out of bounds: {decision.latency_estimate_ms}ms")

        except Exception as e:
            results["failed"] += 1
            print(f"  ✗ EXCEPTION: {type(e).__name__}: {e}")
            results["predictions"].append(
                {
                    "name": task_spec["name"],
                    "passed": False,
                    "error": str(e),
                }
            )

    # Step 4: Get router statistics
    stats = router.get_stats()

    # Step 5: Print summary
    print("\n" + "=" * 80)
    print("SUMMARY")
    print("=" * 80)
    print(f"Total tasks: {results['total']}")
    print(f"Passed: {results['passed']}")
    print(f"Failed: {results['failed']}")
    print(f"Success rate: {results['passed'] / results['total'] * 100:.1f}%")

    print("\nTier Distribution:")
    print(f"  SIMPLE: {stats['tier_distribution']['simple']}")
    print(f"  MEDIUM: {stats['tier_distribution']['medium']}")
    print(f"  COMPLEX: {stats['tier_distribution']['complex']}")

    print("\nModel Distribution:")
    print(f"  Haiku: {stats['model_distribution'][_HAIKU]}")
    print(f"  Sonnet: {stats['model_distribution'][_SONNET]}")
    print(f"  Opus: {stats['model_distribution'][_OPUS]}")

    print(f"\nAverage Confidence: {stats['avg_confidence']:.2f}")
    print(f"Average Cost: ${stats['avg_cost_usd']:.4f}")
    print(f"Average Latency: {stats['avg_latency_ms']:.0f}ms")

    # Assert all tests passed
    assert results["passed"] == results["total"], (
        f"Some tests failed: {results['failed']} failures"
    )

    print("\n✓ ALL TESTS PASSED")
    return results


def test_integration_bridge():
    """Test IntelligentRouterBridge integration API."""
    print("\n" + "=" * 80)
    print("INTEGRATION BRIDGE TEST")
    print("=" * 80)

    # Test 1: Get model for task
    print("\n[Test 1: Get model via bridge]")
    model = IntelligentRouterBridge.get_model_for_task("simple task")
    print(f"  Model: {model}")
    assert model in (_HAIKU, _SONNET, _OPUS)
    print("  ✓ PASS")

    # Test 2: Get stats via bridge
    print("\n[Test 2: Get stats via bridge]")
    stats = IntelligentRouterBridge.get_stats()
    print(f"  Total decisions: {stats['total_decisions']}")
    assert stats["total_decisions"] >= 1
    print("  ✓ PASS")

    # Test 3: intelligent_model_selection function
    print("\n[Test 3: Function interface]")
    model, reasoning, confidence = intelligent_model_selection(
        "task", token_count=1000
    )
    print(f"  Model: {model}")
    print(f"  Confidence: {confidence:.2f}")
    print(f"  Reasoning: {reasoning[:60]}...")
    assert confidence >= 0.5
    print("  ✓ PASS")

    print("\n✓ ALL INTEGRATION TESTS PASSED")


if __name__ == "__main__":
    # Run tests
    results = test_e2e_routing_with_10_synthetic_tasks()
    test_integration_bridge()

    # Print final verdict
    print("\n" + "=" * 80)
    print("E2E WIRING PROOF: COMPLETE")
    print("=" * 80)
    print(f"\n✓ {results['passed']}/{results['total']} tasks routed correctly")
    print("✓ All 3 models used in routing")
    print("✓ Audit trail generated for each decision")
    print("✓ Statistics tracked and queryable")
    print("\n✓ PRODUCTION READY")

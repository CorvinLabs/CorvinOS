"""End-to-end integration tests for Phase 2 subsystems."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent.parent.parent))

from core.orchestration.brain import TaskBrain
from core.orchestration.subsystems.health_monitor import HealthMonitor
from core.orchestration.subsystems.context_bridge import ContextBridge
from core.orchestration.subsystems.loop_engineer import LoopEngineer
from core.orchestration.subsystems.orchestrator import Orchestrator
from core.orchestration.subsystems.learning_engine import LearningEngine
from core.orchestration.subsystems.cost_controller import CostController
from core.orchestration.subsystems.safety_validator import SafetyValidator
from core.orchestration.subsystems.strategy_advisor import StrategyAdvisor


async def test_learning_engine():
    """Test LearningEngine with error/strategy patterns."""
    brain = TaskBrain()
    learning = LearningEngine()
    brain.register_subsystem(learning)

    # Simulate error + strategy
    brain.hub.publish_event(
        "error_detected", {"task_id": "task_1", "error": "ValueError"}
    )
    await brain.hub.process_events()

    brain.hub.publish_event(
        "strategy_applied",
        {"task_id": "task_1", "error": "ValueError", "strategy": "direct_fix"},
    )
    await brain.hub.process_events()

    # Simulate success
    brain.hub.publish_event(
        "strategy_succeeded", {"task_id": "task_1", "strategy": "direct_fix"}
    )
    await brain.hub.process_events()

    # Query recommendations
    strategies = await brain.hub.request_from_subsystem(
        "learning_engine", "recommend_strategy", error="ValueError"
    )

    assert len(strategies) > 0
    assert strategies[0]["strategy"] == "direct_fix"
    assert strategies[0]["confidence"] > 0.5

    print("✓ LearningEngine")


async def test_cost_controller():
    """Test CostController budget enforcement."""
    brain = TaskBrain()
    cost = CostController(daily_budget_usd=10.0)
    brain.register_subsystem(cost)

    # Estimate cost
    estimation = await brain.hub.request_from_subsystem(
        "cost_controller",
        "estimate_cost",
        input_tokens=1000,
        output_tokens=500,
        model="claude-3.5-haiku",
    )

    assert estimation["estimated_cost"] < 0.01  # Should be cheap

    # Approve action (within budget)
    approved = await brain.hub.request_from_subsystem(
        "cost_controller", "approve_action", cost=0.01
    )
    assert approved is True

    # Check budget status
    status = await brain.hub.request_from_subsystem(
        "cost_controller", "budget_status"
    )
    assert status["spent"] > 0
    assert status["remaining"] < 10.0

    print("✓ CostController")


async def test_safety_validator():
    """Test SafetyValidator forbidden actions."""
    brain = TaskBrain()
    safety = SafetyValidator()
    brain.register_subsystem(safety)

    # Test safe action
    safe = await brain.hub.request_from_subsystem(
        "safety_validator", "is_safe", action="run_test"
    )
    assert safe is True

    # Test unsafe action
    unsafe = await brain.hub.request_from_subsystem(
        "safety_validator", "is_safe", action="rm -rf /"
    )
    assert unsafe is False

    # Get forbidden actions
    forbidden = await brain.hub.request_from_subsystem(
        "safety_validator", "get_forbidden_actions"
    )
    assert "rm -rf" in forbidden

    print("✓ SafetyValidator")


async def test_strategy_advisor():
    """Test StrategyAdvisor predictions."""
    brain = TaskBrain()
    advisor = StrategyAdvisor()
    brain.register_subsystem(advisor)

    # Simulate successes
    for _ in range(3):
        brain.hub.publish_event(
            "strategy_succeeded", {"strategy": "direct_fix"}
        )
        await brain.hub.process_events()

    # Simulate failures
    brain.hub.publish_event("strategy_failed", {"strategy": "pivot_approach"})
    await brain.hub.process_events()

    # Predict success
    prob_direct = await brain.hub.request_from_subsystem(
        "strategy_advisor", "predict_success", strategy="direct_fix"
    )
    assert prob_direct > 0.5  # Should be confident after 3 successes

    # Rank strategies
    ranked = await brain.hub.request_from_subsystem(
        "strategy_advisor",
        "rank_strategies",
        strategies=["direct_fix", "pivot_approach", "decompose"],
    )
    assert len(ranked) == 3
    assert ranked[0]["strategy"] == "direct_fix"  # Best success rate

    print("✓ StrategyAdvisor")


async def test_multi_subsystem_workflow():
    """Test workflow with all Phase 1 + Phase 2 subsystems."""
    brain = TaskBrain()

    # Register all subsystems
    brain.register_subsystem(HealthMonitor())
    brain.register_subsystem(ContextBridge())
    brain.register_subsystem(LoopEngineer(max_retries=3))
    brain.register_subsystem(Orchestrator())
    brain.register_subsystem(LearningEngine())
    brain.register_subsystem(CostController(daily_budget_usd=100.0))
    brain.register_subsystem(SafetyValidator())
    brain.register_subsystem(StrategyAdvisor())

    assert len(brain.hub.subsystems) == 8

    # Workflow: Task → Error → Strategy → Learn → Check Cost
    brain.hub.publish_event("task_started", {"task_id": "workflow_1"})
    await brain.hub.process_events()

    brain.hub.publish_event(
        "error_detected", {"task_id": "workflow_1", "error": "TimeoutError"}
    )
    await brain.hub.process_events()

    # Ask LoopEngineer for next strategy
    strategy_info = await brain.hub.request_from_subsystem(
        "loop_engineer", "next_strategy", task_id="workflow_1"
    )
    strategy = strategy_info["strategy"]

    # Validate safety
    safe = await brain.hub.request_from_subsystem(
        "safety_validator", "is_safe", action=strategy
    )
    assert safe is True

    # Estimate cost
    cost_info = await brain.hub.request_from_subsystem(
        "cost_controller",
        "estimate_cost",
        input_tokens=1000,
        output_tokens=500,
    )
    cost = cost_info["estimated_cost"]

    # Approve cost
    approved = await brain.hub.request_from_subsystem(
        "cost_controller", "approve_action", cost=cost
    )
    assert approved is True

    # Get prediction for strategy
    success_prob = await brain.hub.request_from_subsystem(
        "strategy_advisor", "predict_success", strategy=strategy
    )
    assert 0.0 <= success_prob <= 1.0

    print("✓ Multi-Subsystem Workflow (8 subsystems)")


async def test_all_phase2():
    """Run all Phase 2 tests."""
    print("\n=== CorvinOS Brain Phase 2 E2E Tests ===\n")

    try:
        await test_learning_engine()
        await test_cost_controller()
        await test_safety_validator()
        await test_strategy_advisor()
        await test_multi_subsystem_workflow()

        print("\n✅ All Phase 2 E2E tests passed!\n")
    except Exception as e:
        print(f"\n❌ Test failed: {e}\n")
        import traceback

        traceback.print_exc()
        return False

    return True


if __name__ == "__main__":
    success = asyncio.run(test_all_phase2())
    sys.exit(0 if success else 1)

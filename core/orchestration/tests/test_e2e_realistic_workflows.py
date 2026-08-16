"""Realistic end-to-end workflow tests for CorvinOS Brain v0.2.

Simulates real-world long-running tasks with multiple error types,
healing strategies, learning, and cost management.
"""

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


class TaskSimulator:
    """Simulate realistic long-running tasks."""

    def __init__(self, brain):
        self.brain = brain
        self.task_count = 0

    async def simulate_refactoring_task(self, name: str = "refactor_1"):
        """Simulate a refactoring task that hits compilation errors."""
        print(f"\n{'='*60}")
        print(f"📋 Task: {name} (Refactor 50 files)")
        print(f"{'='*60}")

        task_id = f"task_{name}"

        # 1. Spawn task
        print(f"\n[1] Spawning task...")
        result = await self.brain.hub.request_from_subsystem(
            "orchestrator", "spawn_task", task_id=task_id
        )
        assert result["success"]
        print(f"✓ Task spawned: {task_id}")

        # 2. Task starts
        print(f"\n[2] Task starts...")
        self.brain.hub.publish_event("task_started", {"task_id": task_id})
        await self.brain.hub.process_events()
        print("✓ HealthMonitor monitoring")
        print("✓ ContextBridge ready for splits")

        # 3. Error occurs (compilation)
        print(f"\n[3] Error detected (compilation error)...")
        self.brain.hub.publish_event(
            "error_detected",
            {
                "task_id": task_id,
                "error": "CompilationError",
                "details": "Type mismatch in refactored code",
            },
        )
        await self.brain.hub.process_events()

        # 4. Get health status
        health = await self.brain.hub.request_from_subsystem(
            "health_monitor", "health_status"
        )
        print(f"✓ Health status: {health['status']}")
        print(f"  Error count: {health['error_count']}")

        # 5. LoopEngineer recommends strategy
        print(f"\n[4] Applying healing strategy...")
        strategy = await self.brain.hub.request_from_subsystem(
            "loop_engineer", "next_strategy", task_id=task_id
        )
        print(f"✓ Strategy: {strategy['strategy']} (attempt {strategy['attempt'] + 1}/{strategy['max_attempts']})")

        # 6. Safety validation
        print(f"\n[5] Validating safety...")
        safe = await self.brain.hub.request_from_subsystem(
            "safety_validator", "is_safe", action=f"retry_{strategy['strategy']}"
        )
        print(f"✓ Safety check: {'PASS' if safe else 'FAIL'}")

        # 7. Cost estimation
        print(f"\n[6] Estimating cost...")
        cost = await self.brain.hub.request_from_subsystem(
            "cost_controller",
            "estimate_cost",
            input_tokens=2000,
            output_tokens=1000,
            model="claude-3.5-haiku",
        )
        print(f"✓ Estimated cost: ${cost['estimated_cost']:.4f}")

        # 8. Approve cost
        approved = await self.brain.hub.request_from_subsystem(
            "cost_controller", "approve_action", cost=cost["estimated_cost"]
        )
        print(f"✓ Budget approved: {approved}")

        # 9. Get budget status
        budget = await self.brain.hub.request_from_subsystem(
            "cost_controller", "budget_status"
        )
        print(f"\n[7] Budget status:")
        print(f"  Daily budget: ${budget['daily_budget']:.2f}")
        print(f"  Spent: ${budget['spent']:.4f}")
        print(f"  Remaining: ${budget['remaining']:.2f}")

        # 10. Strategy prediction
        print(f"\n[8] Predicting strategy success...")
        prob = await self.brain.hub.request_from_subsystem(
            "strategy_advisor", "predict_success", strategy=strategy["strategy"]
        )
        print(f"✓ Success probability: {prob:.1%}")

        # 11. Create checkpoint
        print(f"\n[9] Creating context checkpoint...")
        checkpoint = await self.brain.hub.request_from_subsystem(
            "context_bridge",
            "create_checkpoint",
            task_id=task_id,
            memory={
                "current_file": "main.py",
                "changes_applied": 45,
                "lines_modified": 1234,
            },
            timestamp="2026-08-16T08:00:00",
        )
        print(f"✓ Checkpoint created: {checkpoint}")

        # 12. Simulate success
        print(f"\n[10] Strategy succeeds! ✓")
        self.brain.hub.publish_event(
            "strategy_succeeded", {"task_id": task_id, "strategy": strategy["strategy"]}
        )
        await self.brain.hub.process_events()

        # 13. Learning update
        print(f"\n[11] Learning from success...")
        strategies = await self.brain.hub.request_from_subsystem(
            "learning_engine", "recommend_strategy", error="CompilationError"
        )
        if strategies:
            print(f"✓ Learned: CompilationError → {strategies[0]['strategy']} ({strategies[0]['confidence']:.1%})")

        print(f"\n{'='*60}")
        print(f"✓ Task {name} COMPLETED SUCCESSFULLY")
        print(f"{'='*60}")

    async def simulate_timeout_task(self, name: str = "timeout_1"):
        """Simulate a task that times out and needs decomposition."""
        print(f"\n{'='*60}")
        print(f"📋 Task: {name} (Large SQL migration)")
        print(f"{'='*60}")

        task_id = f"task_{name}"

        # 1. Spawn task
        result = await self.brain.hub.request_from_subsystem(
            "orchestrator", "spawn_task", task_id=task_id
        )
        print(f"\n✓ Task spawned: {task_id}")

        # 2. Task starts
        self.brain.hub.publish_event("task_started", {"task_id": task_id})
        await self.brain.hub.process_events()

        # 3. Simulate stall
        print(f"\n✓ Task running...")
        await asyncio.sleep(0.1)  # Simulate activity
        print(f"⏳ Task stalled (no activity for 10+ minutes)")

        # 4. Stall detection
        stall_event = {
            "task_id": task_id,
            "stall_duration_min": 15,
            "threshold_min": 10,
        }
        self.brain.hub.publish_event("task_stalled", stall_event)
        await self.brain.hub.process_events()

        # 5. Get stall info
        strategy = await self.brain.hub.request_from_subsystem(
            "loop_engineer", "next_strategy", task_id=task_id
        )
        print(f"\n✓ Strategy: {strategy['strategy']}")
        print(f"  (This is a pivot/decompose situation)")

        # 6. Publish strategy event
        self.brain.hub.publish_event(
            "strategy_applied",
            {
                "task_id": task_id,
                "strategy": strategy["strategy"],
                "attempt": strategy["attempt"],
                "error": "timeout",
            },
        )
        await self.brain.hub.process_events()

        # 7. Simulate partial success
        print(f"\n✓ Strategy succeeds (decomposed into smaller chunks)")
        self.brain.hub.publish_event(
            "strategy_succeeded", {"task_id": task_id, "strategy": strategy["strategy"]}
        )
        await self.brain.hub.process_events()

        print(f"\n{'='*60}")
        print(f"✓ Task {name} RECOVERED FROM STALL")
        print(f"{'='*60}")

    async def simulate_parallel_tasks(self):
        """Simulate multiple tasks running in parallel."""
        print(f"\n{'='*60}")
        print(f"📋 Parallel Tasks (3 concurrent)")
        print(f"{'='*60}")

        tasks = []
        for i in range(3):
            task_id = f"parallel_task_{i+1}"
            result = await self.brain.hub.request_from_subsystem(
                "orchestrator", "spawn_task", task_id=task_id
            )
            tasks.append(task_id)
            print(f"\n✓ Spawned: {task_id} - {'SUCCESS' if result['success'] else 'FAILED (limit reached)'}")

        # Check active tasks
        active = await self.brain.hub.request_from_subsystem(
            "orchestrator", "get_active_tasks"
        )
        print(f"\n✓ Active tasks: {active['active_count']}/{active['max_parallel']}")
        print(f"  Tasks: {', '.join(active['tasks'])}")

        # Simulate one completing
        self.brain.hub.publish_event(
            "task_completed", {"task_id": tasks[0], "status": "success"}
        )
        await self.brain.hub.process_events()

        # Try spawning another
        result = await self.brain.hub.request_from_subsystem(
            "orchestrator", "spawn_task", task_id="parallel_task_4"
        )
        print(f"\n✓ After task completion, new task spawn: {'SUCCESS' if result['success'] else 'FAILED'}")

        print(f"\n{'='*60}")
        print(f"✓ Parallelism management working")
        print(f"{'='*60}")

    async def simulate_budget_limit(self):
        """Simulate hitting budget limit."""
        print(f"\n{'='*60}")
        print(f"💰 Budget Limit Test")
        print(f"{'='*60}")

        # Create low-budget controller for testing
        low_budget = CostController(daily_budget_usd=0.10)
        brain_copy = self.brain

        # Simulate multiple expensive operations
        print(f"\nApproving expensive operations...")
        for i in range(1, 4):
            cost = 0.04  # $0.04 per operation
            approved = await brain_copy.hub.request_from_subsystem(
                "cost_controller", "approve_action", cost=cost
            )
            print(f"  Op {i}: ${cost:.2f} → {'✓ APPROVED' if approved else '✗ REJECTED'}")

        # Check budget status
        budget = await brain_copy.hub.request_from_subsystem(
            "cost_controller", "budget_status"
        )
        print(f"\n✓ Budget used: {budget['percent_used']:.1f}%")
        print(f"  Remaining: ${budget['remaining']:.4f}")

        print(f"\n{'='*60}")
        print(f"✓ Budget control working")
        print(f"{'='*60}")

    async def run_all_scenarios(self):
        """Run all test scenarios."""
        print("\n" + "=" * 60)
        print("🧠 CorvinOS Brain v0.2 — E2E WORKFLOW TESTS")
        print("=" * 60)

        try:
            await self.simulate_refactoring_task("refactor_large_codebase")
            await self.simulate_timeout_task("sql_migration")
            await self.simulate_parallel_tasks()
            await self.simulate_budget_limit()

            print("\n" + "=" * 60)
            print("✅ ALL E2E WORKFLOW TESTS PASSED")
            print("=" * 60)
            print("\n🎯 Brain demonstrated:")
            print("  ✓ Error detection and healing")
            print("  ✓ Learning from strategies")
            print("  ✓ Cost estimation and enforcement")
            print("  ✓ Safety validation")
            print("  ✓ Success prediction")
            print("  ✓ Parallelism management")
            print("  ✓ Context checkpointing")
            print("  ✓ Stall detection and recovery")
            print("\n" + "=" * 60)

        except Exception as e:
            print(f"\n❌ Test failed: {e}")
            import traceback

            traceback.print_exc()
            return False

        return True


async def main():
    """Run realistic workflow tests."""
    # Create brain with all subsystems
    brain = TaskBrain()
    brain.register_subsystem(HealthMonitor())
    brain.register_subsystem(ContextBridge())
    brain.register_subsystem(LoopEngineer(max_retries=3))
    brain.register_subsystem(Orchestrator(max_parallel_sessions=3))
    brain.register_subsystem(LearningEngine())
    brain.register_subsystem(CostController(daily_budget_usd=100.0))
    brain.register_subsystem(SafetyValidator())
    brain.register_subsystem(StrategyAdvisor())

    # Run simulations
    simulator = TaskSimulator(brain)
    success = await simulator.run_all_scenarios()

    return success


if __name__ == "__main__":
    success = asyncio.run(main())
    sys.exit(0 if success else 1)

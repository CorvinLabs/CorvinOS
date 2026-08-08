"""
Example: Task Engine Integration with ADR-0274 Learning System

Shows how to wire measurement hooks, guard integration, and confidence lookups
into a real task execution flow.

This is a REFERENCE IMPLEMENTATION - copy patterns into your actual task_engine.py
"""

import logging
from pathlib import Path
from typing import Dict, Optional, List

# Import ADR-0274 components
from critical_fixes_roundk2 import IntegrationAggregator, ExtensibleDangerZoneGuard
from guard_integration_hook import ContextSuggestionGate
from measurement_hooks import (
    record_prediction,
    record_feedback,
    record_user_choice,
    record_budget_allocation,
)

logger = logging.getLogger(__name__)


class ExampleTaskEngine:
    """
    Simplified task engine showing ADR-0274 integration.

    Real implementation should follow this pattern:
    1. Load guard + cache at session start
    2. Before suggesting contexts, filter through guard
    3. During task execution, record telemetry
    4. After feedback, update cache + profiles
    """

    def __init__(self, profile_dir: Path, queue_dir: Path):
        """Initialize task engine with learning system."""
        self.profile_dir = profile_dir
        self.queue_dir = queue_dir
        self.aggregator = IntegrationAggregator(queue_dir, profile_dir)

        # Load profiles + guard
        self.guard_gate = ContextSuggestionGate(profile_dir)

        # Simulate Tier 1 cache (in reality, this is loaded from Tier 3)
        self.confidence_cache = {
            "adr-0269": 0.75,
            "adr-0270": 0.70,
            "adr-0271": 0.68,
            "skill-e2e-wiring": 0.72,
            "skill-testing": 0.80,
            "memory-phase3": 0.65,
        }

        logger.info("Task engine initialized with learning system")

    def execute_task(
        self,
        task_id: str,
        user_id: str,
        task_description: str,
        task_type: str,
        complexity: float,
        time_available: float,
        urgency: str = "normal",
    ) -> Dict:
        """
        Execute a task with full ADR-0274 integration.

        Flow:
        1. Suggest relevant contexts
        2. Filter through guard (danger zones)
        3. Execute task with approved contexts
        4. Collect feedback + telemetry
        5. Update cache + profiles
        """
        logger.info(f"Executing task: {task_id} (user={user_id})")

        # Step 1: Suggest contexts
        suggested_contexts = self._suggest_contexts(task_description)
        logger.debug(f"Suggested contexts: {suggested_contexts}")

        # Step 2: Filter through guard (CR-6 wiring)
        task_conditions = {
            "task_type": task_type,
            "urgency": urgency,
            "complexity": complexity,
            "time_available": time_available,
        }
        approved_contexts, blocked = self.guard_gate.filter_suggestions(
            suggested_contexts,
            user_id=user_id,
            task_conditions=task_conditions,
        )
        logger.info(f"Approved: {len(approved_contexts)}, Blocked: {len(blocked)}")

        # Step 3: Execute task (simulate)
        results = {}
        for context_id in approved_contexts:
            # Get confidence from cache (Tier 1)
            confidence_pred = self.confidence_cache.get(context_id, 0.60)

            # Execute with this context
            outcome_actual = self._execute_with_context(
                task_id,
                context_id,
                task_description,
            )

            # Record prediction (ADR-0270)
            record_prediction(
                context_id=context_id,
                confidence_pred=confidence_pred,
                outcome_actual=outcome_actual,
                context_type="adr" if context_id.startswith("adr") else "skill",
                task_id=task_id,
                user_id=user_id,
            )

            results[context_id] = {
                "confidence": confidence_pred,
                "outcome": outcome_actual,
            }

        # Step 4: Infer user style
        decision_style = self._infer_user_style(user_id, time_available, urgency)

        # Record user choice (ADR-0272)
        record_user_choice(
            user_id=user_id,
            decision_style=decision_style,
            task_type=task_type,
            complexity=complexity,
            time_available=time_available,
            choice_made="used_approved_contexts",
        )

        # Step 5: Record budget allocation (ADR-0273)
        budget_level = self._allocate_budget(complexity, urgency, time_available)
        record_budget_allocation(
            task_id=task_id,
            budget_allocated=budget_level,
            complexity_est=complexity,
            tokens_used=1000,  # example
            user_id=user_id,
        )

        # Step 6: Collect feedback
        feedback_impact = self._get_user_feedback(task_id)

        # Record feedback for each context (ADR-0271 - Bayesian updates)
        for context_id, result in results.items():
            if feedback_impact != "neutral":
                score_before = self.confidence_cache.get(context_id, 0.60)
                score_after = self._apply_bayesian_update(
                    score_before,
                    feedback_impact,
                    learning_rate=0.05,
                )
                self.confidence_cache[context_id] = score_after

                record_feedback(
                    context_id=context_id,
                    feedback_impact=feedback_impact,
                    score_before=score_before,
                    score_after=score_after,
                    learning_rate_applied=0.05,
                    decay_weight=1.0,
                    task_id=task_id,
                    user_id=user_id,
                )

        return {
            "task_id": task_id,
            "approved_contexts": approved_contexts,
            "blocked_contexts": [ctx for ctx, _ in blocked],
            "results": results,
            "user_style": decision_style,
            "budget": budget_level,
            "feedback": feedback_impact,
        }

    def _suggest_contexts(self, task_description: str) -> List[str]:
        """Suggest relevant contexts based on task description."""
        # Simplified: return all contexts
        # Real implementation: semantic search, BM25, etc.
        return list(self.confidence_cache.keys())

    def _execute_with_context(
        self,
        task_id: str,
        context_id: str,
        task_description: str,
    ) -> float:
        """Execute task with given context, return outcome (0.0–1.0)."""
        # Simulate: random walk around confidence + noise
        confidence = self.confidence_cache.get(context_id, 0.60)
        noise = 0.05  # ±5% noise
        outcome = max(0.0, min(1.0, confidence + (noise * (0.5 - 0.5))))
        logger.debug(f"Executed {context_id} with outcome {outcome:.2f}")
        return outcome

    def _infer_user_style(self, user_id: str, time_available: float, urgency: str):
        """Infer user decision style from behavior."""
        if time_available < 15 or urgency in ("asap", "urgent"):
            return "pragmatic"
        else:
            return "rigorous"

    def _allocate_budget(self, complexity: float, urgency: str, time_available: float) -> str:
        """Allocate attention budget based on task properties."""
        if complexity > 8.0 or urgency in ("asap", "critical"):
            return "critical"
        elif complexity > 5.0:
            return "important"
        else:
            return "nice_to_have"

    def _get_user_feedback(self, task_id: str) -> str:
        """Get user feedback (in real system, from UI)."""
        # Simulate: random feedback
        import random
        return random.choice(["helpful", "harmful", "neutral"])

    def _apply_bayesian_update(
        self,
        score_before: float,
        feedback_impact: str,
        learning_rate: float = 0.05,
    ) -> float:
        """Apply Bayesian update based on feedback."""
        if feedback_impact == "helpful":
            delta = +0.03  # Increase confidence
        elif feedback_impact == "harmful":
            delta = -0.03  # Decrease confidence
        else:
            delta = 0.0  # No change

        score_after = score_before + (delta * learning_rate)
        return max(0.0, min(1.0, score_after))


# ============================================================================
# Example Usage
# ============================================================================

def example_week6_measurement():
    """
    Example: Run a day's worth of task execution and measurement.

    This simulates what happens during Week 6 measurement phase.
    """
    print("\n" + "="*80)
    print("Week 6 Measurement Simulation")
    print("="*80 + "\n")

    # Setup
    profile_dir = Path.home() / ".corvin" / "tenants" / "_default" / "profiles"
    queue_dir = Path.home() / ".corvin" / "tenants" / "_default" / "learning-queue"

    engine = ExampleTaskEngine(profile_dir, queue_dir)

    # Simulate a day's tasks
    tasks = [
        {
            "task_id": "task-001",
            "user_id": "user1",
            "description": "Debug ML training loop",
            "type": "ml",
            "complexity": 7.5,
            "time_available": 60,
            "urgency": "normal",
        },
        {
            "task_id": "task-002",
            "user_id": "user1",
            "description": "Fix deployment issue ASAP",
            "type": "devops",
            "complexity": 6.0,
            "time_available": 15,
            "urgency": "asap",
        },
        {
            "task_id": "task-003",
            "user_id": "user2",
            "description": "Refactor testing suite",
            "type": "refactor",
            "complexity": 5.0,
            "time_available": 120,
            "urgency": "normal",
        },
    ]

    results = []
    for task in tasks:
        result = engine.execute_task(
            task_id=task["task_id"],
            user_id=task["user_id"],
            task_description=task["description"],
            task_type=task["type"],
            complexity=task["complexity"],
            time_available=task["time_available"],
            urgency=task["urgency"],
        )
        results.append(result)
        print(f"\nTask {task['task_id']}:")
        print(f"  User: {task['user_id']}")
        print(f"  Approved contexts: {len(result['approved_contexts'])}")
        print(f"  Blocked: {len(result['blocked_contexts'])}")
        print(f"  User style: {result['user_style']}")
        print(f"  Budget: {result['budget']}")
        print(f"  Feedback: {result['feedback']}")

    print("\n" + "="*80)
    print(f"Summary: {len(results)} tasks executed, telemetry recorded")
    print("="*80 + "\n")

    return results


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(levelname)s: %(message)s"
    )

    results = example_week6_measurement()

    print("✓ Example complete. Check measurement data:")
    print(f"  ~/.corvin/measurement/$(date +%Y-%m-%d)/")

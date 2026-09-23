"""Stream 1 Phase 3: Learning Loop E2E (Days 1–4 of Week 3).

End-to-end proof of concept: baseline routing → collect operator feedback →
learned weights → improved accuracy (target >5% improvement).

**Phases:**
1. Baseline measurement: Route 100 synthetic tasks with Phase 2 weights
2. Feedback collection: Simulate 50 operator feedback events (50% of tasks)
3. Confidence recomputation: Update weights via Bayesian inference
4. Accuracy remeasurement: Route 100 new tasks with learned weights
5. Verification: Calculate improvement, verify >5% target

**Compliance:**
- GDPR Art. 30/32: All steps audit-logged via LearningEvent
- ADR-0314: Feedback → weight update → improved routing (closed loop)
- Fail-closed: missing ground truth → cannot measure accuracy
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, List, Tuple, Any
from uuid import uuid4

from core.learning.event_store import EventStore
from core.learning.learning_events import LearningEvent, EventType
from core.skills.os_skills.learning_loop_utilities import (
    SyntheticTask,
    TaskComplexity,
    AccuracyMetrics,
    calculate_accuracy,
    estimate_confidence_interval,
    measure_baseline_accuracy,
    measure_learned_accuracy,
    simulate_feedback,
)
from core.skills.os_skills.workflow_optimizer_skill.confidence_calculator import (
    ConfidenceCalculator,
    RoutingWeights,
)
from core.skills.os_skills.workflow_optimizer_skill.feedback_handler import (
    FeedbackHandler,
    RoutingFeedback,
    FeedbackType,
)
# Removed unused import - routing done directly with weights

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearningLoopResult:
    """Result of a complete learning loop run."""
    baseline_accuracy: float
    learned_accuracy: float
    improvement_pct: float
    confidence_interval: Tuple[float, float]
    feedback_count: int
    baseline_weights: Dict[str, float]
    learned_weights: Dict[str, float]
    success: bool  # True if improvement > 5%
    timestamp: str


class WorkflowOptimizerLearningLoop:
    """End-to-end learning loop for Workflow Optimizer (Phase 3).

    Orchestrates: baseline → feedback → recompute → measure improvement.
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str = "_default",
        skill_id: str = "os.workflow_optimizer_l5",
        skill_version: str = "1.0.0",
        confidence_calculator: Optional[ConfidenceCalculator] = None,
    ):
        """Initialize learning loop coordinator.

        Args:
            event_store: EventStore for learning events (audit-first)
            tenant_id: Tenant scope (GDPR)
            skill_id: Skill identifier
            skill_version: Skill version
            confidence_calculator: Injected ConfidenceCalculator (for testing)
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        # Phase 2 components
        self.feedback_handler = FeedbackHandler(
            event_store=event_store,
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
        )
        self.confidence_calculator = confidence_calculator or ConfidenceCalculator(
            event_store=event_store,
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
        )

    async def run_learning_loop(
        self,
        baseline_task_count: int = 100,
        feedback_sample_size: int = 50,
        remeasure_task_count: int = 100,
        feedback_quality: float = 0.95,
    ) -> LearningLoopResult:
        """Run complete learning loop: baseline → feedback → remeasure.

        Args:
            baseline_task_count: Tasks for Phase 1 (baseline measurement)
            feedback_sample_size: Number of feedback events to simulate
            remeasure_task_count: Tasks for Phase 4 (learned measurement)
            feedback_quality: Quality of simulated feedback (0.0-1.0)

        Returns:
            LearningLoopResult with baseline, learned, improvement metrics

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        logger.info(
            f"Learning loop START: baseline={baseline_task_count}, "
            f"feedback={feedback_sample_size}, remeasure={remeasure_task_count}"
        )

        # Phase 1: Generate baseline tasks
        logger.info("Phase 1: Generating baseline tasks...")
        baseline_tasks = SyntheticTask.generate(
            count=baseline_task_count,
            tenant_id=self.tenant_id,
        )

        # Phase 2: Measure baseline accuracy
        logger.info("Phase 2: Measuring baseline accuracy...")
        baseline_accuracy = self._measure_baseline_routing(baseline_tasks)
        logger.info(f"Baseline accuracy: {baseline_accuracy:.2%}")

        # Phase 3: Collect feedback
        logger.info(f"Phase 3: Simulating {feedback_sample_size} feedback events...")
        feedback_list = simulate_feedback(
            baseline_tasks,
            count=feedback_sample_size,
            feedback_quality=feedback_quality,
        )

        # Process each feedback event
        for task, feedback_type in feedback_list:
            feedback_obj = RoutingFeedback(
                task_id=task.task_id,
                routed_model=task.correct_model,  # Assume baseline routed correctly
                task_complexity=task.complexity.value,
                feedback_type=FeedbackType(feedback_type),
                confidence_score=feedback_quality,
                tenant_id=self.tenant_id,
            )
            try:
                self.feedback_handler.process_feedback(feedback_obj)
            except (ValueError, RuntimeError) as e:
                logger.error(f"Feedback processing failed for {task.task_id}: {e}")
                raise

        logger.info(
            f"Phase 3: {feedback_sample_size} feedback events processed and audited"
        )

        # Phase 4: Recompute confidence/weights based on feedback
        logger.info("Phase 4: Recomputing confidence weights...")
        updated_weights_obj, feedback_used = self.confidence_calculator.update_from_feedback()
        learned_weights = updated_weights_obj.weights  # Extract dict from RoutingWeights dataclass
        logger.info(f"Learned weights updated (n={len(learned_weights)} cells, feedback_used={feedback_used})")

        # Phase 5: Generate remeasure tasks
        logger.info("Phase 5: Generating remeasure tasks...")
        remeasure_tasks = SyntheticTask.generate(
            count=remeasure_task_count,
            tenant_id=self.tenant_id,
        )

        # Phase 6: Measure learned accuracy
        logger.info("Phase 6: Measuring learned accuracy...")
        learned_accuracy = self._measure_learned_routing(
            remeasure_tasks,
            learned_weights,
        )
        logger.info(f"Learned accuracy: {learned_accuracy:.2%}")

        # Phase 7: Calculate improvement
        logger.info("Phase 7: Calculating improvement metrics...")
        if baseline_accuracy == 0:
            improvement_pct = 0.0
        else:
            improvement_pct = (learned_accuracy - baseline_accuracy) / baseline_accuracy * 100

        ci = estimate_confidence_interval(
            accuracy=improvement_pct / 100 if improvement_pct > 0 else 0.5,
            sample_size=remeasure_task_count,
        )

        success = improvement_pct > 5.0

        result = LearningLoopResult(
            baseline_accuracy=baseline_accuracy,
            learned_accuracy=learned_accuracy,
            improvement_pct=improvement_pct,
            confidence_interval=ci,
            feedback_count=feedback_sample_size,
            baseline_weights=RoutingWeights().weights,  # Get default weights dict
            learned_weights=learned_weights,
            success=success,
            timestamp=datetime.utcnow().isoformat() + "Z",
        )

        # Emit learning outcome event
        outcome_event = LearningEvent.create(
            event_type=EventType.OUTCOME,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal={
                "loop_type": "full_learning_loop",
                "baseline_accuracy": baseline_accuracy,
                "learned_accuracy": learned_accuracy,
                "improvement_pct": improvement_pct,
                "feedback_count": feedback_sample_size,
                "success": success,
            },
            skill_version=self.skill_version,
            lom="workflow_optimizer_skill.learning_loop_phase3:run_learning_loop:L140",
        )

        try:
            await self.event_store.write_event(outcome_event, self.tenant_id)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write learning outcome: {e}")
            raise

        logger.info(
            f"Learning loop COMPLETE: improvement={improvement_pct:.2f}%, "
            f"success={success}, result={result}"
        )

        return result

    def _measure_baseline_routing(self, tasks: List[SyntheticTask]) -> float:
        """Measure accuracy using Phase 2 baseline weights.

        Args:
            tasks: List of SyntheticTask

        Returns:
            Accuracy [0.0, 1.0]
        """
        baseline_weights = RoutingWeights()  # Load defaults
        return self._route_and_measure(tasks, baseline_weights.weights)

    def _measure_learned_routing(
        self,
        tasks: List[SyntheticTask],
        learned_weights: Dict[str, float],
    ) -> float:
        """Measure accuracy using Phase 3 learned weights.

        Args:
            tasks: List of SyntheticTask
            learned_weights: Updated weights from confidence calculator

        Returns:
            Accuracy [0.0, 1.0]
        """
        return self._route_and_measure(tasks, learned_weights)

    def _route_and_measure(
        self,
        tasks: List[SyntheticTask],
        weights: Dict[str, float],
    ) -> float:
        """Route tasks using provided weights and measure accuracy.

        Algorithm: For each task, pick model with highest P(correct | complexity, model).

        Args:
            tasks: List of SyntheticTask
            weights: Routing weights dict

        Returns:
            Accuracy [0.0, 1.0]
        """
        predictions = []
        ground_truths = []

        for task in tasks:
            complexity = task.complexity.value
            models = ["haiku", "sonnet", "opus"]

            # Compute confidence for each model
            confidences = {}
            for model in models:
                key = f"{complexity}_{model}"
                confidences[model] = weights.get(key, 0.5)

            # Pick model with highest confidence
            predicted_model = max(confidences, key=confidences.get)
            predictions.append(predicted_model)
            ground_truths.append(task.correct_model)

        return calculate_accuracy(predictions, ground_truths)

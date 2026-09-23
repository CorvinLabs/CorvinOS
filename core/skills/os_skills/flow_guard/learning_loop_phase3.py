"""Stream 3 Phase 3: Learning Loop E2E (Days 1–4 of Week 3).

End-to-end proof of concept for Flow Guard: baseline classification → collect operator feedback →
learned thresholds → improved accuracy (target >5% improvement).

**Phases:**
1. Baseline measurement: Classify 100 synthetic data flows with Phase 2 baseline
2. Feedback collection: Simulate 50 operator feedback events (50% of flows)
3. Confidence recomputation: Update policy thresholds via feedback
4. Accuracy remeasurement: Classify 100 new flows with learned thresholds
5. Verification: Calculate improvement, verify >5% target

**Compliance:**
- GDPR Art. 30/32: All steps audit-logged via LearningEvent
- ADR-0314: Feedback → threshold update → improved classification (closed loop)
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
    SyntheticDataFlow,
    DataClass,
    AccuracyMetrics,
    calculate_accuracy,
    estimate_confidence_interval,
    simulate_feedback,
)
from core.skills.os_skills.flow_guard.feedback_handler import (
    PolicyFeedbackHandler,
    PolicyFeedback,
    PolicyFeedbackType,
)
from core.skills.os_skills.flow_guard.policy_confidence_scorer import (
    PolicyConfidenceScorer,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LearningLoopResult:
    """Result of a complete learning loop run."""
    baseline_accuracy: float
    learned_accuracy: float
    improvement_pct: float
    confidence_interval: Tuple[float, float]
    feedback_count: int
    baseline_thresholds: Dict[str, float]
    learned_thresholds: Dict[str, float]
    success: bool  # True if improvement > 5%
    timestamp: str


class FlowGuardLearningLoop:
    """End-to-end learning loop for Flow Guard (Phase 3).

    Orchestrates: baseline → feedback → recompute → measure improvement.
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str = "_default",
        skill_id: str = "os.flow_guard",
        skill_version: str = "1.0.0",
        confidence_scorer: Optional[PolicyConfidenceScorer] = None,
    ):
        """Initialize learning loop coordinator.

        Args:
            event_store: EventStore for learning events (audit-first)
            tenant_id: Tenant scope (GDPR)
            skill_id: Skill identifier
            skill_version: Skill version
            confidence_scorer: Injected PolicyConfidenceScorer (for testing)
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        # Phase 2 components
        self.feedback_handler = PolicyFeedbackHandler(
            event_store=event_store,
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
        )
        self.confidence_scorer = confidence_scorer or PolicyConfidenceScorer(
            event_store=event_store,
            tenant_id=tenant_id,
            skill_id=skill_id,
            skill_version=skill_version,
        )

    def run_learning_loop(
        self,
        baseline_flow_count: int = 100,
        feedback_sample_size: int = 50,
        remeasure_flow_count: int = 100,
        feedback_quality: float = 0.95,
    ) -> LearningLoopResult:
        """Run complete learning loop: baseline → feedback → remeasure.

        Args:
            baseline_flow_count: Flows for Phase 1 (baseline measurement)
            feedback_sample_size: Number of feedback events to simulate
            remeasure_flow_count: Flows for Phase 4 (learned measurement)
            feedback_quality: Quality of simulated feedback (0.0-1.0)

        Returns:
            LearningLoopResult with baseline, learned, improvement metrics

        Raises:
            RuntimeError: If audit chain write fails (fail-closed)
        """
        logger.info(
            f"Learning loop START: baseline={baseline_flow_count}, "
            f"feedback={feedback_sample_size}, remeasure={remeasure_flow_count}"
        )

        # Phase 1: Generate baseline flows
        logger.info("Phase 1: Generating baseline flows...")
        baseline_flows = SyntheticDataFlow.generate(
            count=baseline_flow_count,
            tenant_id=self.tenant_id,
        )

        # Phase 2: Measure baseline accuracy
        logger.info("Phase 2: Measuring baseline accuracy...")
        baseline_accuracy = self._measure_baseline_classification(baseline_flows)
        logger.info(f"Baseline accuracy: {baseline_accuracy:.2%}")

        # Phase 3: Collect feedback
        logger.info(f"Phase 3: Simulating {feedback_sample_size} feedback events...")
        feedback_list = simulate_feedback(
            baseline_flows,
            count=feedback_sample_size,
            feedback_quality=feedback_quality,
        )

        # Process each feedback event
        for flow, feedback_type in feedback_list:
            feedback_obj = PolicyFeedback(
                flow_id=flow.flow_id,
                data_class=flow.data_class.value,
                engine=flow.engine,
                destination=flow.destination,
                policy_decision=flow.correct_decision,
                feedback_type=PolicyFeedbackType(feedback_type),
                confidence_score=feedback_quality,
                tenant_id=self.tenant_id,
            )
            try:
                self.feedback_handler.process_feedback(feedback_obj)
            except (ValueError, RuntimeError) as e:
                logger.error(f"Feedback processing failed for {flow.flow_id}: {e}")
                raise

        logger.info(
            f"Phase 3: {feedback_sample_size} feedback events processed and audited"
        )

        # Phase 4: Recompute thresholds based on feedback
        logger.info("Phase 4: Recomputing policy thresholds...")
        learned_thresholds = self.confidence_scorer.recompute_thresholds_from_feedback()
        logger.info(f"Policy thresholds updated (n={len(learned_thresholds)} data classes)")

        # Phase 5: Generate remeasure flows
        logger.info("Phase 5: Generating remeasure flows...")
        remeasure_flows = SyntheticDataFlow.generate(
            count=remeasure_flow_count,
            tenant_id=self.tenant_id,
        )

        # Phase 6: Measure learned accuracy
        logger.info("Phase 6: Measuring learned accuracy...")
        learned_accuracy = self._measure_learned_classification(
            remeasure_flows,
            learned_thresholds,
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
            sample_size=remeasure_flow_count,
        )

        success = improvement_pct > 5.0

        result = LearningLoopResult(
            baseline_accuracy=baseline_accuracy,
            learned_accuracy=learned_accuracy,
            improvement_pct=improvement_pct,
            confidence_interval=ci,
            feedback_count=feedback_sample_size,
            baseline_thresholds=self._get_baseline_thresholds(),
            learned_thresholds=learned_thresholds,
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
            lom="flow_guard.learning_loop_phase3:run_learning_loop:L140",
        )

        try:
            self.event_store.write_event(outcome_event)
        except (RuntimeError, IOError) as e:
            logger.error(f"Failed to write learning outcome: {e}")
            raise

        logger.info(
            f"Learning loop COMPLETE: improvement={improvement_pct:.2f}%, "
            f"success={success}, result={result}"
        )

        return result

    def _measure_baseline_classification(self, flows: List[SyntheticDataFlow]) -> float:
        """Measure accuracy using Phase 2 baseline thresholds.

        Args:
            flows: List of SyntheticDataFlow

        Returns:
            Accuracy [0.0, 1.0]
        """
        baseline_thresholds = self._get_baseline_thresholds()
        return self._classify_and_measure(flows, baseline_thresholds)

    def _measure_learned_classification(
        self,
        flows: List[SyntheticDataFlow],
        learned_thresholds: Dict[str, float],
    ) -> float:
        """Measure accuracy using Phase 3 learned thresholds.

        Args:
            flows: List of SyntheticDataFlow
            learned_thresholds: Updated thresholds from confidence scorer

        Returns:
            Accuracy [0.0, 1.0]
        """
        return self._classify_and_measure(flows, learned_thresholds)

    def _classify_and_measure(
        self,
        flows: List[SyntheticDataFlow],
        thresholds: Dict[str, float],
    ) -> float:
        """Classify flows using provided thresholds and measure accuracy.

        Algorithm: For each flow, compute entropy + schema + regex signals,
        compare against threshold for that data class.

        Args:
            flows: List of SyntheticDataFlow
            thresholds: Policy thresholds dict {data_class: threshold}

        Returns:
            Accuracy [0.0, 1.0]
        """
        predictions = []
        ground_truths = []

        for flow in flows:
            data_class = flow.data_class.value

            # Compute risk score from features
            # Simple heuristic: entropy + regex detections - schema match
            risk_score = (
                flow.features.get("entropy", 0) / 8.0  # Normalize entropy
                + (flow.features.get("regex_detections", 0) / 10.0)  # Normalize regex hits
                - flow.features.get("schema_match_score", 0)  # Subtract schema confidence
            )
            risk_score = max(0.0, min(1.0, risk_score))  # Clamp to [0, 1]

            # Get threshold for this data class
            threshold = thresholds.get(data_class, 0.5)

            # Classify: allow if risk < threshold, deny if risk >= threshold
            predicted_decision = "allow" if risk_score < threshold else "deny"
            predictions.append(predicted_decision)
            ground_truths.append(flow.correct_decision)

        return calculate_accuracy(predictions, ground_truths)

    def _get_baseline_thresholds(self) -> Dict[str, float]:
        """Get baseline (Phase 2) thresholds for all data classes.

        Returns:
            Dict mapping data_class to threshold value
        """
        # Baseline thresholds (Phase 2)
        return {
            "public": 0.8,      # High risk tolerance for public data
            "internal": 0.6,    # Medium risk tolerance
            "financial": 0.3,   # Low risk tolerance
            "health": 0.2,      # Very low risk tolerance
            "pii": 0.1,         # Minimum risk tolerance
        }

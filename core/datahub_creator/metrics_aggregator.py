"""MetricsAggregator — Collects skill metrics from Track B (Learning Loop).

Bridges DataHub Creator project view with Learning Loop infrastructure:
- Reads LearningEvent stream from track B's EventStore
- Aggregates skill execution + feedback + confidence scores
- Detects convergence (when optimizer stabilizes)
- Generates recommendations based on metrics
"""

from typing import Dict, List, Optional, Any
from datetime import datetime, timedelta
import logging
from collections import defaultdict

from ..learning.learning_events import LearningEvent, EventType
from .models import (
    SkillMetricsSnapshot,
    ProjectMetrics,
    ProjectModel,
)

logger = logging.getLogger(__name__)


class MetricsAggregator:
    """Aggregates learning loop metrics for a DataHub project."""

    def __init__(self, project: ProjectModel, event_store=None):
        """
        Args:
            project: ProjectModel instance
            event_store: Learning EventStore (injected; allows mocking in tests)
        """
        self.project = project
        self.event_store = event_store  # Will be injected from Track B
        self._skill_executions: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._feedback_signals: Dict[str, List[Dict[str, Any]]] = defaultdict(list)
        self._confidence_scores: Dict[str, float] = {}

    async def aggregate_metrics(self) -> ProjectMetrics:
        """Fetch all learning events for this project's skills and aggregate."""
        if not self.event_store:
            logger.warning("MetricsAggregator: event_store not initialized")
            return ProjectMetrics(project_id=self.project.project_id)

        # Query learning store for events related to this project's skills
        events = await self._fetch_project_events()

        # Process events
        skill_snapshots = []
        for skill_id in self.project.selected_skills:
            snapshot = await self._compute_skill_snapshot(skill_id, events)
            if snapshot:
                skill_snapshots.append(snapshot)

        # Aggregate project-level metrics
        metrics = ProjectMetrics(
            project_id=self.project.project_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            skill_snapshots=skill_snapshots,
        )

        # Compute aggregates
        metrics.avg_confidence = self._compute_avg_confidence(skill_snapshots)
        metrics.skills_improved = self._count_improved_skills(skill_snapshots)
        metrics.confidence_trend = self._compute_trend(skill_snapshots)
        metrics.convergence_status = self._detect_convergence(skill_snapshots)
        metrics.phases_completed = self.project.get_phases_completed_count()

        return metrics

    async def _fetch_project_events(self) -> List[LearningEvent]:
        """Fetch learning events for skills in this project."""
        if not self.event_store:
            return []

        events = []
        for skill_id in self.project.selected_skills:
            # Query event store for this skill (tenant-scoped)
            skill_events = await self.event_store.query_by_skill(
                skill_id=skill_id,
                tenant_id=self.project.tenant_id,
                event_types=[
                    EventType.SKILL_EXECUTED,
                    EventType.FEEDBACK,
                    EventType.OUTCOME,
                    EventType.CONFIDENCE,
                    EventType.CONFIG_UPDATED,
                ],
            )
            events.extend(skill_events)

        return sorted(events, key=lambda e: e.timestamp)

    async def _compute_skill_snapshot(
        self,
        skill_id: str,
        all_events: List[LearningEvent],
    ) -> Optional[SkillMetricsSnapshot]:
        """Compute metrics snapshot for a single skill."""

        # Filter events for this skill
        skill_events = [e for e in all_events if e.skill_id == skill_id]
        if not skill_events:
            return None

        # Count executions (SKILL_EXECUTED events)
        executions = [e for e in skill_events if e.event_type == EventType.SKILL_EXECUTED]
        if not executions:
            return None

        # Count successful outcomes (OUTCOME events with signal.success == true)
        outcomes = [e for e in skill_events if e.event_type == EventType.OUTCOME]
        successes = sum(
            1 for e in outcomes
            if e.signal and e.signal.get("success") is True
        )
        success_rate = successes / len(executions) if executions else 0.0

        # Compute latency percentiles (p50, p95, p99)
        latencies = []
        for e in executions:
            if e.signal and "latency_ms" in e.signal:
                latencies.append(e.signal["latency_ms"])

        latencies.sort()
        p50 = latencies[len(latencies) // 2] if latencies else 0.0
        p95 = latencies[int(len(latencies) * 0.95)] if len(latencies) > 1 else 0.0
        p99 = latencies[int(len(latencies) * 0.99)] if len(latencies) > 1 else 0.0

        # Count feedback events
        feedback_events = [e for e in skill_events if e.event_type == EventType.FEEDBACK]
        feedback_count = len(feedback_events)

        # Get latest confidence score
        confidence_events = [e for e in skill_events if e.event_type == EventType.CONFIDENCE]
        confidence_score = 0.5  # Default
        if confidence_events:
            latest_conf = confidence_events[-1]
            if latest_conf.signal:
                confidence_score = latest_conf.signal.get("score", 0.5)

        # Compute convergence rate (confidence change over time window)
        convergence_rate = self._compute_convergence_rate(confidence_events)

        # Determine recommendation strength based on confidence
        if confidence_score > 0.8:
            recommendation_strength = "high"
        elif confidence_score > 0.6:
            recommendation_strength = "medium"
        else:
            recommendation_strength = "low"

        return SkillMetricsSnapshot(
            skill_id=skill_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            success_rate=success_rate,
            latency_p50_ms=p50,
            latency_p95_ms=p95,
            latency_p99_ms=p99,
            feedback_count=feedback_count,
            confidence_score=confidence_score,
            convergence_rate=convergence_rate,
            recommendation_strength=recommendation_strength,
        )

    def _compute_convergence_rate(self, confidence_events: List[LearningEvent]) -> float:
        """Compute how quickly confidence is stabilizing (convergence rate)."""
        if len(confidence_events) < 2:
            return 0.0

        # Get last two confidence events
        recent = confidence_events[-2:]
        scores = [
            e.signal.get("score", 0.5) if e.signal else 0.5
            for e in recent
        ]

        # Rate = absolute change between last two events
        # Close to 0 = converging; close to 1 = diverging
        change = abs(scores[1] - scores[0])
        convergence_rate = 1.0 - min(change, 1.0)  # Invert: 0→1 means converging

        return convergence_rate

    def _compute_avg_confidence(self, snapshots: List[SkillMetricsSnapshot]) -> float:
        """Average confidence across all skills."""
        if not snapshots:
            return 0.0
        return sum(s.confidence_score for s in snapshots) / len(snapshots)

    def _count_improved_skills(self, snapshots: List[SkillMetricsSnapshot]) -> int:
        """Count skills with confidence > 0.7 (improvement threshold)."""
        return sum(1 for s in snapshots if s.confidence_score > 0.7)

    def _compute_trend(
        self,
        snapshots: List[SkillMetricsSnapshot],
    ) -> List[tuple[str, float]]:
        """Compute confidence trend over time (simplified for MVP)."""
        # TODO: query metrics_history for historical trend
        # For now, return single point
        avg_confidence = self._compute_avg_confidence(snapshots)
        return [(datetime.utcnow().isoformat() + "Z", avg_confidence)]

    def _detect_convergence(self, snapshots: List[SkillMetricsSnapshot]) -> str:
        """Detect if learning has converged."""
        if not snapshots:
            return "in_progress"

        # Convergence detected if:
        # - avg confidence > 0.8 AND
        # - avg convergence_rate > 0.8 (stable, not oscillating) AND
        # - all skills have sufficient feedback

        avg_conf = self._compute_avg_confidence(snapshots)
        avg_convergence = sum(s.convergence_rate for s in snapshots) / len(snapshots)
        all_have_feedback = all(s.feedback_count >= 5 for s in snapshots)

        if avg_conf > 0.8 and avg_convergence > 0.8 and all_have_feedback:
            return "complete"
        elif avg_conf > 0.6:
            return "in_progress"
        else:
            return "stalled"

    async def record_feedback(
        self,
        skill_id: str,
        feedback_type: str,
        signal: Dict[str, Any],
    ) -> None:
        """Record user feedback (emits to Track B event store)."""
        if not self.event_store:
            logger.warning("MetricsAggregator: cannot record feedback, event_store not initialized")
            return

        # Create feedback event
        event = LearningEvent.create(
            event_type=EventType.FEEDBACK,
            skill_id=skill_id,
            tenant_id=self.project.tenant_id,
            signal={
                "feedback_type": feedback_type,
                **signal,
            },
            lom="datahub_creator.MetricsAggregator.record_feedback",
        )

        # Write to event store (audit-first, hash-chained)
        await self.event_store.write_event(event)

        logger.info(f"Recorded feedback for {skill_id}: {feedback_type}")

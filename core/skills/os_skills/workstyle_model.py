"""Workstyle Inference & Preference Learning (ADR-0550).

Phase 3 of CONCEPT-0029. Infers user preferences per task type, preventing
overfitting and cross-contamination. User preferences stratified by task type:
what works for features is wrong for refactors.

Core constraint (CONCEPT-0029 Constraint 1): Preferences are ALWAYS keyed by
task_type. No global preferences conflation. This prevents Attack 3.

Audit trail:
  - workstyle_observation_recorded (task completed, classified)
  - user_preference_inferred (N >= 5 observations → infer)
  - preference_confidence_updated (recency decay)
  - preference_user_confirmed (user confirms / overrides)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.tenants.validation import validate_tenant_id


__all__ = [
    "TaskTypePreferences",
    "WorkstyleProfile",
    "PreferenceInferencer",
]


TASK_TYPES = frozenset({
    "feature",
    "refactor",
    "bugfix",
    "security",
    "learning",
    "documentation",
    "infrastructure",
    "performance",
    "investigation",
})


@dataclass(frozen=True)
class TaskTypePreferences:
    """User's preferences for ONE task type (ADR-0550)."""

    task_type: str
    tenant_id: str

    # Which skills does user prefer for this task type?
    # Dict: skill_id → preference score [0.0–1.0]
    preferred_skills: dict[str, float] = field(default_factory=dict)
    disliked_skills: dict[str, float] = field(default_factory=dict)

    # Execution style dimensions
    prefers_fast_iteration: float = 0.5  # [0.0–1.0]
    prefers_detailed_explanations: float = 0.5
    accepts_experimental_tools: float = 0.5
    prefers_depth_first: float = 0.5  # vs. breadth_first

    # Learning style
    learns_by_experimentation: float = 0.5
    learns_by_instruction: float = 0.5

    # Attention model
    attention_span_minutes: Optional[int] = None
    breaks_frequency_minutes: Optional[int] = None

    # Confidence
    confidence_score: float = 0.0  # [0.0–1.0] How sure are we?
    observation_count: int = 0  # How many tasks of this type observed?

    # Timestamps
    first_observed: Optional[datetime] = None
    last_updated: Optional[datetime] = None
    user_confirmed: bool = False

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if self.task_type not in TASK_TYPES:
            raise ValueError(f"task_type must be one of {sorted(TASK_TYPES)}, got {self.task_type}")
        if not 0.0 <= self.confidence_score <= 1.0:
            raise ValueError(f"confidence_score must be in [0.0, 1.0], got {self.confidence_score}")


@dataclass
class WorkstyleProfile:
    """Complete user model (per-user, per-tenant). Mutable during learning."""

    user_id: str
    tenant_id: str

    # Per-task-type preferences (KEY CONSTRAINT: stratified by task_type)
    preferences_by_task_type: dict[str, TaskTypePreferences] = field(default_factory=dict)

    # Fallback when task type is new
    global_preferences: Optional[TaskTypePreferences] = None

    # When was this profile last updated?
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    last_updated: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self) -> None:
        validate_tenant_id(self.tenant_id)
        if not self.user_id:
            raise ValueError("user_id required")


class PreferenceInferencer:
    """Infer user preferences from task outcomes (ADR-0550, Phase 3)."""

    @staticmethod
    def infer_preferences(
        task_type: str,
        recent_observations: list[dict],  # [{"skill_seq": [...], "outcome": "success", ...}, ...]
    ) -> TaskTypePreferences:
        """Infer preferences for ONE task type from its observations.

        Args:
            task_type: which task type (e.g., "feature")
            recent_observations: last 5–10 tasks of this type

        Returns:
            TaskTypePreferences with inferred values
        """

        if not recent_observations:
            # No data → return neutral preferences
            return TaskTypePreferences(task_type=task_type, tenant_id="unknown")

        # Count skill frequencies in successful tasks
        successful = [o for o in recent_observations if o.get("outcome") == "success"]
        if not successful:
            # No successes → return neutral
            return TaskTypePreferences(task_type=task_type, tenant_id="unknown")

        # Extract skills from successful tasks
        skill_counts = {}
        for task in successful:
            for skill in task.get("skill_sequence", []):
                skill_counts[skill] = skill_counts.get(skill, 0) + 1

        # Normalize to preference scores
        preferred_skills = {
            skill: count / len(successful)
            for skill, count in skill_counts.items()
        }

        # Compute confidence (simplified)
        confidence = PreferenceInferencer._compute_confidence(
            observation_count=len(successful),
            consistency=PreferenceInferencer._compute_consistency(recent_observations),
            recency=PreferenceInferencer._recency_boost(recent_observations)
        )

        return TaskTypePreferences(
            task_type=task_type,
            tenant_id="unknown",  # Caller will set this
            preferred_skills=preferred_skills,
            confidence_score=confidence,
            observation_count=len(successful),
            first_observed=datetime.now(timezone.utc),
            last_updated=datetime.now(timezone.utc),
        )

    @staticmethod
    def _compute_confidence(
        observation_count: int,
        consistency: float,
        recency: float,
    ) -> float:
        """Confidence = f(N, consistency, recency).

        N < 2: 0.2
        2 <= N < 5: 0.4–0.6
        5 <= N < 10: 0.6–0.8
        N >= 10: 0.85–0.95
        """
        if observation_count < 2:
            base = 0.2
        elif observation_count < 5:
            base = 0.4 + (observation_count - 2) * 0.05
        elif observation_count < 10:
            base = 0.65 + (observation_count - 5) * 0.03
        else:
            base = min(0.85 + (observation_count - 10) * 0.01, 0.95)

        # Adjust for consistency and recency
        consistency_boost = 0.5 + (consistency * 0.45)  # [0.5–0.95]
        final = base * consistency_boost * recency

        return min(final, 0.95)  # Cap at 0.95

    @staticmethod
    def _compute_consistency(observations: list[dict]) -> float:
        """How consistent are outcomes? (0.0 = all failures, 1.0 = all successes)."""
        if not observations:
            return 0.5
        successes = sum(1 for o in observations if o.get("outcome") == "success")
        return successes / len(observations)

    @staticmethod
    def _recency_boost(observations: list[dict]) -> float:
        """Boost for recent observations, decay for stale."""
        if not observations:
            return 0.5

        # Assume observations are sorted by timestamp (newest last)
        last_obs = observations[-1]
        last_time = last_obs.get("timestamp")

        if not last_time:
            return 1.0  # No timestamp info

        if isinstance(last_time, str):
            from datetime import datetime
            try:
                last_time = datetime.fromisoformat(last_time)
            except:
                return 1.0

        days_ago = (datetime.now(timezone.utc) - last_time).days

        if days_ago <= 7:
            return 1.0
        elif days_ago <= 30:
            return 1.0 - (days_ago / 30) * 0.05
        elif days_ago <= 60:
            return 0.95 - ((days_ago - 30) / 30) * 0.15
        else:
            return max(0.60, 0.80 - (days_ago / 365) * 0.1)


class ContextualRouter:
    """Route recommendations based on user's task-type preferences."""

    @staticmethod
    def recommend_workflow(
        task_type: str,
        profile: WorkstyleProfile,
    ) -> list[str]:
        """Recommend skill sequence for task, based on learned preferences.

        Args:
            task_type: which task type
            profile: user's WorkstyleProfile

        Returns:
            List of recommended skills in order
        """

        # Get preferences for this task type
        prefs = profile.preferences_by_task_type.get(task_type)

        if not prefs or prefs.observation_count < 5:
            # Not enough data yet → use global fallback
            prefs = profile.global_preferences

        if not prefs:
            # No preferences at all → return empty (caller will use defaults)
            return []

        # Build recommendation from preferred_skills (sorted by preference)
        recommendation = sorted(
            prefs.preferred_skills.items(),
            key=lambda x: x[1],
            reverse=True
        )

        skills = [skill for skill, _ in recommendation[:5]]  # Top 5

        return skills

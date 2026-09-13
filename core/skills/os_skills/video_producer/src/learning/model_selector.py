"""Model Selection Optimizer using Multi-Armed Bandit (Phase 4b).

Learns which LLM model (GPT-4, Claude-Opus, Claude-Sonnet) produces
best video narration for different video durations using epsilon-greedy algorithm.

Algorithm: Epsilon-Greedy Multi-Armed Bandit
- Epsilon (exploration): 10% of time, try random model
- 1-Epsilon (exploitation): 90% of time, use best model
- Per-duration grouping: 1-min, 5-min, 15-min duration categories
- Confidence threshold: Only switch if new model confidence > current + 0.15

Load-Bearing Constraints:
1. Exploration is UNIFORMLY RANDOM (no biased selection)
2. Model switch requires minimum sample (5+) per duration
3. Win rate computation is DETERMINISTIC (reproducible)
4. Config changes are LOGGED to audit trail
"""

from __future__ import annotations

import json
import logging
import random
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Optional, Literal
from enum import Enum

logger = logging.getLogger(__name__)

Model = Literal["gpt-4", "claude-opus", "claude-sonnet"]
Duration = Literal["1min", "5min", "15min"]


def categorize_duration(seconds: float) -> Duration:
    """Categorize video duration into bucket."""
    if seconds <= 120:
        return "1min"
    elif seconds <= 360:
        return "5min"
    else:
        return "15min"


@dataclass(frozen=True)
class ModelPerformance:
    """Performance metrics for a model on a duration."""
    model: Model
    duration: Duration
    win_count: int = 0  # Number of times selected and got high rating
    total_attempts: int = 0  # Total times used
    average_rating: float = 0.0  # Average rating received
    last_used: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")

    @property
    def win_rate(self) -> float:
        """Compute win rate (wins / attempts)."""
        if self.total_attempts == 0:
            return 0.5  # Neutral default
        return self.win_count / self.total_attempts


@dataclass
class ModelSelectorState:
    """State of model selection optimizer."""
    skill_id: str = "os.model_selector"
    tenant_id: str = "_default"

    # Per-duration model performance
    performances: dict[str, dict[Model, ModelPerformance]] = field(default_factory=dict)

    # Current selection per duration
    selected_models: dict[Duration, Model] = field(default_factory=dict)

    # Exploration/exploitation stats
    total_decisions: int = 0
    exploration_count: int = 0

    # Metadata
    created_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")


class ModelSelector:
    """Selects best LLM model for video narration using bandit algorithm."""

    # Epsilon for exploration (10% random)
    EPSILON = 0.1

    # Confidence threshold for model switching
    CONFIDENCE_THRESHOLD = 0.15  # Must be 15% better to switch

    # Minimum samples before considering for selection
    MIN_SAMPLES = 5

    # Models to evaluate
    MODELS: list[Model] = ["gpt-4", "claude-opus", "claude-sonnet"]

    # Durations
    DURATIONS: list[Duration] = ["1min", "5min", "15min"]

    def __init__(
        self,
        workdir: str | Path,
        tenant_id: str = "_default",
    ):
        """Initialize model selector.

        Args:
            workdir: Directory for state storage
            tenant_id: Tenant scope
        """
        self.workdir = Path(workdir)
        self.workdir.mkdir(parents=True, exist_ok=True)
        self.tenant_id = tenant_id
        self.state_file = self.workdir / "model_selector_state.json"
        self.state = self._load_state()

    def _load_state(self) -> ModelSelectorState:
        """Load or initialize model selector state."""
        if self.state_file.exists():
            try:
                data = json.loads(self.state_file.read_text())
                state = ModelSelectorState(
                    skill_id=data.get("skill_id", "os.model_selector"),
                    tenant_id=data.get("tenant_id", self.tenant_id),
                    total_decisions=data.get("total_decisions", 0),
                    exploration_count=data.get("exploration_count", 0),
                )

                # Reconstruct performances
                for duration_str, models_dict in data.get("performances", {}).items():
                    state.performances[duration_str] = {}
                    for model, perf_data in models_dict.items():
                        state.performances[duration_str][model] = ModelPerformance(**perf_data)

                # Reconstruct selected models
                state.selected_models = data.get("selected_models", {})

                return state
            except Exception as e:
                logger.warning(f"Failed to load model selector state: {e}, using defaults")

        # Initialize default state
        state = ModelSelectorState(tenant_id=self.tenant_id)
        for duration in self.DURATIONS:
            state.performances[duration] = {}
            for model in self.MODELS:
                state.performances[duration][model] = ModelPerformance(
                    model=model,
                    duration=duration,
                )
            # Default to first model for each duration
            state.selected_models[duration] = self.MODELS[0]

        return state

    def _save_state(self) -> None:
        """Persist state to disk."""
        data = {
            "skill_id": self.state.skill_id,
            "tenant_id": self.state.tenant_id,
            "total_decisions": self.state.total_decisions,
            "exploration_count": self.state.exploration_count,
            "created_at": self.state.created_at,
            "updated_at": self.state.updated_at,
            "performances": {},
            "selected_models": self.state.selected_models,
        }

        for duration, models_dict in self.state.performances.items():
            data["performances"][duration] = {}
            for model, perf in models_dict.items():
                data["performances"][duration][model] = {
                    "model": perf.model,
                    "duration": perf.duration,
                    "win_count": perf.win_count,
                    "total_attempts": perf.total_attempts,
                    "average_rating": perf.average_rating,
                    "last_used": perf.last_used,
                }

        self.state_file.write_text(json.dumps(data, indent=2))

    def select_model(self, video_duration_seconds: float) -> Model:
        """Select model for video using epsilon-greedy algorithm.

        Args:
            video_duration_seconds: Duration in seconds

        Returns:
            Selected model name
        """
        duration = categorize_duration(video_duration_seconds)

        # Epsilon-greedy decision
        if random.random() < self.EPSILON:
            # Exploration: random model
            selected = random.choice(self.MODELS)
            self.state.exploration_count += 1
            logger.info(f"Exploration: selected {selected} for {duration}")
        else:
            # Exploitation: best model
            selected = self.state.selected_models.get(duration, self.MODELS[0])
            logger.info(f"Exploitation: selected {selected} for {duration}")

        self.state.total_decisions += 1
        self.state.updated_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()

        return selected

    def report_result(
        self,
        model: Model,
        video_duration_seconds: float,
        quality_rating: float,  # 0.0-1.0
    ) -> Optional[Model]:
        """Report result and update model performance.

        Args:
            model: Model that was used
            video_duration_seconds: Duration
            quality_rating: Quality rating 0.0-1.0

        Returns:
            New selected model if switched, None if no change
        """
        duration = categorize_duration(video_duration_seconds)

        if duration not in self.state.performances:
            self.state.performances[duration] = {}

        if model not in self.state.performances[duration]:
            self.state.performances[duration][model] = ModelPerformance(
                model=model,
                duration=duration,
            )

        old_perf = self.state.performances[duration][model]

        # Update performance (rolling average)
        new_total = old_perf.total_attempts + 1
        new_avg = (
            old_perf.average_rating * old_perf.total_attempts + quality_rating
        ) / new_total

        # Count as "win" if rating >= 0.75
        new_wins = old_perf.win_count + (1 if quality_rating >= 0.75 else 0)

        self.state.performances[duration][model] = ModelPerformance(
            model=model,
            duration=duration,
            win_count=new_wins,
            total_attempts=new_total,
            average_rating=new_avg,
        )

        # Check if we should switch models
        new_selected = self._select_best_model(duration)
        old_selected = self.state.selected_models.get(duration)

        changed = False
        if old_selected != new_selected:
            self.state.selected_models[duration] = new_selected
            changed = True
            logger.info(
                f"Model switch for {duration}: {old_selected} → {new_selected} "
                f"(confidence: {new_selected} has {self.state.performances[duration][new_selected].average_rating:.2f})"
            )

        self.state.updated_at = datetime.utcnow().isoformat() + "Z"
        self._save_state()

        return new_selected if changed else None

    def _select_best_model(self, duration: Duration) -> Model:
        """Select best model for duration based on win rate."""
        models_data = self.state.performances.get(duration, {})

        # Filter models with minimum samples
        candidates = [
            (model, perf)
            for model, perf in models_data.items()
            if perf.total_attempts >= self.MIN_SAMPLES
        ]

        if not candidates:
            # Not enough data, return current
            return self.state.selected_models.get(duration, self.MODELS[0])

        # Sort by win rate
        best_model, best_perf = max(candidates, key=lambda x: x[1].win_rate)

        # Check confidence threshold
        current_selected = self.state.selected_models.get(duration, self.MODELS[0])
        current_perf = models_data.get(current_selected, ModelPerformance(
            model=current_selected,
            duration=duration,
        ))

        confidence_delta = best_perf.win_rate - current_perf.win_rate

        if confidence_delta > self.CONFIDENCE_THRESHOLD:
            return best_model
        else:
            return current_selected

    def get_model_stats(self) -> dict[str, Any]:
        """Get model selection statistics for dashboard."""
        stats = {
            "total_decisions": self.state.total_decisions,
            "exploration_count": self.state.exploration_count,
            "exploration_rate": (
                self.state.exploration_count / max(self.state.total_decisions, 1)
            ),
            "by_duration": {},
        }

        for duration in self.DURATIONS:
            models_data = self.state.performances.get(duration, {})
            stats["by_duration"][duration] = {
                "selected_model": self.state.selected_models.get(duration),
                "models": {
                    model: {
                        "win_rate": perf.win_rate,
                        "average_rating": perf.average_rating,
                        "attempts": perf.total_attempts,
                        "wins": perf.win_count,
                    }
                    for model, perf in models_data.items()
                },
            }

        return stats

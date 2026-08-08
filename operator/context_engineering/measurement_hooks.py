"""
Week 6 Measurement Hooks

Injected into task_engine.py to collect telemetry for ADR-0270–0273 measurement tracks:
- Uncertainty Quantification (ADR-0270)
- Outcome Feedback Loop (ADR-0271)
- User Preferences (ADR-0272)
- Attention Budget (ADR-0273)

Usage:
  from measurement_hooks import MeasurementCollector
  collector = MeasurementCollector()
  collector.record_prediction(context_id, confidence, actual_outcome)
  collector.record_feedback(context_id, feedback_impact)
  collector.record_user_choice(user_id, decision_style, task_type)
  collector.record_budget_allocation(task_id, budget_level, complexity)
"""

import json
import logging
import os
import threading
from pathlib import Path
from datetime import datetime
from typing import Dict, Optional, Literal
from dataclasses import asdict, dataclass

logger = logging.getLogger(__name__)


@dataclass
class PredictionRecord:
    """ADR-0270: Confidence prediction vs. actual outcome."""
    timestamp: str
    context_id: str
    confidence_pred: float  # 0.0–1.0
    outcome_actual: float  # 0.0–1.0 (success)
    context_type: str  # "adr", "skill", "memory"
    task_id: str
    user_id: str


@dataclass
class FeedbackRecord:
    """ADR-0271: User feedback for Bayesian updates."""
    timestamp: str
    context_id: str
    feedback_impact: Literal["helpful", "harmful", "neutral"]
    score_before: float
    score_after: float
    learning_rate_applied: float  # 0.05 standard
    decay_weight: float  # >90d uses lower weight
    task_id: str
    user_id: str


@dataclass
class UserChoiceRecord:
    """ADR-0272: User style inference from decisions."""
    timestamp: str
    user_id: str
    decision_style: Literal["pragmatic", "rigorous"]
    task_type: str  # "ml", "devops", "refactor", etc.
    complexity: float  # 1.0–10.0
    time_available: float  # minutes
    choice_made: str  # "quick_fix" | "thorough_analysis" | etc.


@dataclass
class BudgetRecord:
    """ADR-0273: Attention budget allocation vs. complexity."""
    timestamp: str
    task_id: str
    user_id: str
    budget_allocated: Literal["critical", "important", "nice_to_have"]
    complexity_est: float  # 1.0–10.0
    tokens_used: int
    match_score: float  # 0.0–1.0 (does budget match complexity?)


class MeasurementCollector:
    """
    Collects telemetry for Week 6 measurement tracks.

    Safe to call even if measurement is disabled (no-ops gracefully).
    """

    def __init__(
        self,
        queue_dir: Optional[Path] = None,
        enabled: bool = True,
    ):
        """
        Args:
            queue_dir: Directory to write measurement records (default: ~/.corvin/.measurement)
            enabled: If False, all methods are no-ops

        Raises:
            ValueError: If queue_dir is invalid (exists but is not a directory)
            PermissionError: If queue_dir cannot be created due to permissions
        """
        self.enabled = enabled
        if not enabled:
            logger.debug("MeasurementCollector initialized (disabled)")
            return

        if queue_dir is None:
            home = Path.home()
            queue_dir = home / ".corvin" / "measurement" / datetime.utcnow().strftime("%Y-%m-%d")
        else:
            # MEDIUM FIX M4: Validate queue_dir parameter
            queue_dir = Path(queue_dir) if not isinstance(queue_dir, Path) else queue_dir

            if queue_dir.exists() and not queue_dir.is_dir():
                raise ValueError(f"queue_dir exists but is not a directory: {queue_dir}")

        self.queue_dir = queue_dir
        try:
            self.queue_dir.mkdir(parents=True, exist_ok=True)

            # MEDIUM FIX M4 + CODE REVIEW I6-5: Verify directory is actually writable
            # Distinguish permission errors from other OS errors
            test_file = self.queue_dir / ".write_test"
            try:
                test_file.write_text("")
                test_file.unlink()
            except PermissionError as perm_err:
                raise PermissionError(f"Measurement queue directory is not writable") from perm_err
            except (FileNotFoundError, OSError) as os_err:
                # Directory was deleted between mkdir and write, or other OS error
                raise ValueError(f"Measurement queue directory became inaccessible") from os_err

        except PermissionError as e:
            # MEDIUM FIX M4 + ITERATION 3 FIX I3-6: Don't leak full filesystem paths in exceptions (GDPR)
            raise PermissionError(f"Cannot create measurement queue directory (permission denied)") from e
        except Exception as e:
            raise ValueError(f"Failed to create measurement queue directory") from e

        self.prediction_file = self.queue_dir / "predictions.jsonl"
        self.feedback_file = self.queue_dir / "feedback.jsonl"
        self.user_choice_file = self.queue_dir / "user_choices.jsonl"
        self.budget_file = self.queue_dir / "budget_allocations.jsonl"

        logger.info(f"MeasurementCollector initialized: {self.queue_dir}")

    def record_prediction(
        self,
        context_id: str,
        confidence_pred: float,
        outcome_actual: float,
        context_type: str = "adr",
        task_id: str = "",
        user_id: str = "unknown",
    ) -> None:
        """ADR-0270: Record confidence prediction vs. actual outcome."""
        if not self.enabled:
            return

        # CRITICAL: Validate confidence scores are in valid range [0.0, 1.0]
        if not (0.0 <= confidence_pred <= 1.0):
            logger.warning(f"Invalid confidence_pred {confidence_pred} (must be 0.0-1.0), clamping")
            confidence_pred = max(0.0, min(1.0, confidence_pred))
        if not (0.0 <= outcome_actual <= 1.0):
            logger.warning(f"Invalid outcome_actual {outcome_actual} (must be 0.0-1.0), clamping")
            outcome_actual = max(0.0, min(1.0, outcome_actual))

        record = PredictionRecord(
            timestamp=datetime.utcnow().isoformat(),
            context_id=context_id,
            confidence_pred=confidence_pred,
            outcome_actual=outcome_actual,
            context_type=context_type,
            task_id=task_id,
            user_id=user_id,
        )

        self._append_jsonl(self.prediction_file, asdict(record))
        # GDPR: Don't log raw user_id
        logger.debug(f"Recorded prediction: {context_id} (pred={confidence_pred:.2f}, actual={outcome_actual:.2f})")

    def record_feedback(
        self,
        context_id: str,
        feedback_impact: Literal["helpful", "harmful", "neutral"],
        score_before: float,
        score_after: float,
        learning_rate_applied: float = 0.05,
        decay_weight: float = 1.0,
        task_id: str = "",
        user_id: str = "unknown",
    ) -> None:
        """ADR-0271: Record feedback for Bayesian updates."""
        if not self.enabled:
            return

        record = FeedbackRecord(
            timestamp=datetime.utcnow().isoformat(),
            context_id=context_id,
            feedback_impact=feedback_impact,
            score_before=score_before,
            score_after=score_after,
            learning_rate_applied=learning_rate_applied,
            decay_weight=decay_weight,
            task_id=task_id,
            user_id=user_id,
        )

        self._append_jsonl(self.feedback_file, asdict(record))
        delta = score_after - score_before
        logger.debug(f"Recorded feedback: {context_id} ({feedback_impact}, Δ={delta:+.3f})")

    def record_user_choice(
        self,
        user_id: str,
        decision_style: Literal["pragmatic", "rigorous"],
        task_type: str,
        complexity: float,
        time_available: float,
        choice_made: str,
    ) -> None:
        """ADR-0272: Record user style from decisions."""
        if not self.enabled:
            return

        record = UserChoiceRecord(
            timestamp=datetime.utcnow().isoformat(),
            user_id=user_id,
            decision_style=decision_style,
            task_type=task_type,
            complexity=complexity,
            time_available=time_available,
            choice_made=choice_made,
        )

        self._append_jsonl(self.user_choice_file, asdict(record))
        logger.debug(f"Recorded user choice: {user_id} ({decision_style}, {task_type})")

    def record_budget_allocation(
        self,
        task_id: str,
        budget_allocated: Literal["critical", "important", "nice_to_have"],
        complexity_est: float,
        tokens_used: int,
        user_id: str = "unknown",
        match_score: Optional[float] = None,
    ) -> None:
        """ADR-0273: Record budget allocation vs. complexity."""
        if not self.enabled:
            return

        # Auto-calculate match score if not provided
        if match_score is None:
            budget_scores = {"critical": 9.0, "important": 5.0, "nice_to_have": 2.0}
            expected_budget = budget_scores.get(budget_allocated, 5.0)
            match_score = 1.0 - (abs(expected_budget - complexity_est) / 10.0)
            match_score = max(0.0, min(1.0, match_score))

        record = BudgetRecord(
            timestamp=datetime.utcnow().isoformat(),
            task_id=task_id,
            user_id=user_id,
            budget_allocated=budget_allocated,
            complexity_est=complexity_est,
            tokens_used=tokens_used,
            match_score=match_score,
        )

        self._append_jsonl(self.budget_file, asdict(record))
        logger.debug(f"Recorded budget: {task_id} ({budget_allocated}, match={match_score:.2f})")

    def _append_jsonl(self, filepath: Path, record_dict: Dict) -> None:
        """Append record to JSONL file (durable write with fsync)."""
        try:
            with open(filepath, "a") as f:
                json.dump(record_dict, f)
                f.write("\n")
                f.flush()
                os.fsync(f.fileno())  # CRITICAL: ensure data written to disk
        except Exception as e:
            logger.error(f"Failed to record measurement: {e}", exc_info=True)


# Global collector instance (initialized on import, thread-safe)
_default_collector: Optional[MeasurementCollector] = None
_collector_lock = threading.Lock()


def get_collector() -> MeasurementCollector:
    """Get or create the default measurement collector (thread-safe)."""
    global _default_collector
    if _default_collector is None:
        with _collector_lock:
            # Double-check after acquiring lock
            if _default_collector is None:
                enabled = True  # Check env var or config
                _default_collector = MeasurementCollector(enabled=enabled)
    return _default_collector


# Convenience functions (for ease of use in task_engine.py)

def record_prediction(
    context_id: str,
    confidence_pred: float,
    outcome_actual: float,
    **kwargs
) -> None:
    """Record ADR-0270 prediction."""
    get_collector().record_prediction(context_id, confidence_pred, outcome_actual, **kwargs)


def record_feedback(
    context_id: str,
    feedback_impact: Literal["helpful", "harmful", "neutral"],
    score_before: float,
    score_after: float,
    **kwargs
) -> None:
    """Record ADR-0271 feedback."""
    get_collector().record_feedback(context_id, feedback_impact, score_before, score_after, **kwargs)


def record_user_choice(
    user_id: str,
    decision_style: Literal["pragmatic", "rigorous"],
    task_type: str,
    complexity: float,
    time_available: float,
    choice_made: str,
) -> None:
    """Record ADR-0272 user choice."""
    get_collector().record_user_choice(user_id, decision_style, task_type, complexity, time_available, choice_made)


def record_budget_allocation(
    task_id: str,
    budget_allocated: Literal["critical", "important", "nice_to_have"],
    complexity_est: float,
    tokens_used: int,
    **kwargs
) -> None:
    """Record ADR-0273 budget allocation."""
    get_collector().record_budget_allocation(task_id, budget_allocated, complexity_est, tokens_used, **kwargs)

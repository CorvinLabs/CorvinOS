"""ADR-0214: Loss Profile Tracker (In-Session Learning).

Tracks actual loss from delegated steps to learn delegation patterns.
Post-hoc measurement: After execution, compare local vs delegated output.

Features:
- In-session only (reset on new session)
- Model-ID keying (detect when model changes)
- Exponential decay (forget old entries)
- ε-greedy exploration (learn from non-chosen engines)
"""
from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any, Optional

_logger = logging.getLogger(__name__)


@dataclass
class LossEntry:
    """Single loss measurement."""
    timestamp: float
    task_type: str
    model_id: str
    loss_pct: float  # 0-100
    engine: str  # Which engine was chosen
    alternative_scores: dict[str, float]  # Scores of other engines


class LossProfileTracker:
    """Track and learn from actual delegation outcomes."""

    # Configuration
    MAX_ENTRIES = 1000
    DECAY_HALF_LIFE_DAYS = 7
    DEFAULT_LOSS_PCT = 5.0  # Conservative: 5% default loss until we have data

    def __init__(self, model_id: str = "default"):
        """Initialize tracker.

        Args:
            model_id: Current model (e.g., "Claude-3.5-Sonnet"). Used to detect model changes.
        """
        self.history: list[LossEntry] = []
        self.current_model_id = model_id
        self._access_count = 0  # For stats

    def record_delegation_result(
        self,
        task_type: str,
        engine: str,
        loss_pct: float,
        alternative_scores: Optional[dict[str, float]] = None,
    ):
        """Record a delegation outcome.

        Args:
            task_type: Task classification (code_generation, etc)
            engine: Which engine was chosen
            loss_pct: Measured quality loss (0-100)
            alternative_scores: Softmax scores of other engines (for off-policy learning)
        """

        entry = LossEntry(
            timestamp=time.time(),
            task_type=task_type,
            model_id=self.current_model_id,
            loss_pct=loss_pct,
            engine=engine,
            alternative_scores=alternative_scores or {},
        )

        self.history.append(entry)

        # FIFO eviction if over limit
        if len(self.history) > self.MAX_ENTRIES:
            self.history.pop(0)

        _logger.debug(
            f"Recorded loss: {task_type} / {engine} / {loss_pct:.1f}% / model={self.current_model_id}"
        )

    def record_via_proxy(
        self,
        task_type: str,
        engine: str,
        schema_valid: bool = True,
        downstream_ok: bool = True,
    ):
        """Record outcome via proxy metrics (not actual loss measurement).

        Used for 95% of delegations to avoid 100% measurement overhead.

        Args:
            task_type: Task classification
            engine: Engine chosen
            schema_valid: Did output pass schema validation?
            downstream_ok: Did downstream steps succeed?
        """

        # Proxy loss: assume 1% if schema OK and downstream OK, else 10%
        loss_pct = 1.0 if (schema_valid and downstream_ok) else 10.0

        self.record_delegation_result(
            task_type=task_type,
            engine=engine,
            loss_pct=loss_pct,
            alternative_scores={},
        )

    def estimate_loss_for_task_type(self, task_type: str, complexity: str) -> float:
        """
        Estimate loss for a task type (used in detection).

        Returns average loss for matching entries, or DEFAULT if no history.

        Args:
            task_type: Task classification
            complexity: Task complexity (simple, moderate, complex)

        Returns:
            Estimated loss as fraction (0.0-1.0)
        """

        # Clean stale entries (exponential decay)
        self._decay_history()

        # Find matching entries for current model
        relevant = [
            e for e in self.history
            if e.task_type == task_type and e.model_id == self.current_model_id
        ]

        # Conservative: need at least N samples
        if len(relevant) < 5:
            return self.DEFAULT_LOSS_PCT / 100.0

        # Average loss
        avg_loss_pct = sum(e.loss_pct for e in relevant) / len(relevant)
        return avg_loss_pct / 100.0

    def _decay_history(self):
        """Remove stale entries (older than half-life)."""
        now = time.time()
        half_life_sec = self.DECAY_HALF_LIFE_DAYS * 86400

        # Keep entries that are recent enough
        self.history = [
            e for e in self.history
            if (now - e.timestamp) < half_life_sec
        ]

    def set_model(self, model_id: str):
        """Update model (e.g., after an upgrade from Sonnet to Fable)."""
        self.current_model_id = model_id
        _logger.info(f"Loss profile: updated model_id to {model_id}")

    def stats(self) -> dict[str, Any]:
        """Return stats about learning."""
        self._decay_history()

        by_task_type = {}
        for entry in self.history:
            key = entry.task_type
            if key not in by_task_type:
                by_task_type[key] = []
            by_task_type[key].append(entry.loss_pct)

        return {
            "total_measurements": len(self.history),
            "avg_loss_by_task_type": {
                k: sum(v) / len(v) for k, v in by_task_type.items()
            },
            "model_id": self.current_model_id,
            "measurements_this_model": sum(
                1 for e in self.history if e.model_id == self.current_model_id
            ),
        }

    def clear(self):
        """Clear all history."""
        self.history = []
        _logger.info("Loss profile cleared")

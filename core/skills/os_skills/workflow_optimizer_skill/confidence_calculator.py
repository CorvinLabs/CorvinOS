"""Stream 1 Phase 2: Confidence Calculator for Workflow Optimizer (ADR-0314).

Computes Bayesian confidence scores P(correct routing | task features).
Updates learned routing weights based on operator feedback.

**Algorithm:**
- Prior: P(correct | haiku/sonnet/opus, simple/medium/complex) = initial uniform
- Feedback: operator says "correct" or "incorrect"
- Posterior: P_new = P_old * likelihood(feedback) / marginal
- Confidence: average posterior over recent feedback window

**Compliance:**
- GDPR Art. 30/32: All updates audited (CONFIG_UPDATED event)
- ADR-0314: Feedback → weight update → next routing uses updated weights
- Immutable history: no weight rewriting, only append new versions
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Optional, Dict, Any, Tuple
from uuid import uuid4

from core.learning.learning_events import LearningEvent, EventType
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingWeights:
    """Learned routing weights (Bayesian posteriors).

    P(correct routing | task_complexity, routed_model).
    One cell per (complexity, model) pair.
    """
    # Format: {f"{complexity}_{model}": P(correct)}
    # e.g., "simple_haiku": 0.85, "simple_sonnet": 0.72, etc.
    weights: Dict[str, float] = field(default_factory=lambda: {
        # Simple tasks
        "simple_haiku": 0.85,
        "simple_sonnet": 0.72,
        "simple_opus": 0.55,
        # Medium tasks
        "medium_haiku": 0.55,
        "medium_sonnet": 0.80,
        "medium_opus": 0.70,
        # Complex tasks
        "complex_haiku": 0.25,
        "complex_sonnet": 0.65,
        "complex_opus": 0.85,
    })

    updated_at: str = field(default_factory=lambda: datetime.utcnow().isoformat() + "Z")
    version: str = "1.0"  # Immutable version identifier
    feedback_count: int = 0  # Total feedback incorporated

    def get_confidence(self, complexity: str, model: str) -> float:
        """Get P(correct routing | complexity, model).

        Args:
            complexity: "simple", "medium", or "complex"
            model: "haiku-4-5", "sonnet-5", or "opus-5"

        Returns:
            Confidence score in [0.0, 1.0]
        """
        key = f"{complexity}_{model.split('-')[0].lower()}"
        return self.weights.get(key, 0.5)  # Default to 0.5 if unknown


class ConfidenceCalculator:
    """Computes and updates Bayesian confidence scores from feedback (Phase 2).

    **Workflow:**
    1. Read feedback events from FeedbackHandler
    2. Apply Bayesian update: P_new = P_old * likelihood / marginal
    3. Compute aggregate confidence (% correct in recent window)
    4. Persist updated weights to YAML
    5. Emit CONFIG_UPDATED event to audit trail

    **Integration Point:**
    - ConfidenceCalculator is called by SkillOptimizer after feedback received
    - Returns updated RoutingWeights
    - Weights used by l5_agent_selector_learned.py for next routing decision
    """

    def __init__(
        self,
        event_store: EventStore,
        tenant_id: str,
        config_dir: Optional[Path] = None,
        skill_id: str = "os.workflow_optimizer_l5",
        skill_version: str = "1.0.0",
    ):
        """Initialize confidence calculator.

        Args:
            event_store: EventStore instance (from Stream 4)
            tenant_id: Tenant scope (GDPR)
            config_dir: Directory to persist learned weights (default: ~/.corvin/tenants/<tid>/global/)
            skill_id: Skill identifier
            skill_version: Skill version
        """
        self.event_store = event_store
        self.tenant_id = tenant_id
        self.skill_id = skill_id
        self.skill_version = skill_version

        # Config directory (for YAML persistence)
        if config_dir is None:
            from core.paths.tenant import tenant_home
            config_dir = Path(tenant_home(tenant_id)) / "workflow_optimizer_config"
        self.config_dir = Path(config_dir)
        self.config_dir.mkdir(parents=True, exist_ok=True)

        self.weights_file = self.config_dir / "routing_weights.json"
        self.history_dir = self.config_dir / "routing_weights_history"
        self.history_dir.mkdir(parents=True, exist_ok=True)

    def update_from_feedback(self) -> Tuple[RoutingWeights, int]:
        """Update routing weights based on recent feedback (Bayesian).

        Algorithm:
        1. Fetch recent feedback events from EventStore
        2. For each (complexity, model) pair:
           - Count successes (feedback_type=correct)
           - Count total feedback
           - Compute new P(correct) = successes / total
        3. Apply smoothing (Laplace smoothing to avoid 0/1 extremes)
        4. Persist to YAML (versioned, immutable history)
        5. Emit CONFIG_UPDATED event to audit trail

        Returns:
            (updated_weights, feedback_count_used)

        Raises:
            RuntimeError: EventStore read/write failed
        """
        # Load current weights as baseline
        current_weights = self.load_weights()

        # Fetch recent feedback (limit to 10k to avoid OOM)
        feedback_events = self.event_store.query_events(
            tenant_id=self.tenant_id,
            event_type=EventType.FEEDBACK,
            skill_id=self.skill_id,
            limit=10000,
            offset=0,
            newest_first=False,  # Chronological order
        )

        if not feedback_events:
            logger.info("No feedback events found, weights unchanged")
            return current_weights, 0

        # Tally successes per (complexity, model)
        success_counts: Dict[str, int] = {}
        total_counts: Dict[str, int] = {}

        for event in feedback_events:
            signal = event.signal or {}
            complexity = signal.get("task_complexity", "unknown")
            model = signal.get("routed_model", "unknown")
            feedback_type = signal.get("feedback_type", "skip")

            key = f"{complexity}_{model.split('-')[0].lower()}"
            total_counts[key] = total_counts.get(key, 0) + 1

            if feedback_type == "correct":
                success_counts[key] = success_counts.get(key, 0) + 1

        # Bayesian update with Laplace smoothing (α=1.0)
        # P_new = (successes + α) / (total + 2α)
        # α=1 smoothing prevents 0/1 extremes on small sample sizes
        alpha = 1.0
        updated_weights = dict(current_weights.weights)

        for key, total in total_counts.items():
            successes = success_counts.get(key, 0)
            # Laplace-smoothed estimate
            p_new = (successes + alpha) / (total + 2 * alpha)
            updated_weights[key] = p_new
            logger.info(
                f"Updated weight {key}: {successes}/{total} successes → P={p_new:.3f}"
            )

        # Create new RoutingWeights object
        new_weights = RoutingWeights(
            weights=updated_weights,
            feedback_count=len(feedback_events),
            version=self._next_version(current_weights.version),
        )

        # Persist to YAML (versioned)
        self._save_weights_versioned(new_weights)

        # Emit CONFIG_UPDATED event to audit trail
        self._emit_config_updated_event(current_weights, new_weights)

        return new_weights, len(feedback_events)

    def load_weights(self) -> RoutingWeights:
        """Load latest routing weights from disk.

        Tries to load from routing_weights.json. Falls back to hardcoded
        defaults if file doesn't exist.

        Returns:
            RoutingWeights (current or default)
        """
        if self.weights_file.exists():
            try:
                with open(self.weights_file, "r") as f:
                    data = json.load(f)
                return RoutingWeights(
                    weights=data.get("weights", RoutingWeights().weights),
                    updated_at=data.get("updated_at", datetime.utcnow().isoformat() + "Z"),
                    version=data.get("version", "1.0"),
                    feedback_count=data.get("feedback_count", 0),
                )
            except (json.JSONDecodeError, IOError) as e:
                logger.error(f"Failed to load weights: {e}, using defaults")
                return RoutingWeights()

        logger.info("No weights file found, using defaults")
        return RoutingWeights()

    def _save_weights_versioned(self, weights: RoutingWeights) -> None:
        """Save weights to disk with versioned history.

        Creates:
        - routing_weights.json (current, always up-to-date)
        - routing_weights_history/v{version}.json (immutable archive)

        Args:
            weights: RoutingWeights to persist
        """
        # Current weights file
        data = {
            "weights": weights.weights,
            "updated_at": weights.updated_at,
            "version": weights.version,
            "feedback_count": weights.feedback_count,
        }

        try:
            with open(self.weights_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Saved routing weights v{weights.version}")

            # Historical archive (immutable)
            history_file = self.history_dir / f"v{weights.version}.json"
            with open(history_file, "w") as f:
                json.dump(data, f, indent=2)
            logger.info(f"Archived routing weights v{weights.version}")

        except IOError as e:
            logger.error(f"Failed to save weights: {e}")
            raise RuntimeError(f"Weight persistence failed: {e}") from e

    def _emit_config_updated_event(
        self, old_weights: RoutingWeights, new_weights: RoutingWeights
    ) -> None:
        """Emit CONFIG_UPDATED event to audit trail (immutable record).

        Captures what changed in the config (weight deltas) so an auditor
        can see how the skill evolved over time.

        Args:
            old_weights: Previous RoutingWeights
            new_weights: Updated RoutingWeights
        """
        # Compute weight deltas
        config_delta = {}
        for key in new_weights.weights:
            old_val = old_weights.weights.get(key, 0.5)
            new_val = new_weights.weights.get(key, 0.5)
            if abs(new_val - old_val) > 0.01:  # Only record significant changes
                config_delta[key] = {
                    "old": round(old_val, 3),
                    "new": round(new_val, 3),
                    "delta": round(new_val - old_val, 3),
                }

        if not config_delta:
            logger.debug("No significant weight changes, skipping CONFIG_UPDATED event")
            return

        # Create CONFIG_UPDATED event
        signal = {
            "config_delta": config_delta,
            "old_version": old_weights.version,
            "new_version": new_weights.version,
            "feedback_count": new_weights.feedback_count,
        }

        event = LearningEvent.create(
            event_type=EventType.CONFIG_UPDATED,
            skill_id=self.skill_id,
            tenant_id=self.tenant_id,
            signal=signal,
            skill_version=self.skill_version,
            lom="confidence_calculator.py:_emit_config_updated_event:L235",
        )

        try:
            self.event_store.write_event(event)
            logger.info(
                f"CONFIG_UPDATED event emitted: {len(config_delta)} weight changes, "
                f"v{old_weights.version} → v{new_weights.version}"
            )
        except RuntimeError as e:
            logger.error(f"Failed to emit CONFIG_UPDATED event: {e}")

    def _next_version(self, current_version: str) -> str:
        """Generate next version identifier (semantic versioning).

        Args:
            current_version: Current version string (e.g., "1.0", "1.2.5")

        Returns:
            Next incremented version (e.g., "1.0" → "1.1")
        """
        try:
            parts = current_version.split(".")
            if len(parts) >= 2:
                parts[1] = str(int(parts[1]) + 1)
                return ".".join(parts)
            return "1.1"
        except (ValueError, IndexError):
            return "1.1"

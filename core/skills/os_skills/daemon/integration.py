"""DaemonIntegration — coordinates the learning event loop."""

from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import json
import os
import hashlib
from pathlib import Path

from .change_detector import DataSourceChangeDetector, SourceChangedEvent
from .execution_listener import SkillExecutionListener, SkillExecutedEvent
from .feedback_collector import FeedbackCollector, FeedbackEvent
from .causal_graph import CausalGraph
from .weight_learner import WeightLearner, WeightUpdateEvent, ConvergenceStatus
from .regeneration_scheduler import RegenerationScheduler, RegenerationQueueItem


@dataclass
class DaemonState:
    """Immutable snapshot of daemon state (for checkpointing)."""
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    weights: Dict[str, float] = field(default_factory=dict)
    convergence_statuses: Dict[str, str] = field(default_factory=dict)
    learning_rates: Dict[str, float] = field(default_factory=dict)
    feedback_count: int = 0
    weight_updates_count: int = 0
    skills_regenerated: int = 0
    last_checkpoint: str = ""


class DaemonIntegration:
    """
    Coordinates the learning event loop.

    Lifecycle:
    1. Boot: load checkpoint (if exists)
    2. Event loop (60s cycle):
       a. ChangeDetector: poll for source changes
       b. ExecutionListener: collect skill execution events
       c. FeedbackCollector: collect user feedback
       d. Update causal graph
       e. WeightLearner: update weights from feedback
       f. RegenerationScheduler: queue skills for regen
       g. Every 60 ticks (~1h): checkpoint state
    3. Watchdog: independent process monitors health
    4. Shutdown: save final checkpoint

    Deterministic: no randomness in learning.
    Audit-first: all events immutable and logged.
    """

    def __init__(self, corvin_home: str = "~/.corvin"):
        """
        Initialize daemon integration.

        Args:
            corvin_home: Where to store checkpoints
        """
        self.corvin_home = Path(corvin_home).expanduser()
        self.checkpoint_dir = self.corvin_home / "daemon_checkpoints"
        self.checkpoint_dir.mkdir(parents=True, exist_ok=True)

        # Components
        self.detector = DataSourceChangeDetector()
        self.listener = SkillExecutionListener()
        self.collector = FeedbackCollector()
        self.graph = CausalGraph()
        self.learner = WeightLearner()
        self.scheduler = RegenerationScheduler()

        # State
        self.tick_count = 0
        self.is_running = False
        self.last_checkpoint_tick = 0

    def bootstrap(self) -> None:
        """Load state from last checkpoint (if exists)."""
        latest_checkpoint = self._find_latest_checkpoint()
        if latest_checkpoint:
            self._load_checkpoint(latest_checkpoint)

    def process_event_tick(self) -> Dict:
        """
        Process one event loop tick (60s).

        Returns:
            Summary dict {changes, executions, feedback, weight_updates, regenerations}
        """
        if not self.is_running:
            return {"status": "not_running"}

        self.tick_count += 1
        summary = {
            "tick": self.tick_count,
            "timestamp": datetime.utcnow().isoformat(),
            "changes": [],
            "executions": [],
            "feedback": [],
            "weight_updates": [],
            "regenerations": [],
        }

        # Mock: in real impl, these come from event bus
        # Here we just track state

        # Every 60 ticks (~1h), checkpoint
        if self.tick_count - self.last_checkpoint_tick >= 60:
            self._save_checkpoint()
            self.last_checkpoint_tick = self.tick_count

        return summary

    def on_source_changed(self, event: SourceChangedEvent) -> None:
        """Handle source change event."""
        if event.change_type == "added":
            self.graph.add_node(event.source_id, "source", description=f"Source: {event.source_id}")
            # Prioritize new skills
            self.scheduler.queue_regeneration(
                skill_id=f"auto-skill-{event.source_id}",
                reason="new_source",
                priority=0.9,
            )
        elif event.change_type == "deleted":
            # Mark in graph but don't remove (immutable trail)
            pass

    def on_skill_executed(self, event: SkillExecutedEvent) -> None:
        """Handle skill execution event."""
        # Ensure skill node exists
        if event.skill_id not in self.graph.nodes:
            self.graph.add_node(event.skill_id, "skill", description=f"Skill: {event.skill_id}")

        # Outcome quality becomes a metric (outcome node)
        if event.outcome_quality is not None:
            outcome_id = f"{event.skill_id}-outcome"
            if outcome_id not in self.graph.nodes:
                self.graph.add_node(outcome_id, "outcome")
            # Add edge: skill → outcome
            if (event.skill_id, outcome_id) not in self.graph.edges:
                self.graph.add_edge(event.skill_id, outcome_id, "produces", weight=event.outcome_quality)

    def on_feedback(self, event: FeedbackEvent) -> None:
        """Handle user feedback event."""
        # Get inverse-prevalence weight
        importance_weights = self.collector.compute_inverse_prevalence_weights()
        signal_weight = importance_weights.get(event.signal_type, 1.0)

        # Compute gradient for skill
        skill_id = event.skill_id
        if skill_id not in self.learner.weights:
            self.learner.initialize_weight(skill_id, 0.5)

        # Mock loss: invert rating (higher rating = lower loss)
        loss_before = 1.0 - (event.rating / 5.0)
        loss_after = 0.0  # Assume feedback means we've improved (mock)
        weight_delta = 0.1  # Small synthetic delta

        # Update weight
        self.learner.update_weight(
            skill_id,
            loss_before,
            loss_after,
            weight_delta,
            signal_importance=signal_weight,
        )

        # Check if regen is needed
        loss_delta = loss_before - loss_after
        if self.scheduler.should_regenerate(skill_id, loss_delta):
            self.scheduler.queue_regeneration(
                skill_id,
                reason="weight_change",
                priority=max(0.5, loss_delta),  # Higher priority for bigger improvements
                loss_delta=loss_delta,
            )

    def get_next_regen_batch(self) -> List[RegenerationQueueItem]:
        """Get next batch of skills to regenerate."""
        return self.scheduler.get_next_batch()

    def mark_regen_complete(self, skill_id: str) -> None:
        """Mark a skill regeneration as complete."""
        self.scheduler.mark_completed(skill_id)

    def get_daemon_state(self) -> DaemonState:
        """Get current daemon state."""
        return DaemonState(
            timestamp=datetime.utcnow().isoformat(),
            weights=self.learner.get_all_weights(),
            convergence_statuses={
                k: v.value for k, v in self.learner.get_all_convergence_statuses().items()
            },
            learning_rates=self.learner.learning_rates.copy(),
            feedback_count=len(self.collector.get_all_events()),
            weight_updates_count=len(self.learner.get_all_events()),
            skills_regenerated=len(self.scheduler.processed),
        )

    def _save_checkpoint(self) -> None:
        """Save daemon state to checkpoint file."""
        state = self.get_daemon_state()

        checkpoint_path = self.checkpoint_dir / f"checkpoint-{self.tick_count:06d}.json"

        checkpoint_data = {
            "timestamp": state.timestamp,
            "tick_count": self.tick_count,
            "weights": state.weights,
            "convergence_statuses": state.convergence_statuses,
            "learning_rates": state.learning_rates,
            "feedback_count": state.feedback_count,
            "weight_updates_count": state.weight_updates_count,
            "skills_regenerated": state.skills_regenerated,
        }

        # Compute checksum
        content = json.dumps(checkpoint_data, sort_keys=True)
        checksum = hashlib.sha256(content.encode()).hexdigest()
        checkpoint_data["checksum"] = checksum

        # Write
        with open(checkpoint_path, "w") as f:
            json.dump(checkpoint_data, f, indent=2)

    def _load_checkpoint(self, checkpoint_path: Path) -> bool:
        """Load daemon state from checkpoint."""
        try:
            with open(checkpoint_path, "r") as f:
                data = json.load(f)

            # Verify checksum
            checksum = data.pop("checksum", None)
            content = json.dumps(data, sort_keys=True)
            computed_checksum = hashlib.sha256(content.encode()).hexdigest()
            if checksum != computed_checksum:
                return False  # Corrupted

            # Restore weights
            for weight_name, value in data.get("weights", {}).items():
                self.learner.initialize_weight(weight_name, value)

            # Restore learning rates
            for weight_name, lr in data.get("learning_rates", {}).items():
                if weight_name in self.learner.learning_rates:
                    self.learner.learning_rates[weight_name] = lr

            # Restore convergence statuses
            for weight_name, status_str in data.get("convergence_statuses", {}).items():
                if weight_name in self.learner.convergence_status:
                    self.learner.convergence_status[weight_name] = ConvergenceStatus[status_str.upper()]

            self.tick_count = data.get("tick_count", 0)
            return True
        except Exception:
            return False

    def _find_latest_checkpoint(self) -> Optional[Path]:
        """Find the latest checkpoint file."""
        if not self.checkpoint_dir.exists():
            return None

        checkpoints = sorted(self.checkpoint_dir.glob("checkpoint-*.json"), reverse=True)
        return checkpoints[0] if checkpoints else None

    def start(self) -> None:
        """Start the daemon."""
        self.is_running = True
        self.bootstrap()

    def stop(self) -> None:
        """Stop the daemon and save final checkpoint."""
        self._save_checkpoint()
        self.is_running = False

    def reset(self) -> None:
        """Clear all state (for testing)."""
        self.detector.reset()
        self.listener.reset()
        self.collector.reset()
        self.graph.reset()
        self.learner.reset()
        self.scheduler.reset()
        self.tick_count = 0
        self.last_checkpoint_tick = 0

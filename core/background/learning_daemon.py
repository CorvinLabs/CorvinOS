"""Background Learning Daemon — event-driven optimizer."""

import asyncio
from typing import Dict, List, Any, Optional
from dataclasses import dataclass
from datetime import datetime
import hashlib


@dataclass
class DaemonEvent:
    """Base event for daemon consumption."""
    event_type: str  # "data_changed", "skill_executed", "user_feedback"
    timestamp: str
    payload: Dict[str, Any]


class CausalGraph:
    """Track: DataSource → Skill → Outcome."""

    def __init__(self):
        self.edges: List[Dict[str, Any]] = []
        self.weights: Dict[str, float] = {}

    async def update_from_feedback(
        self, skill_id: str, feedback_signal: float
    ) -> None:
        """User says skill is better/worse → update weights."""
        # Backprop: which data sources contributed?
        # For now: simple attribution
        edge = {"skill_id": skill_id, "signal": feedback_signal, "timestamp": datetime.utcnow().isoformat()}
        self.edges.append(edge)


class WeightLearner:
    """Gradient descent on datahub weights."""

    def __init__(self):
        self.weights = {
            "memory:tier2": 0.50,
            "rag:embeddings": 0.30,
            "files": 0.20,
        }
        self.learning_rate = 0.01

    async def update_weights(self, attribution: Dict[str, float]) -> None:
        """Gradient step: weights[source] += learning_rate * attribution[source]."""
        for source, delta in attribution.items():
            if source in self.weights:
                self.weights[source] += self.learning_rate * delta
                # Clamp to [0, 1]
                self.weights[source] = max(0.0, min(1.0, self.weights[source]))

    def check_convergence(self) -> bool:
        """Are weights stabilizing?"""
        # Mock: always converging
        return True


class DataHubLearningDaemon:
    """Event-driven daemon that learns which data sources work best."""

    def __init__(self):
        self.causal_graph = CausalGraph()
        self.weight_learner = WeightLearner()
        self.event_queue: asyncio.Queue = asyncio.Queue()
        self.regeneration_queue: List[str] = []
        self.audit_trail: List[Dict[str, Any]] = []

    async def on_event(self, event: DaemonEvent) -> None:
        """Dispatch event to handler."""
        if event.event_type == "skill_executed":
            await self.on_skill_executed(event)
        elif event.event_type == "user_feedback":
            await self.on_user_feedback(event)
        elif event.event_type == "data_changed":
            await self.on_data_changed(event)

    async def on_data_changed(self, event: DaemonEvent) -> None:
        """New/updated data in a source."""
        significance = event.payload.get("significance", 0.5)
        if significance > 0.60:
            skill_ids = event.payload.get("affected_skills", [])
            self.regeneration_queue.extend(skill_ids)
            # Log
            self.audit_trail.append(
                {
                    "type": "regeneration_queued",
                    "skills": skill_ids,
                    "timestamp": datetime.utcnow().isoformat(),
                }
            )

    async def on_skill_executed(self, event: DaemonEvent) -> None:
        """Skill used in real code."""
        skill_id = event.payload.get("skill_id")
        success = event.payload.get("success", False)
        # Update causal graph
        await self.causal_graph.update_from_feedback(skill_id, 0.1 if success else -0.1)

    async def on_user_feedback(self, event: DaemonEvent) -> None:
        """User rates skill."""
        skill_id = event.payload.get("skill_id")
        signal = event.payload.get("signal", 0.0)
        sources = event.payload.get("data_sources", [])

        # Attribute feedback to sources
        attribution = {src: signal / len(sources) for src in sources} if sources else {}

        # Update weights
        await self.weight_learner.update_weights(attribution)

        # Log
        self.audit_trail.append(
            {
                "type": "weights_updated",
                "skill_id": skill_id,
                "signal": signal,
                "new_weights": self.weight_learner.weights.copy(),
                "timestamp": datetime.utcnow().isoformat(),
            }
        )

    async def run(self) -> None:
        """Main daemon loop."""
        while True:
            try:
                event = await asyncio.wait_for(self.event_queue.get(), timeout=1.0)
                await self.on_event(event)
            except asyncio.TimeoutError:
                # No events, check convergence
                if self.weight_learner.check_convergence():
                    pass  # weights are stable
            except Exception as e:
                print(f"Daemon error: {e}")

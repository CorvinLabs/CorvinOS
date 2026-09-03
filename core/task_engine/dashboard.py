"""Vibe Dashboard integration stubs (ADR-0545, Phase D)."""

from dataclasses import dataclass
from typing import List, Dict, Any


@dataclass
class DashboardMetrics:
    """Task executor dashboard metrics."""
    task_id: str
    phases_count: int
    phases_completed: int
    current_phase: str
    confidence: float
    audit_events_count: int
    state_hash: str
    estimated_time_remaining: str = "N/A"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "task_id": self.task_id,
            "phases_completed": f"{self.phases_completed}/{self.phases_count}",
            "current_phase": self.current_phase,
            "confidence": f"{self.confidence:.0%}",
            "audit_events": self.audit_events_count,
            "state_hash": self.state_hash[:16] + "...",
            "estimated_time_remaining": self.estimated_time_remaining,
        }


class VibeDashboardAdapter:
    """Adapter for Vibe Dashboard integration (ADR-0545, Phase D)."""

    def __init__(self, executor):
        self.executor = executor

    def get_task_metrics(self, task_id: str) -> DashboardMetrics:
        """Get metrics for task (for Vibe dashboard real-time display)."""
        # Stub: collect from event_store
        events = self.executor.event_store.query(task_id=task_id)
        phases_completed = sum(1 for e in events if e.event_type == "phase_complete")

        return DashboardMetrics(
            task_id=task_id,
            phases_count=3,  # Mock: assume 3 phases
            phases_completed=phases_completed,
            current_phase="phase-3-test",  # Mock
            confidence=0.89,  # Mock
            audit_events_count=len(events),
            state_hash=events[-1].hash if events else "",
        )

    def render_dag_visual(self, task_id: str) -> str:
        """Render task DAG as SVG (for Vibe dashboard)."""
        # Stub: returns placeholder SVG
        return """<svg width="400" height="200">
  <!-- Phase DAG visual would render here (Phase D) -->
  <text x="10" y="20">Task DAG: Phase A → Phase B → Phase C</text>
</svg>"""

    def render_phase_progress(self, task_id: str, phase_id: str) -> Dict[str, Any]:
        """Render phase progress card (real-time)."""
        # Stub: for Phase D Vibe integration
        return {
            "phase_id": phase_id,
            "status": "in_progress",
            "progress": 75,
            "gate_status": "pending",
            "confidence": 0.89,
            "estimated_time_remaining": "1h 23m",
        }

    def render_learning_metrics(self, task_id: str) -> Dict[str, Any]:
        """Render learning optimizer metrics (confidence, config tuning)."""
        # Stub: for Phase D + C integration
        return {
            "confidence_curve": [0.72, 0.76, 0.89],
            "config_changes": [
                {"param": "retry_threshold", "before": 5, "after": 4},
                {"param": "confidence_gate_min", "before": 0.70, "after": 0.72},
            ],
            "drift_detected": False,
            "last_alert": None,
        }

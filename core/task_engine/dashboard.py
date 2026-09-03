"""Phase D: Vibe Dashboard Adapter — Real implementation with DAG visual, revert, drift alerts (ADR-0545)."""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from datetime import datetime
import json


@dataclass
class DashboardMetrics:
    """Real-time task metrics for dashboard (Phase D complete)."""
    task_id: str
    tenant_id: str
    phase_current: str
    phase_total: int
    progress_pct: float  # 0–100
    confidence: float    # EMA-smoothed
    confidence_prev: float
    confidence_delta: float
    status: str  # "running", "blocked", "complete", "rolled_back"
    last_event: Dict[str, Any]
    event_count: int
    skill_results: List[Dict[str, Any]]
    gates_results: List[Dict[str, Any]]

    def to_dict(self) -> Dict[str, Any]:
        """Convert to JSON-serializable dict (Phase D)."""
        return {
            "task_id": self.task_id,
            "tenant_id": self.tenant_id,
            "phase_current": self.phase_current,
            "phase_total": self.phase_total,
            "progress_pct": f"{self.progress_pct:.0f}%",
            "confidence": f"{self.confidence:.2f}",
            "confidence_delta": f"{self.confidence_delta:+.2f}",
            "status": self.status,
            "event_count": self.event_count,
            "skill_results": self.skill_results,
            "gates_results": self.gates_results,
        }


class VibeDashboardAdapter:
    """Adapter for Vibe Dashboard integration (Phase D, ADR-0545, complete implementation)."""

    def __init__(self, event_store, learning_optimizer=None):
        self.event_store = event_store
        self.learning_optimizer = learning_optimizer
        self.last_rendered = {}

    def get_task_metrics(self, task_id: str) -> DashboardMetrics:
        """Collect real-time metrics from EventStore (Phase D complete)."""
        try:
            events = self.event_store.query_tenant_scoped(task_id=task_id)
        except Exception:
            events = []

        if not events:
            return DashboardMetrics(
                task_id=task_id, tenant_id="_default", phase_current="unknown",
                phase_total=0, progress_pct=0, confidence=1.0, confidence_prev=1.0,
                confidence_delta=0, status="not_found", last_event={}, event_count=0,
                skill_results=[], gates_results=[]
            )

        # Extract phases
        phases = {}
        for event in events:
            phase_id = event.payload.get("phase_id", "unknown")
            if phase_id not in phases:
                phases[phase_id] = []
            phases[phase_id].append(event)

        phase_current = list(phases.keys())[-1] if phases else "unknown"
        phase_total = len(phases)
        progress_pct = (len(phases) / max(phase_total, 1)) * 100

        # Extract confidence from latest drift-detection result
        confidence, confidence_prev, confidence_delta = 1.0, 1.0, 0
        for event in reversed(events):
            if event.event_type == "phase_gate_evaluated":
                gate = event.payload.get("gate", {})
                if gate.get("gate_type") == "confidence_drift_detection":
                    p = gate.get("payload", {})
                    confidence = p.get("tuned", 1.0)
                    confidence_prev = p.get("prev", 1.0)
                    confidence_delta = confidence - confidence_prev
                    break

        # Extract status
        last_event = events[-1]
        status = "running"
        if last_event.event_type == "task_complete":
            status = "complete"
        elif last_event.event_type == "task_rolled_back":
            status = "rolled_back"
        elif "blocked" in last_event.event_type:
            status = "blocked"

        # Extract skill results
        skill_results = []
        for event in events:
            if event.event_type == "phase_skill_executed":
                skill_results.append({
                    "skill_id": event.payload.get("skill_id"),
                    "status": event.payload.get("status"),
                })

        # Extract gate results
        gates_results = []
        for event in events:
            if event.event_type == "phase_gate_evaluated":
                gate = event.payload.get("gate", {})
                gates_results.append({
                    "gate_type": gate.get("gate_type"),
                    "passed": gate.get("passed"),
                })

        return DashboardMetrics(
            task_id=task_id,
            tenant_id=events[0].tenant_id if events else "_default",
            phase_current=phase_current,
            phase_total=phase_total,
            progress_pct=progress_pct,
            confidence=confidence,
            confidence_prev=confidence_prev,
            confidence_delta=confidence_delta,
            status=status,
            last_event={"event_type": last_event.event_type, "timestamp": last_event.timestamp},
            event_count=len(events),
            skill_results=skill_results,
            gates_results=gates_results,
        )

    def render_dag_visual(self, task_id: str) -> str:
        """Render DAG as SVG with DoS protection (HIGH FIX 7, ADR-0545)."""
        try:
            events = self.event_store.query_tenant_scoped(task_id=task_id)
        except Exception:
            events = []

        phases = {}
        for event in events:
            phase_id = event.payload.get("phase_id", "unknown")
            if phase_id not in phases:
                phases[phase_id] = {"events": []}
            phases[phase_id]["events"].append(event)

        # HIGH FIX 7: Cap phases to prevent SVG DoS
        MAX_PHASES_IN_DAG = 100
        phase_list = sorted(phases.keys())
        overflow_count = len(phase_list) - MAX_PHASES_IN_DAG if len(phase_list) > MAX_PHASES_IN_DAG else 0
        phase_list = phase_list[:MAX_PHASES_IN_DAG]

        svg_lines = [
            '<svg width="800" height="300" xmlns="http://www.w3.org/2000/svg" style="border: 1px solid #ccc;">',
            '<style>.phase-box { fill: #e3f2fd; stroke: #1976d2; stroke-width: 2; } .phase-text { font-family: monospace; font-size: 12px; } .phase-box.done { fill: #c8e6c9; }</style>',
        ]

        box_w, box_h = 100, 50
        x_spacing = 150
        y_pos = 125

        for i, ph_id in enumerate(phase_list):
            x_pos = 50 + (i * x_spacing)
            phase_class = "done" if i < len(phase_list) - 1 else ""
            svg_lines.append(f'  <rect class="phase-box {phase_class}" x="{x_pos}" y="{y_pos}" width="{box_w}" height="{box_h}" />')
            svg_lines.append(f'  <text class="phase-text" x="{x_pos + 5}" y="{y_pos + 32}">{ph_id}</text>')

        if overflow_count > 0:
            svg_lines.append(f'  <text x="50" y="280" style="font-size: 11px; fill: #999;">... and {overflow_count} more phases</text>')

        svg_lines.append('</svg>')
        return '\n'.join(svg_lines)

    def render_phase_progress(self, task_id: str, metrics: DashboardMetrics) -> str:
        """Render phase progress card (HTML, Phase D)."""
        status_color = {"running": "#2196F3", "complete": "#4CAF50", "blocked": "#F44336", "rolled_back": "#FF9800", "unknown": "#757575"}
        color = status_color.get(metrics.status, "#757575")

        html = f"""<div class="phase-card" style="border-left: 4px solid {color}; padding: 12px; margin: 12px 0; background: #f9f9f9; border-radius: 4px;">
            <div style="display: flex; justify-content: space-between;">
                <div>
                    <p style="margin: 0; font-weight: bold;">{task_id}</p>
                    <p style="margin: 4px 0; font-size: 12px;">Status: <strong>{metrics.status.upper()}</strong></p>
                    <p style="margin: 4px 0; font-size: 12px;">Phase: {metrics.phase_current} / {metrics.phase_total}</p>
                </div>
                <button id="revert-{task_id}" style="height: 32px; padding: 0 12px; background: #ff9800; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 12px;">Revert</button>
            </div>
            <div style="margin-top: 8px; background: #e0e0e0; height: 4px; border-radius: 2px;">
                <div style="background: {color}; height: 100%; width: {metrics.progress_pct}%; border-radius: 2px;"></div>
            </div>
        </div>"""
        return html

    def render_learning_metrics(self, task_id: str, metrics: DashboardMetrics) -> str:
        """Render learning metrics with drift alert (Phase D, Fix 3.5)."""
        drift_alert = ""
        if abs(metrics.confidence_delta) > 0.15:
            drift_alert = f"""<div style="background: #fff3cd; border-left: 4px solid #ffc107; padding: 8px 12px; margin: 8px 0; border-radius: 4px; font-size: 12px;">
                <strong>⚠ Drift Alert:</strong> Confidence {metrics.confidence_delta:+.2f} ({metrics.confidence_prev:.2f} → {metrics.confidence:.2f})
            </div>"""

        html = f"""<div class="learning-metrics" style="margin: 12px 0; padding: 12px; background: #f5f5f5; border-radius: 4px;">
            <p style="margin: 0; font-weight: bold; font-size: 12px;">Learning Metrics</p>
            <p style="margin: 4px 0; font-size: 11px;">Confidence: <strong>{metrics.confidence:.2f}</strong> (Δ {metrics.confidence_delta:+.2f})</p>
            {drift_alert}
        </div>"""
        return html

    def render_full_dashboard(self, task_id: str) -> Dict[str, Any]:
        """Render complete dashboard HTML + metrics (Phase D)."""
        metrics = self.get_task_metrics(task_id)

        dashboard = {
            "dag_visual": self.render_dag_visual(task_id),
            "phase_progress": self.render_phase_progress(task_id, metrics),
            "learning_metrics": self.render_learning_metrics(task_id, metrics),
            "metrics": asdict(metrics),
            "metrics_json": json.dumps(metrics.to_dict()),
        }

        self.last_rendered[task_id] = dashboard
        return dashboard


class RevertControlHandler:
    """Handle revert button clicks with AUTH validation (CRITICAL FIX 3, Phase D)."""

    def __init__(self, validator, event_store):
        self.validator = validator
        self.event_store = event_store

    def handle_revert_click(self, task_id: str, user_id: str, tenant_id: str) -> bool:
        """User clicked revert button in Vibe dashboard (CRITICAL FIX 3: auth + tenant validation)."""
        try:
            events = self.event_store.query_tenant_scoped(task_id=task_id)
        except Exception:
            return False

        if not events:
            return False

        # CRITICAL FIX 3.1: Verify user owns this task
        # Task owner is recorded in first event's payload
        first_event_owner = events[0].payload.get("user_id")
        if first_event_owner and first_event_owner != user_id:
            raise PermissionError(
                f"User {user_id} does not own task {task_id} (owner: {first_event_owner})"
            )

        # CRITICAL FIX 3.2: Verify tenant match
        if events[0].tenant_id != tenant_id:
            raise PermissionError(
                f"Tenant {tenant_id} does not match task tenant {events[0].tenant_id}"
            )

        # Find last completed phase
        last_phase = None
        for event in reversed(events):
            if event.event_type == "phase_complete":
                last_phase = event.payload.get("phase_id")
                break

        if not last_phase:
            return False

        # Trigger atomic rollback with owner attribution
        return self.validator.atomic_rollback(
            task_id,
            last_phase,
            reason=f"User {user_id} clicked revert button (Vibe dashboard)"
        )

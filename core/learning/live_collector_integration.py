#!/usr/bin/env python3
"""
LIVE COLLECTOR INTEGRATION — Hook into Learning Loop

This module connects the learning loop (UnifiedLossOptimizer, SkillSystem, etc.)
to the LiveExperimentCollector, so real metrics flow into persistent storage.

Integration points:
1. Loss computation hook — capture L_total, gradients, components
2. User action hook — track when the system actually runs
3. Audit event hook — log to collector when important events occur
"""

import json
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional


class LiveCollectorIntegration:
    """
    Bridge between CorvinOS systems and LiveExperimentCollector.

    Usage:
        collector = LiveCollectorIntegration()

        # When loss is computed:
        collector.on_loss_computed(
            loss_total=0.35,
            loss_components={'routing': 0.14, 'confidence': 0.09, ...},
            gradients={'routing': 0.005, ...},
            weights={'routing': 0.4, ...}
        )

        # When a user action happens:
        collector.on_user_action('routing_decision', {'decision': 'opus', 'confidence': 0.85})

        # When an anomaly is detected:
        collector.on_anomaly('gradient_oscillation', {'severity': 'medium', 'component': 'confidence'})
    """

    def __init__(self, tenant_id: str = "_default"):
        self.tenant_id = tenant_id
        self.event_log_dir = (
            Path.home() / ".corvin" / "tenants" / tenant_id / "experiments" / "live_events"
        )
        self.event_log_dir.mkdir(parents=True, exist_ok=True)

        self.event_log_file = self.event_log_dir / "events.jsonl"
        self.last_loss = None
        self.event_counter = 0

    def _emit_event(self, event_type: str, payload: Dict[str, Any]):
        """Append event to the live event log."""
        event = {
            "timestamp": datetime.now().isoformat(),
            "unix_time": int(datetime.now().timestamp()),
            "event_type": event_type,
            "tenant_id": self.tenant_id,
            "sequence": self.event_counter,
            **payload
        }

        self.event_counter += 1

        with open(self.event_log_file, "a") as f:
            f.write(json.dumps(event) + "\n")

    def on_loss_computed(
        self,
        loss_total: float,
        loss_components: Dict[str, float],
        gradients: Optional[Dict[str, float]] = None,
        weights: Optional[Dict[str, float]] = None,
        learning_rate: float = 0.01
    ):
        """
        Called when UnifiedLossOptimizer computes L_total.

        Args:
            loss_total: Total unified loss (scalar)
            loss_components: Dict of component losses {routing: 0.14, confidence: 0.09, ...}
            gradients: Dict of gradients {routing: 0.005, ...} (optional)
            weights: Dict of component weights {routing: 0.4, ...} (optional)
            learning_rate: Current learning rate alpha
        """

        # Detect if loss is improving
        trend = None
        if self.last_loss is not None:
            if loss_total < self.last_loss * 0.99:
                trend = "improving"
            elif loss_total > self.last_loss * 1.01:
                trend = "degrading"
            else:
                trend = "stable"

        self.last_loss = loss_total

        self._emit_event("loss_computed", {
            "loss_total": float(loss_total),
            "loss_components": {k: float(v) for k, v in loss_components.items()},
            "gradients": {k: float(v) for k, v in (gradients or {}).items()},
            "weights": {k: float(v) for k, v in (weights or {}).items()},
            "learning_rate": float(learning_rate),
            "trend": trend
        })

    def on_user_action(self, action_type: str, details: Dict[str, Any]):
        """
        Called when user performs an action (task run, routing decision, etc.)

        Args:
            action_type: Type of action (routing_decision, task_completed, etc.)
            details: Action-specific details
        """

        self._emit_event("user_action", {
            "action_type": action_type,
            "details": details
        })

    def on_anomaly(self, anomaly_type: str, details: Dict[str, Any]):
        """
        Called when an anomaly is detected (gradient oscillation, attack, etc.)

        Args:
            anomaly_type: Type of anomaly
            details: Anomaly-specific details (severity, component, etc.)
        """

        self._emit_event("anomaly_detected", {
            "anomaly_type": anomaly_type,
            "details": details
        })

    def on_weight_update(self, component: str, old_weight: float, new_weight: float, reason: str):
        """
        Called when a weight is optimized

        Args:
            component: Name of component (routing, confidence, etc.)
            old_weight: Previous weight value
            new_weight: New weight value
            reason: Why the update happened (gradient_descent, anomaly_recovery, etc.)
        """

        delta = new_weight - old_weight
        pct_change = (delta / old_weight * 100) if old_weight != 0 else 0

        self._emit_event("weight_updated", {
            "component": component,
            "old_weight": float(old_weight),
            "new_weight": float(new_weight),
            "delta": float(delta),
            "pct_change": float(pct_change),
            "reason": reason
        })

    def on_convergence_achieved(self, metrics: Dict[str, Any]):
        """
        Called when convergence is detected

        Args:
            metrics: Convergence metrics (variance_reduction, convergence_rate, etc.)
        """

        self._emit_event("convergence_achieved", metrics)

    # =========================================================================
    # TIER 2: INFRASTRUCTURE LOOP HOOKS (9D Learning Vector)
    # =========================================================================

    def on_memory_decision(
        self,
        context_window_size: int,
        layer_importance: Dict[str, float],
        recall_threshold: float,
        feedback: Dict[str, Any]
    ):
        """
        Called when Memory Loop (ADR-0620) makes a context decision.

        Args:
            context_window_size: bytes (4KB–16KB range)
            layer_importance: {original, preserved, injected} weights
            recall_threshold: [0.5–0.9] context relevance cutoff
            feedback: {missing_context_ratio, irrelevance_score, retrieval_latency_ms, token_waste_ratio}
        """

        self._emit_event("memory_decision", {
            "context_window_size": context_window_size,
            "layer_importance": layer_importance,
            "recall_threshold": float(recall_threshold),
            "feedback": {k: float(v) for k, v in feedback.items()}
        })

    def on_skill_composition_decision(
        self,
        skill_order: list,
        priority_weights: Dict[str, float],
        feedback: Dict[str, Any],
        execution_time_ms: float
    ):
        """
        Called when Skill Composition Loop (ADR-0621) orders the DAG.

        Args:
            skill_order: list of skill IDs in execution order
            priority_weights: learned importance weight per skill
            feedback: {composition_error_rate, dag_execution_time_ms, skill_contradictions}
            execution_time_ms: total time to run all skills
        """

        self._emit_event("skill_composition_decision", {
            "skill_order": skill_order,
            "priority_weights": {k: float(v) for k, v in priority_weights.items()},
            "feedback": {k: float(v) if isinstance(v, (int, float)) else v
                        for k, v in feedback.items()},
            "execution_time_ms": float(execution_time_ms)
        })

    def on_plugin_decision(
        self,
        task_type: str,
        plugins_loaded: list,
        plugin_priorities: Dict[str, float],
        feedback: Dict[str, Dict[str, Any]]
    ):
        """
        Called when Plugin Loop (ADR-0622) selects plugins.

        Args:
            task_type: task classification (e.g., 'image_processing', 'code_review')
            plugins_loaded: list of plugin IDs selected for this task
            plugin_priorities: learned importance weight per plugin
            feedback: {plugin_id: {quality_gain, execution_time_ms, error_rate, conflict_score}}
        """

        self._emit_event("plugin_decision", {
            "task_type": task_type,
            "plugins_loaded": plugins_loaded,
            "plugin_priorities": {k: float(v) for k, v in plugin_priorities.items()},
            "feedback_per_plugin": {
                plugin_id: {k: float(v) if isinstance(v, (int, float)) else v
                           for k, v in metrics.items()}
                for plugin_id, metrics in feedback.items()
            }
        })

    def on_audit_decision(
        self,
        sampling_rate: float,
        anomaly_sensitivity: float,
        feedback: Dict[str, Any]
    ):
        """
        Called when Audit Loop (optional TIER 2) tunes audit parameters.

        Args:
            sampling_rate: [0.01–1.0] fraction of events to log
            anomaly_sensitivity: [0.5–1.0] anomaly detection threshold
            feedback: {audit_overhead_percent, false_positive_rate, missed_anomalies}
        """

        self._emit_event("audit_decision", {
            "sampling_rate": float(sampling_rate),
            "anomaly_sensitivity": float(anomaly_sensitivity),
            "feedback": {k: float(v) for k, v in feedback.items()}
        })

    # =========================================================================
    # TIER 3: META LOOP HOOKS (Self-Tuning Hyperparameters)
    # =========================================================================

    def on_meta_decision(
        self,
        observation: Dict[str, Any],
        meta_action: Dict[str, Any],
        rollback_status: str = "no_rollback_needed"
    ):
        """
        Called when Meta Loop (ADR-0623) adjusts hyperparameters.

        Args:
            observation: {convergence_metrics, gradient_history, efficiency_metrics}
            meta_action: {parameter, old_value, new_value, reason}
            rollback_status: 'no_rollback', 'rollback_applied', 'rollback_checked'
        """

        self._emit_event("learning_meta_decision", {
            "observation": observation,
            "meta_action": meta_action,
            "rollback_status": rollback_status
        })

    def on_meta_rollback(
        self,
        parameter: str,
        old_value: float,
        new_value: float,
        reason: str
    ):
        """
        Called when Meta Loop reverts a hyperparameter change.

        Args:
            parameter: name of parameter that was rolled back
            old_value: value before the bad update
            new_value: value that was applied (now reverted)
            reason: why rollback was triggered (e.g., 'oscillation_worsened')
        """

        self._emit_event("learning_meta_rollback", {
            "parameter": parameter,
            "old_value": float(old_value),
            "new_value": float(new_value),
            "reason": reason,
            "reverted_to": float(old_value)
        })

    def get_event_summary(self, hours: int = 1) -> Dict[str, Any]:
        """Get summary of events in the last N hours."""

        cutoff = datetime.now().timestamp() - (hours * 3600)

        events = []
        if self.event_log_file.exists():
            with open(self.event_log_file, "r") as f:
                for line in f:
                    try:
                        event = json.loads(line)
                        if event["unix_time"] > cutoff:
                            events.append(event)
                    except json.JSONDecodeError:
                        pass

        # Summarize
        summary = {
            "period_hours": hours,
            "total_events": len(events),
            "event_types": {},
            "anomalies": [],
            "last_loss": None
        }

        for event in events:
            et = event["event_type"]
            summary["event_types"][et] = summary["event_types"].get(et, 0) + 1

            if et == "anomaly_detected":
                summary["anomalies"].append(event["anomaly_type"])

            if et == "loss_computed":
                summary["last_loss"] = event["loss_total"]

        return summary


# ============================================================================
# API INTEGRATION — Flask endpoint for monitoring
# ============================================================================

def create_collector_api(integration: LiveCollectorIntegration):
    """
    Create Flask routes for monitoring live collection.

    Usage:
        from flask import Flask
        app = Flask(__name__)
        integration = LiveCollectorIntegration()

        create_collector_api(integration)
        # Routes are auto-registered:
        # GET /v1/experiments/live/status → current state
        # GET /v1/experiments/live/events?hours=1 → recent events
        # GET /v1/experiments/live/statistics?days=7 → rolling stats
    """

    from flask import Blueprint, jsonify, request

    bp = Blueprint("live_collector", __name__, url_prefix="/v1/experiments/live")

    @bp.route("/status", methods=["GET"])
    def status():
        """Get current collection status."""
        return jsonify({
            "status": "collecting",
            "tenant_id": integration.tenant_id,
            "event_log": str(integration.event_log_file),
            "last_loss": integration.last_loss,
            "events_emitted": integration.event_counter
        })

    @bp.route("/events", methods=["GET"])
    def get_events():
        """Get recent events (querystring: hours=1)."""
        hours = int(request.args.get("hours", 1))
        summary = integration.get_event_summary(hours=hours)
        return jsonify(summary)

    @bp.route("/statistics", methods=["GET"])
    def get_statistics():
        """Get rolling statistics (querystring: days=7)."""
        # TODO: Implement statistics aggregation
        days = int(request.args.get("days", 7))
        return jsonify({
            "period_days": days,
            "message": "Statistics aggregation not yet implemented"
        })

    return bp


# ============================================================================
# DAEMON MONITOR — Print collection status
# ============================================================================

def monitor_collection(interval: int = 60):
    """
    Run a monitor that prints collection status periodically.

    Usage:
        from threading import Thread
        monitor_thread = Thread(target=monitor_collection, daemon=True)
        monitor_thread.start()
    """

    import time

    integration = LiveCollectorIntegration()

    print("[Collector Monitor] Starting...")

    while True:
        try:
            summary = integration.get_event_summary(hours=1)

            print(f"\n[Collector Monitor] {datetime.now().isoformat()}")
            print(f"  Events (1h): {summary['total_events']}")
            print(f"  Last loss: {summary['last_loss']:.3f}" if summary["last_loss"] else "  Last loss: N/A")
            print(f"  Event types: {summary['event_types']}")

            if summary["anomalies"]:
                print(f"  ⚠️  Anomalies: {', '.join(set(summary['anomalies']))}")

            time.sleep(interval)

        except Exception as e:
            print(f"[Collector Monitor] Error: {e}")
            time.sleep(interval)


if __name__ == "__main__":
    # Demo mode
    print("Live Collector Integration Demo")
    print("================================\n")

    integration = LiveCollectorIntegration()

    # Simulate some events
    print("Simulating loss computations...")
    for i in range(5):
        loss = 0.4 - i * 0.02  # Improving loss
        integration.on_loss_computed(
            loss_total=loss,
            loss_components={
                'routing': loss * 0.4,
                'confidence': loss * 0.25,
                'feedback': loss * 0.15,
            },
            gradients={'routing': 0.005, 'confidence': -0.002}
        )
        print(f"  Loss computed: {loss:.3f}")

    print("\nSimulating user actions...")
    integration.on_user_action("routing_decision", {
        "decision": "opus",
        "confidence": 0.85,
        "task_id": "task_123"
    })
    print("  User action logged")

    print("\nSimulating anomaly detection...")
    integration.on_anomaly("gradient_oscillation", {
        "severity": "medium",
        "component": "confidence",
        "oscillation_rate": 0.6
    })
    print("  Anomaly logged")

    # Show summary
    print("\n=== Event Summary (last 1 hour) ===")
    summary = integration.get_event_summary(hours=1)
    print(json.dumps(summary, indent=2))

    print(f"\nEvents persisted to: {integration.event_log_file}")
    print("Status: ✅ Live collection ready")

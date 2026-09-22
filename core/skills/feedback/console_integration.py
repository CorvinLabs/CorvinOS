"""
Console Integration for Feedback System — ADR-2033 Week 2

Dashboard panels for feedback trends, learning curves, and system health.

Panels:
  - Feedback Volume Trend (last 7 days)
  - Learning Curve (confidence over time per skill)
  - Skill Health (latency, error rate, last update)
  - Config Updates Log (recent changes)
  - Alert Status (active alerts)
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import List, Dict, Any, Optional


@dataclass
class ChartDataPoint:
    """Single point in a time-series chart."""
    timestamp: str
    value: float
    label: Optional[str] = None
    metadata: Dict[str, Any] = None


@dataclass
class DashboardPanel:
    """Dashboard panel definition."""
    panel_id: str  # Unique identifier
    title: str
    description: str
    panel_type: str  # "chart" | "metric" | "table" | "status"
    data_source: str  # API endpoint or data source
    refresh_interval_s: int = 30  # Auto-refresh interval


# ============================================================================
# Dashboard Panels
# ============================================================================

FEEDBACK_VOLUME_PANEL = DashboardPanel(
    panel_id="feedback_volume",
    title="Feedback Volume (7 days)",
    description="Number of feedback events processed over the last 7 days",
    panel_type="chart",
    data_source="/v1/skills/feedback/metrics",
    refresh_interval_s=60,
)

LEARNING_CURVE_PANEL = DashboardPanel(
    panel_id="learning_curve",
    title="Learning Curves (by Skill)",
    description="Confidence threshold evolution based on feedback",
    panel_type="chart",
    data_source="/v1/skills/feedback/learning-curve/{skill_id}",
    refresh_interval_s=120,
)

SKILL_HEALTH_PANEL = DashboardPanel(
    panel_id="skill_health",
    title="Skill Health Status",
    description="Per-skill latency, error rate, and last update",
    panel_type="table",
    data_source="/v1/skills/feedback/health/extended",
    refresh_interval_s=30,
)

CONFIG_UPDATES_PANEL = DashboardPanel(
    panel_id="config_updates",
    title="Config Updates Log",
    description="Recent config changes triggered by feedback",
    panel_type="table",
    data_source="/v1/skills/feedback/config-updates",
    refresh_interval_s=60,
)

ALERT_STATUS_PANEL = DashboardPanel(
    panel_id="alerts",
    title="Active Alerts",
    description="High latency, high error rate, queue congestion",
    panel_type="status",
    data_source="/v1/skills/feedback/health/extended",
    refresh_interval_s=30,
)

# ============================================================================
# Console Routes
# ============================================================================

CONSOLE_ROUTES = {
    "/console/feedback/dashboard": {
        "title": "Feedback System Dashboard",
        "description": "Stream 4 monitoring and learning curves",
        "panels": [
            FEEDBACK_VOLUME_PANEL.panel_id,
            LEARNING_CURVE_PANEL.panel_id,
            SKILL_HEALTH_PANEL.panel_id,
            CONFIG_UPDATES_PANEL.panel_id,
            ALERT_STATUS_PANEL.panel_id,
        ],
    },
    "/console/feedback/learning": {
        "title": "Learning Curves",
        "description": "Skill confidence and preference evolution",
        "panel": LEARNING_CURVE_PANEL.panel_id,
    },
    "/console/feedback/health": {
        "title": "System Health",
        "description": "Queue depth, latency, error rate, active alerts",
        "panels": [
            SKILL_HEALTH_PANEL.panel_id,
            ALERT_STATUS_PANEL.panel_id,
        ],
    },
}


class ConsoleDashboardData:
    """Helper class to generate dashboard data for console."""

    @staticmethod
    def feedback_volume_chart(
        total_processed: int,
        daily_breakdown: Dict[str, int],
        period_days: int = 7,
    ) -> Dict[str, Any]:
        """
        Generate feedback volume chart data.

        Args:
            total_processed: Total feedback events
            daily_breakdown: Dict[date_iso, count]
            period_days: Number of days displayed

        Returns:
            Chart-ready data dict
        """
        return {
            "chart_type": "line",
            "title": "Feedback Volume (7 days)",
            "series": [
                {
                    "name": "Feedback Events",
                    "data": [
                        {"timestamp": date, "value": count}
                        for date, count in sorted(daily_breakdown.items())
                    ],
                }
            ],
            "total": total_processed,
            "period_days": period_days,
        }

    @staticmethod
    def learning_curve_chart(
        skill_id: str,
        curve_points: List[Dict[str, Any]],
        convergence_status: str = "unknown",
    ) -> Dict[str, Any]:
        """
        Generate learning curve chart.

        Args:
            skill_id: Skill identifier
            curve_points: List of {timestamp, confidence, feedback_type}
            convergence_status: "converged" | "oscillating" | "diverging"

        Returns:
            Chart-ready data dict
        """
        return {
            "chart_type": "line",
            "title": f"Learning Curve — {skill_id}",
            "series": [
                {
                    "name": "Confidence Threshold",
                    "data": curve_points,
                }
            ],
            "convergence_status": convergence_status,
            "last_update": (
                curve_points[-1]["timestamp"]
                if curve_points
                else None
            ),
        }

    @staticmethod
    def skill_health_table(
        skills: Dict[str, Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate skill health table.

        Args:
            skills: Dict[skill_id, {latency_p99_ms, error_rate, last_update_at}]

        Returns:
            Table-ready data dict
        """
        rows = [
            {
                "skill_id": skill_id,
                "latency_p99_ms": health.get("latency_p99_ms", 0),
                "error_rate": health.get("error_rate", 0),
                "last_update": health.get("last_update_at"),
                "status": "healthy" if health.get("error_rate", 0) < 0.01 else "degraded",
            }
            for skill_id, health in skills.items()
        ]
        return {
            "table_type": "skill_health",
            "title": "Skill Health Status",
            "columns": [
                {"key": "skill_id", "label": "Skill"},
                {"key": "latency_p99_ms", "label": "P99 Latency (ms)"},
                {"key": "error_rate", "label": "Error Rate"},
                {"key": "last_update", "label": "Last Update"},
                {"key": "status", "label": "Status"},
            ],
            "rows": rows,
        }

    @staticmethod
    def config_updates_table(
        updates: List[Dict[str, Any]],
    ) -> Dict[str, Any]:
        """
        Generate config updates log table.

        Args:
            updates: List of {update_id, skill_id, timestamp, feedback_type, config_delta}

        Returns:
            Table-ready data dict
        """
        rows = [
            {
                "update_id": u.get("update_id", "")[:8],  # Short ID
                "skill_id": u.get("skill_id", ""),
                "feedback_type": u.get("feedback_type", ""),
                "config_delta": str(u.get("config_delta", {}))[:50],  # Truncate
                "timestamp": u.get("timestamp", ""),
            }
            for u in updates
        ]
        return {
            "table_type": "config_updates",
            "title": "Recent Config Updates",
            "columns": [
                {"key": "update_id", "label": "ID"},
                {"key": "skill_id", "label": "Skill"},
                {"key": "feedback_type", "label": "Type"},
                {"key": "config_delta", "label": "Change"},
                {"key": "timestamp", "label": "Time"},
            ],
            "rows": rows,
        }

    @staticmethod
    def alert_status_badge(
        active_alerts: List[str],
        health_status: str = "healthy",
    ) -> Dict[str, Any]:
        """
        Generate alert status badge.

        Args:
            active_alerts: List of alert names ["high_latency", "high_error_rate", ...]
            health_status: "healthy" | "degraded" | "critical"

        Returns:
            Status badge data
        """
        status_colors = {
            "healthy": "#22c55e",      # Green
            "degraded": "#eab308",     # Yellow
            "critical": "#ef4444",     # Red
        }
        return {
            "status": health_status,
            "color": status_colors.get(health_status, "#6b7280"),
            "active_alerts": active_alerts,
            "alert_count": len(active_alerts),
            "timestamp": datetime.utcnow().isoformat() + "Z",
        }

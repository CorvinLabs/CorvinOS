"""
Stream C: Dashboard Wiring for Phase 3 Observability

Subscribes to A4 (OptimizerConfig) + Stream B (EnrichedOutcomeEvent) streams.
Renders 3 real-time panels:
1. Confidence Scores (time series)
2. Config Deltas Applied (bar chart)
3. Trend Status (gauge: improving/stable/degrading)

Phase 1: Panel structure + WebSocket subscription stubs
Phase 2: Real-time update mechanism + SLA <100ms refresh
Phase 3: Comparative analytics + multi-skill correlation

References: ADR-2089 (Stream C Design), ADR-0297 (Dashboard Observability)
"""

from typing import Optional, Dict, List
from datetime import datetime
import logging

logger = logging.getLogger(__name__)


class StreamCDashboard:
    """
    Phase 1: Dashboard skeleton for Stream A/B event subscription.

    Panels:
    1. ConfidenceScoreboard: time series of confidence_delta over time
    2. ConfigDeltaChart: bar chart of config_delta applied per skill
    3. TrendGauge: current trend status (improving/stable/degrading)

    Invariants:
    - Non-blocking: failures logged, don't break web UI
    - WebSocket-ready: structured for real-time subscription
    - Multi-tenant: all data filtered by tenant_id
    """

    def __init__(self, tenant_id: str):
        """Initialize dashboard for tenant."""
        self.tenant_id = tenant_id
        self._confidence_series = []  # Phase 1: in-memory buffer
        self._config_deltas = {}  # Phase 1: skill → latest config_delta
        self._trends = {}  # Phase 1: skill → current trend

    def panel_confidence_scoreboard(self) -> Dict:
        """
        Panel 1: Confidence score time series.

        Returns:
        {
          "panel": "confidence_scoreboard",
          "title": "Skill Confidence Trends",
          "data": [
            { "timestamp": "2026-09-27T10:30:00Z", "skill_id": "os.skill", "confidence_delta": 0.75, "trend": "improving" },
            ...
          ]
        }
        """
        return {
            "panel": "confidence_scoreboard",
            "title": "Skill Confidence Trends",
            "data": self._confidence_series,
            "sla_ms": 100,
        }

    def panel_config_delta_chart(self) -> Dict:
        """
        Panel 2: Config deltas applied per skill (bar chart).

        Returns:
        {
          "panel": "config_delta_chart",
          "title": "Learning Rate Adjustments",
          "data": [
            { "skill_id": "os.skill", "config_delta": +0.075, "applied": true },
            ...
          ]
        }
        """
        return {
            "panel": "config_delta_chart",
            "title": "Learning Rate Adjustments",
            "data": [
                {
                    "skill_id": skill_id,
                    "config_delta": delta,
                    "applied": True,  # Phase 2: track applied status
                }
                for skill_id, delta in self._config_deltas.items()
            ],
            "sla_ms": 100,
        }

    def panel_trend_gauge(self) -> Dict:
        """
        Panel 3: Current trend status gauge.

        Returns:
        {
          "panel": "trend_gauge",
          "title": "Overall System Trend",
          "data": {
            "status": "improving",
            "skills_improving": 5,
            "skills_stable": 2,
            "skills_degrading": 1
          }
        }
        """
        improving = sum(1 for t in self._trends.values() if t == "improving")
        stable = sum(1 for t in self._trends.values() if t == "stable")
        degrading = sum(1 for t in self._trends.values() if t == "degrading")

        overall = "improving" if improving > degrading else (
            "degrading" if degrading > improving else "stable"
        )

        return {
            "panel": "trend_gauge",
            "title": "Overall System Trend",
            "data": {
                "status": overall,
                "skills_improving": improving,
                "skills_stable": stable,
                "skills_degrading": degrading,
            },
            "sla_ms": 100,
        }

    def ingest_enriched_event(self, event) -> bool:
        """
        Ingest Stream B EnrichedOutcomeEvent.

        Phase 1: Buffer in memory
        Phase 2: Validate tenant, emit to WebSocket subscribers
        """
        if event is None or event.tenant_id != self.tenant_id:
            return False

        try:
            # Record confidence score
            self._confidence_series.append({
                "timestamp": event.timestamp.isoformat(),
                "skill_id": event.skill_id,
                "confidence_delta": event.confidence_delta,
                "trend": event.trend,
            })

            # Update trend cache
            self._trends[event.skill_id] = event.trend

            # Phase 2: Emit to subscribers
            logger.debug(f"Stream C ingested event: {event.skill_id} trend={event.trend}")
            return True

        except Exception as e:
            logger.error(f"Stream C ingest failed: {e}", exc_info=True)
            return False

    def ingest_optimizer_config(self, config) -> bool:
        """
        Ingest Stream A OptimizerConfig.

        Phase 1: Buffer in memory
        Phase 2: Track applied configs, emit to WebSocket
        """
        if config is None or config.tenant_id != self.tenant_id:
            return False

        try:
            # Record config delta
            self._config_deltas[config.skill_id] = config.config_delta

            logger.debug(
                f"Stream C ingested config: {config.skill_id} "
                f"delta={config.config_delta:+.4f}"
            )
            return True

        except Exception as e:
            logger.error(f"Stream C config ingest failed: {e}", exc_info=True)
            return False

    def get_all_panels(self) -> Dict:
        """Return all 3 panels for dashboard render."""
        return {
            "panel_1": self.panel_confidence_scoreboard(),
            "panel_2": self.panel_config_delta_chart(),
            "panel_3": self.panel_trend_gauge(),
            "updated_at": datetime.utcnow().isoformat(),
        }

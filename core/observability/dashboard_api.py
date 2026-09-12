"""Phase 3: Dashboard API for corvin-labs.com/stats.

ADR-0684: Observability Dashboard + corvin-labs.com/stats API

Endpoints (REST):
- /api/v1/instances — List instances (status, uptime, geo)
- /api/v1/skills — Skill execution metrics (latency p50/p99, error rate)
- /api/v1/learning — Learning loop status (convergence, config updates)
- /api/v1/geo-heatmap — Geo distribution (instances per country)
- /api/v1/dashboards/<name> — Named dashboard config
"""

from dataclasses import dataclass, asdict
from typing import List, Dict, Any, Optional
from enum import Enum
import logging

logger = logging.getLogger(__name__)


class MetricType(Enum):
    """Metric types for queries."""
    LATENCY = "latency"
    ERROR_RATE = "error_rate"
    UPTIME = "uptime"


@dataclass(frozen=True)
class InstanceSummary:
    """Summary of a single CorvinOS instance."""
    instance_id: str
    tenant_id: str
    status: str  # "online" | "offline"
    uptime_seconds: int
    geo_country: str
    geo_region: Optional[str]
    platform: str
    plugins_loaded: int
    memory_usage_mb: int
    last_heartbeat_iso: str


@dataclass(frozen=True)
class SkillMetric:
    """Aggregated skill execution metric."""
    skill_id: str
    skill_version: str
    latency_p50_ms: float
    latency_p99_ms: float
    error_rate_percent: float
    sample_count: int
    geo_country: Optional[str] = None


@dataclass(frozen=True)
class LearningStatus:
    """Status of learning loop for a skill."""
    skill_id: str
    convergence_rate: float  # 0-1, higher = more stable
    config_updates_count: int  # Cumulative
    feedback_count: int  # Total feedback events
    last_update_iso: Optional[str] = None


@dataclass(frozen=True)
class GeoHeatmapCell:
    """One cell in geo heatmap (country-level)."""
    country: str  # ISO 3166-1
    instances_count: int
    latency_p99_ms: float  # Avg across all instances in country
    error_rate_percent: float
    metric_type: str  # What metric is this cell colored by


class DashboardAPI:
    """REST API for observability dashboard (Phase 3).

    Serves metrics for:
    1. Console dashboard (Observability page)
    2. corvin-labs.com/stats (public API)
    3. Grafana dashboards (via Prometheus backend)

    TODO Phase 3:
    - Query OTEL Collector for real metrics
    - Aggregate across instances/tenants
    - Compute geos, percentiles, trends
    """

    def __init__(self, tenant_id: str, otel_collector_url: str = "http://localhost:4318"):
        self.tenant_id = tenant_id
        self.otel_collector_url = otel_collector_url
        # TODO: Initialize Prometheus client

    def list_instances(self, status_filter: Optional[str] = None) -> List[InstanceSummary]:
        """List all instances for this tenant.

        Args:
            status_filter: "online" | "offline" | None (all)

        Returns: List of InstanceSummary
        """
        # TODO: Query OTEL Metrics (corvin.instance.online gauge)
        # - Filter by tenant_id
        # - Apply status_filter
        # - Enrich with geo from Resource Attributes
        logger.info(f"Query instances for tenant={self.tenant_id}, status_filter={status_filter}")
        return []

    def list_skills(self, geo_country: Optional[str] = None) -> List[SkillMetric]:
        """List skill execution metrics.

        Args:
            geo_country: Filter by country (e.g., "DE" for Germany)

        Returns: List of SkillMetric
        """
        # TODO: Query OTEL Metrics:
        # - corvin.skill.execution_duration (Histogram)
        # - corvin.skill.errors_total (Counter)
        # - Compute p50, p99, error_rate
        # - Filter by geo_country if provided
        logger.info(f"Query skills for tenant={self.tenant_id}, geo_country={geo_country}")
        return []

    def learning_status(self, skill_id: str) -> Optional[LearningStatus]:
        """Get learning loop status for a skill.

        Args:
            skill_id: Skill identifier

        Returns: LearningStatus or None
        """
        # TODO: Query Learning Optimizer state
        # - convergence_rate from convergence_meter
        # - config_updates_count from audit trail
        # - feedback_count from feedback events
        logger.info(f"Query learning status for skill={skill_id}, tenant={self.tenant_id}")
        return None

    def geo_heatmap(self, metric_type: str = "latency") -> List[GeoHeatmapCell]:
        """Get geo-distributed metrics (for world heatmap).

        Args:
            metric_type: "latency" | "error_rate" | "uptime"

        Returns: List of GeoHeatmapCell (one per country)
        """
        # TODO: Query OTEL Metrics stratified by geo.country attribute
        # - Compute avg/p99 for metric_type per country
        # - Return cells sorted by value (for color gradient)
        logger.info(f"Query geo heatmap for tenant={self.tenant_id}, metric_type={metric_type}")
        return []

    def dashboard_config(self, dashboard_name: str) -> Dict[str, Any]:
        """Get dashboard configuration (Grafana JSON model).

        Args:
            dashboard_name: "instances" | "skills" | "learning" | "geo"

        Returns: Grafana dashboard JSON
        """
        # TODO: Generate Grafana dashboard JSON from Prometheus queries
        # Common dashboards:
        # - "instances": 2x2 grid (uptime, memory, plugin count, last heartbeat)
        # - "skills": 3x2 grid (latency p99 trend, error rate, input size distribution)
        # - "learning": 2x3 grid (convergence, config updates, feedback timeline)
        # - "geo": world map + heatmap table

        if dashboard_name == "instances":
            return {
                "title": "Instance Overview",
                "panels": [
                    {"title": "Instances Online", "type": "stat", "targets": [{"metric": "corvin.instance.online"}]},
                    {"title": "Uptime (hours)", "type": "graph", "targets": [{"metric": "corvin.instance.uptime"}]},
                ],
            }
        elif dashboard_name == "skills":
            return {
                "title": "Skill Execution Metrics",
                "panels": [
                    {"title": "Latency p99", "type": "heatmap", "targets": [{"metric": "corvin.skill.execution_duration"}]},
                    {"title": "Error Rate", "type": "graph", "targets": [{"metric": "corvin.skill.errors_total"}]},
                ],
            }
        elif dashboard_name == "geo":
            return {
                "title": "Geo Distribution",
                "panels": [
                    {"title": "Instances by Country", "type": "worldmap", "targets": []},
                ],
            }

        return {}

    def export_metrics_csv(
        self,
        metric_name: str,
        time_range_hours: int = 24,
    ) -> str:
        """Export metrics as CSV (for analysis tools).

        Args:
            metric_name: Metric to export (e.g., "corvin.skill.execution_duration")
            time_range_hours: How many hours back to export

        Returns: CSV string
        """
        # TODO: Query OTEL Collector, convert to CSV
        logger.info(f"Export {metric_name} for last {time_range_hours}h (tenant={self.tenant_id})")
        return "timestamp,skill_id,latency_ms\n"  # TODO: Real data

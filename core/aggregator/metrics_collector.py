"""Live Stats Aggregator — Metrics Collection from All Tenants (ADR-0638)

Collects 9D learning metrics from all tenant instances and aggregates them
for the public stats dashboard (corvin-labs.com/stats).

Metrics collected (9D Loss Vector per ADR-0614/0615/0616):
  Tier 1 (Core Loops, weight=0.6):
    - routing: decision quality loss
    - confidence: confidence calibration loss
    - feedback: user feedback integration loss
    - attention: attention budget utilization loss
    - latency: response time loss
    - diversity: diversity of routing decisions loss

  Tier 2 (Infrastructure Loops, weight=0.3):
    - memory: memory optimization loss
    - skills: skill composition loss
    - plugins: plugin orchestration loss

  Tier 3 (Meta Loop, weight=0.1):
    - meta: self-tuning hyperparameter loss

Data sources:
  - Event store: learning/events/*.jsonl (per-tenant, per-day partitioned)
  - Instance registry: running instances + their health
  - Live collector: real-time metrics (if available)

Tenant isolation:
  - Aggregation respects tenant boundaries
  - Public stats roll up across ALL tenants (no tenant_id in response)
  - Operator can request per-tenant breakdowns (requires auth)
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from dataclasses import dataclass, asdict

from core.paths.tenant import corvin_home, tenant_home
from core.learning.event_store import EventStore

logger = logging.getLogger(__name__)


@dataclass
class TenantMetrics:
    """Aggregated metrics for a single tenant."""
    tenant_id: str
    timestamp: str
    loss_total: float

    # Tier 1: Core loops (sum should be ~6 components * weight)
    loss_routing: float
    loss_confidence: float
    loss_feedback: float
    loss_attention: float
    loss_latency: float
    loss_diversity: float

    # Tier 2: Infrastructure loops
    loss_memory: float
    loss_skills: float
    loss_plugins: float

    # Tier 3: Meta loop
    loss_meta: float

    # Metadata
    event_count: int
    last_event_time: Optional[str]
    status: str  # "no_data" | "collecting" | "learning" | "error"
    error_message: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)

    @property
    def loss_core(self) -> float:
        """Aggregated Tier 1 loss."""
        return (
            self.loss_routing +
            self.loss_confidence +
            self.loss_feedback +
            self.loss_attention +
            self.loss_latency +
            self.loss_diversity
        ) / 6.0

    @property
    def loss_infra(self) -> float:
        """Aggregated Tier 2 loss."""
        return (
            self.loss_memory +
            self.loss_skills +
            self.loss_plugins
        ) / 3.0


@dataclass
class GlobalMetrics:
    """Global aggregated metrics across all tenants."""
    timestamp: str
    instance_count: int

    # Global 9D losses (weighted average across all tenants)
    loss_total_mean: float
    loss_total_median: float
    loss_total_min: float
    loss_total_max: float
    loss_total_stddev: float

    loss_core_mean: float
    loss_infra_mean: float
    loss_meta_mean: float

    # Per-component global averages (Tier 1)
    loss_routing_mean: float
    loss_confidence_mean: float
    loss_feedback_mean: float
    loss_attention_mean: float
    loss_latency_mean: float
    loss_diversity_mean: float

    # Per-component global averages (Tier 2)
    loss_memory_mean: float
    loss_skills_mean: float
    loss_plugins_mean: float

    # Metadata
    total_events: int
    instances_healthy: int
    instances_degraded: int
    instances_error: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return asdict(self)


class MetricsCollector:
    """Collects and aggregates metrics from all tenant instances.

    Responsibilities:
      1. Discover all tenant instances
      2. Read event stores for each tenant
      3. Compute aggregated 9D metrics
      4. Track instance health and status
      5. Persist aggregated metrics for time-series queries
    """

    def __init__(self, corvin_root: Optional[Path] = None):
        """Initialize the metrics collector.

        Args:
            corvin_root: CORVIN_HOME path. If not provided, auto-detect.
        """
        self.corvin_root = Path(corvin_root) if corvin_root else corvin_home()
        self.tenants_dir = self.corvin_root / "tenants"
        self.aggregation_dir = self.corvin_root / "aggregator" / "metrics"
        self.aggregation_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"MetricsCollector initialized at {self.corvin_root}")

    def discover_tenants(self) -> List[str]:
        """Discover all tenant IDs in this CorvinOS instance.

        Returns:
            List of tenant_ids (e.g., ["_default", "tenant_a", "tenant_b"])
        """
        if not self.tenants_dir.exists():
            return []

        tenants = []
        for tenant_path in self.tenants_dir.iterdir():
            if tenant_path.is_dir() and not tenant_path.name.startswith("."):
                tenants.append(tenant_path.name)

        return sorted(tenants)

    def collect_tenant_metrics(self, tenant_id: str, lookback_hours: int = 24) -> TenantMetrics:
        """Collect aggregated metrics for a single tenant.

        Reads the event store for the tenant and computes:
        - Total loss and component losses from recent events
        - Event count and last event time
        - Health status

        Args:
            tenant_id: Tenant identifier
            lookback_hours: How far back to look (default: last 24 hours)

        Returns:
            TenantMetrics object with aggregated data
        """
        try:
            t_home = tenant_home(tenant_id)
            store = EventStore(t_home, tenant_id=tenant_id)

            # Query events from the lookback window
            since_date = (datetime.utcnow() - timedelta(hours=lookback_hours)).strftime("%Y-%m-%d")
            until_date = datetime.utcnow().strftime("%Y-%m-%d")

            events = list(store.query_events(
                tenant_id,
                since=since_date,
                until=until_date,
                limit=10000
            ))

            # Extract 9D loss components from events
            losses = self._extract_losses_from_events(events)

            # Compute aggregate metrics
            timestamp = datetime.utcnow().isoformat()
            last_event_time = None
            if events:
                # Parse the last event's timestamp
                try:
                    last_event = events[-1]
                    last_event_time = last_event.timestamp if hasattr(last_event, 'timestamp') else None
                except (AttributeError, IndexError):
                    pass

            # Determine health status
            status = "no_data"
            if events:
                if len(events) > 100:
                    status = "learning"
                else:
                    status = "collecting"

            return TenantMetrics(
                tenant_id=tenant_id,
                timestamp=timestamp,
                loss_total=losses.get("total", 0.5),
                loss_routing=losses.get("routing", 0.3),
                loss_confidence=losses.get("confidence", 0.25),
                loss_feedback=losses.get("feedback", 0.2),
                loss_attention=losses.get("attention", 0.25),
                loss_latency=losses.get("latency", 0.2),
                loss_diversity=losses.get("diversity", 0.15),
                loss_memory=losses.get("memory", 0.2),
                loss_skills=losses.get("skills", 0.25),
                loss_plugins=losses.get("plugins", 0.22),
                loss_meta=losses.get("meta", 0.0),
                event_count=len(events),
                last_event_time=last_event_time,
                status=status,
            )

        except Exception as e:
            logger.error(f"Error collecting metrics for tenant {tenant_id}: {e}", exc_info=True)
            return TenantMetrics(
                tenant_id=tenant_id,
                timestamp=datetime.utcnow().isoformat(),
                loss_total=1.0,  # Worst-case loss for error
                loss_routing=1.0,
                loss_confidence=1.0,
                loss_feedback=1.0,
                loss_attention=1.0,
                loss_latency=1.0,
                loss_diversity=1.0,
                loss_memory=1.0,
                loss_skills=1.0,
                loss_plugins=1.0,
                loss_meta=1.0,
                event_count=0,
                last_event_time=None,
                status="error",
                error_message=str(e),
            )

    def _extract_losses_from_events(self, events: List[Any]) -> Dict[str, float]:
        """Extract 9D loss components from a list of learning events.

        Looks for events with loss payloads and averages them.
        Falls back to reasonable defaults if no events carry loss data.

        Args:
            events: List of learning events from the event store

        Returns:
            Dict mapping component names to loss values (0.0-1.0 range)
        """
        losses = {
            "total": 0.5,
            "routing": 0.3,
            "confidence": 0.25,
            "feedback": 0.2,
            "attention": 0.25,
            "latency": 0.2,
            "diversity": 0.15,
            "memory": 0.2,
            "skills": 0.25,
            "plugins": 0.22,
            "meta": 0.0,
        }

        if not events:
            return losses

        # Try to extract loss data from events
        loss_totals = []
        loss_components = {k: [] for k in losses}

        for event in events:
            try:
                # Handle different event formats
                payload = None
                if isinstance(event, dict):
                    payload = event.get("payload", {})
                elif hasattr(event, "payload"):
                    payload = event.payload

                if not payload:
                    continue

                # Look for loss_total
                if "loss_total" in payload:
                    loss_totals.append(float(payload["loss_total"]))

                # Look for component losses
                if "loss_components" in payload:
                    components = payload["loss_components"]
                    if isinstance(components, dict):
                        for key, value in components.items():
                            if key in loss_components:
                                loss_components[key].append(float(value))

            except (TypeError, ValueError, AttributeError):
                # Skip events that don't have proper loss data
                continue

        # Average the extracted losses
        if loss_totals:
            losses["total"] = sum(loss_totals) / len(loss_totals)

        for component, values in loss_components.items():
            if values:
                losses[component] = sum(values) / len(values)

        return losses

    def collect_global_metrics(self) -> GlobalMetrics:
        """Collect aggregated metrics across all tenants.

        Returns:
            GlobalMetrics object with statistics across all instances
        """
        tenants = self.discover_tenants()
        tenant_metrics = []

        for tenant_id in tenants:
            metrics = self.collect_tenant_metrics(tenant_id)
            tenant_metrics.append(metrics)

        return self._aggregate_global_metrics(tenant_metrics)

    def _aggregate_global_metrics(self, tenant_metrics: List[TenantMetrics]) -> GlobalMetrics:
        """Aggregate metrics across all tenants into global statistics.

        Args:
            tenant_metrics: List of TenantMetrics objects

        Returns:
            GlobalMetrics object
        """
        import statistics

        if not tenant_metrics:
            # No tenants; return default empty stats
            return GlobalMetrics(
                timestamp=datetime.utcnow().isoformat(),
                instance_count=0,
                loss_total_mean=0.0,
                loss_total_median=0.0,
                loss_total_min=0.0,
                loss_total_max=0.0,
                loss_total_stddev=0.0,
                loss_core_mean=0.0,
                loss_infra_mean=0.0,
                loss_meta_mean=0.0,
                loss_routing_mean=0.0,
                loss_confidence_mean=0.0,
                loss_feedback_mean=0.0,
                loss_attention_mean=0.0,
                loss_latency_mean=0.0,
                loss_diversity_mean=0.0,
                loss_memory_mean=0.0,
                loss_skills_mean=0.0,
                loss_plugins_mean=0.0,
                total_events=0,
                instances_healthy=0,
                instances_degraded=0,
                instances_error=0,
            )

        # Extract loss values for statistical analysis
        loss_totals = [m.loss_total for m in tenant_metrics]
        loss_cores = [m.loss_core for m in tenant_metrics]
        loss_infras = [m.loss_infra for m in tenant_metrics]
        loss_metas = [m.loss_meta for m in tenant_metrics]

        # Component losses
        routing_losses = [m.loss_routing for m in tenant_metrics]
        confidence_losses = [m.loss_confidence for m in tenant_metrics]
        feedback_losses = [m.loss_feedback for m in tenant_metrics]
        attention_losses = [m.loss_attention for m in tenant_metrics]
        latency_losses = [m.loss_latency for m in tenant_metrics]
        diversity_losses = [m.loss_diversity for m in tenant_metrics]
        memory_losses = [m.loss_memory for m in tenant_metrics]
        skills_losses = [m.loss_skills for m in tenant_metrics]
        plugins_losses = [m.loss_plugins for m in tenant_metrics]

        # Compute statistics
        def safe_mean(values):
            return sum(values) / len(values) if values else 0.0

        def safe_median(values):
            return statistics.median(values) if values else 0.0

        def safe_stdev(values):
            return statistics.stdev(values) if len(values) > 1 else 0.0

        # Count health status
        status_counts = {}
        for metric in tenant_metrics:
            status_counts[metric.status] = status_counts.get(metric.status, 0) + 1

        return GlobalMetrics(
            timestamp=datetime.utcnow().isoformat(),
            instance_count=len(tenant_metrics),
            loss_total_mean=safe_mean(loss_totals),
            loss_total_median=safe_median(loss_totals),
            loss_total_min=min(loss_totals) if loss_totals else 0.0,
            loss_total_max=max(loss_totals) if loss_totals else 0.0,
            loss_total_stddev=safe_stdev(loss_totals),
            loss_core_mean=safe_mean(loss_cores),
            loss_infra_mean=safe_mean(loss_infras),
            loss_meta_mean=safe_mean(loss_metas),
            loss_routing_mean=safe_mean(routing_losses),
            loss_confidence_mean=safe_mean(confidence_losses),
            loss_feedback_mean=safe_mean(feedback_losses),
            loss_attention_mean=safe_mean(attention_losses),
            loss_latency_mean=safe_mean(latency_losses),
            loss_diversity_mean=safe_mean(diversity_losses),
            loss_memory_mean=safe_mean(memory_losses),
            loss_skills_mean=safe_mean(skills_losses),
            loss_plugins_mean=safe_mean(plugins_losses),
            total_events=sum(m.event_count for m in tenant_metrics),
            instances_healthy=status_counts.get("learning", 0),
            instances_degraded=status_counts.get("collecting", 0),
            instances_error=status_counts.get("error", 0),
        )

    def persist_global_metrics(self, global_metrics: GlobalMetrics) -> None:
        """Persist global metrics to time-series storage.

        Uses date-partitioned JSONL files under aggregator/metrics/

        Args:
            global_metrics: GlobalMetrics object to persist
        """
        try:
            date_str = datetime.utcnow().strftime("%Y-%m-%d")
            metrics_file = self.aggregation_dir / f"{date_str}.jsonl"

            with open(metrics_file, "a") as f:
                f.write(json.dumps(global_metrics.to_dict()) + "\n")

            logger.debug(f"Persisted global metrics to {metrics_file}")

        except Exception as e:
            logger.error(f"Error persisting global metrics: {e}", exc_info=True)

    def query_global_metrics_history(
        self,
        days: int = 7,
        limit: int = 1000
    ) -> List[GlobalMetrics]:
        """Query historical global metrics.

        Args:
            days: Look back this many days (default: 7)
            limit: Maximum number of records to return

        Returns:
            List of GlobalMetrics objects (chronological order)
        """
        result = []
        end_date = datetime.utcnow().date()
        start_date = end_date - timedelta(days=days - 1)

        current_date = start_date
        while current_date <= end_date:
            date_str = current_date.strftime("%Y-%m-%d")
            metrics_file = self.aggregation_dir / f"{date_str}.jsonl"

            if metrics_file.exists():
                try:
                    with open(metrics_file, "r") as f:
                        for line in f:
                            if line.strip():
                                data = json.loads(line)
                                # Convert dict back to GlobalMetrics
                                result.append(GlobalMetrics(**data))
                                if len(result) >= limit:
                                    return result
                except Exception as e:
                    logger.warning(f"Error reading {metrics_file}: {e}")

            current_date += timedelta(days=1)

        return result

"""OTEL Metrics Schema (ADR-0681).

Defines all metric names, attributes, and semantics for Phase 1.
"""

from dataclasses import dataclass
from typing import Dict, Any, Optional

# Metric name constants (ADR-0681)
OTEL_METRIC_INSTANCE_ONLINE = "corvin.instance.online"
OTEL_METRIC_INSTANCE_UPTIME = "corvin.instance.uptime"
OTEL_METRIC_INSTANCE_PLUGIN_COUNT = "corvin.instance.plugin_count"
OTEL_METRIC_INSTANCE_MEMORY_USAGE = "corvin.instance.memory_usage"


@dataclass(frozen=True)
class MetricsSchema:
    """Immutable schema for Phase 1 metrics."""

    # Instance signals (Phase 1)
    HEARTBEAT_ONLINE = OTEL_METRIC_INSTANCE_ONLINE  # Gauge: 0/1
    UPTIME_SECONDS = OTEL_METRIC_INSTANCE_UPTIME  # Gauge: seconds
    PLUGIN_COUNT = OTEL_METRIC_INSTANCE_PLUGIN_COUNT  # Gauge: count (per boot_layer)
    MEMORY_MB = OTEL_METRIC_INSTANCE_MEMORY_USAGE  # Gauge: bytes

    # Required Resource Attributes (Phase 1)
    REQUIRED_ATTRS = {
        "tenant_id",  # Mandatory, fail-closed if missing
        "instance_id",
        "service.name",
        "service.version",
        "deployment.environment",
    }

    # Optional Resource Attributes (geo, platform, etc.)
    GEO_ATTRS = {
        "geo.country",  # ISO 3166-1 alpha-2
        "geo.region",  # ISO 3166-2 (if granularity >= "region")
        "geo.city",  # City name (if granularity >= "city")
        "geo.granularity",  # "country" | "region" | "city" | "coordinates"
        "geo.source",  # "cloudflare" | "config" | "asn"
    }

    PLATFORM_ATTRS = {
        "platform",  # linux | darwin | windows
        "python.version",  # 3.11, etc.
        "engine_id",  # opus | sonnet | haiku
    }


OTEL_METRICS = MetricsSchema()

"""Telemetry collection for feature stability metrics (ADR-0288) + Source of Truth (ADR-0325)."""

from core.telemetry.stability_metrics import (
    mark_invocation,
    mark_error,
    compute_digest,
    FeatureStabilityEvent,
    get_flag_metrics,
)
from core.telemetry.telemetry_daemon import (
    TelemetryDaemon,
    initialize_daemon,
    get_daemon,
    send_telemetry_now,
)
from core.telemetry.source_of_truth import (
    MetricType,
    MetricContract,
    MetricValue,
    TelemetryRegistry,
)

__all__ = [
    "mark_invocation",
    "mark_error",
    "compute_digest",
    "get_flag_metrics",
    "FeatureStabilityEvent",
    "TelemetryDaemon",
    "initialize_daemon",
    "get_daemon",
    "send_telemetry_now",
    "MetricType",
    "MetricContract",
    "MetricValue",
    "TelemetryRegistry",
]

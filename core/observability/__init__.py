"""OpenTelemetry observability framework for CorvinOS.

Phase 1: OTEL SDK + Dual-Write Baseline (Heartbeat + Geo Signals)
See: ADR-0680, ADR-0681, ADR-0683
"""

from .otel_exporter.exporter import OTELExporter, OTELExportError, HeartbeatSignal, GeoAttributes
from .otel_exporter.metrics import OTEL_METRICS, MetricsSchema
from .geo_privacy.validator import GeoPrivacyValidator, PrivacyViolationError

__all__ = [
    "OTELExporter",
    "OTELExportError",
    "HeartbeatSignal",
    "GeoAttributes",
    "OTEL_METRICS",
    "MetricsSchema",
    "GeoPrivacyValidator",
    "PrivacyViolationError",
]

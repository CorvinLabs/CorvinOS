"""OTEL Exporter module for Phase 1: Heartbeat + Geo dual-write.

ADR-0680, ADR-0681: OTEL SDK + Dual-Write Baseline
"""

from .exporter import OTELExporter, OTELExportError, HeartbeatSignal, GeoAttributes
from .metrics import OTEL_METRICS, MetricsSchema

__all__ = [
    "OTELExporter",
    "OTELExportError",
    "HeartbeatSignal",
    "GeoAttributes",
    "OTEL_METRICS",
    "MetricsSchema",
]

"""Dual-Write Metrics Collector (OTEL + SQLite).

Phase 2: Skill Execution Metrics Collection
- Write metrics to SQLite (local, durable, single source of truth)
- Async export to OTEL (cloud mirror, observability)
- Learning loop reads from SQLite only (no drift)
"""

from .collector import (
    DualWriteMetricsCollector,
    SkillMetricsEvent,
    SkillExecutionStatus,
    get_metrics_collector,
)

__all__ = [
    "DualWriteMetricsCollector",
    "SkillMetricsEvent",
    "SkillExecutionStatus",
    "get_metrics_collector",
]

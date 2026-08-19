"""Measurement infrastructure for Phase 1-3 context engineering optimization.

ADR-0392 § Phase 1: Measurement

Public API:
  * CanaryRouter: deterministic tenant → group assignment
  * TokenMetric, MetricsCollector: metrics collection
  * load_metrics, compare_groups, generate_report: analysis
"""

from operator.measurement.analysis import (
    compare_groups,
    generate_report,
    load_metrics,
    split_by_group,
)
from operator.measurement.canary_router import CanaryRouter
from operator.measurement.token_metrics import MetricsCollector, TokenMetric

__all__ = [
    "CanaryRouter",
    "TokenMetric",
    "MetricsCollector",
    "load_metrics",
    "split_by_group",
    "compare_groups",
    "generate_report",
]

"""Core modules with health checks and observability (ADR-0327)."""

from core.modules.health_check import (
    HealthState,
    ModuleHealthReport,
    SystemHealthReport,
    HealthCheckEngine,
)
from core.modules.observability import (
    DashboardConfig,
    SubscriberConnection,
    ObservabilityDashboard,
)

__all__ = [
    "HealthState",
    "ModuleHealthReport",
    "SystemHealthReport",
    "HealthCheckEngine",
    "DashboardConfig",
    "SubscriberConnection",
    "ObservabilityDashboard",
]

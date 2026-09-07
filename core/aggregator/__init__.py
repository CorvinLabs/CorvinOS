"""Live Stats Aggregator — Metrics Collection & Aggregation (ADR-0638)

Collects and aggregates 9D learning metrics from all tenant instances
for the public stats dashboard (corvin-labs.com/stats).

Main components:
  - MetricsCollector: Discovers tenants and collects metrics from event stores
  - MetricsServer: Background service for periodic collection and caching
  - Stats Routes: FastAPI endpoints for public metrics access

Usage:
    # In gateway lifespan
    from core.aggregator.metrics_server import MetricsServer, set_metrics_server

    server = MetricsServer(collection_interval_sec=60)
    set_metrics_server(server)
    await server.start()
    # ... handle requests ...
    await server.stop()

    # In route handlers
    from core.aggregator.routes.stats import router as stats_router
    app.include_router(stats_router)
"""

__all__ = [
    "MetricsCollector",
    "MetricsServer",
    "TenantMetrics",
    "GlobalMetrics",
]

from core.aggregator.metrics_collector import (
    MetricsCollector,
    TenantMetrics,
    GlobalMetrics,
)
from core.aggregator.metrics_server import MetricsServer

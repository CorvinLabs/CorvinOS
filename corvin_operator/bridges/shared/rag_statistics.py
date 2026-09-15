"""RAG Statistics Aggregation - Query metrics and performance.

Aggregates statistics from registry, cache, and orchestrator.
Serves performance dashboard in Console UI.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import logging

logger = logging.getLogger(__name__)


@dataclass
class RAGStatistics:
    """Aggregated RAG statistics."""
    total_queries: int = 0
    queries_today: int = 0
    total_results_returned: int = 0
    average_latency_ms: float = 0.0
    cache_hit_rate: float = 0.0
    blocked_queries: int = 0
    providers_active: int = 0
    providers_unhealthy: int = 0
    last_updated: int = 0
    per_provider_stats: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "total_queries": self.total_queries,
            "queries_today": self.queries_today,
            "total_results_returned": self.total_results_returned,
            "average_latency_ms": round(self.average_latency_ms, 2),
            "cache_hit_rate": round(self.cache_hit_rate * 100, 1),
            "blocked_queries": self.blocked_queries,
            "providers_active": self.providers_active,
            "providers_unhealthy": self.providers_unhealthy,
            "last_updated": self.last_updated,
            "per_provider_stats": self.per_provider_stats,
        }


class RAGStatisticsAggregator:
    """Aggregate RAG statistics from multiple sources."""

    def __init__(self, registry_dir: Path):
        self.registry_dir = registry_dir
        # Read the SAME per-tenant L16 forge hash chain the RAG audit emitter
        # writes to: <tenant_home>/global/forge/audit.jsonl. The caller passes
        # registry_dir = <tenant_home>/global/rag, so the forge chain is its
        # sibling. The old standalone <tenant_home>/audit/rag_audit.jsonl path
        # is dead — the emitter routes RAG events into the forge chain (see
        # rag_audit_events.RAGAuditEmitter._chain_path), so the aggregator must
        # read there or it permanently reports zero. Filtering by event_type
        # below is correct: the chain interleaves all layers.
        self.audit_file = registry_dir.parent / "forge" / "audit.jsonl"

    def aggregate(self) -> RAGStatistics:
        """Aggregate current statistics from all sources.

        Returns:
            RAGStatistics with current metrics
        """
        stats = RAGStatistics()

        try:
            # Load registry for provider stats
            registry_file = self.registry_dir / "registry.json"
            if registry_file.exists():
                with open(registry_file) as f:
                    registry = json.load(f)

                # Aggregate per-provider stats
                for provider in registry.get("providers", []):
                    prov_id = provider["id"]
                    prov_stats = provider.get("query_stats", {})

                    stats.total_queries += prov_stats.get("total_queries", 0)
                    stats.queries_today += prov_stats.get("queries_today", 0)

                    # Track per-provider
                    stats.per_provider_stats[prov_id] = {
                        "name": provider.get("name"),
                        "status": provider.get("status"),
                        "health": provider.get("health_status"),
                        "total_queries": prov_stats.get("total_queries", 0),
                        "avg_latency_ms": prov_stats.get("average_latency_ms", 0),
                    }

                    if provider.get("health_status") == "healthy":
                        stats.providers_active += 1
                    else:
                        stats.providers_unhealthy += 1

            # Load audit events for query metrics
            if self.audit_file.exists():
                latencies = []
                cache_hits = 0
                total_audit_queries = 0

                with open(self.audit_file) as f:
                    for line in f:
                        try:
                            event = json.loads(line)
                            details = event.get("details", {})

                            if event.get("event_type") == "rag.query_executed":
                                latencies.append(details.get("latency_ms", 0))
                                total_audit_queries += 1
                                if details.get("cache_hit"):
                                    cache_hits += 1
                                stats.total_results_returned += details.get(
                                    "result_count", 0
                                )

                            elif event.get("event_type") == "rag.query_blocked":
                                stats.blocked_queries += 1
                        except json.JSONDecodeError:
                            continue

                # Calculate averages
                if latencies:
                    stats.average_latency_ms = sum(latencies) / len(latencies)

                if total_audit_queries > 0:
                    stats.cache_hit_rate = cache_hits / total_audit_queries

        except Exception as e:
            logger.warning(f"Failed to aggregate statistics: {e}")

        stats.last_updated = int(__import__("time").time() * 1000)
        return stats

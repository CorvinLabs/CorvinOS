"""Live Stats API (corvin-labs.com/stats) — All Instances, Real-Time

Aggregates telemetry from all CorvinOS instances:
- Instance health (uptime, request count, errors)
- Model usage (Haiku/Sonnet/Opus costs)
- Geographic distribution (world map)
- Learning metrics (confidence, convergence)
"""

from fastapi import APIRouter, Query
from typing import Optional, List
from dataclasses import dataclass, asdict
from datetime import datetime, timedelta
import asyncio

router = APIRouter(prefix="/v1/stats", tags=["stats"])


@dataclass
class InstanceStats:
    instance_id: str
    region: str
    latitude: float
    longitude: float
    uptime_hours: int
    total_requests: int
    error_count: int
    error_rate_pct: float
    models_used: dict  # {"haiku": 150, "sonnet": 45, "opus": 12}
    cost_usd: float
    avg_latency_ms: float
    learning_confidence: float
    last_heartbeat_iso: str


@dataclass
class GlobalStats:
    total_instances: int
    total_requests: int
    total_errors: int
    global_error_rate_pct: float
    total_cost_usd: float
    avg_confidence: float
    instances: List[InstanceStats]
    timestamp: str


class StatsAggregator:
    """Aggregates telemetry from all instances (real-time)."""
    
    def __init__(self):
        self.instances = {}  # instance_id → InstanceStats
    
    async def collect_from_all_instances(self) -> GlobalStats:
        """Gather stats from running instances."""
        # TODO: Query Prometheus/Grafana or direct instance APIs
        # For now: mock data (replace with real collection)
        instances = [
            InstanceStats(
                instance_id="corvin-prod-us-west-1",
                region="us-west-1",
                latitude=37.7749,
                longitude=-122.4194,
                uptime_hours=720,
                total_requests=145230,
                error_count=87,
                error_rate_pct=0.06,
                models_used={"haiku": 89450, "sonnet": 42130, "opus": 13650},
                cost_usd=1247.50,
                avg_latency_ms=245,
                learning_confidence=0.876,
                last_heartbeat_iso=datetime.utcnow().isoformat() + "Z"
            ),
            InstanceStats(
                instance_id="corvin-prod-eu-west-1",
                region="eu-west-1",
                latitude=51.5074,
                longitude=-0.1278,
                uptime_hours=715,
                total_requests=98540,
                error_count=42,
                error_rate_pct=0.04,
                models_used={"haiku": 54230, "sonnet": 31120, "opus": 13190},
                cost_usd=892.30,
                avg_latency_ms=198,
                learning_confidence=0.891,
                last_heartbeat_iso=datetime.utcnow().isoformat() + "Z"
            ),
            InstanceStats(
                instance_id="corvin-prod-apac-sg",
                region="ap-southeast-1",
                latitude=1.3521,
                longitude=103.8198,
                uptime_hours=710,
                total_requests=67340,
                error_count=31,
                error_rate_pct=0.05,
                models_used={"haiku": 42100, "sonnet": 18950, "opus": 6290},
                cost_usd=521.45,
                avg_latency_ms=267,
                learning_confidence=0.843,
                last_heartbeat_iso=datetime.utcnow().isoformat() + "Z"
            )
        ]
        
        total_requests = sum(i.total_requests for i in instances)
        total_errors = sum(i.error_count for i in instances)
        total_cost = sum(i.cost_usd for i in instances)
        avg_confidence = sum(i.learning_confidence for i in instances) / len(instances)
        
        return GlobalStats(
            total_instances=len(instances),
            total_requests=total_requests,
            total_errors=total_errors,
            global_error_rate_pct=(total_errors / total_requests * 100) if total_requests > 0 else 0,
            total_cost_usd=total_cost,
            avg_confidence=avg_confidence,
            instances=instances,
            timestamp=datetime.utcnow().isoformat() + "Z"
        )


@router.get("/live")
async def get_live_stats(
    include_geo: bool = Query(True, description="Include geographic data"),
    include_models: bool = Query(True, description="Include model breakdown")
) -> dict:
    """Get real-time stats for all CorvinOS instances."""
    aggregator = StatsAggregator()
    stats = await aggregator.collect_from_all_instances()
    
    result = {
        "summary": {
            "total_instances": stats.total_instances,
            "total_requests": stats.total_requests,
            "total_errors": stats.total_errors,
            "error_rate_pct": stats.global_error_rate_pct,
            "total_cost_usd": round(stats.total_cost_usd, 2),
            "avg_confidence": round(stats.avg_confidence, 3),
            "timestamp": stats.timestamp
        },
        "instances": [asdict(i) for i in stats.instances]
    }
    
    if not include_models:
        for instance in result["instances"]:
            instance.pop("models_used", None)
    
    if not include_geo:
        for instance in result["instances"]:
            instance.pop("latitude", None)
            instance.pop("longitude", None)
    
    return result


@router.get("/live/geographic")
async def get_geographic_distribution() -> dict:
    """Get geographic distribution for world map."""
    aggregator = StatsAggregator()
    stats = await aggregator.collect_from_all_instances()
    
    return {
        "map_data": [
            {
                "instance_id": i.instance_id,
                "region": i.region,
                "lat": i.latitude,
                "lng": i.longitude,
                "requests": i.total_requests,
                "cost": i.cost_usd,
                "health": "healthy" if i.error_rate_pct < 0.1 else "warning"
            }
            for i in stats.instances
        ],
        "bounds": {
            "north": 51.51,
            "south": 1.35,
            "west": -122.42,
            "east": 103.82
        }
    }


@router.get("/live/models")
async def get_model_usage_breakdown() -> dict:
    """Get model usage across all instances."""
    aggregator = StatsAggregator()
    stats = await aggregator.collect_from_all_instances()
    
    total_haiku = sum(i.models_used.get("haiku", 0) for i in stats.instances)
    total_sonnet = sum(i.models_used.get("sonnet", 0) for i in stats.instances)
    total_opus = sum(i.models_used.get("opus", 0) for i in stats.instances)
    
    total = total_haiku + total_sonnet + total_opus
    
    return {
        "model_usage": {
            "haiku": total_haiku,
            "sonnet": total_sonnet,
            "opus": total_opus,
            "total": total
        },
        "distribution_pct": {
            "haiku": round((total_haiku / total * 100), 1) if total > 0 else 0,
            "sonnet": round((total_sonnet / total * 100), 1) if total > 0 else 0,
            "opus": round((total_opus / total * 100), 1) if total > 0 else 0
        }
    }


@router.get("/live/health")
async def get_health_status() -> dict:
    """Get health summary for all instances."""
    aggregator = StatsAggregator()
    stats = await aggregator.collect_from_all_instances()
    
    healthy = sum(1 for i in stats.instances if i.error_rate_pct < 0.1)
    warning = sum(1 for i in stats.instances if 0.1 <= i.error_rate_pct < 0.5)
    critical = sum(1 for i in stats.instances if i.error_rate_pct >= 0.5)
    
    return {
        "status": "healthy" if critical == 0 else "degraded",
        "healthy_instances": healthy,
        "warning_instances": warning,
        "critical_instances": critical,
        "avg_uptime_hours": sum(i.uptime_hours for i in stats.instances) / len(stats.instances),
        "avg_latency_ms": sum(i.avg_latency_ms for i in stats.instances) / len(stats.instances)
    }

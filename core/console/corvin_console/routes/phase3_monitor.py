"""Phase 3 Complexity Classifier Performance Monitor — confidence-score telemetry (ADR-0902).

Exposes real-time performance metrics for the ComplexityJudge:
- Confidence distribution (simple/medium/complex)
- Model routing statistics
- Error rates and latency P95/P99

Audits all decisions via audit-backend (GDPR Art. 30, 32).
"""
from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from .. import feature_flags
from ..deps import require_session

log = logging.getLogger(__name__)

router = APIRouter()


class ComplexityMetric(BaseModel):
    """Single complexity-level metric."""
    level: str = Field(..., description="simple | medium | complex")
    count: int = Field(default=0, description="Number of decisions at this level")
    avg_confidence: float = Field(default=0.0, description="Average confidence (0.0-1.0)")
    avg_latency_ms: float = Field(default=0.0, description="Average latency in milliseconds")


class ModelRoutingMetric(BaseModel):
    """Model routing distribution."""
    model: str = Field(..., description="haiku | sonnet | opus")
    count: int = Field(default=0, description="Number of tasks routed to this model")
    percentage: float = Field(default=0.0, description="Percentage of total routing (0-100)")


class Phase3PerformanceResponse(BaseModel):
    """Phase 3 monitor response schema."""
    window: str = Field(..., description="Time window (24h, 7d, etc.)")
    timestamp_utc: str = Field(..., description="ISO 8601 timestamp")
    metrics: dict[str, Any] = Field(
        default_factory=dict,
        description="Aggregated metrics"
    )
    by_complexity: dict[str, ComplexityMetric] = Field(
        default_factory=dict,
        description="Metrics by complexity level"
    )
    by_model: list[ModelRoutingMetric] = Field(
        default_factory=list,
        description="Model routing statistics"
    )
    error_rate: float = Field(default=0.0, description="Error rate (0.0-1.0)")
    latency_p95_ms: float = Field(default=0.0, description="P95 latency in milliseconds")


def _read_audit_trail_metrics(hours: int = 24) -> dict[str, Any]:
    """Query audit trail for Phase 3 decision metrics.

    Reads ~/.corvin/audit.jsonl for routing_decision_made events
    and aggregates confidence/model/complexity statistics.
    """
    audit_path = Path.home() / ".corvin" / "audit.jsonl"
    if not audit_path.exists():
        return {
            "decisions_made": 0,
            "avg_confidence": 0.0,
            "error_count": 0,
            "by_complexity": {},
            "by_model": {},
            "latencies_ms": [],
        }

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    metrics = {
        "decisions_made": 0,
        "confidence_scores": [],
        "error_count": 0,
        "by_complexity": defaultdict(lambda: {"count": 0, "confidence": [], "latency": []}),
        "by_model": defaultdict(lambda: {"count": 0}),
        "latencies_ms": [],
    }

    try:
        with open(audit_path, "r") as f:
            for line in f:
                try:
                    event = json.loads(line.strip())
                    if not event.get("timestamp_utc"):
                        continue

                    # Parse timestamp
                    try:
                        event_time = datetime.fromisoformat(
                            event["timestamp_utc"].replace("Z", "+00:00")
                        )
                    except:
                        continue

                    # Only recent events
                    if event_time < cutoff:
                        continue

                    # Routing decision events
                    if event.get("event_type") == "routing_decision_made":
                        metrics["decisions_made"] += 1

                        # Confidence
                        confidence = event.get("confidence", 0.0)
                        metrics["confidence_scores"].append(confidence)

                        # Complexity level
                        complexity = event.get("complexity_level", "unknown")
                        metrics["by_complexity"][complexity]["count"] += 1
                        metrics["by_complexity"][complexity]["confidence"].append(confidence)

                        # Model routed
                        model = event.get("model_routed", "unknown")
                        metrics["by_model"][model]["count"] += 1

                        # Latency
                        latency = event.get("latency_ms", 0)
                        metrics["latencies_ms"].append(latency)
                        metrics["by_complexity"][complexity]["latency"].append(latency)

                    elif event.get("event_type") == "routing_decision_error":
                        metrics["error_count"] += 1

                except json.JSONDecodeError:
                    continue
    except Exception as e:
        log.warning(f"Error reading audit trail: {e}")

    return metrics


@router.get("/monitor/phase3-performance", response_model=Phase3PerformanceResponse)
async def get_phase3_performance(
    _: Any = Depends(require_session),
    window: str = "24h",
) -> Phase3PerformanceResponse:
    """Get Phase 3 Complexity Classifier performance metrics.

    Returns real-time confidence scores, model routing distribution,
    and error rates for the last 24 hours (or specified window).

    **Window options:**
    - `24h` — last 24 hours (default)
    - `7d` — last 7 days
    - `30d` — last 30 days

    **Response includes:**
    - `metrics.decisions_made` — total routing decisions
    - `metrics.avg_confidence` — average confidence score (0.0-1.0)
    - `by_complexity` — metrics aggregated by complexity level
    - `by_model` — model routing distribution
    - `error_rate` — percentage of failed decisions
    - `latency_p95_ms` — 95th percentile latency
    """
    # Parse window to hours
    window_hours = {"24h": 24, "7d": 7 * 24, "30d": 30 * 24}.get(window, 24)

    # Read audit trail metrics
    audit_data = _read_audit_trail_metrics(hours=window_hours)

    # Calculate aggregates
    decisions = audit_data["decisions_made"]
    error_count = audit_data["error_count"]
    error_rate = error_count / (decisions + error_count) if (decisions + error_count) > 0 else 0.0

    avg_confidence = (
        sum(audit_data["confidence_scores"]) / len(audit_data["confidence_scores"])
        if audit_data["confidence_scores"]
        else 0.0
    )

    # Latency percentiles
    latencies = sorted(audit_data["latencies_ms"])
    latency_p95 = latencies[int(len(latencies) * 0.95)] if latencies else 0.0
    latency_p99 = latencies[int(len(latencies) * 0.99)] if latencies else 0.0

    # Build response
    by_complexity = {}
    for level, data in audit_data["by_complexity"].items():
        count = data["count"]
        confidence_scores = data["confidence"]
        latency_scores = data["latency"]

        by_complexity[level] = ComplexityMetric(
            level=level,
            count=count,
            avg_confidence=sum(confidence_scores) / len(confidence_scores) if confidence_scores else 0.0,
            avg_latency_ms=sum(latency_scores) / len(latency_scores) if latency_scores else 0.0,
        )

    # Model routing
    by_model = []
    total_model_decisions = sum(m["count"] for m in audit_data["by_model"].values())
    for model, data in audit_data["by_model"].items():
        count = data["count"]
        percentage = (count / total_model_decisions * 100) if total_model_decisions > 0 else 0.0
        by_model.append(
            ModelRoutingMetric(model=model, count=count, percentage=percentage)
        )

    # Sort by count (descending)
    by_model.sort(key=lambda x: x.count, reverse=True)

    return Phase3PerformanceResponse(
        window=window,
        timestamp_utc=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        metrics={
            "decisions_made": decisions,
            "avg_confidence": round(avg_confidence, 3),
            "error_count": error_count,
            "error_rate": round(error_rate, 4),
            "latency_p95_ms": round(latency_p95, 1),
            "latency_p99_ms": round(latency_p99, 1),
        },
        by_complexity=by_complexity,
        by_model=by_model,
        error_rate=round(error_rate, 4),
        latency_p95_ms=round(latency_p95, 1),
    )

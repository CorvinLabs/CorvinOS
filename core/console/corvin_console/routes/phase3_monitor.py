"""Phase 3 Complexity Classifier Performance Monitor — confidence-score telemetry (ADR-0902).

Exposes real-time performance metrics for the ComplexityJudge:
- Confidence distribution (simple/medium/complex)
- Model routing statistics
- Error rates and latency P95/P99

Audits all decisions via audit-backend (GDPR Art. 30, 32).
"""
from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from core.paths import tenant_audit_chain

from .. import auth as session_auth
from ..chain_tail import DEFAULT_MAX_SCAN_BYTES, iter_chain_tail, scan_window
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
    scan: dict[str, Any] = Field(
        default_factory=dict,
        description=(
            "What the audit-chain tail scan covered: truncated (the requested "
            "window reaches further back than the scanned part), scanned_bytes, "
            "total_bytes, covers_since"
        ),
    )
    coverage_note: str | None = Field(
        default=None,
        description="Plain-text statement when the result is partial; null when complete",
    )


_ROUTING_EVENTS = ("routing_decision_made", "routing_decision_error")


def _record_time(event: dict[str, Any]) -> datetime | None:
    """A chain record's time: epoch ``ts`` (what the audit writer stamps) or an
    ISO ``timestamp_utc`` (older shape). None when neither parses."""
    ts = event.get("ts")
    if isinstance(ts, (int, float)) and not isinstance(ts, bool):
        try:
            return datetime.fromtimestamp(float(ts), tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    raw = event.get("timestamp_utc")
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
    return None


def _num(value: Any, default: float = 0.0) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return default
    return float(value)


def _coverage_note(scan: dict[str, Any], window: str) -> str | None:
    """Plain-text statement for a partial result, None when it is complete."""
    if not scan.get("truncated"):
        return None
    since = scan.get("covers_since") or "an unknown time"
    return (
        f"Partial result: the audit chain is larger than the scanned window, so the "
        f"figures cover records since {since}, not the full {window}."
    )


def _names_other_tenant(event: dict[str, Any], tenant_id: str) -> bool:
    """True when the record names a tenant other than *tenant_id*.

    A chain record carries the tenant in ``details.tenant_id``; the top-level
    key is the older flat shape. Only the top level was checked, so a record
    that named another tenant where chain records actually name it was counted.
    Both places are checked; either one naming another tenant drops the record.
    """
    details = event.get("details")
    candidates = [event.get("tenant_id")]
    if isinstance(details, dict):
        candidates.append(details.get("tenant_id"))
    return any(
        isinstance(value, str) and value and value != tenant_id for value in candidates
    )


def _read_audit_trail_metrics(
    tenant_id: str, hours: int = 24, *,
    max_scan_bytes: int = DEFAULT_MAX_SCAN_BYTES,
) -> dict[str, Any]:
    """Aggregate Phase 3 routing-decision records of ONE tenant.

    Reads the tenant's own chain (``tenant_audit_chain(tenant_id)``). It used
    to read ``~/.corvin/audit.jsonl`` — a path that ignores ``CORVIN_HOME`` and
    the tenant, so every session saw the same (usually absent) file.

    BLOCKING: streams the last ``max_scan_bytes`` of the chain line by line;
    the async handler awaits it through ``asyncio.to_thread``. A record that
    names a different tenant is dropped.
    """
    metrics: dict[str, Any] = {
        "decisions_made": 0,
        "confidence_scores": [],
        "error_count": 0,
        "by_complexity": defaultdict(lambda: {"count": 0, "confidence": [], "latency": []}),
        "by_model": defaultdict(lambda: {"count": 0}),
        "latencies_ms": [],
        # What the tail scan covered; ``truncated`` = the requested time
        # window reaches further back than the scanned part of the chain.
        "scan": {"truncated": False, "scanned_bytes": 0, "total_bytes": 0, "covers_since": None},
    }
    try:
        audit_path = tenant_audit_chain(tenant_id)
    except Exception:  # noqa: BLE001 - invalid tenant id: nothing to read
        return metrics

    cutoff = datetime.now(timezone.utc) - timedelta(hours=hours)
    try:
        scan = scan_window(audit_path, max_scan_bytes=max_scan_bytes)
        if scan["truncated"]:
            first = next(iter_chain_tail(audit_path, max_scan_bytes=max_scan_bytes), None)
            first_time = _record_time(first) if first is not None else None
            # Only a window that starts AFTER the cutoff hides records.
            scan["truncated"] = first_time is None or first_time > cutoff
        metrics["scan"] = scan
    except Exception as e:  # noqa: BLE001
        log.warning("Error measuring the scan window: %s", type(e).__name__)
    try:
        for event in iter_chain_tail(
            audit_path, max_scan_bytes=max_scan_bytes,
            needles=tuple(f'"{name}"' for name in _ROUTING_EVENTS),
        ):
            event_type = event.get("event_type")
            if event_type not in _ROUTING_EVENTS:
                continue
            if _names_other_tenant(event, tenant_id):
                continue
            event_time = _record_time(event)
            if event_time is None or event_time < cutoff:
                continue

            if event_type == "routing_decision_error":
                metrics["error_count"] += 1
                continue

            # Fields live in ``details`` on a chain record; top level is the
            # older flat shape.
            details = event.get("details")
            src = details if isinstance(details, dict) else event
            confidence = _num(src.get("confidence", event.get("confidence")))
            latency = _num(src.get("latency_ms", event.get("latency_ms")))
            complexity = str(src.get("complexity_level") or event.get("complexity_level") or "unknown")
            model = str(src.get("model_routed") or event.get("model_routed") or "unknown")

            metrics["decisions_made"] += 1
            metrics["confidence_scores"].append(confidence)
            metrics["by_complexity"][complexity]["count"] += 1
            metrics["by_complexity"][complexity]["confidence"].append(confidence)
            metrics["by_complexity"][complexity]["latency"].append(latency)
            metrics["by_model"][model]["count"] += 1
            metrics["latencies_ms"].append(latency)
    except Exception as e:  # noqa: BLE001
        log.warning("Error reading audit trail: %s", e)

    return metrics


@router.get("/monitor/phase3-performance", response_model=Phase3PerformanceResponse)
async def get_phase3_performance(
    rec: session_auth.SessionRecord = Depends(require_session),
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
    # Tenant from the authenticated session; blocking scan awaited off the loop.
    audit_data = await asyncio.to_thread(
        _read_audit_trail_metrics, rec.tenant_id, window_hours,
    )

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
    latency_p95 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.95))] if latencies else 0.0
    latency_p99 = latencies[min(len(latencies) - 1, int(len(latencies) * 0.99))] if latencies else 0.0

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
        scan=audit_data.get("scan") or {},
        coverage_note=_coverage_note(audit_data.get("scan") or {}, window),
    )

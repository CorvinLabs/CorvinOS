"""Data models for Production Monitoring Dashboard (ADR-0906).

Defines all request/response Pydantic models for:
  - System health score aggregation
  - Autonomous Forge statistics
  - Per-skill performance metrics
  - Alerts and real-time status updates
  - Historical time series data
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# System Health Models
# ─────────────────────────────────────────────────────────────────────────────


class SystemHealthResponse(BaseModel):
    """Overall system health score and status (0–100)."""

    health_score: float = Field(
        ...,
        ge=0.0,
        le=100.0,
        description="Aggregated health score: 0-33=critical, 34-66=degraded, 67-100=healthy",
    )
    status: str = Field(
        ...,
        description="Health status: 'healthy' | 'degraded' | 'critical'",
    )
    uptime_hours: float = Field(..., ge=0, description="System uptime in hours")
    last_check: datetime = Field(..., description="When health was last checked")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "health_score": 87.5,
                "status": "healthy",
                "uptime_hours": 168.25,
                "last_check": "2026-09-20T12:30:00Z",
                "timestamp": "2026-09-20T12:30:45Z",
                "tenant_id": "_default",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Autonomous Forge Statistics
# ─────────────────────────────────────────────────────────────────────────────


class AutonomousStatsResponse(BaseModel):
    """Autonomous Skill Forge statistics: skills forged, success rate, optimization progress."""

    skills_forged: int = Field(..., ge=0, description="Total number of skills autonomously generated")
    skills_approved: int = Field(..., ge=0, description="Number of approved and rolled out")
    skills_deferred: int = Field(..., ge=0, description="Number deferred for later review")
    skills_active: int = Field(..., ge=0, description="Currently active canary deployments")
    success_rate: float = Field(
        ..., ge=0.0, le=1.0, description="Fraction of forked skills that passed validation"
    )
    avg_optimization_confidence: float = Field(
        ..., ge=0.0, le=1.0, description="Mean confidence score across all forged skills"
    )
    avg_latency_ms: float = Field(..., ge=0, description="Mean latency of forged skills (ms)")
    avg_error_rate: float = Field(
        ..., ge=0.0, le=1.0, description="Mean error rate across forged skills"
    )
    last_fork_timestamp: Optional[datetime] = Field(None, description="When the last skill was forked")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "skills_forged": 12,
                "skills_approved": 9,
                "skills_deferred": 2,
                "skills_active": 1,
                "success_rate": 0.92,
                "avg_optimization_confidence": 0.88,
                "avg_latency_ms": 52.3,
                "avg_error_rate": 0.015,
                "last_fork_timestamp": "2026-09-20T11:45:00Z",
                "timestamp": "2026-09-20T12:30:45Z",
                "tenant_id": "_default",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Per-Skill Performance
# ─────────────────────────────────────────────────────────────────────────────


class SkillPerformanceMetric(BaseModel):
    """Performance metrics for a single skill."""

    skill_id: str = Field(..., description="Unique skill identifier (e.g., 'os.delegation_router')")
    version: str = Field(..., description="Current deployed version")
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score [0, 1]")
    latency_p50_ms: float = Field(..., ge=0, description="Median latency (ms)")
    latency_p95_ms: float = Field(..., ge=0, description="95th percentile latency (ms)")
    latency_p99_ms: float = Field(..., ge=0, description="99th percentile latency (ms)")
    error_rate: float = Field(..., ge=0.0, le=1.0, description="Error rate [0, 1]")
    execution_count: int = Field(..., ge=0, description="Total number of executions in window")
    last_error: Optional[str] = Field(None, max_length=500, description="Description of last error")
    status: str = Field(
        ..., description="Current status: 'healthy' | 'degraded' | 'critical' | 'inactive'"
    )
    last_updated: datetime = Field(..., description="Last metric update time")

    class Config:
        json_schema_extra = {
            "example": {
                "skill_id": "os.delegation_router",
                "version": "2.1.0",
                "confidence": 0.92,
                "latency_p50_ms": 38.2,
                "latency_p95_ms": 52.5,
                "latency_p99_ms": 68.1,
                "error_rate": 0.008,
                "execution_count": 4250,
                "last_error": None,
                "status": "healthy",
                "last_updated": "2026-09-20T12:30:00Z",
            }
        }


class SkillPerformanceResponse(BaseModel):
    """Per-skill performance metrics (table view)."""

    skills: List[SkillPerformanceMetric] = Field(..., description="List of all monitored skills")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "skills": [
                    {
                        "skill_id": "os.delegation_router",
                        "version": "2.1.0",
                        "confidence": 0.92,
                        "latency_p50_ms": 38.2,
                        "latency_p95_ms": 52.5,
                        "latency_p99_ms": 68.1,
                        "error_rate": 0.008,
                        "execution_count": 4250,
                        "last_error": None,
                        "status": "healthy",
                        "last_updated": "2026-09-20T12:30:00Z",
                    }
                ],
                "timestamp": "2026-09-20T12:30:45Z",
                "tenant_id": "_default",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Alerts
# ─────────────────────────────────────────────────────────────────────────────


class AlertEvent(BaseModel):
    """A single alert event."""

    alert_id: str = Field(..., description="Unique alert identifier")
    severity: str = Field(
        ..., description="Alert severity: 'info' | 'warning' | 'critical'"
    )
    title: str = Field(..., max_length=200, description="Alert title")
    message: str = Field(..., max_length=1000, description="Detailed alert message")
    skill_id: Optional[str] = Field(None, description="Associated skill (if applicable)")
    metric_name: Optional[str] = Field(None, description="Metric that triggered alert (e.g., 'error_rate')")
    metric_value: Optional[float] = Field(None, description="Value that triggered alert")
    threshold: Optional[float] = Field(None, description="Alert threshold")
    created_at: datetime = Field(..., description="When alert was generated")
    resolved_at: Optional[datetime] = Field(None, description="When alert was resolved (null=active)")

    class Config:
        json_schema_extra = {
            "example": {
                "alert_id": "alert-20260920-001",
                "severity": "warning",
                "title": "High Error Rate on os.context_adapter",
                "message": "Error rate exceeded 5% threshold (current: 7.2%)",
                "skill_id": "os.context_adapter",
                "metric_name": "error_rate",
                "metric_value": 0.072,
                "threshold": 0.05,
                "created_at": "2026-09-20T12:15:00Z",
                "resolved_at": None,
            }
        }


class AlertsResponse(BaseModel):
    """Active alerts and recent alert history."""

    active_alerts: List[AlertEvent] = Field(..., description="Currently active (unresolved) alerts")
    recent_alerts: List[AlertEvent] = Field(
        ..., description="Recently resolved alerts (last 24 hours)"
    )
    total_active: int = Field(..., ge=0, description="Total number of active alerts")
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "active_alerts": [
                    {
                        "alert_id": "alert-20260920-001",
                        "severity": "warning",
                        "title": "High Error Rate on os.context_adapter",
                        "message": "Error rate exceeded 5% threshold (current: 7.2%)",
                        "skill_id": "os.context_adapter",
                        "metric_name": "error_rate",
                        "metric_value": 0.072,
                        "threshold": 0.05,
                        "created_at": "2026-09-20T12:15:00Z",
                        "resolved_at": None,
                    }
                ],
                "recent_alerts": [],
                "total_active": 1,
                "timestamp": "2026-09-20T12:30:45Z",
                "tenant_id": "_default",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Time Series Data
# ─────────────────────────────────────────────────────────────────────────────


class TimeSeriesDataPoint(BaseModel):
    """A single time series data point."""

    timestamp: datetime = Field(..., description="Point timestamp (UTC)")
    value: float = Field(..., description="Metric value at this timestamp")
    skill_id: Optional[str] = Field(None, description="Associated skill (if metric-specific)")


class TimeSeriesResponse(BaseModel):
    """Historical time series data for trend visualization."""

    metric_name: str = Field(..., description="Metric name (e.g., 'confidence', 'latency_p95_ms', 'error_rate')")
    data_points: List[TimeSeriesDataPoint] = Field(..., description="Time series data points (oldest first)")
    window_start: datetime = Field(..., description="Start of time window (UTC)")
    window_end: datetime = Field(..., description="End of time window (UTC)")
    aggregation_interval_sec: int = Field(
        ..., ge=1, description="Interval between data points (seconds)"
    )
    timestamp: datetime = Field(..., description="Response timestamp (UTC)")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "metric_name": "confidence",
                "data_points": [
                    {"timestamp": "2026-09-20T06:00:00Z", "value": 0.85, "skill_id": "os.delegation_router"},
                    {"timestamp": "2026-09-20T07:00:00Z", "value": 0.87, "skill_id": "os.delegation_router"},
                    {"timestamp": "2026-09-20T08:00:00Z", "value": 0.91, "skill_id": "os.delegation_router"},
                ],
                "window_start": "2026-09-20T00:00:00Z",
                "window_end": "2026-09-21T00:00:00Z",
                "aggregation_interval_sec": 3600,
                "timestamp": "2026-09-20T12:30:45Z",
                "tenant_id": "_default",
            }
        }

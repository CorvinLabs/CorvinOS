"""Data models for Autonomous Skill Forge Console Integration (ADR-0902).

Defines all request/response Pydantic models for:
  - Canary state monitoring
  - Operator decisions (approve, defer, pause, resume, rollback)
  - Audit trail queries
  - Manifest viewing
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, List, Optional
from pydantic import BaseModel, Field


# ─────────────────────────────────────────────────────────────────────────────
# Canary & Monitoring Models
# ─────────────────────────────────────────────────────────────────────────────


class CanaryStateResponse(BaseModel):
    """Current canary deployment state for a single forked skill.

    Immutable metrics from the canary monitor. No mutations allowed on this resource.
    """
    skill_id: str = Field(..., description="Unique skill identifier (e.g., 'os.delegation_router')")
    version: str = Field(..., description="Semantic version of the skill (e.g., '2.1.0')")
    status: str = Field(
        ...,
        description="Canary deployment phase: 'canary' | 'approved' | 'deferred' | 'paused' | 'rolledback'",
    )
    confidence: float = Field(..., ge=0.0, le=1.0, description="Confidence score [0, 1]")
    latency_p95_ms: float = Field(..., description="95th percentile latency in milliseconds")
    error_rate: float = Field(..., ge=0.0, le=1.0, description="Error rate as fraction [0, 1]")
    traffic_percent: int = Field(..., ge=0, le=100, description="Canary traffic allocation [0, 100]")
    time_remaining_sec: int = Field(..., ge=0, description="Seconds until canary expires or decision required")
    created_at: datetime = Field(..., description="When the canary was created")
    tenant_id: str = Field(..., description="Tenant scope (immutable; from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "skill_id": "os.delegation_router",
                "version": "2.1.0",
                "status": "canary",
                "confidence": 0.92,
                "latency_p95_ms": 48.5,
                "error_rate": 0.002,
                "traffic_percent": 15,
                "time_remaining_sec": 3600,
                "created_at": "2026-09-20T12:34:56Z",
                "tenant_id": "_default",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Operator Decision Models (Request + Response)
# ─────────────────────────────────────────────────────────────────────────────


class ApproveRequest(BaseModel):
    """Request to approve a canary and roll out to 100% traffic."""
    skill_id: str = Field(..., description="Skill ID to approve")
    version: str = Field(..., description="Skill version to approve")
    operator_id: str = Field(..., description="Operator fingerprint (from auth)")

    class Config:
        extra = "forbid"


class ApproveResponse(BaseModel):
    """Response after approval."""
    status: str = Field(default="approved", description="Status after approval")
    rolled_out_at: datetime = Field(..., description="Timestamp of 100% rollout")
    audit_event_id: str = Field(..., description="Audit trail event ID")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "approved",
                "rolled_out_at": "2026-09-20T13:00:00Z",
                "audit_event_id": "audit-evt-abc123def456",
            }
        }


class DeferRequest(BaseModel):
    """Request to defer a canary to a later time."""
    skill_id: str = Field(..., description="Skill ID to defer")
    version: str = Field(..., description="Skill version to defer")
    reason: str = Field(..., max_length=500, description="Why the canary is being deferred")
    operator_id: str = Field(..., description="Operator fingerprint (from auth)")

    class Config:
        extra = "forbid"


class DeferResponse(BaseModel):
    """Response after deferral."""
    status: str = Field(default="deferred", description="Status after deferral")
    defer_until: datetime = Field(..., description="When the canary will be retried")

    class Config:
        json_schema_extra = {
            "example": {
                "status": "deferred",
                "defer_until": "2026-09-21T12:00:00Z",
            }
        }


class PauseRequest(BaseModel):
    """Request to pause autonomous skill forge entirely."""
    operator_id: str = Field(..., description="Operator fingerprint (from auth)")
    reason: str = Field(..., max_length=500, description="Why autonomous forge is being paused")

    class Config:
        extra = "forbid"


class PauseResponse(BaseModel):
    """Response after pause."""
    autonomous_forge_enabled: bool = Field(default=False, description="Enable state after pause")
    paused_at: datetime = Field(..., description="Timestamp of pause")
    audit_event_id: str = Field(..., description="Audit trail event ID")

    class Config:
        json_schema_extra = {
            "example": {
                "autonomous_forge_enabled": False,
                "paused_at": "2026-09-20T13:05:00Z",
                "audit_event_id": "audit-evt-pause-001",
            }
        }


class ResumeRequest(BaseModel):
    """Request to resume autonomous skill forge."""
    operator_id: str = Field(..., description="Operator fingerprint (from auth)")

    class Config:
        extra = "forbid"


class ResumeResponse(BaseModel):
    """Response after resume."""
    autonomous_forge_enabled: bool = Field(default=True, description="Enable state after resume")
    resumed_at: datetime = Field(..., description="Timestamp of resume")
    audit_event_id: str = Field(..., description="Audit trail event ID")

    class Config:
        json_schema_extra = {
            "example": {
                "autonomous_forge_enabled": True,
                "resumed_at": "2026-09-20T14:00:00Z",
                "audit_event_id": "audit-evt-resume-001",
            }
        }


class RollbackRequest(BaseModel):
    """Request for emergency rollback of a skill."""
    skill_id: str = Field(..., description="Skill ID to rollback")
    reason: str = Field(..., max_length=500, description="Reason for emergency rollback")
    operator_id: str = Field(..., description="Operator fingerprint (from auth)")

    class Config:
        extra = "forbid"


class RollbackResponse(BaseModel):
    """Response after rollback."""
    rolled_back_to_version: str = Field(..., description="Version that was rolled back to")
    timestamp: datetime = Field(..., description="Timestamp of rollback")
    audit_event_id: str = Field(..., description="Audit trail event ID")

    class Config:
        json_schema_extra = {
            "example": {
                "rolled_back_to_version": "2.0.5",
                "timestamp": "2026-09-20T13:15:00Z",
                "audit_event_id": "audit-evt-rollback-001",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# History / Audit Trail Models
# ─────────────────────────────────────────────────────────────────────────────


class HistoryEntry(BaseModel):
    """Single entry in the autonomous forge audit trail (one fork/decision cycle)."""
    fork_triggered_at: datetime = Field(..., description="When the loss signal triggered a fork")
    skill_id: str = Field(..., description="Forked skill ID")
    version: str = Field(..., description="Forked skill version")
    validation_passed: bool = Field(..., description="Whether validation (layers 1-2) passed")
    canary_deployed_at: Optional[datetime] = Field(None, description="When canary was deployed (if validation passed)")
    canary_verdict: Optional[str] = Field(
        None, description="Canary result if decided: 'approved' | 'deferred' | 'paused' | 'rolledback' | None"
    )
    operator_decision: Optional[str] = Field(None, description="Operator's decision if any")
    decided_at: Optional[datetime] = Field(None, description="When operator made a decision")
    operator_id: Optional[str] = Field(None, description="Operator fingerprint (from auth)")

    class Config:
        json_schema_extra = {
            "example": {
                "fork_triggered_at": "2026-09-20T10:00:00Z",
                "skill_id": "os.delegation_router",
                "version": "2.1.0",
                "validation_passed": True,
                "canary_deployed_at": "2026-09-20T10:05:00Z",
                "canary_verdict": None,
                "operator_decision": None,
                "decided_at": None,
                "operator_id": None,
            }
        }


class HistoryResponse(BaseModel):
    """Audit trail of the last N fork/decision cycles."""
    history: List[HistoryEntry] = Field(..., description="History entries (newest first)")
    total_count: int = Field(..., description="Total forks for this tenant (may exceed limit)")
    limit: int = Field(..., description="Query limit applied")

    class Config:
        json_schema_extra = {
            "example": {
                "history": [
                    {
                        "fork_triggered_at": "2026-09-20T10:00:00Z",
                        "skill_id": "os.delegation_router",
                        "version": "2.1.0",
                        "validation_passed": True,
                        "canary_deployed_at": "2026-09-20T10:05:00Z",
                        "canary_verdict": None,
                        "operator_decision": None,
                        "decided_at": None,
                        "operator_id": None,
                    }
                ],
                "total_count": 23,
                "limit": 10,
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Manifest Viewing Models
# ─────────────────────────────────────────────────────────────────────────────


class ManifestResponse(BaseModel):
    """Generated skill manifest for viewing."""
    skill_json: dict[str, Any] = Field(..., description="The skill.json manifest content")
    generation_context: dict[str, Any] = Field(..., description="Generation parameters and loss signal")
    timestamp: datetime = Field(..., description="When the manifest was generated")

    class Config:
        json_schema_extra = {
            "example": {
                "skill_json": {
                    "id": "os.delegation_router",
                    "version": "2.1.0",
                    "author": "assistant",
                    "description": "Routes tasks to optimal engine based on complexity",
                },
                "generation_context": {
                    "loss_signal": "confidence_below_threshold",
                    "confidence_threshold": 0.8,
                    "loss_value": 0.67,
                },
                "timestamp": "2026-09-20T10:00:00Z",
            }
        }


# ─────────────────────────────────────────────────────────────────────────────
# Internal state dataclass (not for API, but used by routes)
# ─────────────────────────────────────────────────────────────────────────────


@dataclass
class CanaryState:
    """Immutable canary state snapshot (internal use, not JSON-serialized)."""
    skill_id: str
    version: str
    status: str  # 'canary' | 'approved' | 'deferred' | 'paused' | 'rolledback'
    confidence: float
    latency_p95_ms: float
    error_rate: float
    traffic_percent: int
    time_remaining_sec: int
    created_at: datetime
    tenant_id: str


@dataclass
class OperatorDecision:
    """Immutable operator decision record (internal use, audited)."""
    decision_type: str  # 'approved' | 'deferred' | 'paused' | 'resumed' | 'rolledback'
    reason: str
    operator_id: str
    timestamp: datetime
    audit_event_id: str

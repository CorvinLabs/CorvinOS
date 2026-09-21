"""Learning Loop Manifest Schema — Pydantic models for declarative learning loop metadata (ADR-0906).

This module defines the schema for learning loops declared in plugin.json / skill.json.
A learning loop declares a feedback channel that a plugin or skill offers:
  - What events it emits (event_source)
  - What feedback types it accepts (outcome, preference, metric)
  - How to aggregate feedback (rolling_mean, percentile)
  - Health thresholds and dormancy alerts

Schema follows Pydantic v2 strict validation with fail-closed semantics.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator


class FeedbackTypeEnum(str, Enum):
    """Allowed feedback types (ADR-0314 Learning Event taxonomy)."""
    outcome_feedback = "outcome_feedback"
    preference_feedback = "preference_feedback"
    confidence_score = "confidence_score"
    metric_observed = "metric_observed"


class AggregationTypeEnum(str, Enum):
    """Allowed aggregation strategies for health score computation."""
    rolling_mean_7d = "rolling_mean_7d"
    rolling_mean_30d = "rolling_mean_30d"
    percentile_p50 = "percentile_p50"
    percentile_p95 = "percentile_p95"
    percentile_p99 = "percentile_p99"
    count_events_7d = "count_events_7d"
    count_events_30d = "count_events_30d"


class LearningLoopManifest(BaseModel):
    """Declarative Learning Loop metadata.

    A plugin or skill declares one or more learning loops in its manifest:

    ```json
    {
      "id": "confidence_routing",
      "description": "Confidence score predictor for task routing",
      "event_source": "SkillExecutedEvent.confidence_score",
      "feedback_types": ["outcome_feedback", "preference_feedback"],
      "aggregation": "rolling_mean_7d",
      "health_threshold": 0.5,
      "dormancy_alert_hours": 24,
      "owner_skill": "os.delegation_router"
    }
    ```
    """

    id: str = Field(
        ...,
        min_length=1,
        max_length=64,
        description="Unique loop id within plugin (kebab-case or snake_case)",
    )
    """Unique identifier (kebab-case or snake_case, 1–64 chars)."""

    description: str = Field(
        ...,
        min_length=10,
        max_length=500,
        description="Human-readable description of what this loop learns",
    )
    """Short description (10–500 chars)."""

    event_source: str = Field(
        ...,
        description="Event source: 'SkillExecutedEvent.confidence_score' or custom",
    )
    """Where the loop gets its learning signals from (custom format allowed)."""

    feedback_types: list[str] = Field(
        default_factory=list,
        description="Allowed feedback types (outcome, preference, metric, confidence)",
    )
    """Which feedback types this loop accepts (empty = auto-detect from events)."""

    aggregation: str = Field(
        default="rolling_mean_7d",
        description="How to aggregate feedback into a health score",
    )
    """Aggregation strategy (rolling_mean_7d, percentile_p95, etc.)."""

    health_threshold: Optional[float] = Field(
        default=None,
        ge=0.0,
        le=1.0,
        description="Health score threshold; below this triggers 'degrading' status (null = no threshold)",
    )
    """Optional health threshold [0.0–1.0]; None = no health check."""

    dormancy_alert_hours: int = Field(
        default=24,
        ge=1,
        le=8760,  # 1 year
        description="Hours of inactivity before dormancy alert",
    )
    """Dormancy threshold in hours (1–8760, default 24h)."""

    owner_skill: Optional[str] = Field(
        default=None,
        description="Optional owner skill id (e.g. 'os.delegation_router')",
    )
    """Owning skill (used for alerts: 'check if Skill {owner_skill} is running')."""

    model_config = {
        "use_enum_values": False,  # Keep enum objects
        "validate_assignment": True,
        "json_schema_extra": {
            "examples": [
                {
                    "id": "confidence_routing",
                    "description": "Confidence score predictor for task routing",
                    "event_source": "SkillExecutedEvent.confidence_score",
                    "feedback_types": ["outcome_feedback", "preference_feedback"],
                    "aggregation": "rolling_mean_7d",
                    "health_threshold": 0.5,
                    "dormancy_alert_hours": 24,
                    "owner_skill": "os.delegation_router",
                }
            ]
        },
    }

    @field_validator("id")
    @classmethod
    def validate_id(cls, v: str) -> str:
        """Validate id: must be kebab-case or snake_case (no uppercase, special chars)."""
        if not v:
            raise ValueError("id cannot be empty")
        # Allow a-z, 0-9, hyphen, underscore
        if not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError(
                f"id must be kebab-case or snake_case (a-z, 0-9, -, _), got: {v}"
            )
        # Must start with letter or digit
        if not v[0].isalnum():
            raise ValueError(f"id must start with letter or digit, got: {v}")
        # Must end with letter or digit
        if not v[-1].isalnum():
            raise ValueError(f"id must end with letter or digit, got: {v}")
        return v

    @field_validator("feedback_types", mode="before")
    @classmethod
    def validate_feedback_types(cls, v: list[str]) -> list[str]:
        """Validate feedback types are registered in ADR-0314."""
        if not isinstance(v, list):
            raise ValueError(f"feedback_types must be a list, got: {type(v)}")
        allowed = {ft.value for ft in FeedbackTypeEnum}
        for ft in v:
            if ft not in allowed:
                raise ValueError(
                    f"Unknown feedback_type: {ft}. Allowed: {', '.join(allowed)}"
                )
        return v

    @field_validator("aggregation")
    @classmethod
    def validate_aggregation(cls, v: str) -> str:
        """Validate aggregation strategy is registered."""
        allowed = {agg.value for agg in AggregationTypeEnum}
        if v not in allowed:
            raise ValueError(
                f"Unknown aggregation: {v}. Allowed: {', '.join(allowed)}"
            )
        return v

    @field_validator("dormancy_alert_hours")
    @classmethod
    def validate_dormancy_alert_hours(cls, v: int) -> int:
        """Validate dormancy threshold is positive."""
        if v < 1:
            raise ValueError(f"dormancy_alert_hours must be >= 1, got: {v}")
        if v > 8760:  # 1 year
            raise ValueError(
                f"dormancy_alert_hours must be <= 8760 (1 year), got: {v}"
            )
        return v


class LearningLoopIndexEntry(BaseModel):
    """Index entry for a learning loop (computed metadata).

    Created from a LearningLoopManifest + runtime metrics.
    Used internally by the KG MCP service.
    """

    tenant_id: str
    """Tenant scope (multi-tenancy isolation)."""

    plugin_id: str
    """Plugin that declared this loop."""

    loop_id: str
    """Loop id within plugin (composite key: tenant_id:plugin_id:loop_id)."""

    description: str
    """Human-readable description."""

    event_source: str
    """Event source declaration."""

    feedback_types: list[str]
    """Allowed feedback types."""

    aggregation: str
    """Aggregation strategy."""

    health_threshold: Optional[float] = None
    """Health threshold."""

    owner_skill: Optional[str] = None
    """Owning skill."""

    last_event_ts: Optional[datetime] = None
    """Timestamp of last event."""

    event_count_7d: int = 0
    """Number of events in last 7 days."""

    health_score: float = 0.0
    """Computed health score [0.0–1.0]."""

    status: Literal["active", "dormant", "stale", "degrading"] = "active"
    """Current status based on dormancy + health."""

    created_at: datetime = Field(default_factory=datetime.utcnow)
    """When this loop was first indexed."""

    updated_at: datetime = Field(default_factory=datetime.utcnow)
    """Last update timestamp."""

    model_config = {"json_encoders": {datetime: lambda v: v.isoformat() + "Z"}}


class LearningLoopsManifestResponse(BaseModel):
    """HTTP response from /v1/console/capabilities/manifest (learning_loops section).

    Part of the unified console manifest (ADR-0561 Phase 2).
    """

    manifest_version: str = "1.0"
    """Schema version for forward compatibility."""

    learning_loops: list[dict] = Field(
        default_factory=list,
        description="Array of learning loop summaries (empty if none declared)",
    )
    """Learning loops available in the running system."""

    model_config = {
        "json_schema_extra": {
            "example": {
                "manifest_version": "1.0",
                "learning_loops": [
                    {
                        "loop_id": "recommender/feedback:confidence_routing",
                        "plugin_id": "recommender/feedback",
                        "description": "Confidence score predictor...",
                        "event_source": "SkillExecutedEvent.confidence_score",
                        "feedback_types": ["outcome_feedback"],
                        "aggregation": "rolling_mean_7d",
                        "health_threshold": 0.5,
                        "dormancy_alert_hours": 24,
                        "owner_skill": "os.delegation_router",
                        "last_event_ts": "2026-09-21T14:32:00Z",
                        "event_count_7d": 342,
                        "health_score": 0.78,
                        "status": "active",
                    }
                ],
            }
        }
    }

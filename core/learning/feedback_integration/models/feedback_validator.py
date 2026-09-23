"""Stream 4: Feedback Validators — Request/Response schemas + PII detection (ADR-2050).

Pydantic validators for:
  - Bounds checking (confidence 0–1, reason ≤500 chars)
  - PII detection (fail-closed: reject if PII pattern detected)
  - Tenant scoping (always from authenticated session)
  - CSRF protection (checked at route level, @require_csrf)
"""

from __future__ import annotations

import re
from typing import Optional, Literal

from pydantic import BaseModel, Field, field_validator

from .feedback_event import (
    FeedbackEvent,
    FeedbackType,
    OutcomeChoice,
    PreferenceChoice,
)


# PII patterns (fail-closed: when in doubt, redact)
_PII_PATTERNS = [
    (r'\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}\b', 'email'),  # Email
    (r'\b(?:\+?1[-.\s]?)?\(?([0-9]{3})\)?[-.\s]?([0-9]{3})[-.\s]?([0-9]{4})\b', 'phone'),  # Phone
    (r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b', 'credit_card'),  # CC
    (r'\b\d{3}-\d{2}-\d{4}\b', 'ssn'),  # SSN
    (r'\b(?:sk_live_|sk_test_|pk_live_|pk_test_)[A-Za-z0-9]{20,}\b', 'api_key'),  # Stripe keys
]


def _detect_pii(text: Optional[str]) -> tuple[bool, Optional[str]]:
    """Detect PII in text (fail-closed).

    Returns: (has_pii: bool, pattern_type: str | None)
    """
    if not text or not isinstance(text, str):
        return False, None

    for pattern, name in _PII_PATTERNS:
        if re.search(pattern, text, re.IGNORECASE):
            return True, name
    return False, None


class OutcomeFeedbackRequest(BaseModel):
    """Request: outcome_feedback (yes/no/other)."""
    feedback_type: Literal["outcome_feedback"]
    skill_id: str = Field(..., min_length=3, max_length=100)
    outcome: OutcomeChoice
    task_id: Optional[str] = Field(None, max_length=200)
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator('skill_id')
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        """Skill IDs: alphanumeric + underscore/dot."""
        if not re.match(r'^[a-zA-Z0-9_.-]+$', v):
            raise ValueError(f"Invalid skill_id: {v}")
        return v

    @field_validator('reason')
    @classmethod
    def validate_reason_no_pii(cls, v: Optional[str]) -> Optional[str]:
        """Check reason for PII (fail-closed)."""
        if v:
            has_pii, pii_type = _detect_pii(v)
            if has_pii:
                raise ValueError(f"Feedback contains PII ({pii_type}), rejected")
        return v


class PreferenceFeedbackRequest(BaseModel):
    """Request: preference_feedback (LLM/deterministic)."""
    feedback_type: Literal["preference_feedback"]
    skill_id: str = Field(..., min_length=3, max_length=100)
    preference: PreferenceChoice
    policy_class: Optional[str] = Field(None, max_length=100)  # e.g., "pii", "data_access"
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator('skill_id')
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_.-]+$', v):
            raise ValueError(f"Invalid skill_id: {v}")
        return v

    @field_validator('reason')
    @classmethod
    def validate_reason_no_pii(cls, v: Optional[str]) -> Optional[str]:
        if v:
            has_pii, pii_type = _detect_pii(v)
            if has_pii:
                raise ValueError(f"Feedback contains PII ({pii_type}), rejected")
        return v


class ConfidenceFeedbackRequest(BaseModel):
    """Request: confidence_score (0–1)."""
    feedback_type: Literal["confidence_score"]
    skill_id: str = Field(..., min_length=3, max_length=100)
    confidence_score: float = Field(..., ge=0.0, le=1.0)
    task_id: Optional[str] = Field(None, max_length=200)
    reason: Optional[str] = Field(None, max_length=500)

    @field_validator('skill_id')
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_.-]+$', v):
            raise ValueError(f"Invalid skill_id: {v}")
        return v

    @field_validator('reason')
    @classmethod
    def validate_reason_no_pii(cls, v: Optional[str]) -> Optional[str]:
        if v:
            has_pii, pii_type = _detect_pii(v)
            if has_pii:
                raise ValueError(f"Feedback contains PII ({pii_type}), rejected")
        return v


class MetricFeedbackRequest(BaseModel):
    """Request: metric_observed (numeric)."""
    feedback_type: Literal["metric_observed"]
    skill_id: str = Field(..., min_length=3, max_length=100)
    metric_name: str = Field(..., min_length=3, max_length=100)  # e.g., "latency_ms"
    metric_value: float = Field(...)  # Can be any numeric value
    dimension: Optional[str] = Field(None, max_length=100)  # e.g., "workflow_optimizer"

    @field_validator('skill_id')
    @classmethod
    def validate_skill_id(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_.-]+$', v):
            raise ValueError(f"Invalid skill_id: {v}")
        return v

    @field_validator('metric_name')
    @classmethod
    def validate_metric_name(cls, v: str) -> str:
        if not re.match(r'^[a-zA-Z0-9_]+$', v):
            raise ValueError(f"Invalid metric_name: {v}")
        return v


class FeedbackResponse(BaseModel):
    """Response: feedback accepted."""
    feedback_id: str
    feedback_type: str
    skill_id: str
    timestamp: str
    status: str = "recorded"
    message: str = "Feedback recorded and queued for learning loop"


class FeedbackErrorResponse(BaseModel):
    """Response: feedback rejected."""
    error: str
    detail: str
    status_code: int

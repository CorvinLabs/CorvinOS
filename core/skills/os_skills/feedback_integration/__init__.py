"""
Stream 4: Unified Feedback Integration Schema (ADR-2033)

Single API for Phase 10 Skills to emit and consume feedback signals:
  - Confidence scores (0-1)
  - Outcome feedback (yes/no/unknown)
  - User preferences (LLM/deterministic/neutral)
  - Metric observations (latency, error, cost)

Immutable, tenant-scoped, hash-chained to audit trail.
"""

from .schema import (
    FeedbackType,
    OutcomeValue,
    PreferenceValue,
    FeedbackSignal,
    UnifiedFeedbackAPI
)

__version__ = "2.0.0"
__all__ = [
    "FeedbackType",
    "OutcomeValue",
    "PreferenceValue",
    "FeedbackSignal",
    "UnifiedFeedbackAPI"
]

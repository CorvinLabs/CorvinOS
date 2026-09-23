"""Stream 4: Feedback Integration Schema (ADR-2050).

Unified feedback API for all Streams (Workflow Optimizer, Security Orchestrator, Flow Guard).

Phase 1 (EventStore Integration):
  - EventStoreWriter: high-level writer with audit-first + queue/retry
  - FeedbackEvent: immutable feedback dataclass
  - FeedbackType, OutcomeChoice, PreferenceChoice: enums
  - Pydantic validators: request/response schemas
  - routes: FastAPI router for /v1/console/learning/
"""

from .event_store_writer import EventStoreWriter, QueuedFeedback
from .models.feedback_event import (
    FeedbackEvent,
    FeedbackType,
    OutcomeChoice,
    PreferenceChoice,
)
from .models.feedback_validator import (
    OutcomeFeedbackRequest,
    PreferenceFeedbackRequest,
    ConfidenceFeedbackRequest,
    MetricFeedbackRequest,
    FeedbackResponse,
)
from .routes.feedback_integration import router

__all__ = [
    "EventStoreWriter",
    "QueuedFeedback",
    "FeedbackEvent",
    "FeedbackType",
    "OutcomeChoice",
    "PreferenceChoice",
    "OutcomeFeedbackRequest",
    "PreferenceFeedbackRequest",
    "ConfidenceFeedbackRequest",
    "MetricFeedbackRequest",
    "FeedbackResponse",
    "router",
]

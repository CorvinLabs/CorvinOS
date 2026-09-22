"""
Feedback Integration Schema Module — ADR-2033

Unified feedback API for Skills 2.0 learning loop.

Public interface:
- FeedbackEvent, OutcomeFeedback, PreferenceFeedback, ConfidenceFeedback, MetricFeedback
- FeedbackConsumer (async consumer with config hot-reload)
- FastAPI router with routes: POST /feedback, GET /history, PUT /config, GET /metrics
"""

from core.skills.feedback.schema import (
    FeedbackType,
    FeedbackEvent,
    OutcomeFeedback,
    PreferenceFeedback,
    ConfidenceFeedback,
    MetricFeedback,
    validate_feedback,
    feedback_to_audit_event,
)

from core.skills.feedback.consumer import (
    FeedbackConsumer,
    SkillConfig,
    FeedbackQueueItem,
)

from core.skills.feedback.api import (
    router,
    init_feedback_consumer,
    get_feedback_consumer,
)

__all__ = [
    "FeedbackType",
    "FeedbackEvent",
    "OutcomeFeedback",
    "PreferenceFeedback",
    "ConfidenceFeedback",
    "MetricFeedback",
    "validate_feedback",
    "feedback_to_audit_event",
    "FeedbackConsumer",
    "SkillConfig",
    "FeedbackQueueItem",
    "router",
    "init_feedback_consumer",
    "get_feedback_consumer",
]

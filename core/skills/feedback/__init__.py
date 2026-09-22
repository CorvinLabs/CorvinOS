"""
Feedback Integration — ADR-2033

Week 1 (COMPLETE): Feedback Integration Schema
  - schema: Feedback event types (Outcome, Preference, Confidence, Metric)
  - consumer: Async queue consumer for feedback processing
  - api: FastAPI routes for feedback ingestion and config management

Week 2 (COMPLETE): Production Monitoring + Feedback Loop Closure
  - monitoring: Prometheus metrics, health checks, alert rules
  - loop_closure: Feedback → config update transformation
  - api_extended: Config update history, learning curves

All components are audit-first, tenant-scoped, and immutable.
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

from core.skills.feedback.monitoring import (
    FeedbackMetrics,
    HealthCheck,
    AlertManager,
)

from core.skills.feedback.loop_closure import (
    LoopClosureManager,
    ConfigUpdate,
)

from core.skills.feedback.api_extended import router as router_extended

__all__ = [
    # Week 1: Schema
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
    # Week 2: Monitoring
    "FeedbackMetrics",
    "HealthCheck",
    "AlertManager",
    # Week 2: Loop Closure
    "LoopClosureManager",
    "ConfigUpdate",
    # Week 2: Extended API
    "router_extended",
]

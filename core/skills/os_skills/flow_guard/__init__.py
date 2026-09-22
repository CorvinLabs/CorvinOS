"""
Flow Guard Skill — Phase 10 Stream 3 (ADR-2032)

Data flow classification + dynamic policy engine that learns from outcomes.
Fail-closed: unknown/credentials → blocked, uncertain flows require approval.

Core modules:
  - data_classifier: PII/sensitive/public data detection
  - flow_policy: Dynamic allow/deny policies (learned, never weaken)
  - flow_guard: Main skill orchestrator + audit integration
  - learning_integration: ADR-0314 learning loop + feedback schema

Timeline: 12 weeks (Sep 26 – Dec 15)
Status: Week 2 (Learning Integration + Console Routes) IN PROGRESS 🔄
"""

from .data_classifier import (
    DataClassifier,
    DataClassification,
    ClassificationResult,
)
from .flow_policy import (
    FlowPolicy,
    FlowPolicyManager,
    FlowDecision,
    PolicyRule,
    FlowOutcome,
)
from .flow_guard import (
    FlowGuard,
    FlowEvaluation,
    FlowBlockReason,
)
from .learning_integration import (
    LearningIntegration,
    LearningEvent,
    FeedbackType,
)

__version__ = "2.0.0-week2"
__all__ = [
    "DataClassifier",
    "DataClassification",
    "ClassificationResult",
    "FlowPolicy",
    "FlowPolicyManager",
    "FlowDecision",
    "PolicyRule",
    "FlowOutcome",
    "FlowGuard",
    "FlowEvaluation",
    "FlowBlockReason",
    "LearningIntegration",
    "LearningEvent",
    "FeedbackType",
]

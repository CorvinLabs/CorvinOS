"""
Flow Guard Skill — Phase 10 Stream 3 (ADR-2032)

Data flow classification + dynamic policy engine that learns from outcomes.
Fail-closed: unknown/credentials → blocked, uncertain flows require approval.

Core modules:
  - data_classifier: PII/sensitive/public data detection
  - flow_policy: Dynamic allow/deny policies (learned, never weaken)
  - flow_guard: Main skill orchestrator + audit integration

Timeline: 12 weeks (Sep 26 – Dec 15)
Status: Week 1-2 (Data Classification) COMPLETE ✓
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

__version__ = "2.0.0-week1"
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
]

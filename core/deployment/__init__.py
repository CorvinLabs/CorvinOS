"""
Deployment State Management — Phase 3 Production Rollout Orchestration

Phase 1: Drift Prevention (drift detection, manifest sync)
Phase 2a: Canary Traffic Control (automated traffic escalation)
Phase 3: Rollout Orchestration (12-week staged deployment: shadow → canary → skill-primary)

Core systems:
- RolloutOrchestrator: Automates Phase 1→2a→2b transitions with adaptive progression
- Phase 1 Evaluator: Shadow mode success criteria (14 days, 98% agreement, confidence convergence)
- Phase 2a Evaluator: Weekly canary gates (1%→10%→50%→100% traffic escalation)
- Phase 2b Evaluator: Skill activation gates (confidence-gated per-skill primary mode)
- Rollback triggers: Fail-closed auto-rollback on correctness drop, latency spike, audit break
- Audit trail: Immutable, hash-chained, LoM-bound (ADR-0537, ADR-0232/0233)

Compliance: GDPR (Art. 5/6/30/32), EU AI Act (Art. 5/50), Audit-First
"""

from .state_sync import DeploymentStateManager
from .manifest import ManifestManager
from .canary_traffic_controller import (
    CanaryTrafficController,
    TrafficMetrics,
    CanaryState,
    CanaryHealth,
    EscalationDecision,
    create_controller,
)
from .canary_validation import (
    CanaryValidationEngine,
    ValidationRunResult,
    GateEvaluation,
    GateStatus,
    AuditEventType,
    AuditBackend,
    LocalFileAuditBackend,
    create_validation_engine,
)
from .phase3_rollout_orchestrator import (
    RolloutOrchestrator,
    SkillMetrics,
    Phase,
    SkillMode,
    RollbackReason,
    Phase1Evaluator,
    Phase2aGateEvaluator,
    Phase2bActivationGate,
    get_orchestrator,
    reset_orchestrator,
)

__all__ = [
    # Phase 1: Drift Prevention
    "DeploymentStateManager",
    "ManifestManager",
    # Phase 2a: Canary Traffic Control
    "CanaryTrafficController",
    "TrafficMetrics",
    "CanaryState",
    "CanaryHealth",
    "EscalationDecision",
    "create_controller",
    "CanaryValidationEngine",
    "ValidationRunResult",
    "GateEvaluation",
    "GateStatus",
    "AuditEventType",
    "AuditBackend",
    "LocalFileAuditBackend",
    "create_validation_engine",
    # Phase 3: Rollout Orchestration
    "RolloutOrchestrator",
    "SkillMetrics",
    "Phase",
    "SkillMode",
    "RollbackReason",
    "Phase1Evaluator",
    "Phase2aGateEvaluator",
    "Phase2bActivationGate",
    "get_orchestrator",
    "reset_orchestrator",
]

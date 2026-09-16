"""OS-Skills Phase 1: Production-ready foundation (Health Monitor + Context Bridge + Orchestrator).

This package implements three core Skills per ADR-0532/0535:
- Health Monitor: subsystem state tracking and anomaly detection
- Context Bridge: auto-context-splitting, session continuity management
- Basic Orchestrator: plugin loading, task routing, worker coordination

Compliance (load-bearing):
- GDPR Art. 30, 32: audit-trail integration, every decision logged before disk write
- EU AI Act Art. 50: line-of-moral-responsibility binding
- ADR-0232/0233: boot tripwire validation, hash-chain integrity
- ADR-0535: composition dependencies, topological sort execution order
- ADR-0314: outcome feedback integration, learning loop closure

Composition Model (ADR-0535):
- Dependencies resolved at boot (DAG validation, cycle detection)
- Execution ordered topologically (dependencies before dependents)
- Timeouts enforced per-call with degradation fallbacks
- Audit events independent per skill (no cascade blame)

Entry Points:
- `health_monitor.HealthMonitor` — Skill 1, required dependency for Orchestrator
- `context_bridge.ContextBridge` — Skill 2, routes context splits across sessions
- `orchestrator.BasicOrchestrator` — Skill 3, composes both, orchestrates tasks

Test Coverage:
- 25 E2E tests (happy path + composition)
- 12 adversarial tests (timeouts, errors, degradation)
- 5 DoD checks (reachability, audit, tests, docs, reproducibility)
"""

from .health_monitor import HealthMonitor, HealthStatus, SubsystemMetric, HealthLevel
from .context_bridge import ContextBridge, ContextSnapshot, SessionBridge, SplitReason
from .orchestrator import BasicOrchestrator, TaskDefinition, ExecutionPlan, RoutingStrategy
from .base_skill import BaseSkill, SkillExecutedEvent, SkillExecutionStatus, AuditTrail
from .mock_audit_trail import MockAuditTrail

__all__ = [
    # Base
    "BaseSkill",
    "SkillExecutedEvent",
    "SkillExecutionStatus",
    "AuditTrail",
    "MockAuditTrail",
    # Health Monitor
    "HealthMonitor",
    "HealthStatus",
    "SubsystemMetric",
    "HealthLevel",
    # Context Bridge
    "ContextBridge",
    "ContextSnapshot",
    "SessionBridge",
    "SplitReason",
    # Orchestrator
    "BasicOrchestrator",
    "TaskDefinition",
    "ExecutionPlan",
    "RoutingStrategy",
]

"""Quality Gates System (ADR-0688).

Automated quality gates for CorvinOS with knowledge graph backend,
audit trail integration, and learning infrastructure.
"""

from .models import (
    GateResult,
    AuditEvent,
    KGNode,
    KGEdge,
    VerdictType,
    EventType,
    KGNodeType,
)
from .graph import KnowledgeGraph, VerifyResult
from .validators import (
    BaseValidator,
    IdeaGateValidator,
    ConceptGateValidator,
    ADRGateValidator,
    ImplementationPlanGateValidator,
)
from .audit import QualityGateAuditLogger
from .cli import QualityGateCLI
from .config import load_gate_config, list_gates, get_validator_class
from .schema import GateSchema
from .learning import (
    BayesianGateTuner,
    GateFeedback,
    BayesianThreshold,
    FeedbackType,
)

__all__ = [
    "GateResult",
    "AuditEvent",
    "KGNode",
    "KGEdge",
    "VerdictType",
    "EventType",
    "KGNodeType",
    "KnowledgeGraph",
    "VerifyResult",
    "BaseValidator",
    "IdeaGateValidator",
    "ConceptGateValidator",
    "ADRGateValidator",
    "ImplementationPlanGateValidator",
    "QualityGateAuditLogger",
    "QualityGateCLI",
    "load_gate_config",
    "list_gates",
    "get_validator_class",
    "GateSchema",
    "BayesianGateTuner",
    "GateFeedback",
    "BayesianThreshold",
    "FeedbackType",
]

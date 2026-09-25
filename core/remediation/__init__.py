"""
Phase 5: Automated Remediation & Operator Approval Workflow

Automatically fix low-risk drifts; require approval for high-risk.
See core/remediation/ for modules.
"""

from .drift_categories import (
    DriftCategorizer,
    DriftCategory,
    DriftSeverity,
    RemediationType,
    RiskAssessment,
    categorize_drift,
    categorize_drifts,
)
from .auto_remediate import (
    SafeAutoRemediator,
    RemediationResult,
)
from .approval_workflow import (
    ApprovalGate,
    ApprovalRequest,
    ApprovalState,
    get_approval_gate,
)
from .orchestrator import (
    RemediationOrchestrator,
    RemediationState,
    RemediationEvent,
    RemediationPlan,
)

__all__ = [
    # Categorization
    "DriftCategorizer",
    "DriftCategory",
    "DriftSeverity",
    "RemediationType",
    "RiskAssessment",
    "categorize_drift",
    "categorize_drifts",
    # Auto-remediation
    "SafeAutoRemediator",
    "RemediationResult",
    # Approval workflow
    "ApprovalGate",
    "ApprovalRequest",
    "ApprovalState",
    "get_approval_gate",
    # Orchestration
    "RemediationOrchestrator",
    "RemediationState",
    "RemediationEvent",
    "RemediationPlan",
]

"""Infrastructure Hardening — Phase 12 (ADR-0328-0334)

7 protection layers with fail-closed contracts:
- L1: Boot Verification (ADR-0328)
- L2: Data Classification (ADR-0329)
- L3: Compartmentalization (ADR-0330)
- L4: Module Contracts (ADR-0331)
- L5: Self-Healing (ADR-0332)
- L6: Subprocess Isolation (ADR-0333)
- L7: Operator Dashboard (ADR-0334)
"""

from core.infrastructure.boot_verification import (
    BootVerifier,
    BootState,
    BootVerificationError,
)
from core.infrastructure.data_classification import (
    DataClassifier,
    ClassificationLevel,
    ClassificationError,
)
from core.infrastructure.compartmentalization import (
    CompartmentBoundary,
    ExecutionTier,
    TierValidationError,
)
from core.infrastructure.module_contracts import (
    ModuleContract,
    ContractValidationError,
)
from core.infrastructure.self_healing import (
    SelfHealingLoop,
    RecoveryStrategy,
    RecoveryError,
)
from core.infrastructure.subprocess_isolation import (
    SubprocessBoundary,
    IsolationPolicy,
    IsolationError,
)
from core.infrastructure.operator_dashboard import (
    OperatorDashboard,
    HealthWidget,
    HealthSummary,
)

__all__ = [
    "BootVerifier",
    "BootState",
    "BootVerificationError",
    "DataClassifier",
    "ClassificationLevel",
    "ClassificationError",
    "CompartmentBoundary",
    "ExecutionTier",
    "TierValidationError",
    "ModuleContract",
    "ContractValidationError",
    "SelfHealingLoop",
    "RecoveryStrategy",
    "RecoveryError",
    "SubprocessBoundary",
    "IsolationPolicy",
    "IsolationError",
    "OperatorDashboard",
    "HealthWidget",
    "HealthSummary",
]

"""Phase 10-12 Integration Layer

Cross-phase integration between:
  - Phase 10: Input Validation Integration
  - Phase 11: Dual-Gate Pipeline
  - Phase 12: Infrastructure Hardening

Modules:
  - phase10_phase11_integration: Validator → Pipeline integration
  - phase12_boot_integration: Boot sequence + Layer initialization
"""

from .phase10_phase11_integration import (
    Phase10Phase11Integrator,
    IntegrationValidationResult,
    PHASE10_PHASE11_INTEGRATION_ENABLED,
)
from .phase12_boot_integration import (
    Phase12BootIntegrator,
    Phase12BootIntegrationResult,
    LayerBootState,
)

__all__ = [
    "Phase10Phase11Integrator",
    "IntegrationValidationResult",
    "PHASE10_PHASE11_INTEGRATION_ENABLED",
    "Phase12BootIntegrator",
    "Phase12BootIntegrationResult",
    "LayerBootState",
]

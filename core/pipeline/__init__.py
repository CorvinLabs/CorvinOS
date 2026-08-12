"""
Dual-Gate Context Pipeline — ADR-0300 + ADR-0301

Fail-closed three-gate pipeline:
  Gate 1: Capability Gate (auth, roles, tiers)
  Gate 2: Validation Gate (input validation, PII detection, queue integrity)
  Gate 3: Audit Gate (immutable hash-chained trail)

Every operation validated, capability-checked, PII-scanned, and audit-logged.

ADR-0300: DualGatePipeline (core dual-gate logic)
ADR-0301: Transport Adapters + Call-Site Registry (wiring into 50+ entry points)
"""

from core.pipeline.dual_gate import (
    DualGatePipeline,
    PipelineContext,
    ValidationState,
    PipelineExecutionError,
    CapabilityGateError,
    ValidationGateError,
    PIIDetectionError,
    QueueIntegrityError,
    AuditGateError,
)
from core.pipeline.adapters import (
    FlaskAdapter,
    CLIAdapter,
    AsyncAdapter,
    InternalFunctionAdapter,
)
from core.pipeline.call_site_registry import (
    CallSiteRegistry,
    EntryPoint,
    EntryPointCategory,
    WiringStatus,
    get_registry,
    register_entry_point,
)

__all__ = [
    # ADR-0300: Dual-Gate Pipeline
    "DualGatePipeline",
    "PipelineContext",
    "ValidationState",
    "PipelineExecutionError",
    "CapabilityGateError",
    "ValidationGateError",
    "PIIDetectionError",
    "QueueIntegrityError",
    "AuditGateError",
    # ADR-0301: Transport Adapters
    "FlaskAdapter",
    "CLIAdapter",
    "AsyncAdapter",
    "InternalFunctionAdapter",
    # ADR-0301: Call-Site Registry
    "CallSiteRegistry",
    "EntryPoint",
    "EntryPointCategory",
    "WiringStatus",
    "get_registry",
    "register_entry_point",
]

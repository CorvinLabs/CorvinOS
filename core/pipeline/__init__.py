"""
Dual-Gate Context Pipeline — ADR-0300

Fail-closed pipeline: Capability Gate → Audit Gate → Execution.
Every operation validated, capability-checked, and audit-logged.
"""

from core.pipeline.dual_gate import (
    DualGatePipeline,
    PipelineContext,
    PipelineExecutionError,
    CapabilityGateError,
    AuditGateError,
)

__all__ = [
    "DualGatePipeline",
    "PipelineContext",
    "PipelineExecutionError",
    "CapabilityGateError",
    "AuditGateError",
]

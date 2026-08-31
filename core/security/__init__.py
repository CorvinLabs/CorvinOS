"""Integrated Security Layer: unified 5-role pipeline for EU AI Act compliance."""

from .context import (
    GateName,
    GateResult,
    PiiFinding,
    SecurityContext,
)
from .pipeline import IntegratedSecurityPipeline
from .registry import CallSiteRegistry, EntryPoint, EntryPointCategory, WiringStatus
from .exceptions import (
    PipelineExecutionError,
    CapabilityGateError,
    ValidationGateError,
    PIIDetectionError,
    AuditGateError,
)

__all__ = [
    "GateName",
    "GateResult",
    "PiiFinding",
    "SecurityContext",
    "IntegratedSecurityPipeline",
    "CallSiteRegistry",
    "EntryPoint",
    "EntryPointCategory",
    "WiringStatus",
    "PipelineExecutionError",
    "CapabilityGateError",
    "ValidationGateError",
    "PIIDetectionError",
    "AuditGateError",
]

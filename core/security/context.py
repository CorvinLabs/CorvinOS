"""Security context and gate result dataclasses."""

import json
import hashlib
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, List, Optional
from uuid import uuid4


class GateName(Enum):
    """Security gate identifiers."""
    CAPABILITY = "capability"
    VALIDATION = "validation"
    PII_DETECTION = "pii_detection"
    CONTEXT_ENGINEERING = "context_engineering"
    AUDIT_RECORDING = "audit_recording"


@dataclass(frozen=True)
class GateResult:
    """Immutable result from a security gate."""
    gate_name: GateName
    passed: bool
    reason_code: str
    details: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)

    def to_audit_dict(self) -> dict:
        """Convert to audit-safe format (content-free)."""
        return {
            "gate": self.gate_name.value,
            "passed": self.passed,
            "reason": self.reason_code,
            "details_keys": list(self.details.keys()),
            "ts": self.timestamp,
        }


@dataclass(frozen=True)
class PiiFinding:
    """A single PII/secret detection result."""
    data_type: str
    severity: str  # "low", "medium", "high"
    location: str
    action_taken: str


@dataclass
class SecurityContext:
    """Unified context flowing through all five security gates."""
    # Request metadata
    actor: str
    action: str
    resource: str
    capability_required: str
    tenant_id: str
    transport: str
    input_data: dict = field(default_factory=dict)
    timestamp: float = field(default_factory=time.time)
    request_id: str = field(default_factory=lambda: str(uuid4()))

    # Mutable state as request flows through pipeline
    capability_granted: bool = False
    validation_passed: bool = False
    pii_detected: List[PiiFinding] = field(default_factory=list)
    context_brief: Optional[dict] = None
    audit_recorded: bool = False
    error: Optional[str] = None
    result: Any = None

    # Audit trail
    gate_results: List[GateResult] = field(default_factory=list)
    decision_record_hash: Optional[str] = None
    _audit_recorded_lock: bool = field(default=False, init=False)

    def deny_with_reason(
        self,
        gate_name: GateName,
        reason: str,
        details: dict = None,
    ) -> None:
        """Record a gate denial."""
        self.error = f"{gate_name.value}_denied: {reason}"
        self.gate_results.append(GateResult(
            gate_name=gate_name,
            passed=False,
            reason_code=reason,
            details=details or {},
        ))

    def append_gate_result(self, result: GateResult) -> None:
        """Append gate result (fail-closed: error if already locked)."""
        if self._audit_recorded_lock:
            raise RuntimeError("Cannot modify gate_results after audit recording")
        self.gate_results.append(result)

    def _lock_gate_results(self) -> None:
        """Lock gate_results after audit recording (Finding #2 mitigation)."""
        self._audit_recorded_lock = True

    def compute_audit_hash(self) -> str:
        """Compute SHA256 hash of decision record (content-free)."""
        audit_dict = {
            "actor": self.actor,
            "action": self.action,
            "resource": self.resource,
            "tenant_id": self.tenant_id,
            "ts": self.timestamp,
            "request_id": self.request_id,
            "gates": [g.to_audit_dict() for g in self.gate_results],
            "context_sources_count": len(self.context_brief.get("sources", [])) if self.context_brief else 0,
            "pii_finding_count": len(self.pii_detected),
        }
        audit_str = json.dumps(audit_dict, sort_keys=True)
        return hashlib.sha256(audit_str.encode()).hexdigest()

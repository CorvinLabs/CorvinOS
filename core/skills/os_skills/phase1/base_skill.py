"""Base Skill class for OS-Skills Phase 1.

Enforces audit-first design: every decision MUST be logged to the core audit chain
BEFORE being written to disk or returned to the caller.

Contract (ADR-0535 + ADR-0232):
1. Skill.execute() is called with immutable input (frozen dataclass)
2. Decision logic runs deterministically (no randomness outside RNG seeding)
3. Before returning result, skill logs SkillExecutedEvent to audit trail
4. Audit trail write MUST succeed (fail-closed); if it fails, decision is rejected
5. Skill's own state/config changes are ALSO audited (SkillConfigUpdatedEvent)
6. Every skill call is isolated: caller timeout/crash ≠ dependency timeout/crash

Compliance binding:
- ADR-0232: boot tripwire verifies audit chain before any skill runs
- GDPR Art. 30/32: audit trail is immutable, hash-chained, tenant-scoped
- EU AI Act Art. 50: line-of-moral-responsibility (lom) bound cryptographically
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional, TypeVar, Generic
from enum import Enum
import logging
import time
import hashlib
from datetime import datetime
import json

logger = logging.getLogger(__name__)

T = TypeVar("T")  # Output type


class SkillExecutionStatus(Enum):
    """Execution outcome for audit trail."""
    SUCCESS = "success"
    TIMEOUT = "timeout"
    DEGRADED = "degraded"  # Soft dependency failed, continued
    ERROR = "error"
    REJECTED = "rejected"  # Audit chain rejected write


@dataclass(frozen=True)
class SkillExecutedEvent:
    """Immutable audit event: one skill execution."""
    tenant_id: str
    timestamp: str  # ISO8601
    skill_id: str  # e.g., "os.health_monitor"
    version: str  # Skill version semver
    input_hash: str  # SHA256 of input (PII-safe)
    output_hash: str  # SHA256 of output
    status: SkillExecutionStatus
    latency_ms: int
    lom: str  # Line of moral responsibility (file:line:function)
    lom_hash: str  # Cryptographic bind of LoM to source
    error_message: Optional[str] = None
    prev_hash: Optional[str] = None  # Hash chain link
    hash: str = field(default="")  # Will be computed on write

    def compute_hash(self) -> str:
        """Compute this event's hash for chain."""
        payload = json.dumps({
            "tenant_id": self.tenant_id,
            "timestamp": self.timestamp,
            "skill_id": self.skill_id,
            "version": self.version,
            "input_hash": self.input_hash,
            "output_hash": self.output_hash,
            "status": self.status.value,
            "latency_ms": self.latency_ms,
            "lom": self.lom,
            "lom_hash": self.lom_hash,
            "error_message": self.error_message,
            "prev_hash": self.prev_hash,
        }, sort_keys=True, separators=(",", ":"))
        return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SkillConfigUpdatedEvent:
    """Immutable audit event: skill configuration changed (e.g., learned params)."""
    tenant_id: str
    timestamp: str
    skill_id: str
    version: str
    param_name: str  # Which parameter changed
    value_before_hash: str  # SHA256 of old value
    value_after_hash: str  # SHA256 of new value
    reason: str  # Why it changed (e.g., "learning_update", "operator_manual")
    confidence_delta: float  # Change in skill's confidence score
    lom: str
    lom_hash: str
    prev_hash: Optional[str] = None
    hash: str = field(default="")


class AuditTrail(ABC):
    """Abstract audit trail interface. Real implementation in core/compliance/."""

    @abstractmethod
    def write_event(self, event: SkillExecutedEvent) -> bool:
        """Write event to audit trail. Fail-closed: return False if write fails.

        Contract:
        - Event MUST be written to core chain FIRST (before disk)
        - If core chain write fails, entire skill execution fails
        - Event is immutable and hash-chained
        - Tenant isolation enforced (event filtered by tenant_id)

        Args:
            event: SkillExecutedEvent to append

        Returns:
            True if audit trail accepted and persisted
            False if write failed (skill must reject decision)
        """
        pass

    @abstractmethod
    def write_config_event(self, event: SkillConfigUpdatedEvent) -> bool:
        """Write config update to audit trail."""
        pass


class BaseSkill(ABC, Generic[T]):
    """Base class for all OS-Skills Phase 1.

    Subclasses MUST:
    1. Implement execute() with deterministic logic
    2. Call _audit_execution() before returning
    3. Handle dependencies via composition middleware
    4. Never bypass audit trail

    Example:
        class MySkill(BaseSkill[str]):
            skill_id = "os.my_skill"
            version = "1.0.0"
            required_dependencies = ["os.health_monitor"]  # From ADR-0535

            def execute(self, input_data):
                # Deterministic logic
                result = self._do_work(input_data)
                # MUST audit before returning
                self._audit_execution(
                    input_data,
                    result,
                    status=SkillExecutionStatus.SUCCESS,
                    latency_ms=time.perf_counter() - start
                )
                return result
    """

    # Subclass MUST override these
    skill_id: str = ""  # e.g., "os.health_monitor"
    version: str = "1.0.0"

    # Composition (ADR-0535)
    required_dependencies: list[str] = field(default_factory=list)
    soft_dependencies: list[str] = field(default_factory=list)
    call_budget_ms: int = 1000  # Max time budget per call

    def __init__(self, tenant_id: str, audit_trail: AuditTrail):
        """Initialize skill with audit trail reference.

        Args:
            tenant_id: Tenant scope for all operations
            audit_trail: Audit trail implementation (from core/compliance)
        """
        self.tenant_id = tenant_id
        self.audit_trail = audit_trail
        self._call_start: Optional[float] = None

    @abstractmethod
    def execute(self, input_data: Any) -> T:
        """Execute skill deterministically.

        Contract:
        - MUST be deterministic (same input → same output)
        - MUST call _audit_execution before returning
        - MUST timeout gracefully (timeout_handling per manifest)
        - MUST NOT make external network calls (only local queries)

        Args:
            input_data: Immutable skill input

        Returns:
            Skill output (will be hashed before audit)
        """
        pass

    def _audit_execution(
        self,
        input_data: Any,
        output_data: T,
        status: SkillExecutionStatus,
        latency_ms: int,
        error_message: Optional[str] = None,
    ) -> bool:
        """Audit skill execution to immutable trail.

        MUST be called before returning from execute().

        Args:
            input_data: Raw input
            output_data: Raw output
            status: Execution outcome
            latency_ms: Wall-clock time
            error_message: Optional error if status != SUCCESS

        Returns:
            True if audit wrote successfully
            False if audit failed (decision rejected, raise exception)
        """
        input_hash = hashlib.sha256(str(input_data).encode()).hexdigest()
        output_hash = hashlib.sha256(str(output_data).encode()).hexdigest()

        event = SkillExecutedEvent(
            tenant_id=self.tenant_id,
            timestamp=datetime.utcnow().isoformat() + "Z",
            skill_id=self.skill_id,
            version=self.version,
            input_hash=input_hash,
            output_hash=output_hash,
            status=status,
            latency_ms=latency_ms,
            lom=self._get_lom(),
            lom_hash=self._compute_lom_hash(),
            error_message=error_message,
            hash="",  # Computed on write
        )

        success = self.audit_trail.write_event(event)
        if not success:
            logger.error(
                f"Skill {self.skill_id} execution audit FAILED: {input_hash} → {output_hash}",
                extra={"tenant_id": self.tenant_id}
            )
            raise RuntimeError(f"Audit trail rejected {self.skill_id} execution (fail-closed)")

        return True

    def _get_lom(self) -> str:
        """Get line of moral responsibility (file:line:function)."""
        import inspect
        frame = inspect.currentframe()
        if frame and frame.f_back:
            caller_frame = frame.f_back
            return f"{caller_frame.f_code.co_filename}:{caller_frame.f_lineno}:{caller_frame.f_code.co_name}"
        return "unknown"

    def _compute_lom_hash(self) -> str:
        """Cryptographic hash of LoM (bind to source code)."""
        lom = self._get_lom()
        return hashlib.sha256(lom.encode()).hexdigest()

    def timeout_remaining_ms(self) -> int:
        """Time remaining in call budget."""
        if self._call_start is None:
            return self.call_budget_ms
        elapsed = (time.perf_counter() - self._call_start) * 1000
        return max(0, int(self.call_budget_ms - elapsed))

"""
Skill Invocation RPC API — Request/Response models (ADR-0598).

Immutable dataclasses for Skill invocation contract.
All engines (Claude Code, Hermes, Copilot, OpenCode) use this contract.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Optional
from datetime import datetime, timezone
import uuid
import json
from types import MappingProxyType


class WorkerEngine(Enum):
    """Supported orchestration engines."""
    CLAUDE_CODE = "claude_code"
    HERMES = "hermes"
    COPILOT = "copilot"
    OPENCODE = "opencode"


def _hash_dict(data: Dict[str, Any]) -> str:
    """Deterministic SHA256 hash of dict (shared utility, JSON-sorted)."""
    import hashlib
    payload = json.dumps(data, sort_keys=True)
    return hashlib.sha256(payload.encode()).hexdigest()


@dataclass(frozen=True)
class SkillInvocationRequest:
    """
    Immutable request to invoke a Skill.
    Required contract for all engines.
    """
    tenant_id: str                          # Fail-closed if missing
    skill_id: str                           # e.g., "os.delegation_router"
    skill_version: str                      # e.g., "1.2.3"
    input: Dict[str, Any]                   # Validated against manifest.input_schema
    engine: WorkerEngine                    # Which engine is invoking
    request_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    user_id: Optional[str] = None           # May be None (daemon context)
    context: Optional[Dict[str, Any]] = None  # Engine-specific metadata
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def __post_init__(self):
        """Validate at construction time (fail-closed)."""
        # Canonical tenant validation (ADR-0007): same regex as every other
        # tenant boundary — not just "non-empty".
        from core.tenants.validation import validate_tenant_id

        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise ValueError("tenant_id required (fail-closed, GDPR Art. 32)")
        try:
            validate_tenant_id(self.tenant_id)
        except ValueError as exc:
            raise ValueError(f"tenant_id required to be valid: {exc}") from exc
        if not self.skill_id:
            raise ValueError("skill_id is required")
        if not self.skill_version:
            raise ValueError("skill_version is required")
        if self.engine is None:
            raise ValueError("engine is required")
        # Accept the enum's string value; an unknown engine is refused.
        if not isinstance(self.engine, WorkerEngine):
            try:
                object.__setattr__(self, "engine", WorkerEngine(self.engine))
            except ValueError as exc:
                raise ValueError(f"engine must be a WorkerEngine, got {self.engine!r}") from exc
        # The request is IMMUTABLE — including its input mapping. A frozen
        # dataclass only freezes the attribute; the dict behind it was still
        # writable (``req.input["x"] = ...``), so a Skill or middleware could
        # mutate a request after its input_hash was audited.
        if not isinstance(self.input, MappingProxyType):
            object.__setattr__(self, "input", MappingProxyType(dict(self.input)))

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict (for hashing, logging)."""
        return {
            "tenant_id": self.tenant_id,
            "skill_id": self.skill_id,
            "skill_version": self.skill_version,
            "input": dict(self.input),
            "engine": self.engine.value,
            "request_id": self.request_id,
            "user_id": self.user_id,
            "context": self.context,
            "timestamp": self.timestamp.isoformat(),
        }

    def input_hash(self) -> str:
        """Immutable hash of input (for audit)."""
        return _hash_dict(dict(self.input))


@dataclass(frozen=True)
class SkillInvocationResponse:
    """
    Immutable response from Skill execution.
    Guaranteed to be valid (output validated against manifest.output_schema).
    """
    output: Dict[str, Any]                  # Validated against manifest.output_schema
    latency_ms: int                         # Wall-clock time
    execution_trace: list = field(default_factory=list)  # Phase trace ("Phase 0: Intake", etc.)
    lom: str = "unknown"                    # Line of Moral Responsibility
    audit_event_id: str = ""                # Cross-reference to audit event
    phase_completed: int = 10               # 0–10: which phase succeeded (10 = full success)
    error: Optional[str] = None             # Error message if phase failed

    def to_dict(self) -> Dict[str, Any]:
        """Serialize to dict."""
        return {
            "output": self.output,
            "latency_ms": self.latency_ms,
            "execution_trace": self.execution_trace,
            "lom": self.lom,
            "audit_event_id": self.audit_event_id,
            "phase_completed": self.phase_completed,
            "error": self.error,
        }

    def output_hash(self) -> str:
        """Immutable hash of output (for audit)."""
        return _hash_dict(self.output)

    @property
    def is_success(self) -> bool:
        """True if Skill completed all phases (phase_completed == 10, immutable)."""
        # FIX: == 10, not >= 10 (phase_completed is 0–10, 10 is terminal)
        return self.phase_completed == 10 and self.error is None


class SkillInvocationError(Exception):
    """Base exception for Skill invocation failures."""
    pass


class SkillInvocationTimeout(SkillInvocationError):
    """Raised when a Skill phase times out."""
    def __init__(self, phase: int, timeout_ms: int):
        self.phase = phase
        self.timeout_ms = timeout_ms
        super().__init__(f"Skill invocation timed out at phase {phase} (>{timeout_ms}ms)")


class SkillInvocationValidationError(SkillInvocationError):
    """Raised when input/output schema validation fails."""
    pass


class SkillInvocationTenantError(SkillInvocationError):
    """Raised when tenant isolation is violated."""
    pass

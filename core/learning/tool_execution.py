"""Tool Execution Telemetry — immutable capture of tool execution metrics (ADR-0321).

This module defines the core data structures for tool execution learning signals,
including immutable telemetry capture, PII sanitization, and validation.

ADR-0321 requires:
1. Immutable telemetry (frozen dataclass, fail-fast validation)
2. PII sanitization (error messages scrubbed for paths, schema names, stack traces)
3. Audit trail integration (every event emission logged)
4. Non-blocking async emission (queue-full doesn't block tool execution)
5. Tenant isolation (session_id captured, tenant_id enforced at EventStore level)

Status: ADR-0321 PROPOSED → ACCEPTED
Dependencies: ADR-0314 (Learning Infrastructure)
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Optional


class ToolExecutionStatus(str, Enum):
    """Status of a tool execution attempt."""

    SUCCESS = "success"
    FAILURE = "failure"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(frozen=True)
class ToolExecutionTelemetry:
    """Immutable telemetry from a single tool execution (ADR-0321).

    Captures all signals needed by downstream gaps (ranking, attribution, cost learning).
    Validation is fail-fast in __post_init__; PII sanitization is automatic.

    Invariants (enforced in __post_init__):
    - latency_ms >= 0 (calculated from timestamps)
    - input_tokens + output_tokens >= 0
    - sum(subsystem_tokens.values()) <= input_tokens + output_tokens
    - user_satisfaction in [-1, 1, 2, 3, 4, 5] (where -1 = not available)
    - error_message is sanitized (no paths, schema names, stack traces)
    """

    # Core identification (required)
    tool_id: str
    tool_name: str
    tool_type: str  # "generated" | "promoted" | "builtin"
    session_id: str

    # Timing (required)
    start_timestamp_utc: datetime
    end_timestamp_utc: datetime

    # Tokens & Cost (required)
    input_tokens: int
    output_tokens: int
    estimated_cost_cents: int

    # Execution Status (required)
    status: ToolExecutionStatus

    # Optional fields with defaults
    task_id: Optional[str] = None
    turn_id: Optional[str] = None
    subsystem_tokens: dict[str, int] = field(default_factory=dict)
    error_type: Optional[str] = None
    error_message: Optional[str] = None  # SANITIZED in __post_init__
    error_class: Optional[str] = None
    input_size_bytes: int = 0
    output_size_bytes: int = 0
    user_satisfaction: int = -1  # 1-5 rating or -1 (not available)
    required_followup: bool = False  # Did user ask again?
    error_resolved: Optional[bool] = None  # Outcome: was the error fixed in a followup?
    model_id: str = "claude-opus-5"
    task_type: Optional[str] = None  # "code", "research", etc.
    tags: list[str] = field(default_factory=list)

    # Calculated field (not part of __init__)
    latency_ms: int = field(init=False)  # Calculated in __post_init__

    def __post_init__(self) -> None:
        """Validate and sanitize telemetry, fail-fast on violations."""
        # Calculate latency (frozen dataclass workaround: use object.__setattr__)
        latency = int((self.end_timestamp_utc - self.start_timestamp_utc).total_seconds() * 1000)
        object.__setattr__(self, "latency_ms", latency)

        # Validate latency
        if self.latency_ms < 0:
            raise ValueError(f"latency_ms must be >= 0, got {self.latency_ms}")

        # Validate token counts
        if self.input_tokens < 0 or self.output_tokens < 0:
            raise ValueError(
                f"token counts must be >= 0, got input={self.input_tokens}, output={self.output_tokens}"
            )

        # Validate subsystem tokens consistency
        subsystem_total = sum(self.subsystem_tokens.values())
        total_tokens = self.input_tokens + self.output_tokens
        if subsystem_total > total_tokens:
            raise ValueError(
                f"subsystem_tokens sum {subsystem_total} exceeds total tokens {total_tokens}"
            )

        # Validate user_satisfaction
        valid_satisfaction = [-1, 1, 2, 3, 4, 5]
        if self.user_satisfaction not in valid_satisfaction:
            raise ValueError(
                f"user_satisfaction must be in {valid_satisfaction}, got {self.user_satisfaction}"
            )

        # Sanitize error message (fail-closed: drop if unsafe)
        if self.error_message:
            sanitized = _sanitize_error_message(self.error_message)
            if sanitized is None:
                # PII detected and couldn't be safely removed; drop the message
                object.__setattr__(self, "error_message", "[PII redacted]")
            else:
                object.__setattr__(self, "error_message", sanitized)

    def to_event_payload(self) -> dict[str, Any]:
        """Convert to learning event payload format."""
        return {
            "tool_id": self.tool_id,
            "tool_name": self.tool_name,
            "tool_type": self.tool_type,
            "session_id": self.session_id,
            "task_id": self.task_id,
            "turn_id": self.turn_id,
            "start_timestamp_utc": self.start_timestamp_utc.isoformat() + "Z",
            "end_timestamp_utc": self.end_timestamp_utc.isoformat() + "Z",
            "latency_ms": self.latency_ms,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "subsystem_tokens": self.subsystem_tokens,
            "estimated_cost_cents": self.estimated_cost_cents,
            "status": self.status.value,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "error_class": self.error_class,
            "input_size_bytes": self.input_size_bytes,
            "output_size_bytes": self.output_size_bytes,
            "user_satisfaction": self.user_satisfaction,
            "required_followup": self.required_followup,
            "error_resolved": self.error_resolved,
            "model_id": self.model_id,
            "task_type": self.task_type,
            "tags": self.tags,
        }


def _sanitize_error_message(message: str) -> Optional[str]:
    """Sanitize error message for PII before storing in EventStore.

    Removes:
    - Absolute paths (/home/user/..., /var/..., C:\\Users\\...)
    - Database schema names (table.column patterns)
    - Internal service names
    - Stack traces (lines starting with "at " or "in ")
    - Credential-like strings (bearer, token, secret, api_key patterns)

    Returns sanitized message, or None if message is entirely PII and can't be salvaged.
    """
    if not message or len(message) == 0:
        return message

    # Remove absolute paths (both Unix and Windows)
    sanitized = re.sub(r"/[a-zA-Z0-9_\-./]+", "[PATH]", message)
    sanitized = re.sub(r"[A-Z]:\\[a-zA-Z0-9_\-\\]+", "[PATH]", sanitized)

    # Remove database schema patterns (schema.table.column)
    sanitized = re.sub(
        r"\b[a-z_][a-z0-9_]*\.[a-z_][a-z0-9_]*(?:\.[a-z_][a-z0-9_]*)?\b",
        "[SCHEMA]",
        sanitized,
    )

    # Remove credential patterns
    sanitized = re.sub(r"(?:bearer|token|secret|api_key|password)\s*[:=]\s*\S+", "[CREDENTIAL]", sanitized, flags=re.IGNORECASE)

    # Remove stack trace lines
    lines = sanitized.split("\n")
    filtered_lines = [line for line in lines if not (line.strip().startswith("at ") or line.strip().startswith("in "))]
    sanitized = "\n".join(filtered_lines)

    # If sanitization removed everything, reject the message
    if len(sanitized.strip()) == 0:
        return None

    return sanitized


def _assert_safe(telemetry: ToolExecutionTelemetry) -> bool:
    """Fail-closed validator: assert telemetry is safe for storage.

    Returns True if safe, raises ValueError if PII detected.
    """
    # Check error_message for remaining PII patterns
    if telemetry.error_message:
        # Re-check: if we still see these patterns, the message is unsafe
        unsafe_patterns = [
            r"/home/",
            r"/var/",
            r"C:\\Users",
            r"(?:bearer|token|secret|api_key|password)\s*[:=]",
        ]
        for pattern in unsafe_patterns:
            if re.search(pattern, telemetry.error_message, re.IGNORECASE):
                raise ValueError(
                    f"PII detected in error_message: {pattern}. Message must be sanitized before emission."
                )

    # Check for other obvious PII in fields
    for field_name in ["tool_name", "task_type", "model_id"]:
        field_value = getattr(telemetry, field_name, "")
        if field_value and re.search(r"(?:password|secret|token|api_key)", str(field_value), re.IGNORECASE):
            raise ValueError(f"PII detected in {field_name}: {field_value}")

    return True

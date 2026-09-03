"""
Centralized telemetry for deprecated Brain/Vibe/Context-v1 API calls.

All deprecated API calls are logged here for:
1. Audit trail (ADR-0314 SkillAuditEvent integration)
2. Telemetry dashboard (track migration progress during Phase B+C)
3. Phase C decision gate (measure: <5 compat calls/day = safe to delete)

Events are tenant-scoped, immutable, and hash-chained per ADR-0232/0233.
"""

import logging
import traceback
import inspect
import signal
from datetime import datetime
from typing import Any, Optional
from dataclasses import dataclass, asdict
from contextlib import contextmanager

logger = logging.getLogger(__name__)

# Try to import audit writer (may not be available in all contexts)
try:
    from core.compliance.audit_chain_writer import get_audit_writer
    AUDIT_WRITER_AVAILABLE = True
except ImportError:
    AUDIT_WRITER_AVAILABLE = False


@contextmanager
def skill_call_timeout(seconds=5):
    """Timeout handler for Skill calls (fail-closed guarantee)."""
    def timeout_handler(signum, frame):
        raise TimeoutError(f"Skill call timed out after {seconds}s")

    original_handler = signal.signal(signal.SIGALRM, timeout_handler)
    signal.alarm(seconds)
    try:
        yield
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, original_handler)


@dataclass(frozen=True)
class DeprecatedAPIEvent:
    """Immutable event for deprecated API calls (audit-safe)."""

    timestamp: str  # ISO 8601
    event_type: str = "deprecated_api_call"
    api_name: str = ""  # e.g., "get_session_context"
    module: str = ""  # e.g., "core.brain.conversation_recall"
    caller_file: str = ""  # File that called the deprecated API
    caller_line: int = 0  # Line number
    caller_func: str = ""  # Function name
    stack_trace: str = ""  # Full stack (for debugging)
    tenant_id: str = "_default"
    task_id: Optional[str] = None
    user_id: Optional[str] = None
    error_message: Optional[str] = None  # If call failed

    def to_dict(self):
        return asdict(self)

    def to_audit_event(self):
        """Convert to ADR-0314 SkillAuditEvent format for audit chain."""
        return {
            "event_type": "deprecated_api_call",
            "timestamp": self.timestamp,
            "tenant_id": self.tenant_id,
            "task_id": self.task_id,
            "user_id": self.user_id,
            "payload": {
                "api_name": self.api_name,
                "module": self.module,
                "caller_file": self.caller_file,
                "caller_line": self.caller_line,
                "caller_func": self.caller_func,
            },
            "metadata": {
                "phase": "A",  # Phase A = marking; Phase B+ = compat layer usage
                "error": self.error_message,
            }
        }


class DeprecatedAPICallLogger:
    """Centralized logging for all deprecated Brain/Vibe/Context-v1 API calls."""

    @staticmethod
    def log_call(
        api_name: str,
        module: str,
        tenant_id: str = "_default",
        task_id: Optional[str] = None,
        user_id: Optional[str] = None,
        **kwargs
    ) -> DeprecatedAPIEvent:
        """
        Log a deprecated API call (CRITICAL-2 FIX: now writes to audit trail).

        Args:
            api_name: e.g., "get_session_context"
            module: e.g., "core.brain.conversation_recall"
            tenant_id: Tenant scope (GDPR Art. 5)
            task_id: Optional task ID for correlation
            user_id: Optional user ID (scrubbed, no PII)
            **kwargs: Extra context (error_message, etc.)

        Returns:
            DeprecatedAPIEvent (immutable audit record)
        """
        frame = inspect.currentframe().f_back
        caller_file = frame.f_code.co_filename
        caller_line = frame.f_lineno
        caller_func = frame.f_code.co_name

        event = DeprecatedAPIEvent(
            timestamp=datetime.utcnow().isoformat() + "Z",
            api_name=api_name,
            module=module,
            caller_file=caller_file,
            caller_line=caller_line,
            caller_func=caller_func,
            stack_trace="".join(traceback.format_stack()),
            tenant_id=tenant_id,
            task_id=task_id,
            user_id=user_id,
            error_message=kwargs.get("error_message"),
        )

        # CRITICAL-2 FIX: Write to immutable audit trail
        if AUDIT_WRITER_AVAILABLE:
            try:
                audit_writer = get_audit_writer()
                audit_writer.write_event_dict(
                    event_type="deprecated_api_call",
                    tenant_id=tenant_id,
                    task_id=task_id,
                    details=event.to_dict()
                )
            except Exception as audit_error:
                logger.error(f"Audit trail write failed: {audit_error}", exc_info=True)
                # Fail-closed: re-raise so caller knows audit failed
                raise

        # Log to structured logger (telemetry)
        logger.warning(
            f"DEPRECATED_API_CALL: {api_name} ({module})",
            extra={
                "event": event.to_dict(),
                "audit": event.to_audit_event(),
            }
        )

        return event

    @staticmethod
    def log_error(
        api_name: str,
        module: str,
        error: Exception,
        tenant_id: str = "_default",
        **kwargs
    ) -> DeprecatedAPIEvent:
        """Log a deprecated API call that raised an exception."""
        return DeprecatedAPICallLogger.log_call(
            api_name=api_name,
            module=module,
            tenant_id=tenant_id,
            error_message=f"{type(error).__name__}: {str(error)}",
            **kwargs
        )


# Public API (singleton-style, stateless)
log_deprecated_call = DeprecatedAPICallLogger.log_call
log_deprecated_error = DeprecatedAPICallLogger.log_error

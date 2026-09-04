"""SkillExecutor — run and monitor skills with safety guardrails (ADR-0307).

This module implements:
1. Skill execution with timeout enforcement
2. Resource limit enforcement (memory, CPU time)
3. Error classification and tracking
4. Execution telemetry (time, success rate, error count)
5. Per-tenant isolation
6. Auto-disable on 3+ consecutive failures
7. Fail-safe: partial results on degradation

Compliance Notes:
- GDPR Art. 32: Timeout prevents resource exhaustion (safety)
- Every execution emits a metadata-only ``skill.executed`` event to the
  tenant core audit chain (GDPR Art. 30) via ``core.skills.skill_audit``
- No PII in error messages (fail-closed by default)
- Tenant isolation enforced on all stats (GDPR Art. 32)
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sys
import time
import traceback
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Callable, Dict, List, Optional

from core.skills.skill_audit import emit_skill_audit
from core.skills.context_isolation import (
    IsolatedTaskContext,
    ContextMutationValidator,
    ContextMerger,
)


class ErrorClass(str, Enum):
    """Classification of execution errors."""

    TIMEOUT = "timeout"  # Exceeded time limit
    RESOURCE = "resource"  # Memory/CPU limit exceeded
    EXCEPTION = "exception"  # Unhandled exception
    PARTIAL = "partial"  # Partial result fallback
    UNKNOWN = "unknown"  # Unclassified error


@dataclass
class ExecutionResult:
    """Result of a skill execution.

    Attributes:
        status: "success", "failure", or "partial"
        output: Return value (None if failed)
        execution_time_ms: Wall-clock time in milliseconds
        error_class: ErrorClass if status != "success", else None
        error_message: Human-readable error description
        timestamp: ISO8601 execution time
        context_state_before_hash: SHA256 of context before execution (for isolated execution)
        context_state_after_hash: SHA256 of context after execution (for isolated execution)
        mutations: Dict of context mutations made by skill (for isolated execution)
    """

    status: str  # "success", "failure", "partial"
    output: Optional[Any]
    execution_time_ms: float
    error_class: Optional[ErrorClass] = None
    error_message: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.utcnow().isoformat())
    context_state_before_hash: Optional[str] = None
    context_state_after_hash: Optional[str] = None
    mutations: Optional[Dict[str, Any]] = None

    def __post_init__(self):
        """Validate result consistency."""
        if self.status == "success":
            assert self.error_class is None, "success result cannot have error_class"
        elif self.status in ["failure", "partial"]:
            assert self.error_class is not None, f"{self.status} result must have error_class"


@dataclass
class ExecutorStats:
    """Aggregated execution statistics for a skill.

    Attributes:
        skill_name: Skill identifier
        total_executions: Total number of execution attempts
        successful_executions: Number of successful runs
        failed_executions: Number of failed runs
        success_rate: Fraction of successful runs [0.0-1.0]
        avg_execution_time_ms: Mean execution time
        recent_errors: Last N error classes (for failure tracking)
        last_execution_time: ISO8601 timestamp of most recent execution
        is_disabled: True if auto-disabled (3+ consecutive failures)
    """

    skill_name: str
    total_executions: int = 0
    successful_executions: int = 0
    failed_executions: int = 0
    success_rate: float = 0.0
    avg_execution_time_ms: float = 0.0
    recent_errors: List[str] = field(default_factory=list)  # Last 10 errors
    last_execution_time: Optional[str] = None
    is_disabled: bool = False

    @classmethod
    def from_executions(
        cls, skill_name: str, results: List[ExecutionResult]
    ) -> ExecutorStats:
        """Compute stats from execution history.

        Args:
            skill_name: Skill identifier
            results: List of ExecutionResult objects

        Returns:
            ExecutorStats with aggregated metrics
        """
        if not results:
            return cls(skill_name=skill_name)

        successes = sum(1 for r in results if r.status == "success")
        failures = sum(1 for r in results if r.status == "failure")
        total = len(results)
        success_rate = successes / total if total > 0 else 0.0
        avg_time = sum(r.execution_time_ms for r in results) / total if total > 0 else 0.0
        recent_errors = [
            r.error_message or r.error_class.value
            for r in results[-10:]
            if r.error_class is not None
        ]

        # Auto-disable if 3+ consecutive failures
        is_disabled = False
        if len(results) >= 3:
            last_three = results[-3:]
            if all(r.status == "failure" for r in last_three):
                is_disabled = True

        return cls(
            skill_name=skill_name,
            total_executions=total,
            successful_executions=successes,
            failed_executions=failures,
            success_rate=success_rate,
            avg_execution_time_ms=avg_time,
            recent_errors=recent_errors,
            last_execution_time=results[-1].timestamp if results else None,
            is_disabled=is_disabled,
        )


class SkillExecutor:
    """Execute skills with monitoring, timeouts, and resource limits.

    Features:
    - Configurable per-skill timeouts (default 30s)
    - Resource limits (memory, CPU time)
    - Error classification and tracking
    - Per-tenant execution stat isolation
    - Auto-disable on 3+ consecutive failures
    - Partial result fallback
    - Full audit trail

    Example:
        >>> executor = SkillExecutor()
        >>> executor.set_timeout("code_analysis", 10000)  # 10 seconds
        >>> result = await executor.execute("tenant_1", skill_fn, {"input": "data"})
        >>> stats = executor.get_execution_stats("tenant_1", "code_analysis")
        >>> if stats.is_disabled:
        ...     print(f"Skill disabled after failures: {stats.recent_errors}")
    """

    def __init__(self):
        """Initialize executor."""
        self._timeouts: Dict[str, float] = {}  # skill_name → timeout_ms
        self._resource_limits: Dict[str, float] = {
            "memory_mb": 512,
            "cpu_ms": 30000,
        }
        # Execution history per tenant: {tenant_id → {skill_name → [ExecutionResult]}}
        self._execution_history: Dict[str, Dict[str, List[ExecutionResult]]] = {}
        self._default_timeout_ms = 30000  # 30 seconds

    def set_timeout(self, skill_name: str, timeout_ms: float) -> None:
        """Set timeout for a specific skill.

        Args:
            skill_name: Skill identifier
            timeout_ms: Timeout in milliseconds
        """
        self._timeouts[skill_name] = timeout_ms

    def get_timeout(self, skill_name: str) -> float:
        """Get configured timeout for a skill (or default if not set).

        Args:
            skill_name: Skill identifier

        Returns:
            Timeout in milliseconds
        """
        return self._timeouts.get(skill_name, self._default_timeout_ms)

    def set_resource_limits(
        self, memory_mb: float = 512, cpu_ms: float = 30000
    ) -> None:
        """Set resource limits for all skill executions.

        Args:
            memory_mb: Memory limit in MB (default 512)
            cpu_ms: CPU time limit in milliseconds (default 30s)
        """
        self._resource_limits["memory_mb"] = memory_mb
        self._resource_limits["cpu_ms"] = cpu_ms

    def get_resource_limits(self) -> Dict[str, float]:
        """Get current resource limits.

        Returns:
            Dict with keys 'memory_mb' and 'cpu_ms'
        """
        return self._resource_limits.copy()

    async def execute(
        self, tenant_id: str, skill: Callable, context: Dict[str, Any]
    ) -> ExecutionResult:
        """Execute a skill with monitoring and error handling.

        Args:
            tenant_id: Tenant identifier (for isolation)
            skill: Async callable that implements the skill
            context: Input context/parameters for the skill

        Returns:
            ExecutionResult with status, output, timing, and errors

        Note:
            - Timeouts are enforced via asyncio.wait_for
            - Resource limits are best-effort (OS-dependent)
            - All errors are classified and logged
            - Per-tenant execution history is maintained
        """
        # Identifier first: ``id`` is the stats/audit key; ``name`` may be a
        # display string ("Test Skill"), ``__name__`` the bare function.
        skill_name = getattr(skill, "id", None) or getattr(skill, "name", None) \
            or getattr(skill, "__name__", "skill")
        start_time = time.time()

        try:
            # Get timeout for this skill
            timeout_s = self.get_timeout(skill_name) / 1000.0  # Convert ms to seconds

            # Execute skill with timeout
            try:
                output = await asyncio.wait_for(skill(**context), timeout=timeout_s)
            except asyncio.TimeoutError:
                elapsed_ms = (time.time() - start_time) * 1000
                result = ExecutionResult(
                    status="failure",
                    output=None,
                    execution_time_ms=elapsed_ms,
                    error_class=ErrorClass.TIMEOUT,
                    error_message=f"Execution exceeded {self.get_timeout(skill_name)}ms timeout",
                )
                self._record_execution(tenant_id, skill_name, result)
                return result

            # Success case
            elapsed_ms = (time.time() - start_time) * 1000
            result = ExecutionResult(
                status="success",
                output=output,
                execution_time_ms=elapsed_ms,
            )
            self._record_execution(tenant_id, skill_name, result)
            return result

        except Exception as e:
            # Capture exception details (sanitized, no PII)
            elapsed_ms = (time.time() - start_time) * 1000
            error_class = self._classify_exception(e)
            error_message = self._sanitize_error_message(e)

            result = ExecutionResult(
                status="failure",
                output=None,
                execution_time_ms=elapsed_ms,
                error_class=error_class,
                error_message=error_message,
            )
            self._record_execution(tenant_id, skill_name, result)
            return result

    async def execute_isolated(
        self,
        tenant_id: str,
        skill_id: str,
        skill: Callable,
        context: Dict[str, Any],
        task_id: str = "unknown",
    ) -> ExecutionResult:
        """Execute skill with context isolation (L4: Copy-on-Write).

        Args:
            tenant_id: Tenant identifier
            skill_id: Skill identifier (for isolation tracking)
            skill: Async callable that implements the skill
            context: Input context (will be isolated)
            task_id: Task ID (for audit trail)

        Returns:
            ExecutionResult with status, output, timing, and context deltas

        Note:
            - Context is isolated before skill execution
            - Mutations are tracked and validated
            - Original context is never modified
            - Isolation is verified before returning
        """
        # Get skill name for executor tracking
        skill_name = getattr(skill, "id", None) or getattr(skill, "name", None) \
            or skill_id or getattr(skill, "__name__", "skill")

        start_time = time.time()

        try:
            # Step 1: Create isolated context (Copy-on-Write)
            try:
                isolated_ctx = IsolatedTaskContext.create_isolated(
                    original_context=context,
                    skill_id=skill_id,
                    task_id=task_id,
                    tenant_id=tenant_id,
                )
            except ValueError as e:
                # Tenant safety check failed
                elapsed_ms = (time.time() - start_time) * 1000
                result = ExecutionResult(
                    status="failure",
                    output=None,
                    execution_time_ms=elapsed_ms,
                    error_class=ErrorClass.EXCEPTION,
                    error_message=f"Context isolation setup failed: {str(e)}",
                )
                self._record_execution(tenant_id, skill_name, result)
                return result

            # Step 2: Execute skill with isolated context
            timeout_s = self.get_timeout(skill_name) / 1000.0

            try:
                # Pass isolated context to skill (as context_copy for mutation)
                skill_output = await asyncio.wait_for(
                    skill(context=isolated_ctx._context_copy),
                    timeout=timeout_s,
                )
            except asyncio.TimeoutError:
                elapsed_ms = (time.time() - start_time) * 1000
                result = ExecutionResult(
                    status="failure",
                    output=None,
                    execution_time_ms=elapsed_ms,
                    error_class=ErrorClass.TIMEOUT,
                    error_message=f"Isolated execution exceeded {self.get_timeout(skill_name)}ms timeout",
                )
                self._record_execution(tenant_id, skill_name, result)
                return result

            # Step 3: Verify isolation is intact
            try:
                isolated_ctx.assert_isolation_intact()
            except RuntimeError as e:
                elapsed_ms = (time.time() - start_time) * 1000
                result = ExecutionResult(
                    status="failure",
                    output=None,
                    execution_time_ms=elapsed_ms,
                    error_class=ErrorClass.EXCEPTION,
                    error_message=f"Isolation violation detected: {str(e)}",
                )
                self._record_execution(tenant_id, skill_name, result)
                return result

            # Step 4: Compute deltas by diffing before/after context
            deltas = self._compute_context_deltas(
                original_context=context,
                modified_context=isolated_ctx._context_copy,
                skill_id=skill_id,
            )

            # Step 5: Create augmented result with deltas
            elapsed_ms = (time.time() - start_time) * 1000
            result = ExecutionResult(
                status="success",
                output=skill_output,
                execution_time_ms=elapsed_ms,
            )

            # Attach isolation metadata (for audit chain linking)
            result.context_state_before_hash = hashlib.sha256(
                json.dumps(context, sort_keys=True, default=str).encode()
            ).hexdigest()
            result.context_state_after_hash = isolated_ctx.get_context_hash()
            result.mutations = deltas

            self._record_execution(tenant_id, skill_name, result)
            return result

        except Exception as e:
            elapsed_ms = (time.time() - start_time) * 1000
            error_class = self._classify_exception(e)
            error_message = self._sanitize_error_message(e)

            result = ExecutionResult(
                status="failure",
                output=None,
                execution_time_ms=elapsed_ms,
                error_class=error_class,
                error_message=error_message,
            )
            self._record_execution(tenant_id, skill_name, result)
            return result

    def _sanitize_error_message(self, exc: Exception) -> str:
        """Return sanitized error message (GDPR Art. 32, no PII)."""
        import re
        exc_type = type(exc).__name__
        exc_msg = str(exc)

        # Remove PII patterns (consistent with feedback_ingester patterns)
        # Note: Ideally these should be imported from core.pii.patterns (TODO: refactor)
        exc_msg = re.sub(r'\b[\w\.-]+@[\w\.-]+\.\w+\b', '[EMAIL]', exc_msg)
        # Phone: dash-separated only (consistent with feedback_ingester line 20)
        exc_msg = re.sub(r'\b\d{3}-\d{3}-\d{4}\b', '[PHONE]', exc_msg)
        # Remove paths only if clearly filesystem paths (more conservative than original)
        exc_msg = re.sub(r'(?:/[^/\s]+)+', '[PATH]', exc_msg)
        # Limit to first 200 chars
        if len(exc_msg) > 200:
            exc_msg = exc_msg[:197] + '...'

        return f"{exc_type}: {exc_msg}"

    def _compute_context_deltas(
        self,
        original_context: Dict[str, Any],
        modified_context: Dict[str, Any],
        skill_id: str,
    ) -> Dict[str, Dict[str, Any]]:
        """Compute deltas between original and modified context by diffing.

        Args:
            original_context: Context state before skill execution
            modified_context: Context state after skill execution
            skill_id: Skill ID (for audit)

        Returns:
            Dict of {path: {old_value, new_value}} for changed paths
        """

        def dict_diff(orig: Any, modified: Any, path: str = "") -> Dict[str, Dict]:
            """Recursively diff two dicts, collecting all changes."""
            changes = {}

            # Check modified keys
            if isinstance(modified, dict):
                for key, new_val in modified.items():
                    new_path = f"{path}.{key}" if path else key

                    if key not in orig:
                        # New key
                        changes[new_path] = {"old_value": None, "new_value": new_val}
                    elif isinstance(new_val, dict) and isinstance(orig.get(key), dict):
                        # Recurse into nested dict
                        nested_changes = dict_diff(orig[key], new_val, new_path)
                        changes.update(nested_changes)
                    elif orig[key] != new_val:
                        # Value changed
                        changes[new_path] = {"old_value": orig[key], "new_value": new_val}

                # Check for deleted keys
                for key in orig:
                    new_path = f"{path}.{key}" if path else key
                    if key not in modified:
                        changes[new_path] = {"old_value": orig[key], "new_value": None}

            return changes

        return dict_diff(original_context, modified_context)

    def _classify_exception(self, exc: Exception) -> ErrorClass:
        """Classify an exception into an ErrorClass.

        Args:
            exc: The exception that occurred

        Returns:
            ErrorClass for this exception
        """
        exc_type = type(exc).__name__

        if "timeout" in exc_type.lower() or "timeout" in str(exc).lower():
            return ErrorClass.TIMEOUT
        elif "memory" in exc_type.lower() or "resource" in str(exc).lower():
            return ErrorClass.RESOURCE
        else:
            return ErrorClass.EXCEPTION

    def _record_execution(
        self, tenant_id: str, skill_name: str, result: ExecutionResult
    ) -> None:
        """Record execution result in history.

        Args:
            tenant_id: Tenant identifier
            skill_name: Skill identifier
            result: ExecutionResult to record
        """
        if tenant_id not in self._execution_history:
            self._execution_history[tenant_id] = {}
        if skill_name not in self._execution_history[tenant_id]:
            self._execution_history[tenant_id][skill_name] = []

        self._execution_history[tenant_id][skill_name].append(result)

        # GDPR Art. 30 — every execution is a link in the tenant core audit
        # chain. METADATA ONLY: identifiers, status, timing, error CLASS —
        # never context, output or the error message text.
        emit_skill_audit(
            tenant_id, "skill.executed", tool=str(skill_name),
            details={
                "skill_id": str(skill_name),
                "status": result.status,
                "latency_ms": round(result.execution_time_ms, 3),
                "error_class": result.error_class.value if result.error_class else None,
            },
            severity="WARNING" if result.status != "success" else None,
        )

        # Keep only last 1000 executions per skill per tenant (memory-bounded)
        if len(self._execution_history[tenant_id][skill_name]) > 1000:
            self._execution_history[tenant_id][skill_name] = (
                self._execution_history[tenant_id][skill_name][-1000:]
            )

    def get_execution_stats(
        self, tenant_id: str, skill_name: str
    ) -> ExecutorStats:
        """Get aggregated execution statistics for a skill.

        Args:
            tenant_id: Tenant identifier
            skill_name: Skill identifier

        Returns:
            ExecutorStats with aggregated metrics

        Note:
            Stats are isolated per tenant (GDPR Art. 32)
        """
        if (
            tenant_id not in self._execution_history
            or skill_name not in self._execution_history[tenant_id]
        ):
            return ExecutorStats(skill_name=skill_name)

        results = self._execution_history[tenant_id][skill_name]
        return ExecutorStats.from_executions(skill_name, results)

    def get_all_stats(self, tenant_id: str) -> Dict[str, ExecutorStats]:
        """Get stats for all skills in a tenant.

        Args:
            tenant_id: Tenant identifier

        Returns:
            Dict mapping skill_name → ExecutorStats
        """
        if tenant_id not in self._execution_history:
            return {}

        return {
            skill_name: ExecutorStats.from_executions(
                skill_name, results
            )
            for skill_name, results in self._execution_history[tenant_id].items()
        }

    def reset_stats(self, tenant_id: str, skill_name: Optional[str] = None) -> None:
        """Reset execution statistics.

        Args:
            tenant_id: Tenant identifier
            skill_name: Skill to reset (None = reset all skills in tenant)
        """
        if tenant_id not in self._execution_history:
            return

        if skill_name is None:
            self._execution_history[tenant_id] = {}
        else:
            self._execution_history[tenant_id].pop(skill_name, None)

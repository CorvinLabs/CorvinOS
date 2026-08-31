"""Metric recorders for engine, workflow, and context metrics.

Phase 2: Engine/Workflow Integration — k=4.

Recorders emit audit events to the tenant's unified hash chain,
which are then aggregated by the KPICollectorDaemon into Prometheus
metrics available at /v1/tenants/{tid}/metrics.

Design constraints
------------------

* **Best-effort audit writes:** failures never crash the dispatcher.
* **Tenant isolation:** all metrics scoped by tenant_id.
* **No blocking I/O:** use asyncio.to_thread for any synchronous writes.
* **Audit-first:** every metric is a first-class audit event in the chain.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# Lazy import of security_events (same pattern as dispatcher.py)
_security_events = None
_REPO = Path(__file__).resolve().parents[3]
_BRIDGES_SHARED = _REPO / "operator" / "bridges" / "shared"
if str(_BRIDGES_SHARED) not in sys.path:
    sys.path.insert(0, str(_BRIDGES_SHARED))

try:
    from forge import security_events as _security_events
except ImportError:
    pass


class EngineMetricsCollector:
    """Collector for engine-level metrics (latency, token usage, errors)."""

    @staticmethod
    def record_success(
        tenant_id: str,
        engine_id: str,
        latency_ms: int,
        tokens_used: int | None = None,
    ) -> None:
        """Record a successful engine execution.

        Args:
            tenant_id: tenant identifier
            engine_id: engine name/identifier
            latency_ms: total execution time in milliseconds
            tokens_used: optional token count (if applicable)
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            details = {
                "engine_id": engine_id,
                "latency_ms": latency_ms,
                "status": "success",
            }
            if tokens_used is not None:
                details["tokens_used"] = tokens_used

            _security_events.write_event(
                chain_path,
                "engine.execution_completed",
                severity="INFO",
                details=details,
                hash_chain=True,
            )
        except Exception:
            # Best-effort: never crash on audit failure
            logger.warning(
                "Failed to record engine.execution_completed for %s:%s",
                tenant_id, engine_id, exc_info=True,
            )

    @staticmethod
    def record_error(
        tenant_id: str,
        engine_id: str,
        error_type: str,
        latency_ms: int,
    ) -> None:
        """Record a failed engine execution.

        Args:
            tenant_id: tenant identifier
            engine_id: engine name/identifier
            error_type: error classification (e.g., "timeout", "crash")
            latency_ms: time until failure in milliseconds
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "engine.execution_failed",
                severity="WARNING",
                details={
                    "engine_id": engine_id,
                    "error_type": error_type,
                    "latency_ms": latency_ms,
                    "status": "failed",
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record engine.execution_failed for %s:%s",
                tenant_id, engine_id, exc_info=True,
            )


class WorkflowMetricsCollector:
    """Collector for workflow-level metrics (completion time, status)."""

    @staticmethod
    def record_completion_time(
        tenant_id: str,
        workflow_id: str,
        status: str,
        duration_ms: int,
    ) -> None:
        """Record workflow completion with duration and status.

        Args:
            tenant_id: tenant identifier
            workflow_id: workflow identifier
            status: terminal status (e.g., "completed", "failed")
            duration_ms: total workflow duration in milliseconds
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "workflow.completed",
                severity="INFO",
                details={
                    "workflow_id": workflow_id,
                    "status": status,
                    "duration_ms": duration_ms,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record workflow.completed for %s:%s",
                tenant_id, workflow_id, exc_info=True,
            )


class ContextMetricsCollector:
    """Collector for execution context metrics (stack depth, memory)."""

    @staticmethod
    def record_push(
        tenant_id: str,
        context_id: str,
        context_size_bytes: int,
    ) -> None:
        """Record a context push (entry into nested scope).

        Args:
            tenant_id: tenant identifier
            context_id: context identifier
            context_size_bytes: size of context in bytes
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "context.push",
                severity="DEBUG",
                details={
                    "context_id": context_id,
                    "size_bytes": context_size_bytes,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record context.push for %s:%s",
                tenant_id, context_id, exc_info=True,
            )

    @staticmethod
    def record_pop(
        tenant_id: str,
        context_id: str,
    ) -> None:
        """Record a context pop (exit from nested scope).

        Args:
            tenant_id: tenant identifier
            context_id: context identifier
        """
        if _security_events is None:
            return

        try:
            from forge import paths as _fp
            chain_path = _fp.tenant_global_dir(tenant_id) / "forge" / "audit.jsonl"

            _security_events.write_event(
                chain_path,
                "context.pop",
                severity="DEBUG",
                details={
                    "context_id": context_id,
                },
                hash_chain=True,
            )
        except Exception:
            logger.warning(
                "Failed to record context.pop for %s:%s",
                tenant_id, context_id, exc_info=True,
            )


__all__ = [
    "EngineMetricsCollector",
    "WorkflowMetricsCollector",
    "ContextMetricsCollector",
]

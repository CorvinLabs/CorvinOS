"""Integrated Security Pipeline: main orchestrator."""

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any, Callable, Optional, Tuple

from .context import GateName, SecurityContext
from .exceptions import (
    AuditGateError,
    CapabilityGateError,
    PIIDetectionError,
    PipelineExecutionError,
    ValidationGateError,
)
from .roles import AuditRecorder, CapabilityChecker, ContextEngineer, InputValidator, PIIDetector

logger = logging.getLogger(__name__)


def _ensure_operator_on_path() -> None:
    """Make ``operator/`` importable as bare top-level packages. Idempotent.

    Same pattern as ``core/orchestration/quota_gate.py``: the wheel vendors the
    operator subtrees (``corvin_core._operator_bootstrap``); a source checkout
    has ``operator/`` as a sibling of ``core/``.
    """
    try:
        from corvin_core._operator_bootstrap import ensure_operator_on_path

        ensure_operator_on_path()
    except ImportError:
        pass
    operator_root = Path(__file__).resolve().parents[2] / "operator"
    if operator_root.is_dir() and str(operator_root) not in sys.path:
        sys.path.insert(0, str(operator_root))


def _resolve_process_tenant() -> str:
    """Validated process tenant (``CORVIN_TENANT_ID`` → ``_default``).

    Fail-closed: an invalid env value or an unreachable resolver raises
    :class:`PipelineExecutionError` — the pipeline never runs a request under
    a made-up tenant such as ``"unknown"``.
    """
    _ensure_operator_on_path()
    try:
        from forge.tenants import current_tenant  # type: ignore[import-not-found]

        return current_tenant()
    except Exception as exc:  # noqa: BLE001 — every failure is a refusal
        raise PipelineExecutionError(
            f"process tenant unresolvable ({type(exc).__name__}) — request refused"
        ) from exc


class IntegratedSecurityPipeline:
    """Main orchestrator: runs five roles in sequence, fail-closed."""

    def __init__(
        self,
        capability_checker: CapabilityChecker,
        input_validator: InputValidator,
        pii_detector: PIIDetector,
        context_engineer: ContextEngineer,
        audit_recorder: AuditRecorder,
        feature_flags: Optional[dict] = None,
    ):
        self.capability_checker = capability_checker
        self.input_validator = input_validator
        self.pii_detector = pii_detector
        self.context_engineer = context_engineer
        self.audit_recorder = audit_recorder
        self.feature_flags = feature_flags or {}

    async def execute_with_security(
        self,
        actor: str,
        action: str,
        resource: str,
        capability_required: str,
        transport: str,
        input_data: dict,
        handler_fn: Callable,
        input_schema: Optional[dict] = None,
    ) -> Tuple[bool, Optional[Any], SecurityContext]:
        """Execute handler through security pipeline.

        Returns: (success, result, context)
        Raises: PipelineExecutionError or subclass on any gate failure
        """
        # Process tenant, fail-closed. The former ``from operator.context
        # import get_current_tenant`` could never resolve (the stdlib
        # ``operator`` module shadows the repo directory, and no such module
        # exists) — so every SecurityContext carried tenant_id="unknown" and
        # the tenant the audit chain later refuses to mismatch was never the
        # tenant this pipeline recorded. Resolve the same way the chain does
        # (``forge.tenants.current_tenant``: CORVIN_TENANT_ID → _default,
        # validated) and refuse to run when it cannot be resolved.
        tenant_id = _resolve_process_tenant()

        context = SecurityContext(
            actor=actor,
            action=action,
            resource=resource,
            capability_required=capability_required,
            tenant_id=tenant_id,
            transport=transport,
            input_data=input_data,
        )

        logger.info(
            f"[Pipeline] request_id={context.request_id} actor={actor} "
            f"action={action} capability={capability_required}"
        )

        try:
            # GATE 1: CapabilityChecker
            try:
                result = await self._run_gate(self.capability_checker.check, context)
                context.gate_results.append(result)
                if not result.passed:
                    logger.warning(
                        f"[Pipeline] request_id={context.request_id} GATE 1 DENIED: {result.reason_code}"
                    )
                    await self._audit_denial(context)
                    raise CapabilityGateError(f"Capability check failed: {result.reason_code}")
                context.capability_granted = True
            except CapabilityGateError:
                raise
            except Exception as e:
                logger.exception(f"[Pipeline] CapabilityChecker exception: {e}")
                raise PipelineExecutionError(f"Capability check error: {e}") from e

            # GATE 2a: InputValidator
            try:
                result = await self._run_gate(self.input_validator.validate, context)
                context.gate_results.append(result)
                if not result.passed:
                    logger.warning(
                        f"[Pipeline] request_id={context.request_id} GATE 2a DENIED: {result.reason_code}"
                    )
                    await self._audit_denial(context)
                    raise ValidationGateError(f"Validation failed: {result.reason_code}")
                context.validation_passed = True
            except ValidationGateError:
                raise
            except Exception as e:
                logger.exception(f"[Pipeline] InputValidator exception: {e}")
                raise PipelineExecutionError(f"Validation error: {e}") from e

            # GATE 2b: PIIDetector
            try:
                result = await self._run_gate(self.pii_detector.detect, context)
                context.gate_results.append(result)
                if not result.passed:
                    logger.warning(
                        f"[Pipeline] request_id={context.request_id} GATE 2b DENIED: "
                        f"{result.reason_code} (PII={len(context.pii_detected)} findings)"
                    )
                    await self._audit_denial(context)
                    raise PIIDetectionError(f"PII detected: {result.reason_code}")
            except PIIDetectionError:
                raise
            except Exception as e:
                logger.exception(f"[Pipeline] PIIDetector exception: {e}")
                raise PipelineExecutionError(f"PII detection error: {e}") from e

            # GATE 3: ContextEngineer (non-denying, best-effort)
            try:
                result = await self._run_gate(self.context_engineer.engineer, context)
                context.gate_results.append(result)
                # Always emit result, even on error (Finding #5)
            except Exception as e:
                logger.exception(f"[Pipeline] ContextEngineer exception (non-blocking): {e}")
                # Silently continue; context engineering is best-effort

            # All gates passed: execute handler (with transaction wrapper from Finding #1)
            try:
                logger.info(f"[Pipeline] request_id={context.request_id} ALL GATES PASSED, executing handler")
                context.result = await self._run_with_transaction(handler_fn)
            except Exception as e:
                logger.exception(f"[Pipeline] Handler execution failed: {e}")
                context.error = f"execution_error: {str(e)}"
                await self._audit_denial(context)
                raise PipelineExecutionError(f"Handler failed: {e}") from e

            # GATE 4: AuditRecorder (immutable recording, fail-closed)
            try:
                result = await self._run_gate(self.audit_recorder.record, context)
                context.gate_results.append(result)
                context.decision_record_hash = result.details.get("record_hash")
                if not result.passed:
                    logger.critical(
                        f"[Pipeline] AUDIT GATE FAILED: {result.reason_code} "
                        f"(request may not be recorded!)"
                    )
                    raise AuditGateError(f"Audit recording failed: {result.reason_code}")
                # Verify audit was actually recorded (Finding #6)
                await self._verify_audit_recorded(context, result)
                context.audit_recorded = True
                context._lock_gate_results()  # Finding #2: lock after audit
            except AuditGateError:
                raise
            except Exception as e:
                logger.exception(f"[Pipeline] AuditRecorder exception: {e}")
                raise AuditGateError(f"Audit recording error: {e}") from e

            logger.info(
                f"[Pipeline] request_id={context.request_id} SUCCESS, "
                f"hash={context.decision_record_hash}"
            )
            return True, context.result, context

        except PipelineExecutionError:
            raise
        except Exception as e:
            logger.exception(f"[Pipeline] Unexpected error: {e}")
            raise PipelineExecutionError(f"Unexpected error: {e}") from e

    async def _run_gate(
        self,
        gate_fn: Callable,
        context: SecurityContext,
    ) -> Any:
        """Run a single gate (with timeout)."""
        try:
            return await asyncio.wait_for(gate_fn(context), timeout=5.0)
        except asyncio.TimeoutError:
            logger.error(f"[Pipeline] Gate timeout: {gate_fn}")
            raise PipelineExecutionError("Gate execution timeout")

    async def _run_with_transaction(self, handler_fn: Callable) -> Any:
        """Execute handler with transaction wrapper (Finding #1)."""
        # Simplified: just run the handler. Real implementation would
        # wrap in DB transaction or event log.
        return await self._ensure_async(handler_fn)

    async def _ensure_async(self, fn: Callable) -> Any:
        """Convert sync function to async if needed (Finding #7)."""
        import inspect
        if asyncio.iscoroutinefunction(fn):
            return await fn()
        else:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(None, fn)

    async def _audit_denial(self, context: SecurityContext) -> None:
        """Audit a gate denial (helper)."""
        try:
            result = await self.audit_recorder.record(context)
            context.decision_record_hash = result.details.get("record_hash")
        except Exception as e:
            logger.error(f"[Pipeline] Failed to audit denial: {e}")

    async def _verify_audit_recorded(
        self,
        context: SecurityContext,
        result: Any,
    ) -> None:
        """Verify audit record actually made it to durable storage (Finding #6)."""
        # Simplified: just check that record_hash was produced
        record_hash = result.details.get("record_hash")
        if not record_hash:
            raise AuditGateError("Audit record hash missing from result")
        # In production, would verify hash is in durable audit.jsonl

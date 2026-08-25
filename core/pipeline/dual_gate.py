"""
Dual-Gate Pipeline: Capability + Validation + Audit

Fail-closed: both gates must pass before execution.
ContextVar-based for transport-agnostic isolation.

Gates:
  Gate 1: Capability Check (authorization, roles, tiers)
  Gate 2: Validation Gate (input validation, PII detection, queue integrity)
  Gate 3: Audit Recording (immutable hash-chained audit trail)

ADR-0300: Dual-Gate Context Pipeline
ADR-0296: Input Validators
ADR-0297: PII Detection
ADR-0298: Queue Integrity
ADR-0299: Audit Durability
"""

import asyncio
import logging
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, Callable, Optional, Dict, List

logger = logging.getLogger(__name__)

# ContextVars for pipeline state
_current_actor: ContextVar[Optional[str]] = ContextVar(
    "pipeline_actor", default=None
)
_current_capability: ContextVar[Optional[str]] = ContextVar(
    "pipeline_capability", default=None
)
_current_tenant_id: ContextVar[Optional[str]] = ContextVar(
    "pipeline_tenant_id", default=None
)
_current_resource: ContextVar[Optional[str]] = ContextVar(
    "pipeline_resource", default=None
)
_current_validation_state: ContextVar[Optional[Dict[str, Any]]] = ContextVar(
    "pipeline_validation_state", default=None
)


@dataclass
class ValidationState:
    """Validation gate state tracking."""

    passed: bool = False  # Did validation pass?
    pii_detected: bool = False  # Was PII detected?
    validation_errors: List[str] = field(default_factory=list)  # Error messages
    pii_findings: List[Dict[str, Any]] = field(default_factory=list)  # PII details
    queue_integrity_ok: bool = True  # Queue integrity check passed?
    checked_fields: List[str] = field(default_factory=list)  # Which fields were checked?


@dataclass
class PipelineContext:
    """Pipeline execution context."""

    actor: str  # Who is performing the action
    capability: str  # What capability is required
    action: str  # Action name (e.g., "read", "write", "delete")
    resource: str  # Resource being accessed
    tenant_id: str  # Tenant context
    details: Optional[dict[str, Any]] = None  # Additional metadata

    # Validation gate inputs
    input_data: Optional[Dict[str, Any]] = None  # Data to validate
    validator_rules: Optional[Dict[str, Any]] = None  # Custom validation rules

    # Validation gate outputs (populated during execution)
    validation_state: Optional[ValidationState] = field(default_factory=ValidationState)


class PipelineExecutionError(Exception):
    """Base pipeline execution error."""

    pass


class CapabilityGateError(PipelineExecutionError):
    """Gate 1: Capability gate check failed."""

    pass


class ValidationGateError(PipelineExecutionError):
    """Gate 2a: Input validation gate failed."""

    pass


class PIIDetectionError(PipelineExecutionError):
    """Gate 2b: PII detected in input (fail-closed)."""

    pass


class QueueIntegrityError(PipelineExecutionError):
    """Gate 2c: Queue integrity check failed."""

    pass


class AuditGateError(PipelineExecutionError):
    """Gate 3: Audit gate check failed."""

    pass


class DualGatePipeline:
    """
    Fail-closed dual-gate pipeline: Capability → Validation → Audit → Execute.

    Three-gate architecture:
      Gate 1: Capability check (auth, roles, tiers) — fail-closed
      Gate 2: Validation gate (input validation, PII detection, queue integrity) — fail-closed
      Gate 3: Audit recording (immutable hash-chained trail) — fail-closed

    All gates must pass. Any failure is audited and the operation is rejected.
    """

    def __init__(
        self,
        audit_chain: Any,
        capability_checker: Any,
        pii_detector: Optional[Any] = None,
        validator_factory: Optional[Any] = None,
        queue_monitor: Optional[Any] = None,
        feature_flags: Optional[Dict[str, bool]] = None,
    ):
        """
        Initialize pipeline with all gate components.

        Args:
            audit_chain: AuditChain instance (ADR-0299)
            capability_checker: CapabilityRegistry instance (ADR-0302)
            pii_detector: PIIDetector instance (ADR-0297) — optional
            validator_factory: ValidatorFactory instance (ADR-0296) — optional
            queue_monitor: QueueIntegrityMonitor instance (ADR-0298) — optional
            feature_flags: Dict of feature flags (default: all off)
        """
        self.audit_chain = audit_chain
        self.capability_checker = capability_checker
        self.pii_detector = pii_detector
        self.validator_factory = validator_factory
        self.queue_monitor = queue_monitor
        self.feature_flags = feature_flags or {}

        # Feature flag: enable dual-gate validation
        # Validation gate: optional (feature flag)
        self._validation_enabled = self.feature_flags.get(
            "dual_gate_pipeline_enabled", False
        )
        # PII Detection: optional (feature flag, default OFF)
        self._pii_detection_enabled = self.feature_flags.get(
            "dual_gate_pii_detection_enabled", False
        )
        # Queue Integrity: optional (feature flag)
        self._queue_integrity_enabled = self.feature_flags.get(
            "dual_gate_queue_integrity_enabled", False
        )

    def _list_available_validators(self) -> List[str]:
        """
        List all available validator methods (for error messages).

        Returns:
            List of callable methods in validator_factory
        """
        if not self.validator_factory:
            return []
        return [
            name
            for name in dir(self.validator_factory)
            if not name.startswith("_") and callable(getattr(self.validator_factory, name))
        ]

    def set_context(self, context: PipelineContext) -> None:
        """Set execution context via ContextVars."""
        _current_actor.set(context.actor)
        _current_capability.set(context.capability)
        _current_tenant_id.set(context.tenant_id)
        _current_resource.set(context.resource)
        _current_validation_state.set(
            {
                "pii_detected": False,
                "validation_errors": [],
                "pii_findings": [],
            }
        )

    def get_actor(self) -> Optional[str]:
        """Get current actor from context."""
        return _current_actor.get()

    def get_capability(self) -> Optional[str]:
        """Get required capability from context."""
        return _current_capability.get()

    def get_tenant_id(self) -> Optional[str]:
        """Get tenant ID from context."""
        return _current_tenant_id.get()

    def get_resource(self) -> Optional[str]:
        """Get resource from context."""
        return _current_resource.get()

    def validate_input(
        self, context: PipelineContext
    ) -> tuple[bool, List[str]]:
        """
        Gate 2a: Validate input data against configured rules (fail-closed).

        Args:
            context: Pipeline context with input_data and validator_rules

        Returns:
            Tuple of (is_valid, errors_list)

        Raises:
            ValidationGateError if validation check fails structurally
        """
        if not self._validation_enabled or context.input_data is None:
            return True, []

        try:
            if not self.validator_factory:
                # No validator configured, skip validation
                return True, []

            errors = []
            input_data = context.input_data or {}
            rules = context.validator_rules or {}

            # Validate each field against its rules
            for field_name, field_value in input_data.items():
                if field_name not in rules:
                    continue  # No rules for this field, skip

                rule_spec = rules[field_name]
                validator_type = rule_spec.get("type")

                # Verify validator method exists (fail-closed)
                if not hasattr(self.validator_factory, validator_type):
                    # Fail-closed: if validator is misconfigured, reject
                    raise ValidationGateError(
                        f"Validator not found: {validator_type}. "
                        f"Possible validator types: {self._list_available_validators()}"
                    )

                validator_func = getattr(self.validator_factory, validator_type)

                # Verify it's callable
                if not callable(validator_func):
                    raise ValidationGateError(
                        f"Validator {validator_type} is not callable"
                    )

                try:
                    # Call validator with tenant context
                    result = validator_func(
                        field_value,
                        tenant_id=context.tenant_id,
                        **rule_spec.get("options", {}),
                    )

                    if hasattr(result, "is_valid"):  # ValidationResult dataclass
                        if not result.is_valid:
                            errors.append(f"{field_name}: {result.error_message}")
                    elif not result:  # Boolean result
                        errors.append(f"{field_name}: validation failed")

                except Exception as e:
                    errors.append(f"{field_name}: {str(e)}")

            is_valid = len(errors) == 0
            if context.validation_state:
                context.validation_state.validation_errors = errors
                context.validation_state.checked_fields = list(input_data.keys())

            return is_valid, errors

        except ValidationGateError:
            raise  # Re-raise validation gate errors
        except Exception as e:
            raise ValidationGateError(f"Validation check failed: {e}") from e

    def detect_pii(self, context: PipelineContext) -> tuple[bool, List[Dict]]:
        """
        Gate 2b: Detect PII in input data (fail-closed).

        Args:
            context: Pipeline context with input_data

        Returns:
            Tuple of (pii_detected, findings_list)

        Raises:
            PIIDetectionError if PII is found or detection fails
        """
        if not self._pii_detection_enabled or context.input_data is None:
            return False, []

        try:
            if not self.pii_detector:
                # No PII detector configured, skip
                return False, []

            findings = []
            input_data = context.input_data or {}

            # Scan all input values for PII
            for field_name, field_value in input_data.items():
                if isinstance(field_value, str):
                    try:
                        detection = self.pii_detector.detect(
                            field_value, tenant_id=context.tenant_id
                        )
                        if detection:
                            findings.append(
                                {
                                    "field": field_name,
                                    "pii_class": detection.pii_class,
                                    "confidence": detection.confidence,
                                    "pattern": detection.source_pattern,
                                }
                            )
                    except Exception as e:
                        # PII detection failed (fail-closed)
                        raise PIIDetectionError(
                            f"PII detection failed for {field_name}: {e}"
                        ) from e

            pii_detected = len(findings) > 0
            if context.validation_state:
                context.validation_state.pii_detected = pii_detected
                context.validation_state.pii_findings = findings

            if pii_detected:
                raise PIIDetectionError(
                    f"PII detected in {len(findings)} field(s): "
                    f"{', '.join(f['field'] for f in findings)}"
                )

            return pii_detected, findings

        except PIIDetectionError:
            raise  # Re-raise PII errors
        except Exception as e:
            raise PIIDetectionError(f"PII detection error: {e}") from e

    def check_queue_integrity(self, context: PipelineContext) -> tuple[bool, str]:
        """
        Gate 2c: Check queue integrity status (fail-closed).

        Args:
            context: Pipeline context with queue_id if applicable

        Returns:
            Tuple of (is_ok, status_message)

        Raises:
            QueueIntegrityError if queue integrity fails
        """
        if not self._queue_integrity_enabled:
            return True, "queue_integrity_disabled"

        try:
            if not self.queue_monitor:
                # No queue monitor configured, skip
                return True, "queue_monitor_not_configured"

            # Check queue integrity by calling the monitor
            # Verify queue monitor has check_integrity method
            if not hasattr(self.queue_monitor, "check_integrity"):
                # Fallback: try is_healthy method if check_integrity not available
                if hasattr(self.queue_monitor, "is_healthy"):
                    is_ok = self.queue_monitor.is_healthy()
                    status_message = "queue_integrity_ok" if is_ok else "queue_integrity_failed"
                else:
                    # Fail-closed: queue monitor is misconfigured (no check methods)
                    raise QueueIntegrityError(
                        "Queue monitor misconfigured: neither check_integrity() nor is_healthy() method found"
                    )
            else:
                # Call monitor's check_integrity method (implementation-specific)
                try:
                    result = self.queue_monitor.check_integrity(
                        tenant_id=context.tenant_id
                    )
                    # Result can be: bool, or object with .is_ok attribute
                    if hasattr(result, "is_ok"):
                        is_ok = result.is_ok
                        status_message = getattr(result, "message", "queue_integrity_ok")
                    else:
                        is_ok = bool(result)
                        status_message = "queue_integrity_ok" if is_ok else "queue_integrity_failed"
                except Exception as check_err:
                    # If the check itself fails (network error, etc.), fail-closed
                    raise QueueIntegrityError(
                        f"Queue integrity check failed: {check_err}"
                    ) from check_err

            if context.validation_state:
                context.validation_state.queue_integrity_ok = is_ok

            if not is_ok:
                raise QueueIntegrityError(f"Queue integrity check failed: {status_message}")

            return is_ok, status_message

        except QueueIntegrityError:
            raise  # Re-raise queue errors
        except Exception as e:
            raise QueueIntegrityError(f"Queue integrity check error: {e}") from e

    def check_capability(
        self, actor: str, capability: str, tenant_id: str
    ) -> bool:
        """
        Gate 1: Check if actor has capability (fail-closed).

        Returns:
            True if actor has capability
            False otherwise (denial)

        Raises:
            CapabilityGateError if check fails structurally (gate broken, not denied)
        """
        try:
            return self.capability_checker.has_capability(
                actor=actor, capability=capability, tenant_id=tenant_id
            )
        except (ValueError, AttributeError, KeyError) as e:
            # Expected errors: missing field, wrong type, etc.
            raise CapabilityGateError(
                f"Capability check failed for {actor}/{capability}: {e}"
            )
        except Exception as e:
            # Unexpected errors: database down, connection lost, etc.
            logger.exception(f"GATE BROKEN (not denied): {e}")
            raise CapabilityGateError(
                f"Capability gate broken (not denied): {e}"
            )

    def record_audit(
        self,
        event_type: str,
        actor: str,
        action: str,
        resource: str,
        result: str,
        tenant_id: str,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        """
        Gate 2: Record audit entry atomically (fail-closed).

        Args:
            event_type: Type of event (e.g., "auth", "write", "delete")
            actor: Who performed the action
            action: What was done
            resource: Resource being accessed
            result: "success" | "failure"
            tenant_id: Tenant context
            details: Additional metadata

        Raises:
            AuditGateError if audit recording fails
        """
        try:
            from core.audit import AuditEntry

            entry = AuditEntry(
                event_type=event_type,
                actor=actor,
                action=action,
                resource=resource,
                result=result,
                timestamp=self._get_timestamp(),
                tenant_id=tenant_id,
                details=details or {},
            )
            self.audit_chain.record(entry)
        except Exception as e:
            raise AuditGateError(f"Audit recording failed: {e}")

    def execute_guarded(
        self,
        context: PipelineContext,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Execute function through three gates (fail-closed).

        Flow:
        1. Gate 1: Check capability (fail-closed)
        2. Gate 2: Validate input + detect PII + check queue (fail-closed)
        3. Gate 3: Record audit entry (pre-execution)
        4. Execute function
        5. Post-audit: Record success/failure

        Args:
            context: Pipeline context
            func: Function to execute
            *args, **kwargs: Arguments to func

        Returns:
            Result from func

        Raises:
            CapabilityGateError if Gate 1 fails
            ValidationGateError if Gate 2a fails
            PIIDetectionError if Gate 2b fails
            QueueIntegrityError if Gate 2c fails
            AuditGateError if audit record fails
            Exception from func if execution fails
        """
        # Initialize context and validation state
        self.set_context(context)
        if context.validation_state is None:
            context.validation_state = ValidationState()

        # Gate 1: Capability check (fail-closed)
        if not self.check_capability(
            context.actor, context.capability, context.tenant_id
        ):
            # Audit the denial
            self.record_audit(
                event_type="capability_denied",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "capability_denied", "gate": "capability"},
            )
            raise CapabilityGateError(
                f"Actor {context.actor} lacks capability {context.capability}"
            )

        # Gate 2a: Input validation (fail-closed, if enabled)
        try:
            is_valid, errors = self.validate_input(context)
            if not is_valid:
                self.record_audit(
                    event_type="validation_failed",
                    actor=context.actor,
                    action=context.action,
                    resource=context.resource,
                    result="failure",
                    tenant_id=context.tenant_id,
                    details={"reason": "validation_failed", "errors": errors, "gate": "validation"},
                )
                raise ValidationGateError(
                    f"Input validation failed: {'; '.join(errors)}"
                )
        except ValidationGateError:
            raise  # Re-raise validation errors
        except Exception as e:
            self.record_audit(
                event_type="validation_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "validation_error", "error": str(e), "gate": "validation"},
            )
            raise

        # Gate 2b: PII detection (fail-closed, if enabled)
        try:
            pii_detected, findings = self.detect_pii(context)
            # PIIDetectionError is raised by detect_pii if PII is found
        except PIIDetectionError as e:
            self.record_audit(
                event_type="pii_detected",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "pii_detected", "findings_count": len(context.validation_state.pii_findings), "gate": "pii"},
            )
            raise  # Re-raise PII errors
        except Exception as e:
            self.record_audit(
                event_type="pii_detection_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "pii_detection_error", "error": str(e), "gate": "pii"},
            )
            raise

        # Gate 2c: Queue integrity (fail-closed, if enabled)
        try:
            is_ok, status = self.check_queue_integrity(context)
            # QueueIntegrityError is raised if check fails
        except QueueIntegrityError as e:
            self.record_audit(
                event_type="queue_integrity_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "queue_integrity_failed", "gate": "queue"},
            )
            raise  # Re-raise queue errors
        except Exception as e:
            self.record_audit(
                event_type="queue_integrity_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "queue_integrity_error", "error": str(e), "gate": "queue"},
            )
            raise

        # Gate 3: Record audit entry (pre-execution)
        self.record_audit(
            event_type="operation",
            actor=context.actor,
            action=context.action,
            resource=context.resource,
            result="pending",
            tenant_id=context.tenant_id,
            details={**(context.details or {}), "validation_passed": True},
        )

        # Execute function
        try:
            result = func(*args, **kwargs)

            # Post-execution success audit
            self.record_audit(
                event_type="operation_complete",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="success",
                tenant_id=context.tenant_id,
                details={"output_type": type(result).__name__},
            )

            return result
        except Exception as e:
            # Post-execution failure audit
            self.record_audit(
                event_type="operation_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"error_type": type(e).__name__, "error": str(e)},
            )
            raise

    async def execute_guarded_async(
        self,
        context: PipelineContext,
        func: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """
        Async variant of execute_guarded (same three-gate flow).

        Flow:
        1. Gate 1: Check capability (fail-closed)
        2. Gate 2: Validate input + detect PII + check queue (fail-closed)
        3. Gate 3: Record audit entry (pre-execution)
        4. Execute async function
        5. Post-audit: Record success/failure
        """
        # Initialize context and validation state
        self.set_context(context)
        if context.validation_state is None:
            context.validation_state = ValidationState()

        # Gate 1: Capability check (fail-closed)
        if not self.check_capability(
            context.actor, context.capability, context.tenant_id
        ):
            # Audit the denial
            self.record_audit(
                event_type="capability_denied",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "capability_denied", "gate": "capability"},
            )
            raise CapabilityGateError(
                f"Actor {context.actor} lacks capability {context.capability}"
            )

        # Gate 2a: Input validation (fail-closed, if enabled)
        try:
            is_valid, errors = self.validate_input(context)
            if not is_valid:
                self.record_audit(
                    event_type="validation_failed",
                    actor=context.actor,
                    action=context.action,
                    resource=context.resource,
                    result="failure",
                    tenant_id=context.tenant_id,
                    details={"reason": "validation_failed", "errors": errors, "gate": "validation"},
                )
                raise ValidationGateError(
                    f"Input validation failed: {'; '.join(errors)}"
                )
        except ValidationGateError:
            raise
        except Exception as e:
            self.record_audit(
                event_type="validation_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "validation_error", "error": str(e), "gate": "validation"},
            )
            raise

        # Gate 2b: PII detection (fail-closed, if enabled)
        try:
            pii_detected, findings = self.detect_pii(context)
        except PIIDetectionError as e:
            self.record_audit(
                event_type="pii_detected",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "pii_detected", "findings_count": len(context.validation_state.pii_findings), "gate": "pii"},
            )
            raise
        except Exception as e:
            self.record_audit(
                event_type="pii_detection_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "pii_detection_error", "error": str(e), "gate": "pii"},
            )
            raise

        # Gate 2c: Queue integrity (fail-closed, if enabled)
        try:
            is_ok, status = self.check_queue_integrity(context)
        except QueueIntegrityError as e:
            self.record_audit(
                event_type="queue_integrity_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "queue_integrity_failed", "gate": "queue"},
            )
            raise
        except Exception as e:
            self.record_audit(
                event_type="queue_integrity_error",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"reason": "queue_integrity_error", "error": str(e), "gate": "queue"},
            )
            raise

        # Gate 3: Record audit entry (pre-execution)
        self.record_audit(
            event_type="operation",
            actor=context.actor,
            action=context.action,
            resource=context.resource,
            result="pending",
            tenant_id=context.tenant_id,
            details={**(context.details or {}), "validation_passed": True},
        )

        # Execute async function
        try:
            if asyncio.iscoroutinefunction(func):
                result = await func(*args, **kwargs)
            else:
                result = func(*args, **kwargs)

            # Post-execution success audit
            self.record_audit(
                event_type="operation_complete",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="success",
                tenant_id=context.tenant_id,
                details={"output_type": type(result).__name__},
            )

            return result
        except Exception as e:
            # Post-execution failure audit
            self.record_audit(
                event_type="operation_failed",
                actor=context.actor,
                action=context.action,
                resource=context.resource,
                result="failure",
                tenant_id=context.tenant_id,
                details={"error_type": type(e).__name__, "error": str(e)},
            )
            raise

    @staticmethod
    def _get_timestamp() -> str:
        """Get ISO 8601 timestamp."""
        from datetime import datetime, timezone

        return datetime.now(timezone.utc).isoformat()

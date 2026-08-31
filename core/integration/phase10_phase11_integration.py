"""Phase 10 (Input Validation) ← → Phase 11 (Dual-Gate Pipeline) Integration

This module demonstrates how Phase 10 input validators integrate with Phase 11's
DualGatePipeline Gate 2a (input validation gate).

Integration pattern:
  1. @validate_input decorator (Phase 10) performs fail-closed input validation
  2. Validation state propagates to DualGatePipeline.validate_input() (Phase 11)
  3. Both gates must pass before handler execution
  4. Invalid input is audited and rejected (403, 400, 422)

ADR-0297: Input Validation Integration
ADR-0300: Dual-Gate Pipeline
"""

from __future__ import annotations

from typing import Any, Dict, Optional
from dataclasses import dataclass

from core.validation.route_validators import ValidateInputError
from core.pipeline.dual_gate import DualGatePipeline, PipelineContext, ValidationGateError


@dataclass
class IntegrationValidationResult:
    """Result of Phase 10 + Phase 11 integrated validation."""

    phase10_passed: bool  # Phase 10 decorator validation
    phase11_passed: bool  # Phase 11 pipeline Gate 2a validation
    combined_valid: bool  # Both gates passed
    errors: list[str]  # Combined error messages
    source: str  # Which stage failed (phase10/phase11/both)


class Phase10Phase11Integrator:
    """Coordinate Phase 10 and Phase 11 validation stages.

    Fail-closed: both validators must pass for input to be accepted.
    """

    def __init__(self, pipeline: DualGatePipeline):
        """Initialize integrator with DualGatePipeline.

        Args:
            pipeline: DualGatePipeline instance (Phase 11)
        """
        self.pipeline = pipeline

    def validate_request_input(
        self,
        context: PipelineContext,
        phase10_validator_errors: Optional[list[str]] = None,
    ) -> IntegrationValidationResult:
        """Validate input through both Phase 10 and Phase 11.

        Args:
            context: Pipeline context with input_data
            phase10_validator_errors: Errors from Phase 10 @validate_input decorator

        Returns:
            IntegrationValidationResult with both validation stages

        Raises:
            ValidationGateError if either phase fails
        """
        phase10_passed = True
        phase10_errors = phase10_validator_errors or []

        # Phase 10 validation already occurred (via decorator)
        if phase10_errors:
            phase10_passed = False

        # Phase 11 validation: Gate 2a (dual-gate pipeline)
        try:
            phase11_passed, phase11_errors = self.pipeline.validate_input(context)
        except Exception as e:
            phase11_passed = False
            phase11_errors = [str(e)]

        # Combine results
        combined_valid = phase10_passed and phase11_passed
        all_errors = phase10_errors + phase11_errors

        # Determine source of failure
        if not phase10_passed and not phase11_passed:
            source = "both"
        elif not phase10_passed:
            source = "phase10"
        elif not phase11_passed:
            source = "phase11"
        else:
            source = "none"

        result = IntegrationValidationResult(
            phase10_passed=phase10_passed,
            phase11_passed=phase11_passed,
            combined_valid=combined_valid,
            errors=all_errors,
            source=source,
        )

        # Fail-closed: if either phase fails, raise
        if not combined_valid:
            raise ValidationGateError(
                f"Input validation failed ({source}): {'; '.join(all_errors)}"
            )

        return result

    def get_validation_summary(
        self, result: IntegrationValidationResult
    ) -> Dict[str, Any]:
        """Get human-readable validation summary.

        Args:
            result: IntegrationValidationResult from validate_request_input

        Returns:
            Dict suitable for error response or audit logging
        """
        return {
            "valid": result.combined_valid,
            "phase10": {
                "passed": result.phase10_passed,
                "stage": "Input Validation Decorator",
            },
            "phase11": {
                "passed": result.phase11_passed,
                "stage": "Dual-Gate Pipeline Gate 2a",
            },
            "failure_source": result.source,
            "error_count": len(result.errors),
            "errors": result.errors[:5],  # Show first 5 errors
        }


# Integration constants
PHASE10_PHASE11_INTEGRATION_ENABLED = True  # Feature flag
VALIDATION_FAIL_CLOSED_RESPONSE_CODES = {
    "phase10": 400,  # @validate_input decorator error
    "phase11": 422,  # Dual-gate pipeline validation error
    "both": 400,  # Both phases failed
}


__all__ = [
    "Phase10Phase11Integrator",
    "IntegrationValidationResult",
    "PHASE10_PHASE11_INTEGRATION_ENABLED",
    "VALIDATION_FAIL_CLOSED_RESPONSE_CODES",
]

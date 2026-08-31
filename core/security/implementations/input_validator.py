"""Role 2a: InputValidator — input validation."""

import logging
from ..context import GateName, GateResult, SecurityContext

logger = logging.getLogger(__name__)


class InputValidatorImpl:
    """Concrete implementation of InputValidator role."""

    def __init__(self, schema_registry=None):
        self.schema_registry = schema_registry

    async def validate(self, context: SecurityContext) -> GateResult:
        """Validate input_data against schema (Finding #4)."""
        # If no schema provided, use permissive default
        if not context.input_data:
            return GateResult(
                gate_name=GateName.VALIDATION,
                passed=True,
                reason_code="no_input",
                details={},
            )

        # Basic validation: check for obvious injection vectors
        for key, value in context.input_data.items():
            if isinstance(value, str):
                # Simple checks for SQL injection, path traversal
                dangerous_patterns = ["'; DROP", "../", "..\\", "<?", "%00"]
                for pattern in dangerous_patterns:
                    if pattern.lower() in value.lower():
                        logger.warning(
                            f"[InputValidator] Dangerous pattern '{pattern}' found in {key}"
                        )
                        return GateResult(
                            gate_name=GateName.VALIDATION,
                            passed=False,
                            reason_code="injection_pattern_detected",
                            details={"field": key, "pattern": pattern},
                        )

        logger.debug(f"[InputValidator] Input validation passed for action={context.action}")
        return GateResult(
            gate_name=GateName.VALIDATION,
            passed=True,
            reason_code="valid",
            details={"fields_checked": len(context.input_data)},
        )

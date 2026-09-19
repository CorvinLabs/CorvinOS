"""Role 3: ContextEngineer — context selection via CEL."""

import logging
from ..context import GateName, GateResult, SecurityContext

logger = logging.getLogger(__name__)


class ContextEngineerImpl:
    """Concrete implementation of ContextEngineer role (Finding #8)."""

    def __init__(self, cel_pipeline=None):
        """Initialize with CEL pipeline (ADR-0269)."""
        self.cel = cel_pipeline

    async def engineer(self, context: SecurityContext) -> GateResult:
        """Build context via CEL (non-denying, best-effort)."""
        try:
            if self.cel is None:
                # Phase 1: permissive (empty context)
                context.context_brief = {
                    "sources": [],
                    "confidence": "medium",
                    "tokens": 0,
                }
                logger.debug("[ContextEngineer] No CEL pipeline; using empty context")
                return GateResult(
                    gate_name=GateName.CONTEXT_ENGINEERING,
                    passed=True,
                    reason_code="no_cel",
                    details={},
                )

            # Call CEL (would integrate ADR-0269 here)
            cel_input = {
                "actor": context.actor,
                "action": context.action,
                "input_data": context.input_data,
            }
            # Simplified: no actual CEL call in Phase 1
            context.context_brief = {
                "sources": [],
                "confidence": "medium",
                "tokens": 0,
            }

            logger.debug("[ContextEngineer] Context built successfully")
            return GateResult(
                gate_name=GateName.CONTEXT_ENGINEERING,
                passed=True,
                reason_code="ok",
                details={"sources": 0},
            )
        except Exception as e:
            logger.exception(f"[ContextEngineer] Exception (non-blocking): {e}")
            # Finding #5: Always emit gate result, even on error
            return GateResult(
                gate_name=GateName.CONTEXT_ENGINEERING,
                passed=True,  # Still non-blocking
                reason_code="context_engineering_exception",
                details={"error": str(e)},
            )

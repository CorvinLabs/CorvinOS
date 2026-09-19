"""Role 3: ContextEngineer — context selection via CEL (ADR-0532 L10 wiring)."""

import logging
from ..context import GateName, GateResult, SecurityContext

logger = logging.getLogger(__name__)

# Phase 2 Blocker 2 Fix: Import os.context_adapter Skill (ADR-0555)
try:
    from ...skills.os_skills.context_adapter import ContextAdapterSkill
except ImportError:
    ContextAdapterSkill = None


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

            # Phase 2 Blocker 2 Fix: Wire os.context_adapter Skill (ADR-0555 + ADR-0532)
            if ContextAdapterSkill is not None:
                skill = ContextAdapterSkill()
                adapter_input = {
                    "actor": context.actor,
                    "action": context.action,
                    "input_data": context.input_data,
                    "tenant_id": getattr(context, 'tenant_id', '_default'),
                }
                context_output = await skill.execute(adapter_input)
                context.context_brief = context_output.get("context_brief", {
                    "sources": [],
                    "confidence": "medium",
                    "tokens": 0,
                })
                logger.debug(f"[ContextEngineer] os.context_adapter wired and executed (sources={len(context.context_brief.get('sources', []))})")
            else:
                # Fallback: empty context (non-blocking fail-open per ADR-0300)
                context.context_brief = {
                    "sources": [],
                    "confidence": "medium",
                    "tokens": 0,
                }
                logger.warning("[ContextEngineer] os.context_adapter Skill unavailable; using empty context (fail-open)")

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
